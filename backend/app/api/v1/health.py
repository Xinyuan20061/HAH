import json
from uuid import uuid4
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.time import business_today, utc_iso, utc_now
from app.models import (
    AIJob,
    AIWorkerNode,
    FoodAnalysisSession,
    HealthCheckIn,
    HealthGoalSetting,
    MotionScore,
    PlanTaskState,
)
from app.schemas.health_extra import CheckInIn, PlanTaskIn, CustomPlanIn
from app.services.agent.actions import execute_action
from app.services.ai.gateway import get_provider
from app.services.dynamic_goals import apply_adjustment, evaluate_dynamic_goals
from app.services.health import (
    energy_dashboard,
    get_goal_settings,
    streak_summary,
    today_summary,
    trend_7d,
)
from app.services.timeline import add_event

router = APIRouter(prefix="/health", tags=["health"])


class GoalSettingsIn(BaseModel):
    water_target: int = Field(1800, ge=500, le=6000)
    sleep_target: float = Field(8, ge=4, le=12)
    exercise_target: int = Field(30, ge=5, le=180)
    steps_target: int = Field(8000, ge=1000, le=50000)
    protein_target: float = Field(90, ge=20, le=300)
    calorie_target: int = Field(2000, ge=1000, le=5000)
    weekly_checkin_target: int = Field(5, ge=1, le=7)


@router.get("/today")
def today(user=Depends(current_user), db: Session = Depends(get_db)):
    return today_summary(db, user.id, user.profile)


@router.get("/checkin/today")
def get_checkin(user=Depends(current_user), db: Session = Depends(get_db)):
    d = business_today().isoformat()
    item = db.scalar(
        select(HealthCheckIn).where(
            HealthCheckIn.user_id == user.id, HealthCheckIn.record_date == d
        )
    )
    return {
        "water_ml": item.water_ml if item else 0,
        "sleep_hours": item.sleep_hours if item else 0,
        "weight_kg": item.weight_kg
        if item
        else (user.profile.weight_kg if user.profile else 0),
        "steps": item.steps if item else 0,
        "mood": item.mood if item else "normal",
    }


@router.put("/checkin/today")
def save_checkin(
    body: CheckInIn, user=Depends(current_user), db: Session = Depends(get_db)
):
    d = business_today().isoformat()
    item = db.scalar(
        select(HealthCheckIn).where(
            HealthCheckIn.user_id == user.id, HealthCheckIn.record_date == d
        )
    ) or HealthCheckIn(user_id=user.id, record_date=d)
    for k, v in body.model_dump().items():
        setattr(item, k, v)
    db.add(item)
    db.flush()
    add_event(
        db,
        user.id,
        "checkin",
        body.model_dump(mode="json"),
        ref_type="checkin",
        ref_id=item.id,
    )
    db.commit()
    db.refresh(item)
    if user.profile and body.weight_kg > 0:
        user.profile.weight_kg = body.weight_kg
        db.add(user.profile)
        db.commit()
    return {"ok": True, "date": d, "streak": streak_summary(db, user.id)}


@router.get("/trends/7d")
def trends(user=Depends(current_user), db: Session = Depends(get_db)):
    return {"days": trend_7d(db, user.id)}


@router.get("/energy-dashboard")
def get_energy_dashboard(
    user=Depends(current_user), db: Session = Depends(get_db)
):
    return energy_dashboard(db, user.id, user.profile)


@router.get("/streak")
def streak(user=Depends(current_user), db: Session = Depends(get_db)):
    return streak_summary(db, user.id)


@router.get("/goals")
def goals(user=Depends(current_user), db: Session = Depends(get_db)):
    return get_goal_settings(db, user.id, user.profile)


@router.put("/goals")
def save_goals(
    body: GoalSettingsIn, user=Depends(current_user), db: Session = Depends(get_db)
):
    item = db.scalar(
        select(HealthGoalSetting).where(HealthGoalSetting.user_id == user.id)
    ) or HealthGoalSetting(user_id=user.id)
    for k, v in body.model_dump().items():
        setattr(item, k, v)
    db.add(item)
    db.flush()
    add_event(
        db,
        user.id,
        "goals_updated",
        body.model_dump(mode="json"),
        source="user",
        ref_type="health_goals",
        ref_id=item.id,
    )
    db.commit()
    db.refresh(item)
    return {"ok": True, **get_goal_settings(db, user.id, user.profile)}


