"""Durable Action proposal runtime (spec §8.1–§8.4).

The model may only *propose*: this module validates the action-specific
arguments, hashes the canonical payload and persists a proposal. Execution
happens exclusively through ``confirm_proposal`` (the ``POST /agent/actions/
{proposal_id}/confirm`` endpoint), which re-verifies the hash so a payload
mutated after approval can never be executed.

State machine (spec §8.1)::

    pending → confirmed → executing → executed
    pending → rejected
    pending → expired
    executing → failed
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any, Callable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import AgentActionAudit, AgentActionProposal
from app.schemas.errors import ApiException
from app.services.redaction import redact

# Pre-execution validity window per risk level (privacy operations are shorter).
DEFAULT_TTL_MINUTES = 30
SHORT_TTL_MINUTES = 10

# Actions that cannot be authorised by a generic confirmation flag alone.
TYPED_CONFIRMATION_REQUIRED: dict[str, str] = {
    "privacy.account.delete": "DELETE MY DATA",
}

# Actions the model may never propose (spec §8.4).
MODEL_FORBIDDEN_ACTIONS: frozenset[str] = frozenset(
    {"experiment.start", "experiment.finish", "experiment.cancel", "policy.episode.start", "policy.episode.finish", "policy.episode.stop", "policy.episode.rereview", "policy.memory.reset"}
)


class ActionArguments(BaseModel):
    """Base class for per-action argument schemas.

    ``extra="forbid"`` means a hallucinated argument is a hard error instead of
    being silently persisted into an executable payload.
    """

    model_config = ConfigDict(extra="forbid")


class PlanApplyArgs(ActionArguments):
    run_id: int = Field(ge=1)


class GoalAdjustmentApplyArgs(ActionArguments):
    adjustment_id: int = Field(ge=1)


class PlanReplanApplyArgs(ActionArguments):
    """Replan confirmation.

    The diff itself is recomputed from the live plan at confirm time rather than
    accepted from the caller: a client-supplied diff would let an edited payload
    rewrite arbitrary plan items, which is exactly what the payload hash exists to
    prevent. ``reason`` is recorded for the audit trail.
    """

    plan_id: int = Field(ge=1)
    goal: str = Field(default="fitness", max_length=20)
    days_per_week: int = Field(default=3, ge=1, le=7)
    minutes_per_session: int = Field(default=30, ge=10, le=120)
    reason: str = Field(default="state_change", max_length=120)


class DietFinalizeArgs(ActionArguments):
    analysis_id: int = Field(ge=1)
    meal_type: str | None = Field(
        default=None, pattern="^(breakfast|lunch|dinner|snack|other)$"
    )


class ExperimentStartArgs(ActionArguments):
    insight_code: str = Field(min_length=2, max_length=60)
    variant: str = Field(default="gentle", max_length=20)


class ExperimentIdArgs(ActionArguments):
    experiment_id: int = Field(ge=1)


class PrivacyExportArgs(ActionArguments):
    confirmation: str = Field(default="EXPORT", max_length=40)


class PrivacyDeleteArgs(ActionArguments):
    confirmation: str = Field(default="", max_length=40)
    reason: str = Field(default="user_request", max_length=120)


class PolicyEpisodeStartArgs(ActionArguments):
    strategy_unit_id: str = Field(min_length=1, max_length=64)
    protocol_hash: str = Field(min_length=64, max_length=64)
    state_snapshot_hash: str = Field(min_length=64, max_length=64)
    capability_snapshot_hash: str = Field(min_length=64, max_length=64)
    version: int = Field(default=1, ge=1)
    decision_id: str | None = Field(default=None, max_length=64)


class PolicyEpisodeFinishArgs(ActionArguments):
    episode_id: str = Field(min_length=1, max_length=64)
    episode_version: int = Field(ge=1)


class PolicyEpisodeStopArgs(PolicyEpisodeFinishArgs):
    reason_code: str = Field(min_length=1, max_length=120)


class PolicyEpisodeRereviewArgs(ActionArguments):
    episode_id: str = Field(min_length=1, max_length=64)
    episode_version: int = Field(ge=1)
    expected_adjudication_revision: int = Field(ge=1)
    expected_evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class PolicyMemoryResetArgs(ActionArguments):
    strategy_id: str | None = Field(default=None, max_length=100)
    scope: str = Field(default="strategy", pattern="^(strategy|all)$")
    version: int = Field(default=1, ge=1)


ARGUMENT_SCHEMAS: dict[str, type[ActionArguments]] = {
    "plan.apply": PlanApplyArgs,
    "plan.replan.apply": PlanReplanApplyArgs,
    "goal.adjustment.apply": GoalAdjustmentApplyArgs,
    "diet.ai.finalize": DietFinalizeArgs,
    "experiment.start": ExperimentStartArgs,
    "experiment.finish": ExperimentIdArgs,
    "experiment.cancel": ExperimentIdArgs,
    "privacy.export": PrivacyExportArgs,
    "privacy.account.delete": PrivacyDeleteArgs,
    "policy.episode.start": PolicyEpisodeStartArgs,
    "policy.episode.finish": PolicyEpisodeFinishArgs,
    "policy.episode.stop": PolicyEpisodeStopArgs,
    "policy.episode.rereview": PolicyEpisodeRereviewArgs,
    "policy.memory.reset": PolicyMemoryResetArgs,
}


class ActionProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_key: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    user_visible_reason: str = Field(default="", max_length=300)


class ActionProposalView(BaseModel):
    proposal_id: str
    action_key: str
    title: str
    risk_level: str
    requires_confirmation: bool = True
    summary: str
    expires_at: str


def canonical_payload(action_key: str, arguments: dict[str, Any]) -> tuple[str, str]:
    """Return ``(payload_json, payload_hash)`` over the *validated* arguments."""
    payload = {"action_key": action_key, "arguments": arguments}
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return payload_json, hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def _expiry_minutes(action_key: str, risk_level: str) -> int:
    if action_key in TYPED_CONFIRMATION_REQUIRED or risk_level == "critical":
        return SHORT_TTL_MINUTES
    return DEFAULT_TTL_MINUTES


def validate_arguments(action_key: str, arguments: dict[str, Any]) -> dict[str, Any]:
    schema = ARGUMENT_SCHEMAS.get(action_key)
    if schema is None:
        raise ValueError(f"未定义参数契约的 Action: {action_key}")
    try:
        return schema.model_validate(arguments or {}).model_dump()
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in (first.get("loc") or []))
        raise ValueError(f"Action 参数不合法: {action_key}.{location}") from None


def build_display(action_key: str, title: str, arguments: dict[str, Any]) -> dict:
    """User-visible summary. Never contains raw or sensitive values."""
    return {
        "action_key": action_key,
        "title": title,
        "argument_keys": sorted(arguments),
    }


def propose_action(
    db: Session,
    *,
    user_id: int,
    action_key: str,
    arguments: dict[str, Any],
    title: str,
    risk_level: str,
    requires_confirmation: bool,
    run_id: int | None = None,
    user_visible_reason: str = "",
    executor: Callable[..., dict] | None = None,
) -> tuple[AgentActionProposal | None, str]:
    """Persist a proposal. Returns ``(proposal, error_reason)``.

    ``proposal`` is ``None`` when the action needs no confirmation: the caller is
    expected to execute it directly (``error_reason`` is ``"no_confirmation"``).
    """
    from app.services.agent.actions import ACTION_REGISTRY

    spec = ACTION_REGISTRY.get(action_key)
    if spec is None:
        raise ValueError(f"未注册的 Action: {action_key}")
    validated = validate_arguments(action_key, arguments)
    payload_json, payload_hash = canonical_payload(action_key, validated)
    if not requires_confirmation:
        return None, "no_confirmation"

    ttl = _expiry_minutes(action_key, risk_level)
    proposal = AgentActionProposal(
        proposal_id="ap_" + uuid4().hex,
        user_id=user_id,
        run_id=run_id,
        action_key=action_key,
        risk_level=risk_level,
        status="pending",
        payload_json=payload_json,
        payload_hash=payload_hash,
        display_json=json.dumps(
            {
                **build_display(action_key, title, validated),
                "reason": (user_visible_reason or "")[:300],
            },
            ensure_ascii=False,
        ),
        expires_at=utc_now() + timedelta(minutes=ttl),
        version=1,
    )
    db.add(proposal)
    # Commit immediately: the proposal must be readable by the confirm endpoint
    # (a separate session) even if the caller's turn later fails or is cancelled.
    db.commit()
    db.refresh(proposal)
    # `active_actions` is part of the snapshot contract: it is what stops a second
    # proposal for the same action from being offered. A new pending proposal changes
    # that set, so the snapshot must be refreshed or the Decision Contract would keep
    # offering the action it already has pending (plan §4.5/§8.4).
    from app.services.health_state.invalidation import active_actions_changed

    active_actions_changed(db, proposal.user_id)
    return proposal, ""


def proposal_view(proposal: AgentActionProposal) -> dict:
    try:
        display = json.loads(proposal.display_json or "{}")
    except (TypeError, ValueError):
        display = {}
    return {
        "proposal_id": proposal.proposal_id,
        "action_key": proposal.action_key,
        "title": str(display.get("title") or proposal.action_key),
        "risk_level": proposal.risk_level,
        "requires_confirmation": True,
        "status": proposal.status,
        "summary": str(display.get("reason") or "")[:300],
        "expires_at": proposal.expires_at.isoformat() + "Z"
        if proposal.expires_at
        else None,
        "version": proposal.version,
    }


def _load_owned(
    db: Session, user_id: int, proposal_id: str, *, for_update: bool = False
) -> AgentActionProposal:
    stmt = select(AgentActionProposal).where(
        AgentActionProposal.proposal_id == proposal_id
    )
    if for_update:
        stmt = stmt.with_for_update()
    proposal = db.scalar(stmt)
    # A foreign proposal is indistinguishable from a missing one (spec §8.4).
    if proposal is None or proposal.user_id != user_id:
        raise ApiException(404, "ACTION_PROPOSAL_NOT_FOUND", "确认请求不存在或已失效")
    return proposal


def get_proposal(db: Session, user_id: int, proposal_id: str) -> dict:
    return proposal_view(_load_owned(db, user_id, proposal_id))


def _mark_expired(db: Session, proposal: AgentActionProposal) -> None:
    proposal.status = "expired"
    db.add(proposal)
    db.commit()


def resolve_payload(proposal: AgentActionProposal) -> dict[str, Any]:
    payload_json, payload_hash = canonical_payload(
        proposal.action_key, _stored_arguments(proposal)
    )
    if payload_hash != proposal.payload_hash:
        raise ApiException(
            409,
            "ACTION_PAYLOAD_HASH_MISMATCH",
            "该操作内容已变化，请重新确认后再执行",
            details={"action_key": proposal.action_key},
        )
    try:
        return json.loads(payload_json)["arguments"]
    except (TypeError, ValueError):
        raise ApiException(
            409, "ACTION_PAYLOAD_INVALID", "操作内容无法校验，请重新发起"
        ) from None


def _stored_arguments(proposal: AgentActionProposal) -> dict[str, Any]:
    try:
        stored = json.loads(proposal.payload_json or "{}")
    except (TypeError, ValueError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    return stored.get("arguments") or {}


def reject_proposal(
    db: Session, user_id: int, proposal_id: str, *, version: int | None = None
) -> dict:
    proposal = _load_owned(db, user_id, proposal_id, for_update=True)
    if proposal.status != "pending":
        raise ApiException(
            409,
            "ACTION_PROPOSAL_NOT_PENDING",
            "该操作已处理，无法再次拒绝",
            details={"status": proposal.status},
        )
    proposal.status = "rejected"
    db.add(proposal)
    db.commit()
    return {"proposal_id": proposal.proposal_id, "status": "rejected"}


def confirm_proposal(
    db: Session,
    user_id: int,
    proposal_id: str,
    *,
    version: int | None = None,
    confirmation: bool = False,
    typed_confirmation: str | None = None,
    executor: Callable[..., dict] | None = None,
) -> dict:
    """Confirm and execute exactly once (spec §8.4).

    * an expired proposal is 410 and must be re-proposed;
    * a non-pending proposal is 409;
    * a mutated payload is 409;
    * ``privacy.account.delete`` additionally requires the typed phrase;
    * repeating a confirm returns the first result instead of writing again.
    """
    proposal = _load_owned(db, user_id, proposal_id, for_update=True)

    if proposal.status == "executed":
        # Idempotent replay: report the original outcome, never re-execute.
        return {
            "proposal_id": proposal.proposal_id,
            "status": "executed",
            "action_key": proposal.action_key,
            "audit_id": proposal.audit_id,
            **proposal_result(db, proposal),
        }
    if proposal.status in {"rejected", "expired"} or proposal.status not in {
        "pending",
        "failed",
    }:
        raise ApiException(
            409,
            "ACTION_PROPOSAL_NOT_PENDING",
            "该操作已处理，请重新发起",
            details={"status": proposal.status},
        )
    if proposal.expires_at is not None and proposal.expires_at < utc_now():
        _mark_expired(db, proposal)
        raise ApiException(
            410,
            "ACTION_PROPOSAL_EXPIRED",
            "该确认请求已过期，请重新生成",
            details={"expires_at": proposal.expires_at.isoformat() + "Z"},
        )
    if not confirmation:
        raise ApiException(
            422,
            "CONFIRMATION_REQUIRED",
            "需要明确确认后才能执行",
            details={"required_confirmation": "confirmation=true"},
        )

    required_phrase = TYPED_CONFIRMATION_REQUIRED.get(proposal.action_key)
    if required_phrase is not None and (typed_confirmation or "") != required_phrase:
        raise ApiException(
            422,
            "TYPED_CONFIRMATION_REQUIRED",
            f"请输入 {required_phrase} 进行二次确认",
            details={"required_confirmation": required_phrase},
        )

    arguments = resolve_payload(proposal)

    if executor is None:
        from app.services.agent.action_executors import get_executor

        executor = get_executor(proposal.action_key)
    if executor is None:
        raise ApiException(
            409,
            "ACTION_EXECUTOR_UNAVAILABLE",
            "该操作暂不可执行，请稍后重试",
            details={"action_key": proposal.action_key},
        )

    proposal.status = "executing"
    proposal.confirmed_at = utc_now()
    proposal.version += 1
    db.add(proposal)
    db.commit()

    audit = AgentActionAudit(
        user_id=user_id,
        run_id=proposal.run_id,
        action_key=proposal.action_key,
        risk_level=proposal.risk_level,
        requires_confirmation=True,
        status="pending",
        input_json=json.dumps(redact(arguments), ensure_ascii=False, default=str),
    )
    db.add(audit)
    db.flush()

    try:
        result = executor(db, user_id=user_id, arguments=arguments)
    except ApiException as exc:
        proposal.status = "failed"
        audit.status = "failed"
        audit.block_reason = exc.code
        db.add(proposal)
        db.add(audit)
        db.commit()
        raise
    except Exception as exc:  # noqa: BLE001 - surface a safe code, keep the audit
        proposal.status = "failed"
        audit.status = "failed"
        audit.block_reason = type(exc).__name__
        db.add(proposal)
        db.add(audit)
        db.commit()
        raise ApiException(
            500,
            "ACTION_EXECUTION_FAILED",
            "操作执行失败，请稍后重试",
            retryable=True,
            details={"action_key": proposal.action_key},
        ) from None

    audit.status = "executed"
    audit.output_json = json.dumps(
        redact(result if isinstance(result, dict) else {"result": str(result)}),
        ensure_ascii=False,
        default=str,
    )
    proposal.status = "executed"
    proposal.executed_at = utc_now()
    proposal.audit_id = audit.id
    db.add_all([audit, proposal])
    db.commit()
    _record_outcome(db, proposal, result)
    # The action is no longer pending, so `active_actions` changed. Refresh before
    # returning so the next Decision Contract sees the new state rather than the
    # proposal it just executed.
    from app.services.health_state.invalidation import active_actions_changed

    active_actions_changed(db, proposal.user_id)
    return {
        "proposal_id": proposal.proposal_id,
        "status": "executed",
        "action_key": proposal.action_key,
        "audit_id": audit.id,
        "result": result,
    }


def _record_outcome(db: Session, proposal: AgentActionProposal, result) -> None:
    """Feed the observed result into the policy statistics (plan §9.2).

    Recording is best-effort: a failed outcome write must never turn a successful
    action into an error for the user, and the safety-relevant audit row is already
    committed above.
    """
    try:
        from app.services.agent.outcome import record_outcome

        record_outcome(
            db,
            user_id=proposal.user_id,
            action_key=proposal.action_key,
            result="completed",
            source="proposal",
            # Keyed by the proposal so an executor that already recorded a more
            # specific verdict for the same event is not double-counted.
            source_id=f"proposal:{proposal.proposal_id}",
            variant="default",
            conclusion="changed",
            observed={
                "audit_id": proposal.audit_id,
                "result_keys": sorted(result) if isinstance(result, dict) else [],
            },
        )
    except Exception:  # noqa: BLE001 - never fail a confirmed user action here
        db.rollback()


def proposal_result(db: Session, proposal: AgentActionProposal) -> dict:
    """The stored outcome of an already-executed proposal.

    Replaying a confirm must return the first result, so the audit row written at
    execution time is the single source of truth for the outcome.
    """
    if not proposal.audit_id:
        return {"result": None}
    audit = db.get(AgentActionAudit, proposal.audit_id)
    if audit is None:
        return {"result": None}
    try:
        output = json.loads(audit.output_json or "{}")
    except (TypeError, ValueError):
        output = {}
    return {"result": output if isinstance(output, dict) else {"value": output}}


def expire_stale_proposals(db: Session, user_id: int | None = None) -> int:
    """Mark overdue pending proposals as expired (swept by the API on read)."""
    stmt = select(AgentActionProposal).where(
        AgentActionProposal.status == "pending",
        AgentActionProposal.expires_at < utc_now(),
    )
    if user_id is not None:
        stmt = stmt.where(AgentActionProposal.user_id == user_id)
    rows = db.scalars(stmt).all()
    for row in rows:
        row.status = "expired"
        db.add(row)
    if rows:
        db.commit()
    return len(rows)
