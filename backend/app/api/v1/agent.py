import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.deps import current_user
from app.core.database import get_db
from app.core.streaming import display_tokens
from app.core.time import utc_iso, utc_now
from app.models import EvaluationEvent, HealthAgentRun
from app.schemas.agent import (
    AgentRequest,
    AgentPlanItemUpdate,
    AgentInsightFeedback,
    AgentExperimentStart,
    ActionConfirmIn,
    ActionRejectIn,
)
from app.services.agent.orchestrator import (
    agent_stats,
    cancel_run,
    get_run_view,
    respond,
    retry_run,
    apply_plan,
    current_plan,
    update_plan_item,
)
from app.services.agent.tools import read_context
from app.services.agent.actions import list_actions
from app.services.agent.action_proposals import (
    confirm_proposal,
    expire_stale_proposals,
    get_proposal as get_action_proposal,
    reject_proposal,
)
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
    get_decision,
    experiment_timeline,
    variant_history,
    EXPERIMENT_VERSION,
)
from app.harness.personas import list_personas

router = APIRouter(prefix="/agent", tags=["health-agent"])


def _contract_evidence(item: dict, data_quality: dict) -> dict:
    """Map internal evidence_meta + global coverage into the §5 evidence shape.

    observed_days and expected_days are always both returned; missing records
    are never silently turned into zero coverage claims.
    """
    meta = item.get("evidence_meta") or {}
    coverage = data_quality or {}
    return {
        "facts": meta.get("facts", []),
        "data_coverage": {
            "observed_days": coverage.get("recorded_days", 0),
            "expected_days": coverage.get("expected_days", 7),
        },
        "knowledge_ids": meta.get("knowledge_ids", []),
        "limitations": meta.get("limitations", []),
        "evidence_type": meta.get("evidence_type", "general_guidance"),
    }


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
        item["evidence_contract"] = _contract_evidence(item, insights.get("data_quality", {}))
        item["decision_id"] = (
            current_payload.get("decision_id")
            if current_experiment and current_experiment.insight_code == item.get("code") and current_payload
            else None
        )
        item["action_timeline"] = experiment_timeline(db, user.id, item.get("code"))
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


