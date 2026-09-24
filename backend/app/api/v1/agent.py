import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.deps import current_user
from app.core.database import get_db
from app.core.time import utc_iso, utc_now
from app.models import EvaluationEvent
from app.schemas.agent import (
    AgentRequest,
    AgentPlanItemUpdate,
    AgentInsightFeedback,
    AgentExperimentStart,
)
from app.services.agent.orchestrator import (
    agent_stats,
    respond,
    apply_plan,
    current_plan,
    update_plan_item,
)
from app.services.agent.tools import read_context
from app.services.agent.actions import list_actions
from app.services.agent.proactive import (
    build_proactive_insights,
    PROACTIVE_CODES,
    PROACTIVE_VERSION,
)
from app.services.evaluation import record_metric
from app.services.agent.experiments import (
    build_experiment_proposal,
    active_experiment,
    serialize_experiment,
    list_experiments,
    start_experiment,
    finish_experiment,
    cancel_experiment,
    variant_history,
    EXPERIMENT_VERSION,
)

router = APIRouter(prefix="/agent", tags=["health-agent"])


def _recent_insight_feedback(db: Session, user_id: int) -> dict:
    events = db.scalars(
        select(EvaluationEvent)
        .where(
            EvaluationEvent.user_id == user_id,
            EvaluationEvent.metric_name == "proactive_insight_feedback",
            EvaluationEvent.occurred_at >= utc_now() - timedelta(hours=24),
        )
        .order_by(EvaluationEvent.occurred_at.desc(), EvaluationEvent.id.desc())
    ).all()
    latest = {}
    for event in events:
        try:
            meta = json.loads(event.meta_json or "{}")
        except (TypeError, ValueError):
            continue
        code = str(meta.get("insight_code") or "")
        verdict = str(meta.get("verdict") or "")
        if code in PROACTIVE_CODES and verdict in {"helpful", "inaccurate", "resolved"} and code not in latest:
            latest[code] = {"verdict": verdict, "recorded_at": utc_iso(event.occurred_at)}
    return latest


@router.get("/insights")
def proactive_insights(user=Depends(current_user), db: Session = Depends(get_db)):
    context = read_context(db, user)
    insights = context.get("proactive_insights") or build_proactive_insights(context)
    recent_feedback = _recent_insight_feedback(db, user.id)
    current_experiment = active_experiment(db, user.id)
    current_payload = serialize_experiment(db, current_experiment) if current_experiment else None
    insights["experiment_version"] = EXPERIMENT_VERSION
    insights["active_experiment"] = current_payload
    for item in insights.get("insights", []):
        item["user_feedback"] = recent_feedback.get(item.get("code"))
        item["experiment_proposal"] = build_experiment_proposal(item.get("code"))
        item["proposal_history"] = variant_history(db, user.id, item.get("code"))
        item["active_experiment"] = (
            current_payload if current_experiment and current_experiment.insight_code == item.get("code") else None
        )
    insights["trace"] = {
        "specialist_version": PROACTIVE_VERSION,
        "specialist": "proactive_guardian",
        "routing": "确定性规则扫描 recent_7d/goals/motion_profile",
    }
    return insights


@router.get("/experiments")
def experiments(user=Depends(current_user), db: Session = Depends(get_db), limit: int = 10):
    if limit < 1 or limit > 50:
        raise HTTPException(status_code=400, detail="limit 必须在 1-50 之间")
    items = list_experiments(db, user.id, limit)
    return {
        "version": EXPERIMENT_VERSION,
        "experiments": items,
        "active": next((item for item in items if item["status"] == "active"), None),
        "policy": "微实验必须由用户确认启动；结果只描述相关变化，不证明因果。",
    }


