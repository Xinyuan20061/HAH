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


def plan_context_for(db: Session, user_id: int):
    """Build a :class:`PlanContext` from real state for executors and tools."""
    from app.models import User
    from app.services.agent.tools import read_context
    from app.services.health_state import build_snapshot
    from app.services.planning.contracts import PlanContext

    user = db.get(User, user_id)
    if user is None:
        return PlanContext()
    context = read_context(db, user)
    from app.harness.plugins import health_state_excluded_sources

    snapshot = build_snapshot(
        db, user_id, persist=False,
        excluded_sources=health_state_excluded_sources(db, user_id),
    )
    recovery: list[str] = []
    debt = snapshot.numeric("sleep_debt_7d")
    if debt is not None and debt >= 5:
        recovery.append("sleep_debt")
    adherence = snapshot.numeric("plan_adherence_7d")
    focus: list[str] = []
    for item in (context.get("proactive_insights") or {}).get("insights", []):
        for key in item.get("evidence", []) or []:
            if isinstance(key, str) and key.endswith("_consistency"):
                focus.append(key)
    return PlanContext(
        health_state=snapshot,
        motion_focus=sorted(set(focus)),
        recovery_constraints=recovery,
        adherence_history=(
            {"plan_adherence_7d": adherence} if adherence is not None else {}
        ),
    )


def _plan_replan_apply(
    db: Session, *, user_id: int, arguments: dict[str, Any]
) -> dict:
    """Execute ``plan.replan.apply``: recompute the diff, then write it.

    Recomputing (rather than trusting a caller-supplied diff) plus applying through
    ``apply_diff`` is what guarantees completed items stay frozen.
    """
    from app.schemas.errors import ApiException
    from app.services.planning.contracts import PlanDiff, PlanRequest
    from app.services.planning.replan import apply_diff, replan, suggestions_from_state

    request = PlanRequest(
        goal=str(arguments.get("goal") or "fitness"),  # type: ignore[arg-type]
        days_per_week=int(arguments.get("days_per_week") or 3),
        minutes_per_session=int(arguments.get("minutes_per_session") or 30),
    )
    context = plan_context_for(db, user_id)
    proposal = replan(
        db,
        user_id=user_id,
        plan_id=int(arguments["plan_id"]),
        request=request,
        context=context,
        reasons=suggestions_from_state(context, goal=request.goal),
    )
    if not proposal.get("found"):
        raise ApiException(404, "PLAN_NOT_FOUND", "计划不存在")
    diff = PlanDiff.model_validate(proposal["diff"])
    result = apply_diff(
        db, user_id=user_id, plan_id=int(arguments["plan_id"]), diff=diff
    )
    return {**result, "diff": diff.model_dump(mode="json")}


def _goal_adjustment_apply(
    db: Session, *, user_id: int, arguments: dict[str, Any]
) -> dict:
    from app.schemas.errors import ApiException
    from app.services.dynamic_goals import apply_adjustment

    adjustment = apply_adjustment(db, user_id, int(arguments["adjustment_id"]))
    if adjustment is None:
        raise ApiException(404, "GOAL_ADJUSTMENT_NOT_FOUND", "目标建议不存在")
    # "Applied" is the outcome: the user accepted a data-driven target change. The
    # metric is recorded so a later analysis can ask whether the new target held.
    _record_action_outcome(
        db,
        user_id=user_id,
        action_key="goal.adjustment.apply",
        result="completed",
        source_id=f"goal-adjustment:{adjustment.id}",
        conclusion="changed",
        observed={
            "metric": adjustment.metric,
            "applied_target": adjustment.recommended_target,
        },
    )
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


def _record_action_outcome(
    db: Session,
    *,
    user_id: int,
    action_key: str,
    result: str,
    source_id: str,
    variant: str = "default",
    conclusion: str = "changed",
    observed: dict | None = None,
) -> None:
    """Record a *specific* outcome verdict from inside an executor.

    The generic post-confirmation hook records "completed" for any successful
    action; when the executor knows the real verdict (an experiment's conclusion, a
    goal adjustment that did or did not apply), it records that instead. The
    ``source_id`` makes both paths idempotent, so the pair cannot double-count.

    Never raises: outcome learning must not turn a successful action into an error.
    """
    try:
        from app.services.agent.outcome import record_outcome

        record_outcome(
            db,
            user_id=user_id,
            action_key=action_key,
            result=result,
            source="proposal",
            source_id=source_id,
            variant=variant,
            conclusion=conclusion,
            observed=observed or {},
        )
    except Exception:  # noqa: BLE001 - best-effort by design
        db.rollback()


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

    # A finished experiment is the strongest outcome signal the product has: the
    # user actually ran the variable. The verdict comes from the experiment record's
    # own `conclusion`, not from this hook, and `insufficient_data` stays
    # `insufficient_data` — a short experiment is not evidence of anything.
    if result.get("already_finished"):
        return result
    # `finish_experiment` nests the verdict inside the serialised experiment; the
    # conclusion is read from there rather than guessed from the outer dict.
    experiment = result.get("experiment") or {}
    outcome_payload = experiment.get("outcome") or {}
    verdict = str(outcome_payload.get("conclusion") or "").strip().lower()
    if not verdict:
        verdict = str(experiment.get("status") or "").strip().lower()

    # The declared vocabulary is {changed, unchanged, insufficient_data, stopped}.
    # An experiment that reached its target is a *change*; one that did not is
    # `unchanged`. There is deliberately no "improved"/"worsened" value, because a
    # micro-experiment can show a metric moved, not that it improved the person.
    if verdict in {"supports_hypothesis", "supported", "improved"}:
        outcome, conclusion = "completed", "changed"
    elif verdict in {"not_supported_yet", "unsupported", "contradicts_hypothesis"}:
        outcome, conclusion = "completed", "unchanged"
    elif verdict in {"insufficient_data", "inconclusive"}:
        outcome, conclusion = "completed", "insufficient_data"
    elif verdict in {"cancelled", "abandoned", "dropped"}:
        outcome, conclusion = "abandoned", "stopped"
    else:
        outcome, conclusion = "completed", "insufficient_data"
    _record_action_outcome(
        db,
        user_id=user_id,
        action_key="experiment.finish",
        result=outcome,
        source_id=f"experiment:{arguments['experiment_id']}",
        conclusion=conclusion,
        observed={"conclusion": verdict or "recorded"},
    )
    return result


