import json, re, time
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.api.deps import current_user
from app.core.database import get_db
from app.services.ai.gateway import get_provider
from app.services.weekly_facts import build_weekly_facts
from app.services.evaluation import record_metric

router = APIRouter(prefix="/insights", tags=["insights"])

from app.core.json_output import json_object as _json


def _compat_report(facts):
    days = []
    for x in facts["days"]:
        row = dict(x)
        row["checkin"] = bool(x["observed"]["checkin"])
        days.append(row)
    return {
        "range": facts["period"]["label"],
        "score": facts["score"],
        "averages": {k: (0 if v is None else v) for k, v in facts["averages"].items()},
        "highlights": facts["highlights"],
        "days": days,
        "goals": facts["goals"],
        "streak": facts["streak"],
        "coverage": facts["coverage"],
        "changes": facts["changes"],
        "completion": facts["completion"],
        "data_quality": facts["data_quality"],
        "facts_version": facts["facts_version"],
    }


@router.get("/weekly-facts")
def weekly_facts(user=Depends(current_user), db: Session = Depends(get_db)):
    return build_weekly_facts(user, db, 7, persist=True)


@router.get("/weekly-report")
def weekly_report(user=Depends(current_user), db: Session = Depends(get_db)):
    return _compat_report(build_weekly_facts(user, db, 7, persist=True))


@router.post("/weekly-report/ai-summary")
async def weekly_ai_summary(user=Depends(current_user), db: Session = Depends(get_db)):
    started = time.perf_counter()
    facts = build_weekly_facts(user, db, 7, persist=True)
    system = "你是谨慎、克制的健康周报助手。程序已经算好事实，你只能解释这些事实，不得补造数据，不诊断疾病，不制造焦虑。"
    prompt = f"""以下 JSON 是程序事实层，不允许更改或臆测：{json.dumps(facts, ensure_ascii=False)}\n只返回JSON：{{"title":"一句话标题","summary":"80字以内总结","wins":["最多2条"],"focus":"下周最值得关注的一件事","action":"一个可执行的小行动","caution":"健康边界提醒"}}。必须根据 data_quality/coverage 表述置信度；未记录日期不是0。"""
    try:
        r = await get_provider(user).chat(system, prompt)
        data = _json(r.text)
        provider = r.provider
    except Exception:
        data = {}
        provider = "rules-fallback"
    if not isinstance(data.get("summary"), str) or not data["summary"]:
        provider = "rules-fallback"
        a = facts["averages"]
        g = facts["goals"]
        quality = facts["data_quality"]["confidence"]
        parts = []
        if a["sleep_hours"] is not None:
            parts.append(f"有记录日期平均睡眠 {a['sleep_hours']} 小时")
        if a["exercise_min"] is not None:
            parts.append(f"平均运动 {a['exercise_min']} 分钟")
        if a["water_ml"] is not None:
            parts.append(f"平均饮水 {round(a['water_ml'])} ml")
        summary = (
            "、".join(parts)
            if parts
            else "本周有效记录还不够多，暂时更适合继续积累数据。"
        )
        focus = "继续提高记录完整度"
        action = "明天完成一次简短健康打卡，让趋势判断更可靠。"
        if a["water_ml"] is not None and a["water_ml"] < g["water_target"] * 0.75:
            focus = "让饮水更均匀"
            action = "上午和下午各固定准备一杯水，分散完成目标。"
        elif (
            a["sleep_hours"] is not None and a["sleep_hours"] < g["sleep_target"] * 0.85
        ):
            focus = "优先恢复睡眠节奏"
            action = "今晚把上床时间提前 20 分钟，并减少临睡前高强度训练。"
        elif (
            a["exercise_min"] is not None
            and a["exercise_min"] < g["exercise_target"] * 0.7
        ):
            focus = "把运动拆小"
            action = "先安排一次 15–20 分钟快走或基础力量训练。"
        data = {
            "title": "这一周，先看稳定性，不看完美",
            "summary": summary + f"。当前数据置信度为 {quality}。",
            "wins": facts["highlights"][:2],
            "focus": focus,
            "action": action,
            "caution": "记录和 AI 总结仅用于日常健康管理参考，不替代专业医疗判断。",
        }
    elapsed = (time.perf_counter() - started) * 1000
    record_metric(
        db,
        user.id,
        "weekly_summary_latency_ms",
        elapsed,
        "ms",
        "weekly_report",
        True,
        {"provider": provider},
    )
    db.commit()
    data["degraded"] = provider == "rules-fallback"
    data["summary"] = (
        ("DeepSeek 暂不可用，以下是规则摘要。" + data["summary"])
        if data["degraded"]
        else data["summary"]
    )
    data["provider"] = provider
    data["facts_version"] = facts["facts_version"]
    return data


class WorkoutPlanIn(BaseModel):
    goal: str = Field(default="综合体能", min_length=1, max_length=120)
    days: int = Field(default=4, ge=1, le=7)
    minutes: int = Field(default=35, ge=10, le=120)
    level: str = Field(default="初级", max_length=40)
    equipment: str = Field(default="徒手", max_length=120)


@router.post("/workout-plan")
async def workout_plan(
    body: WorkoutPlanIn, user=Depends(current_user), db: Session = Depends(get_db)
):
    goal, days, minutes, level, equipment = (
        body.goal,
        body.days,
        body.minutes,
        body.level,
        body.equipment,
    )
    profile = user.profile
    system = "你是谨慎的健身计划助手。只生成一般健康人群的生活健身建议；出现疼痛、术后、疾病治疗等情况应提示先咨询专业人士。"
    prompt = f"""为用户生成一周健身计划，目标={goal}，每周{days}天，每次{minutes}分钟，水平={level}，器械={equipment}，年龄={getattr(profile, "age", 20)}，身高={getattr(profile, "height_cm", 170)}，体重={getattr(profile, "weight_kg", 65)}。只返回JSON：{{"title":"","summary":"","days":[{{"day":"周一","focus":"","duration":35,"exercises":[{{"name":"","sets":"","reps":"","rest":""}}]}}],"tips":[""]}}。days数组只保留训练日。"""
    try:
        r = await get_provider(user).chat(system, prompt)
        data = _json(r.text)
        provider = r.provider
    except Exception:
        data = {}
        provider = "rules-fallback"
    if (
        not isinstance(data.get("days"), list)
        or not data["days"]
        or not isinstance(data.get("summary"), str)
    ):
        provider = "rules-fallback"
        data = {
            "title": f"{goal} · {days}日计划",
            "summary": "循序渐进，优先保证动作质量。",
            "days": [
                {
                    "day": f"训练日 {i + 1}",
                    "focus": "全身基础",
                    "duration": minutes,
                    "exercises": [
                        {
                            "name": "深蹲",
                            "sets": "3组",
                            "reps": "10-12次",
                            "rest": "60秒",
                        },
                        {
                            "name": "俯卧撑/跪姿俯卧撑",
                            "sets": "3组",
                            "reps": "8-12次",
                            "rest": "60秒",
                        },
                        {
                            "name": "平板支撑",
                            "sets": "3组",
                            "reps": "30秒",
                            "rest": "45秒",
                        },
                    ],
                }
                for i in range(days)
            ],
            "tips": ["训练前热身5分钟", "出现明显疼痛时停止训练"],
        }
    data["degraded"] = provider == "rules-fallback"
    data["summary"] = (
        ("DeepSeek 暂不可用，以下是规则计划。" + data["summary"])
        if data["degraded"]
        else data["summary"]
    )
    data["provider"] = provider
    return data