def _plan_items(summary):
    items = []
    observed = summary.get("observed") or {}
    if not observed.get("diet"):
        items.append(
            {
                "task_key": "diet_record",
                "type": "饮食",
                "title": "记录一餐真实饮食",
                "desc": "先留下真实记录，再根据数据给出营养建议，未记录不等于摄入不足。",
            }
        )
    elif summary["protein"] < summary["protein_target"] * 0.6:
        items.append(
            {
                "task_key": "protein",
                "type": "饮食",
                "title": "补足优质蛋白",
                "desc": "下一餐加入鸡蛋、鱼禽肉、豆制品或奶类中的一种。",
            }
        )
    else:
        items.append(
            {
                "task_key": "balanced_meal",
                "type": "饮食",
                "title": "保持均衡饮食",
                "desc": "继续保持蔬菜、优质蛋白和适量主食的组合。",
            }
        )
    items.append(
        {
            "task_key": "exercise",
            "type": "运动",
            "title": f"完成 {summary['exercise_target']} 分钟活动",
            "desc": "快走、慢跑或力量训练都可以，优先选择你能持续的方式。",
        }
    )
    items.append(
        {
            "task_key": "water",
            "type": "习惯",
            "title": "完成今日饮水目标",
            "desc": f"把饮水分散到全天，今日目标约 {summary['water_target']} ml，可根据活动量适度调整。",
        }
    )
    return items[:3]


def _plan_snapshot(db: Session, user_id: int, profile=None, summary=None):
    d = business_today().isoformat()
    summary = summary or today_summary(db, user_id, profile)
    items = _plan_items(summary)
    states = {
        x.task_key: x.done
        for x in db.scalars(
            select(PlanTaskState).where(
                PlanTaskState.user_id == user_id, PlanTaskState.record_date == d
            )
        ).all()
    }
    for item in items:
        item["done"] = states.get(item["task_key"], False)
    custom = db.scalars(
        select(PlanTaskState)
        .where(
            PlanTaskState.user_id == user_id,
            PlanTaskState.record_date == d,
            PlanTaskState.task_type == "custom",
        )
        .order_by(PlanTaskState.id.asc())
    ).all()
    for row in custom:
        items.append(
            {
                "task_key": row.task_key,
                "type": "custom",
                "custom": True,
                "title": row.title or row.task_key,
                "desc": row.description or "",
                "done": row.done,
            }
        )
    return {
        "headline": "今天只做三件真正有用的事",
        "done_count": sum(1 for x in items if x["done"]),
        "items": items,
    }


@router.get("/plan/today")
def plan(user=Depends(current_user), db: Session = Depends(get_db)):
    return _plan_snapshot(db, user.id, user.profile)


@router.get("/plan/activity/year")
def plan_activity_year(user=Depends(current_user), db: Session = Depends(get_db)):
    """Return a rolling 365-day completion map for the daily plan."""
    end = business_today()
    start = end - timedelta(days=364)
    states = db.scalars(
        select(PlanTaskState).where(
            PlanTaskState.user_id == user.id,
            PlanTaskState.record_date >= start.isoformat(),
            PlanTaskState.record_date <= end.isoformat(),
        )
    ).all()
    by_date: dict[str, list[PlanTaskState]] = {}
    for state in states:
        by_date.setdefault(state.record_date, []).append(state)

    days = []
    complete_days = 0
    partial_days = 0
    for offset in range(365):
        current = start + timedelta(days=offset)
        key = current.isoformat()
        rows = by_date.get(key, [])
        custom = [row for row in rows if row.task_type == "custom"]
        # The generated daily plan has three base tasks; custom tasks extend
        # the denominator so completing one stored task cannot appear as 100%.
        total = 3 + len(custom)
        done = min(total, sum(1 for row in rows if row.done))
        completion = done / total if total else 0
        status = "complete" if done == total else "partial" if done else "empty"
        complete_days += int(status == "complete")
        partial_days += int(status == "partial")
        days.append(
            {
                "date": key,
                "done": done,
                "total": total,
                "completion": round(completion, 3),
                "status": status,
            }
        )
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": days,
        "summary": {
            "complete_days": complete_days,
            "partial_days": partial_days,
            "active_days": complete_days + partial_days,
        },
    }


