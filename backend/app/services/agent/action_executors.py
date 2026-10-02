"""Action key → domain service dispatch table (spec §8.4).

The model never names a function: it names a registered ``action_key``, and this
table maps that key to the real domain service. An unknown key has no executor
and the confirm endpoint refuses to run it.
"""

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session


Executor = Callable[..., dict]


def _plan_apply(db: Session, *, user_id: int, arguments: dict[str, Any]) -> dict:
    """Execute ``plan.apply``: write the run's plan into this week's plan.

    This is the ONE implementation of the write. ``apply_plan`` (the user-facing
    tap) reaches it through propose -> confirm, and so does the Agent: neither
    path may call the other, which is what keeps a repeated tap from
    double-writing.
    """
    import json

    from app.models import HealthAgentRun, HealthPlan, User
    from app.schemas.errors import ApiException
    from app.services.agent.orchestrator import (
        _perform_apply_plan,
        _sanitize_plan,
        serialize_plan,
    )

    run_id = int(arguments["run_id"])
    run = db.get(HealthAgentRun, run_id)
    if run is None or run.user_id != user_id:
        raise ApiException(404, "AGENT_RUN_NOT_FOUND", "Agent 记录不存在")
    try:
        data = json.loads(run.result_json or "{}")
    except (TypeError, ValueError):
        data = {}
    plan_data = _sanitize_plan(data)
    if not plan_data:
        raise ApiException(409, "PLAN_NOT_AVAILABLE", "本次 Agent 响应没有可加入的计划")

    existing = db.scalar(
        select(HealthPlan).where(
            HealthPlan.user_id == user_id, HealthPlan.source_run_id == run_id
        )
    )
    if existing is not None:
        # Already applied: return the same plan instead of creating a second one.
        return {"already_applied": True, "plan": serialize_plan(db, existing)}

    user = db.get(User, user_id)
    if user is None:
        raise ApiException(404, "AGENT_RUN_NOT_FOUND", "Agent 记录不存在")
    result = _perform_apply_plan(db, user, run, plan_data)
    return {**result, "plan_id": (result.get("plan") or {}).get("id")}


def _goal_adjustment_apply(
    db: Session, *, user_id: int, arguments: dict[str, Any]
) -> dict:
    from app.schemas.errors import ApiException
    from app.services.dynamic_goals import apply_adjustment

    adjustment = apply_adjustment(db, user_id, int(arguments["adjustment_id"]))
    if adjustment is None:
        raise ApiException(404, "GOAL_ADJUSTMENT_NOT_FOUND", "目标建议不存在")
    return {
        "adjustment_id": adjustment.id,
        "metric": adjustment.metric,
        "applied_target": adjustment.recommended_target,
        "status": adjustment.status,
    }


def _diet_ai_finalize(db: Session, *, user_id: int, arguments: dict[str, Any]) -> dict:
    from app.schemas.errors import ApiException
    from app.services.vision.finalize import finalize_food_analysis

    result = finalize_food_analysis(
        db,
        user_id=user_id,
        analysis_id=int(arguments["analysis_id"]),
        meal_type=arguments.get("meal_type"),
        confirmed=True,
    )
    if not result.get("ok"):
        raise ApiException(
            409, "DIET_FINALIZE_NOT_EXECUTED", "识餐结果未能写入，请重新确认"
        )
    return result


def _experiment_start(db: Session, *, user_id: int, arguments: dict[str, Any]) -> dict:
    from app.schemas.errors import ApiException
    from app.services.agent.tools import read_context
    from app.services.agent.proactive import build_proactive_insights, PROACTIVE_CODES
    from app.services.agent.experiments import start_experiment

    insight_code = str(arguments["insight_code"])
    if insight_code not in PROACTIVE_CODES:
        raise ApiException(404, "INSIGHT_NOT_FOUND", "健康提醒类型不存在")
    from app.models import User

    user = db.get(User, user_id)
    context = read_context(db, user)
    payload = context.get("proactive_insights") or build_proactive_insights(context)
    active_codes = {item.get("code") for item in payload.get("insights", [])}
    if insight_code not in active_codes:
        raise ApiException(
            409, "INSIGHT_NOT_ACTIVE", "只能针对当前活跃提醒启动微实验"
        )
    try:
        return start_experiment(db, user_id, insight_code, arguments.get("variant"))
    except RuntimeError as exc:
        raise ApiException(
            409, "EXPERIMENT_ALREADY_ACTIVE", "已有进行中的微实验，请先完成或停止"
        ) from exc
    except ValueError as exc:
        raise ApiException(422, "EXPERIMENT_PLAN_INVALID", "微实验方案无效") from exc


def _experiment_finish(db: Session, *, user_id: int, arguments: dict[str, Any]) -> dict:
    from app.schemas.errors import ApiException
    from app.services.agent.experiments import finish_experiment

    try:
        result = finish_experiment(db, user_id, int(arguments["experiment_id"]))
    except RuntimeError as exc:
        raise ApiException(
            409, "EXPERIMENT_NOT_READY", "实验周期尚未结束，请继续按真实情况记录"
        ) from exc
    if result is None:
        raise ApiException(404, "EXPERIMENT_NOT_FOUND", "微实验不存在")
    return result


def _experiment_cancel(db: Session, *, user_id: int, arguments: dict[str, Any]) -> dict:
    from app.schemas.errors import ApiException
    from app.services.agent.experiments import cancel_experiment

    result = cancel_experiment(db, user_id, int(arguments["experiment_id"]))
    if result is None:
        raise ApiException(404, "EXPERIMENT_NOT_FOUND", "微实验不存在")
    return result


def _privacy_export(db: Session, *, user_id: int, arguments: dict[str, Any]) -> dict:
    from app.services.privacy import build_export_zip

    payload = build_export_zip(db, user_id)
    return {"export_bytes": len(payload), "format": "zip/json"}


def _privacy_account_delete(
    db: Session, *, user_id: int, arguments: dict[str, Any]
) -> dict:
    """Server-verified account deletion (spec §10.2).

    Cloud objects cannot be removed from here without a platform credential, so
    the deletion ledger records what the client reported and marks anything the
    server cannot verify as ``manual_review`` instead of claiming success.
    """
    from app.services.media_reconciliation import finalize_account_deletion

    return finalize_account_deletion(
        db, user_id=user_id, reason=str(arguments.get("reason") or "user_request")
    )


EXECUTORS: dict[str, Executor] = {
    "plan.apply": _plan_apply,
    "goal.adjustment.apply": _goal_adjustment_apply,
    "diet.ai.finalize": _diet_ai_finalize,
    "experiment.start": _experiment_start,
    "experiment.finish": _experiment_finish,
    "experiment.cancel": _experiment_cancel,
    "privacy.export": _privacy_export,
    "privacy.account.delete": _privacy_account_delete,
}


def get_executor(action_key: str) -> Executor | None:
    return EXECUTORS.get(action_key)
