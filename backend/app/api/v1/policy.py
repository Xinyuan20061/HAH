"""Evidence-gated personal strategy learning API (policy spec §14)."""

from __future__ import annotations

import json
import hashlib
import math
from datetime import timedelta, timezone
from types import SimpleNamespace
from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.database import get_db
from app.core.time import utc_now
from app.schemas.errors import ApiException
from app.models import PersonalPolicyBelief, PersonalStrategyUnit, PolicyDecision, PolicyEpisode, PolicyAdjudication, PolicyDomainGeneration
from app.services.policy_learning.algorithm import Candidate, BeliefSet, BetaBelief, Scope
from app.services.policy_learning.compiler import compile_strategy
from app.services.policy_learning.contracts import (
    DecisionRequest, EpisodeFinishRequest, EpisodeStartRequest,
    ExecutionReportRequest, ObservationRequest, ResetRequest, StopRequest,
    StrategyCompileRequest,
)
from app.services.policy_learning.repository import (
    PolicyError, current_control, episode_view, finish_episode, learning_epoch,
    report_opportunity,
)
from app.services.policy_learning.templates import TEMPLATE_REGISTRY, get_template
from app.harness.plugins import authorize_capability, record_capability_api_access
from app.services.policy_learning.source_registry import SourceResolutionError, resolve_point

router = APIRouter(prefix="/policy", tags=["policy-learning"])


def _raise(exc: PolicyError):
    status = 404 if exc.code == "POLICY_NOT_FOUND" else 409 if exc.code in {"POLICY_PROTOCOL_CHANGED", "POLICY_VERSION_CONFLICT", "POLICY_EPISODE_ACTIVE", "POLICY_OPPORTUNITY_NOT_DUE"} else 422
    raise ApiException(status, exc.code, exc.message)


def _require_policy_capability(
    db: Session,
    user_id: int,
    *,
    phase: str,
    scopes: tuple[str, ...] = ("policy.goals.read",),
    operation: str = "read",
    audit: bool = True,
) -> None:
    result = authorize_capability(db, user_id, "personal_policy", operation, scopes, phase)
    if audit:
        record_capability_api_access(
            db, user_id, "personal_policy", route=phase,
            allowed=bool(result["allowed"]), reason=result.get("reason"),
        )
    if result["allowed"]:
        return
    reason = result.get("reason")
    code = "PLUGIN_DISABLED" if reason == "capability_paused" else "PLUGIN_SCOPE_NOT_GRANTED" if reason == "scope_not_granted" else "PLUGIN_ACTIONS_DISABLED" if reason == "proposals_disabled" else "PLUGIN_UNAVAILABLE"
    message = {
        "PLUGIN_DISABLED": "个人策略能力已暂停；仍可查看历史并结束已有周期",
        "PLUGIN_SCOPE_NOT_GRANTED": "当前授权范围不足，请在能力设置中调整数据来源",
        "PLUGIN_ACTIONS_DISABLED": "当前配置不允许提出行动申请，请在能力设置中明确开启",
        "PLUGIN_UNAVAILABLE": "个人策略能力尚未授权或审核版本已变化",
    }[code]
    raise ApiException(409, code, message)


@router.get("/templates")
def templates(user=Depends(current_user)):
    return {"templates": [{"template_id": key, **value} for key, value in TEMPLATE_REGISTRY.items()], "policy": "模板和指标由服务端审核，客户端不能改写判定门槛。"}


@router.get("/candidates")
def policy_candidates(user=Depends(current_user), db: Session = Depends(get_db)):
    """Read-only candidates with actionable capability and safety gaps."""
    from app.harness.plugins import health_state_excluded_sources
    from app.services.health_state import build_snapshot, frozen_state_hash

    read_capability = authorize_capability(
        db,
        user.id,
        "personal_policy",
        "read",
        ("policy.goals.read", "health.profile.read", "health.records.read"),
        "new_work",
    )
    propose_capability = authorize_capability(
        db, user.id, "personal_policy", "propose", ("policy.goals.read",), "new_work"
    )
    active_episode = db.scalar(select(PolicyEpisode.id).where(
        PolicyEpisode.user_id == user.id,
        PolicyEpisode.status.in_(("active", "awaiting_review")),
    ))
    if not active_episode:
        from app.models import AgentMicroExperiment

        active_episode = db.scalar(select(AgentMicroExperiment.id).where(
            AgentMicroExperiment.user_id == user.id,
            AgentMicroExperiment.status == "active",
        ))
    snapshot = build_snapshot(
        db,
        user.id,
        window_days=7,
        persist=False,
        excluded_sources=health_state_excluded_sources(db, user.id),
    )
    hard_constraints = snapshot.hard_constraints()
    hard_blocked = bool(hard_constraints)
    reasons = []
    if not read_capability.get("allowed"):
        reasons.append("请先在能力设置中启用个人策略并审核所需数据范围。")
    if active_episode:
        reasons.append("当前已有一个进行中的周期；完成或停止后才能开始新的周期。")
    reasons.extend(item.description for item in hard_constraints)
    if read_capability.get("allowed") and not propose_capability.get("allowed"):
        reasons.append("当前仅开放查看；如需开始周期，请在能力设置中审核并开启个人策略操作权限。")
    return {
        "can_compile": bool(read_capability.get("allowed")) and not active_episode and not hard_blocked,
        "can_propose": bool(propose_capability.get("allowed")) and not active_episode and not hard_blocked,
        "active_episode": bool(active_episode),
        "blocked_by_safety": any(item.source == "safety_rule" for item in hard_constraints),
        "blocked_by_constraint": hard_blocked,
        "reasons": reasons,
        "state_snapshot_hash": frozen_state_hash(snapshot),
        "candidates": [{
            "template_id": "session_duration",
            "title": "尝试更短的单次训练",
            "description": "冻结一个单次训练时长，连续 7 天记录执行情况与主观负担。结论只是个人周期的描述性观察，不代表健康效果或因果关系。",
            "metric_label": "训练负担自评（0–10）",
            "allowed_session_minutes": [10, 15, 20],
            "expected_days": TEMPLATE_REGISTRY["session_duration"]["expected_days"],
            "minimum_observations": TEMPLATE_REGISTRY["session_duration"]["minimum_days"],
            "metric_version": TEMPLATE_REGISTRY["session_duration"]["metric_version"],
        }],
        "policy": "只呈现当前已接通的用户旅程；缺少授权、安全条件或足够证据时不会强行推荐。",
    }