def _command_focus(summary: dict, plan_data: dict, active_job: AIJob | None):
    observed = summary.get("observed") or {}
    if active_job:
        is_motion = active_job.job_type == "motion_pose"
        return {
            "key": "active_analysis",
            "eyebrow": "分析正在进行",
            "title": "动作视频正在分析" if is_motion else "餐食图片正在识别",
            "reason": "任务已安全保存在云端，可以进入详情页查看进度。",
            "cta": "查看进度",
            "route": "/pages/media/index" if is_motion else "/pages/scan/index",
            "tone": "processing",
        }
    if not observed.get("checkin"):
        return {
            "key": "checkin",
            "eyebrow": "建议先做",
            "title": "用 30 秒记录今天的身体状态",
            "reason": "补齐睡眠、饮水和步数后，今日建议才会基于真实数据。",
            "cta": "开始记录",
            "route": "/pages/checkin/index",
            "tone": "calm",
        }
    if not observed.get("diet"):
        return {
            "key": "food",
            "eyebrow": "下一步",
            "title": "拍下今天的一餐",
            "reason": "识别结果会先给出估算区间，确认或校正后才写入健康记录。",
            "cta": "拍照识餐",
            "route": "/pages/scan/index",
            "tone": "food",
        }
    if not observed.get("exercise") or summary.get("exercise_min", 0) < summary.get(
        "exercise_target", 30
    ):
        return {
            "key": "motion",
            "eyebrow": "今日重点",
            "title": "完成一段可坚持的运动",
            "reason": "可直接记录时长，也可以上传短视频获取动作质量反馈。",
            "cta": "开始运动",
            "route": "/pages/workout/index",
            "tone": "motion",
        }
    if plan_data["done_count"] < len(plan_data["items"]):
        return {
            "key": "plan",
            "eyebrow": "接近完成",
            "title": "把今天剩下的计划收个尾",
            "reason": f"还有 {len(plan_data['items']) - plan_data['done_count']} 项未完成，选择最容易的一项即可。",
            "cta": "查看计划",
            "route": "/pages/plan/index",
            "tone": "plan",
        }
    return {
        "key": "complete",
        "eyebrow": "今天完成得很好",
        "title": "保持节奏，不必继续加码",
        "reason": "健康管理的价值来自长期稳定，而不是单日追求满分。",
        "cta": "查看趋势",
        "route": "/pages/trends/index",
        "tone": "complete",
    }