def _experiment_cancel(db: Session, *, user_id: int, arguments: dict[str, Any]) -> dict:
    from app.schemas.errors import ApiException
    from app.services.agent.experiments import cancel_experiment

    result = cancel_experiment(db, user_id, int(arguments["experiment_id"]))
    if result is None:
        raise ApiException(404, "EXPERIMENT_NOT_FOUND", "微实验不存在")
    return result


def _policy_episode_start(db: Session, *, user_id: int, arguments: dict[str, Any]) -> dict:
    from app.services.policy_learning.repository import PolicyError, start_episode
    try:
        return start_episode(
            db,
            user_id,
            str(arguments["strategy_unit_id"]),
            str(arguments["protocol_hash"]),
            int(arguments.get("version") or 1),
            arguments.get("decision_id"),
            state_snapshot_hash=str(arguments["state_snapshot_hash"]),
            capability_snapshot_hash=str(arguments["capability_snapshot_hash"]),
        )
    except PolicyError as exc:
        from app.schemas.errors import ApiException
        status = 404 if exc.code == "POLICY_NOT_FOUND" else 409 if exc.code.endswith("CONFLICT") or exc.code in {"POLICY_EPISODE_ACTIVE", "POLICY_STATE_CHANGED", "POLICY_CAPABILITY_CHANGED", "POLICY_PROTOCOL_CHANGED"} else 422
        raise ApiException(status, exc.code, exc.message) from exc


def _policy_episode_finish(db: Session, *, user_id: int, arguments: dict[str, Any]) -> dict:
    from app.services.policy_learning.repository import PolicyError, finish_episode
    try:
        return finish_episode(db, user_id, str(arguments["episode_id"]), int(arguments["episode_version"]))
    except PolicyError as exc:
        from app.schemas.errors import ApiException
        status = 404 if exc.code == "POLICY_NOT_FOUND" else 409 if exc.code.endswith("CONFLICT") else 422
        raise ApiException(status, exc.code, exc.message) from exc


def _policy_episode_stop(db: Session, *, user_id: int, arguments: dict[str, Any]) -> dict:
    from app.services.policy_learning.repository import PolicyError, stop_episode
    try:
        return stop_episode(db, user_id, str(arguments["episode_id"]), int(arguments["episode_version"]), str(arguments["reason_code"]))
    except PolicyError as exc:
        from app.schemas.errors import ApiException
        status = 404 if exc.code == "POLICY_NOT_FOUND" else 409 if exc.code.endswith("CONFLICT") else 422
        raise ApiException(status, exc.code, exc.message) from exc


def _policy_memory_reset(db: Session, *, user_id: int, arguments: dict[str, Any]) -> dict:
    from app.services.policy_learning.repository import current_control, learning_epoch
    scope = str(arguments.get("strategy_id") or "*") if arguments.get("scope", "strategy") == "strategy" else "*"
    row = current_control(db, user_id, scope)
    row.epoch_counter += 1
    from app.core.time import utc_now
    row.reset_at = utc_now()
    return {"reset": True, "scope_key": scope, "learning_epoch": learning_epoch(db, user_id, scope)}


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
    "plan.replan.apply": _plan_replan_apply,
    "goal.adjustment.apply": _goal_adjustment_apply,
    "diet.ai.finalize": _diet_ai_finalize,
    "experiment.start": _experiment_start,
    "experiment.finish": _experiment_finish,
    "experiment.cancel": _experiment_cancel,
    "policy.episode.start": _policy_episode_start,
    "policy.episode.finish": _policy_episode_finish,
    "policy.episode.stop": _policy_episode_stop,
    "policy.memory.reset": _policy_memory_reset,
    "privacy.export": _privacy_export,
    "privacy.account.delete": _privacy_account_delete,
}


def get_executor(action_key: str) -> Executor | None:
    return EXECUTORS.get(action_key)