@router.post("/compile")
def compile_policy(
    body: StrategyCompileRequest,
    user=Depends(current_user),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=120),
):
    _require_policy_capability(db, user.id, phase="new_work", scopes=("policy.goals.read", "health.profile.read", "health.records.read"))
    if not idempotency_key:
        raise ApiException(400, "IDEMPOTENCY_KEY_REQUIRED", "请重试这项操作；请求编号缺失")
    request_hash = hashlib.sha256(json.dumps({
        "template_id": body.template_id,
        "template_version": body.template_version,
        "parameters": body.parameters,
        "goal_key": body.goal_key,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()).hexdigest()
    # Compilation is a write: retries with the same user-level key must return
    # the original frozen unit, while reusing that key with different inputs is
    # a conflict rather than a second strategy.
    if idempotency_key:
        existing = db.scalar(select(PersonalStrategyUnit).where(
            PersonalStrategyUnit.user_id == user.id,
            PersonalStrategyUnit.compile_idempotency_key == idempotency_key,
        ))
        if existing is not None:
            if existing.compile_request_hash != request_hash:
                raise ApiException(409, "IDEMPOTENCY_PARAM_MISMATCH", "相同幂等键的策略参数与已有编译任务不一致")
            try:
                return json.loads(existing.compile_response_json or "{}")
            except (TypeError, ValueError):
                raise ApiException(409, "IDEMPOTENCY_RESULT_UNAVAILABLE", "原编译结果暂时无法恢复，请查看已有策略后再继续") from None
    try:
        payload = compile_strategy(db, user.id, body.template_id, body.template_version, body.parameters, body.goal_key)
    except KeyError:
        raise ApiException(422, "POLICY_TEMPLATE_NOT_FOUND", "策略模板不存在或未审核") from None
    except ValueError as exc:
        raise ApiException(422, "POLICY_UNVERIFIABLE", str(exc)) from None
    if idempotency_key:
        row = db.get(PersonalStrategyUnit, payload["strategy_unit_id"])
        row.compile_idempotency_key = idempotency_key
        row.compile_request_hash = request_hash
        row.compile_response_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(PersonalStrategyUnit).where(
            PersonalStrategyUnit.user_id == user.id,
            PersonalStrategyUnit.compile_idempotency_key == idempotency_key,
        ))
        if existing is not None and existing.compile_request_hash == request_hash:
            try:
                return json.loads(existing.compile_response_json or "{}")
            except (TypeError, ValueError):
                raise ApiException(409, "IDEMPOTENCY_RESULT_UNAVAILABLE", "原编译结果暂时无法恢复，请查看已有策略后再继续") from None
        if existing is not None:
            raise ApiException(409, "IDEMPOTENCY_PARAM_MISMATCH", "相同幂等键的策略参数与已有编译任务不一致") from None
        raise ApiException(409, "IDEMPOTENCY_IN_PROGRESS", "这项策略正在处理，请稍后刷新查看") from None
    return payload


@router.get("/units/{unit_id}")
def read_unit(unit_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    _require_policy_capability(db, user.id, phase="read_history", scopes=("policy.goals.read",), operation="user_action")
    row = db.get(PersonalStrategyUnit, unit_id)
    if row is None or row.user_id != user.id:
        raise ApiException(404, "POLICY_NOT_FOUND", "策略不存在")
    payload = {"strategy_unit_id": row.id, "strategy_id": row.strategy_id, "template_id": row.template_id, "status": row.status, "protocol_hash": row.protocol_hash, "context_key": row.context_key, "protocol": json.loads(row.protocol_json), "context": json.loads(row.context_json), "state_snapshot_hash": row.state_snapshot_hash}
    db.commit()
    return payload


def _create_episode_start_proposal(
    db: Session,
    user,
    row: PersonalStrategyUnit,
    *,
    version: int = 1,
    decision_id: str | None = None,
):
    active_episode = db.scalar(select(PolicyEpisode.id).where(
        PolicyEpisode.user_id == user.id,
        PolicyEpisode.status.in_(("active", "awaiting_review")),
    ))
    if not active_episode:
        from app.models import AgentMicroExperiment

        active_episode = db.scalar(select(AgentMicroExperiment.id).where(
            AgentMicroExperiment.user_id == user.id,
            AgentMicroExperiment.status == "active",
        ))
    if active_episode:
        raise ApiException(409, "POLICY_EPISODE_ACTIVE", "已有一个进行中的个人周期，请完成或停止后再开始")
    if row.status != "compiled":
        raise ApiException(422, "POLICY_UNVERIFIABLE", "策略还有必需信息未补齐")
    from app.harness.plugins import capability_snapshot_hash, health_state_excluded_sources
    from app.services.health_state import build_snapshot, frozen_state_hash

    state = build_snapshot(
        db, user.id, window_days=7, persist=False,
        excluded_sources=health_state_excluded_sources(db, user.id),
    )
    if frozen_state_hash(state) != row.state_snapshot_hash:
        raise ApiException(409, "POLICY_STATE_CHANGED", "健康记录或约束已变化，请重新查看并确认协议")
    capability_hash = capability_snapshot_hash(db, user.id)
    from app.services.agent.action_proposals import propose_action, proposal_view
    proposal, _ = propose_action(
        db,
        user_id=user.id,
        action_key="policy.episode.start",
        arguments={
            "strategy_unit_id": row.id,
            "protocol_hash": row.protocol_hash,
            "state_snapshot_hash": row.state_snapshot_hash,
            "capability_snapshot_hash": capability_hash,
            "version": version,
            "decision_id": decision_id,
        },
        title="开始个人策略验证",
        risk_level="low",
        requires_confirmation=True,
        user_visible_reason="这是一次可撤销的个人策略验证周期，需你确认后开始。",
    )
    return {"approval_required": True, **proposal_view(proposal)}


@router.post("/units/{unit_id}/proposal")
def unit_proposal(unit_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    _require_policy_capability(db, user.id, phase="new_work", scopes=("policy.goals.read",), operation="propose")
    row = db.get(PersonalStrategyUnit, unit_id)
    if row is None or row.user_id != user.id:
        raise ApiException(404, "POLICY_NOT_FOUND", "策略不存在")
    return _create_episode_start_proposal(db, user, row)


def _calculate_decision(db: Session, user_id: int, body: DecisionRequest) -> tuple[dict, str, int]:
    context_key = body.context_key
    from app.services.health_state import build_snapshot
    from app.harness.plugins import health_state_excluded_sources
    snapshot = build_snapshot(
        db, user_id, window_days=7, persist=False,
        excluded_sources=health_state_excluded_sources(db, user_id),
    )
    hard_blocked = bool(snapshot.hard_constraints())
    domain_generations = db.scalars(select(PolicyDomainGeneration).where(
        PolicyDomainGeneration.user_id == user_id
    )).all()
    replay_pending = any(
        row.source_generation > row.processed_generation for row in domain_generations
    )
    rows = db.scalars(select(PersonalStrategyUnit).where(PersonalStrategyUnit.user_id == user_id, PersonalStrategyUnit.status == "compiled").order_by(PersonalStrategyUnit.created_at.desc()).limit(20)).all()
    if context_key:
        rows = [row for row in rows if row.context_key == context_key]
    candidates, beliefs = [], {}
    for row in rows:
        scope = Scope(row.user_id, row.strategy_id, row.protocol_version, row.metric_version, row.context_key)
        candidates.append(Candidate(row.strategy_id, scope, 0.3, 1.0, not hard_blocked, True))
        epoch = learning_epoch(db, user_id, row.strategy_id, create=False)
        belief_rows = [] if replay_pending else db.scalars(select(PersonalPolicyBelief).where(
            PersonalPolicyBelief.user_id == user_id,
            PersonalPolicyBelief.strategy_id == row.strategy_id,
            PersonalPolicyBelief.protocol_version == row.protocol_version,
            PersonalPolicyBelief.metric_version == row.metric_version,
            PersonalPolicyBelief.context_key == row.context_key,
            PersonalPolicyBelief.learning_epoch == epoch,
        )).all()
        values = {item.endpoint: item for item in belief_rows}
        beliefs[scope] = BeliefSet(BetaBelief(values.get("execution").alpha, values.get("execution").beta) if values.get("execution") else BetaBelief(), BetaBelief(values.get("support").alpha, values.get("support").beta) if values.get("support") else BetaBelief(), BetaBelief(values.get("availability").alpha, values.get("availability").beta) if values.get("availability") else BetaBelief())
    from app.services.policy_learning.algorithm import rank_candidates
    result = rank_candidates(
        beliefs,
        candidates,
        user_id=user_id,
        context_key=context_key or (rows[0].context_key if rows else "unknown"),
        exploration_consented=body.exploration_consented,
    )
    generation = max((x.source_generation for x in domain_generations), default=0)
    result["personal_memory_replay_pending"] = replay_pending
    return result, snapshot.snapshot_hash, generation


def _decision_capability_hash(db: Session, user_id: int) -> str:
    from app.harness.plugins import capability_snapshot_hash

    return capability_snapshot_hash(db, user_id)


def _decision_response(result: dict, *, decision_id: str | None, preview: bool) -> dict:
    missing = (
        ["source_correction_replay_pending"]
        if result.get("personal_memory_replay_pending")
        else ["verified_personal_episode_evidence"] if not result["personalised"] else []
    )
    return {
        "decision_id": decision_id,
        "preview": preview,
        "algorithm_version": result["algorithm_version"],
        "policy_mode": result["policy_mode"],
        "requires_user_confirmation": result["requires_user_confirmation"],
        "personalised": bool(result["ranked"] and result["ranked"][0]["personalised"]),
        "evidence_used": [],
        "evidence_missing": missing,
        **result,
    }


def _episode_allowed_actions(
    db: Session, user_id: int, episode: PolicyEpisode
) -> list[str]:
    """Server-owned action map; the client must not infer lifecycle rights."""
    from app.harness.plugins import authorize_capability

    actions = ["view", "explanation", "export"]
    if episode.status == "active":
        actions.extend(["report_execution", "add_observation", "stop_proposal"])
        if utc_now() >= episode.end_at:
            actions.extend(["review_preview", "finish_proposal"])
    elif episode.status == "awaiting_review":
        actions.extend(["review_preview", "finish_proposal", "stop_proposal"])
    elif episode.status == "reviewed":
        actions.append("reset_memory")
        can_start = authorize_capability(
            db,
            user_id,
            "personal_policy",
            "propose",
            ("policy.goals.read",),
            "new_work",
        ).get("allowed")
        if can_start:
            actions.append("start_another")
    return actions


@router.post("/decide", deprecated=True)
def decide_policy(body: DecisionRequest, user=Depends(current_user), db: Session = Depends(get_db)):
    """Compatibility alias for a pure preview; decisions are saved at /decisions."""
    _require_policy_capability(
        db, user.id, phase="new_work",
        scopes=("policy.goals.read", "policy.outcomes.read", "health.profile.read", "health.records.read"),
        audit=False,
    )
    result, _state_hash, _generation = _calculate_decision(db, user.id, body)
    return _decision_response(result, decision_id=None, preview=True)


@router.post("/decisions")
def persist_decision(
    body: DecisionRequest,
    user=Depends(current_user),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=120),
):
    _require_policy_capability(
        db, user.id, phase="new_work",
        scopes=("policy.goals.read", "policy.outcomes.read", "health.profile.read", "health.records.read"),
    )
    if not idempotency_key:
        raise ApiException(400, "IDEMPOTENCY_KEY_REQUIRED", "请重试这项操作；请求编号缺失")
    request_hash = hashlib.sha256(json.dumps(
        body.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()).hexdigest()
    existing = db.scalar(select(PolicyDecision).where(
        PolicyDecision.user_id == user.id,
        PolicyDecision.idempotency_key == idempotency_key,
    ))
    if existing is not None:
        if existing.idempotency_request_hash != request_hash:
            raise ApiException(409, "IDEMPOTENCY_PARAM_MISMATCH", "相同请求编号不能用于不同的决策参数")
        try:
            return json.loads(existing.response_json or "{}")
        except (TypeError, ValueError):
            raise ApiException(409, "IDEMPOTENCY_RESULT_UNAVAILABLE", "原决策结果暂时无法恢复，请查看历史记录") from None

    result, state_hash, generation = _calculate_decision(db, user.id, body)
    from app.core.time import utc_now
    from uuid import uuid4
    decision_id = "pd_" + uuid4().hex
    ranked_material = {"ranked": result["ranked"], "filtered": result["filtered"]}
    candidate_set_hash = hashlib.sha256(json.dumps(
        ranked_material, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()).hexdigest()
    capability_hash = _decision_capability_hash(db, user.id)
    response = _decision_response(result, decision_id=decision_id, preview=False)
    config_hash = hashlib.sha256(json.dumps({
        "algorithm_version": result["algorithm_version"],
        "exploration_consented": body.exploration_consented,
    }, sort_keys=True).encode()).hexdigest()
    db.add(PolicyDecision(
        id=decision_id,
        user_id=user.id,
        state_hash=state_hash,
        belief_generation=generation,
        candidate_json=json.dumps(ranked_material, ensure_ascii=False),
        selected_id=result["selected"],
        policy_mode=result["policy_mode"],
        propensity_json=json.dumps({"selected": result["selection_propensity"]}),
        config_hash=config_hash,
        idempotency_key=idempotency_key,
        idempotency_request_hash=request_hash,
        candidate_set_hash=candidate_set_hash,
        capability_snapshot_hash=capability_hash,
        response_json=json.dumps(response, ensure_ascii=False, sort_keys=True),
        created_at=utc_now(),
    ))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(PolicyDecision).where(
            PolicyDecision.user_id == user.id,
            PolicyDecision.idempotency_key == idempotency_key,
        ))
        if existing is not None and existing.idempotency_request_hash == request_hash:
            return json.loads(existing.response_json or "{}")
        if existing is not None:
            raise ApiException(409, "IDEMPOTENCY_PARAM_MISMATCH", "相同请求编号不能用于不同的决策参数") from None
        raise ApiException(409, "IDEMPOTENCY_IN_PROGRESS", "这项决策正在处理，请稍后刷新查看") from None
    return response


@router.get("/decide")
def decide_policy_read(user=Depends(current_user), db: Session = Depends(get_db), context_key: str | None = Query(default=None)):
    """Read-only default decision for the state page; never enables exploration."""
    _require_policy_capability(
        db, user.id, phase="new_work",
        scopes=("policy.goals.read", "policy.outcomes.read", "health.profile.read", "health.records.read"),
        audit=False,
    )
    result, _state_hash, _generation = _calculate_decision(
        db, user.id, DecisionRequest(context_key=context_key, exploration_consented=False)
    )
    return _decision_response(result, decision_id=None, preview=True)


@router.post("/episodes/start", deprecated=True)
def start(body: EpisodeStartRequest, user=Depends(current_user), db: Session = Depends(get_db)):
    """Compatibility entry point that creates a proposal but never starts directly."""
    _require_policy_capability(
        db, user.id, phase="new_work", scopes=("policy.goals.read",), operation="propose"
    )
    row = db.get(PersonalStrategyUnit, body.strategy_unit_id)
    if row is None or row.user_id != user.id:
        raise ApiException(404, "POLICY_NOT_FOUND", "策略不存在")
    if row.protocol_hash != body.protocol_hash:
        raise ApiException(409, "POLICY_PROTOCOL_CHANGED", "策略协议已变化，需要重新查看并确认")
    return _create_episode_start_proposal(
        db, user, row, version=body.version, decision_id=body.decision_id
    )


@router.get("/episodes/current")
def current_episode(user=Depends(current_user), db: Session = Depends(get_db)):
    _require_policy_capability(db, user.id, phase="read_history", scopes=("policy.execution.read",), operation="user_action")
    row = db.scalar(select(PolicyEpisode).where(PolicyEpisode.user_id == user.id, PolicyEpisode.status.in_(("active", "awaiting_review"))).order_by(PolicyEpisode.start_at.desc()))
    payload = {"episode": episode_view(db, row) if row else None}
    if row is not None:
        payload["episode"]["allowed_actions"] = _episode_allowed_actions(db, user.id, row)
    db.commit()
    return payload


@router.get("/episodes/{episode_id}")
def read_episode(episode_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    _require_policy_capability(db, user.id, phase="read_history", scopes=("policy.execution.read",), operation="user_action")
    row = db.get(PolicyEpisode, episode_id)
    if row is None or row.user_id != user.id:
        raise ApiException(404, "POLICY_NOT_FOUND", "周期不存在")
    payload = episode_view(db, row)
    payload["allowed_actions"] = _episode_allowed_actions(db, user.id, row)
    db.commit()
    return payload


@router.get("/episodes/{episode_id}/observations")
def read_episode_observations(
    episode_id: str, user=Depends(current_user), db: Session = Depends(get_db)
):
    _require_policy_capability(
        db, user.id, phase="read_history", scopes=("policy.execution.read",), operation="user_action"
    )
    episode = db.get(PolicyEpisode, episode_id)
    if episode is None or episode.user_id != user.id:
        raise ApiException(404, "POLICY_NOT_FOUND", "周期不存在")
    payload = episode_view(db, episode)
    return {"episode_id": episode.id, "observations": payload["observations"]}


@router.get("/episodes/{episode_id}/explanation")
def episode_explanation(episode_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    _require_policy_capability(
        db, user.id, phase="read_history", scopes=("policy.execution.read",), operation="user_action"
    )
    episode = db.get(PolicyEpisode, episode_id)
    if episode is None or episode.user_id != user.id:
        raise ApiException(404, "POLICY_NOT_FOUND", "周期不存在")
    unit = db.get(PersonalStrategyUnit, episode.unit_id)
    adjudication = db.scalar(select(PolicyAdjudication).where(
        PolicyAdjudication.episode_id == episode.id,
        PolicyAdjudication.revision == episode.effective_adjudication_revision,
    )) if episode.effective_adjudication_revision else None
    evidence_refs = []
    if adjudication is not None:
        try:
            evidence_refs = json.loads(adjudication.evidence_refs_json or "[]")
        except (TypeError, ValueError):
            evidence_refs = []
    protocol = {}
    if unit is not None and unit.user_id == user.id:
        try:
            protocol = json.loads(unit.protocol_json or "{}")
        except (TypeError, ValueError):
            protocol = {}
    db.commit()
    return {
        "episode_id": episode.id,
        "status": episode.status,
        "protocol": protocol,
        "context_snapshot": {
            "baseline_context_key": episode.baseline_context_key or episode.context_key,
            "followup_context_key": episode.followup_context_key or episode.context_key,
            "baseline_state_snapshot_hash": episode.baseline_state_snapshot_hash,
            "followup_state_snapshot_hash": episode.followup_state_snapshot_hash,
            "changed_variables": json.loads(episode.changed_variables_json or "[]"),
        },
        "conclusion": ({
            "label": adjudication.conclusion,
            "reasons": json.loads(adjudication.reasons_json or "[]"),
            "valid": adjudication.valid,
            "stale": adjudication.stale,
            "algorithm_version": adjudication.algorithm_version,
            "gate_version": adjudication.gate_version,
        } if adjudication else None),
        "evidence_refs": evidence_refs,
        "limitations": [
            "个人周期只提供描述性观察，不证明因果或健康效果。",
            "用户自报的负担与系统从本人记录核验的指标会分开标示。",
        ],
    }


@router.get("/history")
def policy_history(
    user=Depends(current_user),
    db: Session = Depends(get_db),
    cursor: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=20, ge=1, le=50),
):
    _require_policy_capability(
        db, user.id, phase="read_history", scopes=("policy.execution.read",), operation="user_action"
    )
    query = select(PolicyEpisode).where(PolicyEpisode.user_id == user.id)
    if cursor:
        anchor = db.get(PolicyEpisode, cursor)
        if anchor is None or anchor.user_id != user.id:
            raise ApiException(404, "POLICY_CURSOR_NOT_FOUND", "历史位置已失效，请重新载入")
        query = query.where(
            (PolicyEpisode.created_at < anchor.created_at)
            | ((PolicyEpisode.created_at == anchor.created_at) & (PolicyEpisode.id < anchor.id))
        )
    rows = db.scalars(
        query.order_by(PolicyEpisode.created_at.desc(), PolicyEpisode.id.desc()).limit(limit + 1)
    ).all()
    has_more = len(rows) > limit
    page = rows[:limit]
    items = []
    for episode in page:
        adjudication = db.scalar(select(PolicyAdjudication).where(
            PolicyAdjudication.episode_id == episode.id,
            PolicyAdjudication.revision == episode.effective_adjudication_revision,
        )) if episode.effective_adjudication_revision else None
        items.append({
            "episode_id": episode.id,
            "status": episode.status,
            "started_at": episode.start_at.isoformat() + "Z",
            "ended_at": episode.end_at.isoformat() + "Z",
            "conclusion": adjudication.conclusion if adjudication else None,
            "conclusion_valid": bool(adjudication and adjudication.valid and not adjudication.stale),
        })
    db.commit()
    return {"items": items, "next_cursor": page[-1].id if has_more and page else None}


@router.post("/episodes/{episode_id}/reports")
def report(episode_id: str, body: ExecutionReportRequest, user=Depends(current_user), db: Session = Depends(get_db)):
    _require_policy_capability(db, user.id, phase="close_existing", scopes=("policy.execution.read",), operation="user_action")
    try:
        payload = report_opportunity(db, user.id, episode_id, body); db.commit(); return payload
    except PolicyError as exc:
        db.rollback(); _raise(exc)


@router.post("/episodes/{episode_id}/observations")
def observations(episode_id: str, body: ObservationRequest, user=Depends(current_user), db: Session = Depends(get_db)):
    _require_policy_capability(db, user.id, phase="close_existing", scopes=("policy.execution.read",), operation="user_action")
    episode = db.get(PolicyEpisode, episode_id)
    if episode is None or episode.user_id != user.id:
        raise ApiException(404, "POLICY_NOT_FOUND", "周期不存在")
    if episode.status != "active":
        raise ApiException(409, "POLICY_NOT_READY", "周期已关闭，不能追加观察证据")
    if episode.version != body.episode_version:
        raise ApiException(409, "POLICY_VERSION_CONFLICT", "周期版本已变化，请刷新后重试")
    from app.models import PolicyObservationRef
    unit = db.get(PersonalStrategyUnit, episode.unit_id)
    if unit is None or unit.user_id != user.id:
        raise ApiException(404, "POLICY_NOT_FOUND", "策略协议不存在")
    try:
        protocol = json.loads(unit.protocol_json or "{}")
        expected_metric = str((protocol.get("template") or {}).get("metric_key") or "")
    except (TypeError, ValueError):
        expected_metric = ""
    if not expected_metric:
        raise ApiException(422, "POLICY_UNVERIFIABLE", "策略没有受审核的观测指标")

    # ``points`` is the one-cycle compatibility shape. New clients send refs
    # only; neither shape is allowed to author a trusted numeric value.
    raw_refs = [item.model_dump(mode="json") for item in body.refs]
    if not raw_refs:
        raw_refs = [item.model_dump(mode="json") for item in body.points]
    for item in body.self_reports:
        raw = item.model_dump(mode="json")
        raw.update({"source_type": "user_report", "source_revision": 1})
        raw_refs.append(raw)
    if not raw_refs:
        raise ApiException(422, "EVIDENCE_SOURCE_REQUIRED", "请选择要核验的记录来源")

    def _window(endpoint: str):
        if endpoint == "baseline":
            return episode.start_at - timedelta(days=30), episode.start_at
        return episode.start_at, episode.end_at + timedelta(days=1)

    inserted = False
    seen_slots = set()
    for raw in raw_refs:
        endpoint = str(raw.get("endpoint") or "followup")
        if endpoint not in {"baseline", "followup"}:
            raise ApiException(422, "EVIDENCE_ENDPOINT_INVALID", "证据阶段无效")
        source_type = str(raw.get("source_type") or "")
        source_id = str(raw.get("source_id") or "")
        try:
            slot = int(raw.get("slot"))
            source_revision = int(raw.get("source_revision") or 1)
        except (TypeError, ValueError):
            raise ApiException(422, "EVIDENCE_SOURCE_INVALID", "证据来源编号或版本无效") from None
        if not 0 <= slot < int(protocol.get("expected_days") or 0):
            raise ApiException(422, "EVIDENCE_SLOT_INVALID", "观察序号超出当前周期范围")
        slot_key = (endpoint, slot)
        if slot_key in seen_slots:
            raise ApiException(422, "EVIDENCE_SLOT_DUPLICATE", "同一请求不能重复提交同一观察序号")
        seen_slots.add(slot_key)
        metric_version = str(raw.get("metric_version") or unit.metric_version)
        if metric_version != unit.metric_version:
            raise ApiException(409, "EVIDENCE_METRIC_VERSION_MISMATCH", "证据指标版本与当前协议不一致")

        try:
            if source_type in {"user_confirmed", "user_report"}:
                # Compatibility only: self-reported values are explicitly
                # labelled as such and never masquerade as diet/check-in data.
                if raw.get("value") is None:
                    raise SourceResolutionError("EVIDENCE_VALUE_REQUIRED", "自报证据需要填写数值")
                if expected_metric != "burden":
                    raise SourceResolutionError("EVIDENCE_SELF_REPORT_NOT_ALLOWED", "当前协议不接受该指标的自报数值")
                observed_at = raw.get("observed_at")
                if not observed_at:
                    raise SourceResolutionError("EVIDENCE_SOURCE_INVALID", "自报证据缺少时间")
                from datetime import datetime
                observed_at = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
                if observed_at.tzinfo is None:
                    raise SourceResolutionError("EVIDENCE_SOURCE_INVALID", "自报时间必须包含时区")
                observed_at = observed_at.astimezone(timezone.utc).replace(tzinfo=None)
                if observed_at > utc_now():
                    raise SourceResolutionError("EVIDENCE_SOURCE_IN_FUTURE", "不能提前提交尚未发生的观察")
                if not (_window(endpoint)[0] <= observed_at <= _window(endpoint)[1]):
                    raise SourceResolutionError("EVIDENCE_SOURCE_OUTSIDE_WINDOW", "证据不在本周期观察窗口内")
                self_report_value = float(raw["value"])
                if not math.isfinite(self_report_value):
                    raise SourceResolutionError("EVIDENCE_VALUE_INVALID", "自报数值必须为有限数值")
                if not 0 <= self_report_value <= 10:
                    raise SourceResolutionError("EVIDENCE_VALUE_INVALID", "训练负担自评必须在 0 到 10 之间")
                resolved = SimpleNamespace(
                    source_type="user_report", source_id=source_id, source_revision=source_revision,
                    observed_at=observed_at, metric_version=metric_version,
                    value=self_report_value, evidence_grade="self_report",
                )
            else:
                ref = SimpleNamespace(
                    source_type=source_type, source_id=source_id,
                    source_revision=source_revision, metric_version=metric_version,
                )
                resolved = resolve_point(db, user.id, ref, expected_metric, _window(endpoint))
        except SourceResolutionError as exc:
            raise ApiException(409 if exc.code == "EVIDENCE_SOURCE_STALE" else 422, exc.code, exc.message, details={"source_type": source_type, "source_id": source_id}) from None

        existing = db.scalar(select(PolicyObservationRef).where(PolicyObservationRef.episode_id == episode.id, PolicyObservationRef.endpoint == endpoint, PolicyObservationRef.slot == slot))
        if existing is not None:
            same = (
                existing.source_type == resolved.source_type
                and existing.source_id == resolved.source_id
                and existing.source_revision == resolved.source_revision
                and existing.metric_version == resolved.metric_version
                and existing.value_json == json.dumps(float(resolved.value))
                and existing.observed_at.replace(tzinfo=None) == resolved.observed_at.replace(tzinfo=None)
                and existing.confirmed == (resolved.evidence_grade in {"user_confirmed", "self_report"})
            )
            if same and existing.valid:
                continue
            if existing.valid:
                raise ApiException(409, "POLICY_VERSION_CONFLICT", "相同观察槽已有不同证据，请刷新后重试")
            # Keep the slot stable while allowing a user to replace evidence
            # invalidated by a source correction. Prior adjudications retain
            # their own immutable evidence_refs_json snapshot.
            existing.source_type = resolved.source_type
            existing.source_id = resolved.source_id
            existing.source_revision = resolved.source_revision
            existing.value_json = json.dumps(float(resolved.value))
            existing.observed_at = resolved.observed_at.replace(tzinfo=None)
            existing.metric_version = resolved.metric_version
            existing.confirmed = resolved.evidence_grade in {"user_confirmed", "self_report"}
            existing.valid = True
            inserted = True
            continue
        db.add(PolicyObservationRef(
            user_id=user.id,
            episode_id=episode.id,
            endpoint=endpoint,
            slot=slot,
            source_type=resolved.source_type,
            source_id=resolved.source_id,
            source_revision=resolved.source_revision,
            value_json=json.dumps(float(resolved.value)),
            observed_at=resolved.observed_at.replace(tzinfo=None),
            metric_version=resolved.metric_version,
            confirmed=resolved.evidence_grade in {"user_confirmed", "self_report"},
            valid=True,
        ))
        inserted = True
    if inserted:
        episode.version += 1
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiException(409, "POLICY_VERSION_CONFLICT", "观察记录已在别处更新，请刷新后重试") from None
    return episode_view(db, episode)


@router.post("/episodes/{episode_id}/review-preview")
def review_preview(episode_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    _require_policy_capability(db, user.id, phase="close_existing", scopes=("policy.execution.read", "policy.outcomes.read"), operation="user_action")
    row = db.get(PolicyEpisode, episode_id)
    if row is None or row.user_id != user.id:
        raise ApiException(404, "POLICY_NOT_FOUND", "周期不存在")
    from app.services.policy_learning.repository import _build_evidence
    from app.services.policy_learning.algorithm import adjudicate
    verdict = adjudicate(_build_evidence(db, row, invalidate_stale=False))
    return {"episode_id": episode_id, "preview": True, "execution_label": verdict.execution_label, "support_label": verdict.support_label, "availability_label": verdict.availability_label, "conclusion": verdict.conclusion, "reasons": list(verdict.reasons), "observed_score": verdict.observed_score}


@router.post("/episodes/{episode_id}/finish")
def finish(episode_id: str, body: EpisodeFinishRequest, user=Depends(current_user), db: Session = Depends(get_db)):
    _require_policy_capability(db, user.id, phase="close_existing", scopes=("policy.execution.read", "policy.outcomes.read"), operation="user_action")
    try:
        payload = finish_episode(db, user.id, episode_id, body.episode_version); db.commit(); return payload
    except PolicyError as exc:
        db.rollback(); _raise(exc)


@router.post("/episodes/{episode_id}/finish-proposal")
def finish_proposal(episode_id: str, body: EpisodeFinishRequest, user=Depends(current_user), db: Session = Depends(get_db)):
    _require_policy_capability(db, user.id, phase="close_existing", scopes=("policy.execution.read",), operation="user_action")
    row = db.get(PolicyEpisode, episode_id)
    if row is None or row.user_id != user.id:
        raise ApiException(404, "POLICY_NOT_FOUND", "周期不存在")
    if row.version != body.episode_version:
        raise ApiException(409, "POLICY_VERSION_CONFLICT", "周期版本已变化，请刷新后重试")
    from app.services.agent.action_proposals import propose_action, proposal_view
    proposal, _ = propose_action(db, user_id=user.id, action_key="policy.episode.finish", arguments={"episode_id": row.id, "episode_version": row.version}, title="复查个人策略周期", risk_level="low", requires_confirmation=True, user_visible_reason="周期已到复查阶段，是否按当前证据生成裁决？")
    return {"approval_required": True, **proposal_view(proposal)}


@router.post("/episodes/{episode_id}/stop")
def stop(episode_id: str, body: StopRequest, user=Depends(current_user), db: Session = Depends(get_db)):
    _require_policy_capability(db, user.id, phase="close_existing", scopes=("policy.execution.read",), operation="user_action")
    row = db.get(PolicyEpisode, episode_id)
    if row is None or row.user_id != user.id:
        raise ApiException(404, "POLICY_NOT_FOUND", "周期不存在")
    if row.version != body.episode_version:
        raise ApiException(409, "POLICY_VERSION_CONFLICT", "周期版本已变化，请刷新后重试")
    from app.services.policy_learning.repository import stop_episode
    try:
        payload = stop_episode(db, user.id, episode_id, body.episode_version, body.reason_code); db.commit(); return payload
    except PolicyError as exc:
        db.rollback(); _raise(exc)


@router.post("/episodes/{episode_id}/stop-proposal")
def stop_proposal(episode_id: str, body: StopRequest, user=Depends(current_user), db: Session = Depends(get_db)):
    _require_policy_capability(db, user.id, phase="close_existing", scopes=("policy.execution.read",), operation="user_action")
    row = db.get(PolicyEpisode, episode_id)
    if row is None or row.user_id != user.id:
        raise ApiException(404, "POLICY_NOT_FOUND", "周期不存在")
    if row.version != body.episode_version:
        raise ApiException(409, "POLICY_VERSION_CONFLICT", "周期版本已变化，请刷新后重试")
    from app.services.agent.action_proposals import propose_action, proposal_view
    proposal, _ = propose_action(db, user_id=user.id, action_key="policy.episode.stop", arguments={"episode_id": row.id, "episode_version": row.version, "reason_code": body.reason_code}, title="停止个人策略周期", risk_level="low", requires_confirmation=True, user_visible_reason="你要求停止当前策略周期，已保留此前记录。")
    return {"approval_required": True, **proposal_view(proposal)}


@router.get("/strategies/{strategy_id}/memory")
def memory(strategy_id: str, context_key: str = Query(...), user=Depends(current_user), db: Session = Depends(get_db)):
    _require_policy_capability(db, user.id, phase="read_history", scopes=("policy.execution.read", "policy.outcomes.read"), operation="user_action")
    try:
        from app.services.policy_learning.api_helpers import read_memory
        payload = read_memory(db, user.id, strategy_id, context_key)
        db.commit()
        return payload
    except PolicyError as exc:
        _raise(exc)


@router.post("/memory/reset")
def reset_memory(body: ResetRequest, user=Depends(current_user), db: Session = Depends(get_db)):
    _require_policy_capability(db, user.id, phase="delete", scopes=("policy.execution.read",), operation="user_action")
    scope = body.strategy_id if body.scope == "strategy" and body.strategy_id else "*"
    row = current_control(db, user.id, scope)
    from app.core.time import utc_now
    row.epoch_counter += 1; row.reset_at = utc_now()
    db.commit()
    return {"reset": True, "scope": body.scope, "scope_key": scope, "learning_epoch": learning_epoch(db, user.id, scope)}


@router.post("/memory/reset-proposal")
def reset_memory_proposal(body: ResetRequest, user=Depends(current_user), db: Session = Depends(get_db)):
    """Create the auditable user-confirmation path for destructive learning reset."""
    _require_policy_capability(db, user.id, phase="delete", scopes=("policy.execution.read",), operation="user_action")
    from app.services.agent.action_proposals import propose_action, proposal_view
    arguments = {"scope": body.scope, "strategy_id": body.strategy_id, "version": body.version}
    proposal, _ = propose_action(db, user_id=user.id, action_key="policy.memory.reset", arguments=arguments, title="重置个人策略经验", risk_level="medium", requires_confirmation=True, user_visible_reason="这会让指定策略重新从中性先验开始，历史证据仍保留但不再参与当前学习。")
    return {"approval_required": True, **proposal_view(proposal)}