@router.get("/command-center")
def command_center(user=Depends(current_user), db: Session = Depends(get_db)):
    """One read model for the mini-program's daily decision surface."""
    summary = today_summary(db, user.id, user.profile)
    plan_data = _plan_snapshot(db, user.id, user.profile, summary)
    streak_data = streak_summary(db, user.id)

    jobs = db.scalars(
        select(AIJob)
        .where(AIJob.user_id == user.id)
        .order_by(AIJob.created_at.desc())
        .limit(6)
    ).all()
    active_states = {"queued", "processing", "retry_wait", "waiting_source_refresh"}
    active_job = next((job for job in jobs if job.status in active_states), None)

    threshold = utc_now() - timedelta(seconds=settings.worker_offline_after_seconds)
    nodes = db.scalars(
        select(AIWorkerNode).order_by(AIWorkerNode.last_seen_at.desc()).limit(10)
    ).all()
    live_nodes = [node for node in nodes if node.last_seen_at >= threshold]
    capabilities = set()
    for node in live_nodes:
        try:
            values = json.loads(node.capabilities_json or "[]")
            if isinstance(values, list):
                capabilities.update(str(value) for value in values)
        except (TypeError, ValueError):
            continue

    latest_motion = db.scalar(
        select(MotionScore)
        .where(MotionScore.user_id == user.id)
        .order_by(MotionScore.created_at.desc())
        .limit(1)
    )
    latest_food = db.scalar(
        select(FoodAnalysisSession)
        .where(FoodAnalysisSession.user_id == user.id)
        .order_by(FoodAnalysisSession.created_at.desc())
        .limit(1)
    )

    observed = summary.get("observed") or {}
    observed_keys = [
        key for key in ("diet", "exercise", "checkin") if observed.get(key)
    ]
    metric_values = []
    if observed.get("diet"):
        metric_values.append(
            min(
                100, round(summary["protein"] / max(summary["protein_target"], 1) * 100)
            )
        )
    if observed.get("exercise"):
        metric_values.append(
            min(
                100,
                round(
                    summary["exercise_min"] / max(summary["exercise_target"], 1) * 100
                ),
            )
        )
    if observed.get("checkin"):
        metric_values.extend(
            [
                min(
                    100,
                    round(summary["water_ml"] / max(summary["water_target"], 1) * 100),
                ),
                min(
                    100,
                    round(
                        summary["sleep_hours"] / max(summary["sleep_target"], 1) * 100
                    ),
                ),
                min(
                    100, round(summary["steps"] / max(summary["steps_target"], 1) * 100)
                ),
            ]
        )

    return {
        "version": "3.0",
        "date": business_today().isoformat(),
        "today": summary,
        "plan": plan_data,
        "streak": streak_data,
        "focus": _command_focus(summary, plan_data, active_job),
        "data_quality": {
            "sources_observed": len(observed_keys),
            "sources_total": 3,
            "observed": observed_keys,
            "score": round(sum(metric_values) / len(metric_values))
            if metric_values
            else None,
            "score_metrics": len(metric_values),
            "message": "数据越完整，建议越个性化"
            if len(observed_keys) < 3
            else "今日关键数据已完整",
        },
        "ai_system": {
            "online": bool(live_nodes),
            "motion_ready": "motion_pose" in capabilities,
            "food_ready": "food_vision" in capabilities,
            "kinetics400_ready": "kinetics400" in capabilities,
            "active_jobs": sum(job.status in active_states for job in jobs),
            "last_seen_at": utc_iso(live_nodes[0].last_seen_at) if live_nodes else None,
        },
        "latest": {
            "motion": {
                "score": round(latest_motion.overall),
                "confidence": round(latest_motion.confidence, 3),
                "exercise_type": latest_motion.exercise_type,
                "created_at": utc_iso(latest_motion.created_at),
            }
            if latest_motion
            else None,
            "food": {
                "confidence": round(latest_food.confidence, 3),
                "status": latest_food.status,
                "created_at": utc_iso(latest_food.created_at),
            }
            if latest_food
            else None,
        },
    }