@router.post("/experiments")
def experiment_start(
    body: AgentExperimentStart,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    context = read_context(db, user)
    payload = context.get("proactive_insights") or build_proactive_insights(context)
    active_codes = {item.get("code") for item in payload.get("insights", [])}
    if body.insight_code not in PROACTIVE_CODES:
        raise HTTPException(status_code=404, detail="健康提醒类型不存在")
    if body.insight_code not in active_codes:
        raise HTTPException(status_code=409, detail="只能针对当前活跃提醒启动微实验")
    try:
        return start_experiment(db, user.id, body.insight_code, body.variant)
    except RuntimeError as exc:
        if str(exc) == "active_experiment_exists":
            raise HTTPException(status_code=409, detail="已有进行中的微实验，请先完成或停止") from exc
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="微实验方案无效") from exc


@router.post("/experiments/{experiment_id}/finish")
def experiment_finish(
    experiment_id: int,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    try:
        result = finish_experiment(db, user.id, experiment_id)
    except RuntimeError as exc:
        if str(exc) == "experiment_not_ready":
            raise HTTPException(status_code=409, detail="实验周期尚未结束，请继续按真实情况记录") from exc
        raise
    if result is None:
        raise HTTPException(status_code=404, detail="微实验不存在")
    return result


@router.post("/experiments/{experiment_id}/cancel")
def experiment_cancel(
    experiment_id: int,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    result = cancel_experiment(db, user.id, experiment_id)
    if result is None:
        raise HTTPException(status_code=404, detail="微实验不存在")
    return result


@router.post("/insights/{insight_code}/feedback")
def proactive_insight_feedback(
    insight_code: str,
    body: AgentInsightFeedback,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    if insight_code not in PROACTIVE_CODES:
        raise HTTPException(status_code=404, detail="健康提醒类型不存在")
    context = read_context(db, user)
    payload = context.get("proactive_insights") or build_proactive_insights(context)
    active_codes = {item.get("code") for item in payload.get("insights", [])}
    if insight_code not in active_codes:
        raise HTTPException(status_code=409, detail="这条提醒已不再活跃，请刷新后查看")
    record_metric(
        db,
        user.id,
        "proactive_insight_feedback",
        1,
        "feedback",
        "user_feedback",
        True,
        {
            "insight_code": insight_code,
            "verdict": body.verdict,
            "proactive_version": PROACTIVE_VERSION,
        },
        commit=True,
    )
    return {
        "ok": True,
        "insight_code": insight_code,
        "verdict": body.verdict,
        "policy": "反馈仅用于质量评测，不会自动用于训练或改写健康记录。",
    }


@router.get("/actions/registry")
def action_registry(user=Depends(current_user)):
    return {
        "actions": list_actions(),
        "policy": "所有写操作必须经过 Registry；需要确认的 Action 不能由 LLM 静默执行。",
    }


@router.get("/context")
def context(user=Depends(current_user), db: Session = Depends(get_db)):
    return read_context(db, user)


@router.get("/stats")
def stats(user=Depends(current_user), db: Session = Depends(get_db), days: int = 30):
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days 必须在 1-365 之间")
    return agent_stats(db, user.id, days)


@router.post("/respond")
async def agent_respond(
    body: AgentRequest, user=Depends(current_user), db: Session = Depends(get_db)
):
    return await respond(db, user, body.message)


@router.post("/runs/{run_id}/apply-plan")
def agent_apply(run_id: int, user=Depends(current_user), db: Session = Depends(get_db)):
    result = apply_plan(db, user, run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Agent run 不存在")
    if result.get("plan") is None:
        raise HTTPException(status_code=400, detail="本次 Agent 响应没有可加入的计划")
    return result


@router.get("/plans/current")
def agent_current_plan(user=Depends(current_user), db: Session = Depends(get_db)):
    return {"plan": current_plan(db, user.id)}


@router.put("/plans/items/{item_id}")
def agent_update_item(
    item_id: int,
    body: AgentPlanItemUpdate,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    item = update_plan_item(db, user.id, item_id, body.done)
    if not item:
        raise HTTPException(status_code=404, detail="计划任务不存在")
    return {"ok": True, "id": item.id, "done": item.done}