@router.get("/actions/{proposal_id}")
def read_action_proposal(
    proposal_id: str,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    """Read one durable proposal (spec §8.4). Foreign ids answer 404."""
    expire_stale_proposals(db, user.id)
    return get_action_proposal(db, user.id, proposal_id)


@router.post("/actions/{proposal_id}/confirm")
def confirm_action_proposal(
    proposal_id: str,
    body: ActionConfirmIn,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    """Confirm and execute exactly once (spec §8.4).

    * expired → 410; foreign → 404; mutated payload → 409;
    * a bare ``confirmation=true`` cannot authorise ``privacy.account.delete``;
    * repeating the call returns the first result instead of writing twice.
    """
    return confirm_proposal(
        db,
        user.id,
        proposal_id,
        version=body.version,
        confirmation=body.confirmation,
        typed_confirmation=body.typed_confirmation,
    )


@router.post("/actions/{proposal_id}/reject")
def reject_action_proposal(
    proposal_id: str,
    body: ActionRejectIn,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    return reject_proposal(db, user.id, proposal_id, version=body.version)


@router.get("/profiles")
def agent_profiles(user=Depends(current_user)):
    return {"agents": list_personas(), "default_agent_id": "steward"}


@router.get("/decisions/{decision_id}")
def decision_ledger(
    decision_id: str,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    """Read model of one confirmed decision: signal, evidence, proposal,
    progress and review joined by decision_id (plan §5). The id is validated
    against the current user; unknown or foreign ids return 404 so the ledger
    is not enumerable."""
    if len(decision_id) < 4 or len(decision_id) > 64:
        raise HTTPException(status_code=404, detail="决策不存在")
    result = get_decision(db, user.id, decision_id)
    if result is None:
        raise HTTPException(status_code=404, detail="决策不存在")
    return result


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
    return await respond(db, user, body.message, body.agent_id, body.channel)


@router.post("/respond/stream")
async def agent_respond_stream(
    body: AgentRequest,
    http_request: Request,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    """Stream real *stage* events, then the already safety-reviewed answer.

    Health advice must pass the output safety review before the user sees it, so
    raw model tokens are never pushed: the stream reports genuine pipeline stage
    transitions (routing / worker:* / decision), and the final ``answer`` event
    carries the reviewed text (spec §8.7). The UI may animate that text, but it
    must be described as "逐字展示", not as live model generation.
    """
    result = await respond(db, user, body.message, body.agent_id, body.channel)
    reply = str(result.get("reply") or "")
    final_result = {key: value for key, value in result.items() if key != "reply"}
    trace = result.get("trace") or {}
    stages = trace.get("stages") or []
    harness_trace_id = (trace.get("multi_agent") or {}).get("harness_trace_id")
    request_id = getattr(http_request.state, "request_id", None)

    async def generate():
        yield ndjson(
            {
                "type": "meta",
                "run_id": result.get("run_id"),
                "request_id": request_id,
                "harness_trace_id": harness_trace_id,
                "intent": result.get("intent"),
                "safety_level": result.get("safety_level", "normal"),
                "agent": result.get("agent"),
            }
        )
        for stage in stages:
            yield ndjson(
                {
                    "type": "stage",
                    "stage": stage.get("stage_key"),
                    "status": stage.get("status"),
                    "label": STAGE_LABELS.get(stage.get("stage_key"), "处理中"),
                    "attempt": stage.get("attempt"),
                }
            )
        yield ndjson({"type": "answer", "reply": reply})
        # Display animation only: the text below is already final and reviewed.
        for token in display_tokens(reply):
            yield ndjson({"type": "delta", "content": token})
        yield ndjson(
            {
                "type": "done",
                "provider": result.get("provider"),
                "result": {**final_result, "reply": reply},
            }
        )

    return StreamingResponse(generate(), media_type="application/x-ndjson")


def ndjson(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


# Stage key -> honest user-facing label (spec §8.7). Never claims live inference.
STAGE_LABELS = {
    "safety": "正在做安全评估",
    "router": "正在理解你的问题",
    "decision": "正在整理建议",
    "action": "正在准备待确认的操作",
    "worker:coach": "正在核对训练记录",
    "worker:nutritionist": "正在核对饮食记录",
    "worker:recovery": "正在核对恢复情况",
    "worker:records": "正在核对健康记录",
    "worker:planner": "正在整理计划",
    "worker:general": "正在核对健康信息",
}


@router.get("/runs/{run_id}")
def agent_run_detail(
    run_id: int, user=Depends(current_user), db: Session = Depends(get_db)
):
    """Read one owned run with its durable stage ledger (spec §8.8)."""
    view = get_run_view(db, user.id, run_id)
    if view is None:
        raise HTTPException(status_code=404, detail="Agent run 不存在")
    return view


@router.post("/runs/{run_id}/cancel")
def agent_run_cancel(
    run_id: int, user=Depends(current_user), db: Session = Depends(get_db)
):
    result = cancel_run(db, user.id, run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Agent run 不存在")
    return result


@router.post("/runs/{run_id}/retry")
async def agent_run_retry(
    run_id: int, user=Depends(current_user), db: Session = Depends(get_db)
):
    """Re-run a failed/cancelled turn from the failed stage (spec §8.8).

    Non-idempotent writes are never replayed: only read-only stages are reused,
    and any earlier proposal must be confirmed again by the user.
    """
    prepared = retry_run(db, user, run_id)
    if prepared is None:
        raise HTTPException(status_code=404, detail="Agent run 不存在")
    agent_id, _attempt_id = prepared
    run = db.get(HealthAgentRun, run_id)
    result = await respond(
        db, user, run.user_message, agent_id, "text", run=run
    )
    return {**result, "retried": True, "attempt_id": _attempt_id}


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