@router.put("/plan/today/{task_key}")
def update_plan(
    task_key: str,
    body: PlanTaskIn,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    d = business_today().isoformat()
    item = db.scalar(
        select(PlanTaskState).where(
            PlanTaskState.user_id == user.id,
            PlanTaskState.record_date == d,
            PlanTaskState.task_key == task_key,
        )
    ) or PlanTaskState(user_id=user.id, record_date=d, task_key=task_key)
    item.done = body.done
    db.add(item)
    db.flush()
    add_event(
        db,
        user.id,
        "daily_plan_completed" if body.done else "daily_plan_reopened",
        {"task_key": task_key, "date": d},
        source="user",
        ref_type="plan_task",
        ref_id=item.id,
    )
    db.commit()
    return {"ok": True, "task_key": task_key, "done": body.done}


@router.post("/plan/today/custom")
def create_custom_plan(
    body: CustomPlanIn,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    d = business_today().isoformat()
    title = body.title.strip()
    if not title:
        raise HTTPException(422, "计划内容不能为空")
    task_key = "custom:" + uuid4().hex[:10]
    item = PlanTaskState(
        user_id=user.id,
        record_date=d,
        task_key=task_key,
        done=False,
        title=title,
        description=body.description.strip(),
        task_type="custom",
    )
    db.add(item)
    db.flush()
    add_event(
        db,
        user.id,
        "custom_plan_created",
        {"task_key": task_key, "title": item.title, "date": d},
        source="user",
        ref_type="plan_task",
        ref_id=item.id,
    )
    db.commit()
    return {
        "ok": True,
        "item": {
            "task_key": task_key,
            "custom": True,
            "title": item.title,
            "desc": item.description or "",
            "done": False,
        },
    }


@router.delete("/plan/today/custom/{task_key}")
def delete_custom_plan(
    task_key: str,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    d = business_today().isoformat()
    if not task_key.startswith("custom:"):
        raise HTTPException(400, "只能删除手动添加的计划")
    item = db.scalar(
        select(PlanTaskState).where(
            PlanTaskState.user_id == user.id,
            PlanTaskState.record_date == d,
            PlanTaskState.task_key == task_key,
        )
    )
    if not item:
        raise HTTPException(404, "计划不存在")
    db.delete(item)
    db.commit()
    return {"ok": True, "task_key": task_key}


@router.post("/goals/dynamic/evaluate")
def dynamic_goal_evaluate(
    window_days: int = 14, user=Depends(current_user), db: Session = Depends(get_db)
):
    window_days = max(7, min(30, int(window_days)))
    return evaluate_dynamic_goals(db, user, window_days, persist=True)


@router.post("/goals/dynamic/{adjustment_id}/apply")
def dynamic_goal_apply(
    adjustment_id: int, user=Depends(current_user), db: Session = Depends(get_db)
):
    def perform():
        a = apply_adjustment(db, user.id, adjustment_id)
        if not a:
            raise LookupError("目标调整建议不存在或已过期")
        add_event(
            db,
            user.id,
            "goal_adjustment_applied",
            {
                "metric": a.metric,
                "previous_target": a.previous_target,
                "new_target": a.recommended_target,
                "window_days": a.window_days,
            },
            source="rule_engine",
            ref_type="goal_adjustment",
            ref_id=a.id,
        )
        db.flush()
        return {
            "adjustment_id": a.id,
            "metric": a.metric,
            "new_target": a.recommended_target,
            "goals": get_goal_settings(db, user.id, user.profile),
        }

    try:
        action = execute_action(
            db,
            user.id,
            "goal.adjustment.apply",
            perform,
            confirmed=True,
            source="user",
            input_data={"adjustment_id": adjustment_id},
        )
    except LookupError:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="目标调整建议不存在或已过期")
    return {"ok": True, "action_audit_id": action["audit_id"], **action["result"]}


@router.post("/goals/dynamic/explain")
async def dynamic_goal_explain(
    user=Depends(current_user), db: Session = Depends(get_db)
):
    evaluation = evaluate_dynamic_goals(db, user, 14, persist=False)
    changed = [
        x for x in evaluation["items"] if x["decision"] in {"increase", "reduce"}
    ]
    if not changed:
        return {
            "summary": "目前没有需要调整的目标。数据不足时系统会保持原目标，避免把漏记误判成执行失败。",
            "provider": "rule-engine",
        }
    system = "你是 HealthMate 目标解释助手。数值已经由规则引擎决定，你不能更改任何数值，只能用克制、非医疗化的中文解释为什么这样调整。"
    prompt = f"规则结果：{json.dumps(changed, ensure_ascii=False)}。用100字以内解释，强调单次小幅调整、可持续性和用户可自行决定是否应用。"
    try:
        r = await get_provider(user).chat(system, prompt)
        return {"summary": r.text, "provider": r.provider}
    except Exception:
        return {
            "summary": "DeepSeek 暂不可用，以下为规则解释。系统只对有足够记录的数据做小幅调整：长期难以完成时适当降低，稳定完成后再小幅递增。是否应用由你决定。",
            "provider": "rules-fallback",
            "degraded": True,
        }
