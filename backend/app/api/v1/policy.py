"""Evidence-gated personal strategy learning API (policy spec §14)."""

from __future__ import annotations

import json
import hashlib
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.database import get_db
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
    rebuild_beliefs, report_opportunity, start_episode,
)
from app.services.policy_learning.templates import TEMPLATE_REGISTRY, get_template
from app.harness.plugins import enabled_plugin_ids

router = APIRouter(prefix="/policy", tags=["policy-learning"])


def _raise(exc: PolicyError):
    status = 404 if exc.code == "POLICY_NOT_FOUND" else 409 if exc.code in {"POLICY_PROTOCOL_CHANGED", "POLICY_VERSION_CONFLICT", "POLICY_EPISODE_ACTIVE"} else 422
    raise ApiException(status, exc.code, exc.message)


@router.get("/templates")
def templates(user=Depends(current_user)):
    return {"templates": [{"template_id": key, **value} for key, value in TEMPLATE_REGISTRY.items()], "policy": "模板和指标由服务端审核，客户端不能改写判定门槛。"}


@router.post("/compile")
def compile_policy(body: StrategyCompileRequest, user=Depends(current_user), db: Session = Depends(get_db)):
    if "personal_policy" not in enabled_plugin_ids(db, user.id):
        raise ApiException(409, "PLUGIN_DISABLED", "个人策略能力已暂停，请先在能力中心启用")
    try:
        payload = compile_strategy(db, user.id, body.template_id, body.template_version, body.parameters, body.goal_key)
    except KeyError:
        raise ApiException(422, "POLICY_TEMPLATE_NOT_FOUND", "策略模板不存在或未审核") from None
    except ValueError as exc:
        raise ApiException(422, "POLICY_UNVERIFIABLE", str(exc)) from None
    db.commit()
    return payload


@router.get("/units/{unit_id}")
def read_unit(unit_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(PersonalStrategyUnit, unit_id)
    if row is None or row.user_id != user.id:
        raise ApiException(404, "POLICY_NOT_FOUND", "策略不存在")
    return {"strategy_unit_id": row.id, "strategy_id": row.strategy_id, "template_id": row.template_id, "status": row.status, "protocol_hash": row.protocol_hash, "context_key": row.context_key, "protocol": json.loads(row.protocol_json), "context": json.loads(row.context_json), "state_snapshot_hash": row.state_snapshot_hash}


@router.post("/units/{unit_id}/proposal")
def unit_proposal(unit_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    if "personal_policy" not in enabled_plugin_ids(db, user.id):
        raise ApiException(409, "PLUGIN_DISABLED", "个人策略能力已暂停，请先在能力中心启用")
    row = db.get(PersonalStrategyUnit, unit_id)
    if row is None or row.user_id != user.id:
        raise ApiException(404, "POLICY_NOT_FOUND", "策略不存在")
    if row.status != "compiled":
        raise ApiException(422, "POLICY_UNVERIFIABLE", "策略还有必需信息未补齐")
    from app.services.agent.action_proposals import propose_action, proposal_view
    proposal, _ = propose_action(db, user_id=user.id, action_key="policy.episode.start", arguments={"strategy_unit_id": row.id, "protocol_hash": row.protocol_hash, "version": 1}, title="开始个人策略验证", risk_level="low", requires_confirmation=True, user_visible_reason="这是一次可撤销的个人策略验证周期，需你确认后开始。")
    return {"approval_required": True, **proposal_view(proposal)}


@router.post("/decide")
def decide_policy(body: DecisionRequest, user=Depends(current_user), db: Session = Depends(get_db)):
    if "personal_policy" not in enabled_plugin_ids(db, user.id):
        raise ApiException(409, "PLUGIN_DISABLED", "个人策略能力已暂停，请先在能力中心启用")
    context_key = body.context_key
    from app.services.health_state import build_snapshot
    snapshot = build_snapshot(db, user.id, window_days=7, persist=False)
    hard_blocked = bool(snapshot.hard_constraints())
    rows = db.scalars(select(PersonalStrategyUnit).where(PersonalStrategyUnit.user_id == user.id, PersonalStrategyUnit.status == "compiled").order_by(PersonalStrategyUnit.created_at.desc()).limit(20)).all()
    if context_key:
        rows = [row for row in rows if row.context_key == context_key]
    candidates, beliefs = [], {}
    for row in rows:
        scope = Scope(row.user_id, row.strategy_id, row.protocol_version, row.metric_version, row.context_key)
        candidates.append(Candidate(row.strategy_id, scope, 0.3, 1.0, not hard_blocked, True))
        belief_rows = db.scalars(select(PersonalPolicyBelief).where(PersonalPolicyBelief.user_id == user.id, PersonalPolicyBelief.strategy_id == row.strategy_id, PersonalPolicyBelief.context_key == row.context_key)).all()
        values = {item.endpoint: item for item in belief_rows}
        beliefs[scope] = BeliefSet(BetaBelief(values.get("execution").alpha, values.get("execution").beta) if values.get("execution") else BetaBelief(), BetaBelief(values.get("support").alpha, values.get("support").beta) if values.get("support") else BetaBelief(), BetaBelief(values.get("availability").alpha, values.get("availability").beta) if values.get("availability") else BetaBelief())
    from app.services.policy_learning.algorithm import rank_candidates
    result = rank_candidates(beliefs, candidates, user_id=user.id, context_key=context_key or (rows[0].context_key if rows else "unknown"), exploration_consented=body.exploration_consented)
    from app.core.time import utc_now
    from uuid import uuid4
    decision_id = "pd_" + uuid4().hex
    generation = max((x.source_generation for x in db.scalars(select(PolicyDomainGeneration).where(PolicyDomainGeneration.user_id == user.id)).all()), default=0)
    config_hash = hashlib.sha256(json.dumps({"algorithm_version": result["algorithm_version"], "exploration_consented": body.exploration_consented}, sort_keys=True).encode()).hexdigest()
    db.add(PolicyDecision(id=decision_id, user_id=user.id, state_hash=snapshot.snapshot_hash, belief_generation=generation, candidate_json=json.dumps({"ranked": result["ranked"], "filtered": result["filtered"]}, ensure_ascii=False), selected_id=result["selected"], policy_mode=result["policy_mode"], propensity_json=json.dumps({"selected": result["selection_propensity"]}), config_hash=config_hash, created_at=utc_now()))
    db.commit()
    return {"decision_id": decision_id, "algorithm_version": result["algorithm_version"], "policy_mode": result["policy_mode"], "requires_user_confirmation": result["requires_user_confirmation"], "personalised": bool(result["ranked"] and result["ranked"][0]["personalised"]), "evidence_used": [], "evidence_missing": [] if result["selected"] else ["compiled_strategy_unit"], **result}


@router.get("/decide")
def decide_policy_read(user=Depends(current_user), db: Session = Depends(get_db), context_key: str | None = Query(default=None)):
    """Read-only default decision for the state page; never enables exploration."""
    return decide_policy(DecisionRequest(context_key=context_key, exploration_consented=False), user, db)


@router.post("/episodes/start")
def start(body: EpisodeStartRequest, user=Depends(current_user), db: Session = Depends(get_db)):
    if "personal_policy" not in enabled_plugin_ids(db, user.id):
        raise ApiException(409, "PLUGIN_DISABLED", "个人策略能力已暂停，请先在能力中心启用")
    try:
        payload = start_episode(db, user.id, body.strategy_unit_id, body.protocol_hash, body.version, body.decision_id)
        db.commit()
        return payload
    except PolicyError as exc:
        db.rollback(); _raise(exc)


@router.get("/episodes/current")
def current_episode(user=Depends(current_user), db: Session = Depends(get_db)):
    row = db.scalar(select(PolicyEpisode).where(PolicyEpisode.user_id == user.id, PolicyEpisode.status.in_(("active", "awaiting_review"))).order_by(PolicyEpisode.start_at.desc()))
    return {"episode": episode_view(db, row) if row else None}


@router.get("/episodes/{episode_id}")
def read_episode(episode_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(PolicyEpisode, episode_id)
    if row is None or row.user_id != user.id:
        raise ApiException(404, "POLICY_NOT_FOUND", "周期不存在")
    return episode_view(db, row)


@router.post("/episodes/{episode_id}/reports")
def report(episode_id: str, body: ExecutionReportRequest, user=Depends(current_user), db: Session = Depends(get_db)):
    try:
        payload = report_opportunity(db, user.id, episode_id, body); db.commit(); return payload
    except PolicyError as exc:
        db.rollback(); _raise(exc)


@router.post("/episodes/{episode_id}/observations")
def observations(episode_id: str, body: ObservationRequest, user=Depends(current_user), db: Session = Depends(get_db)):
    episode = db.get(PolicyEpisode, episode_id)
    if episode is None or episode.user_id != user.id:
        raise ApiException(404, "POLICY_NOT_FOUND", "周期不存在")
    if episode.status != "active":
        raise ApiException(409, "POLICY_NOT_READY", "周期已关闭，不能追加观察证据")
    if episode.version != body.episode_version:
        raise ApiException(409, "POLICY_VERSION_CONFLICT", "周期版本已变化，请刷新后重试")
    from app.models import PolicyObservationRef
    inserted = False
    for point in body.points:
        existing = db.scalar(select(PolicyObservationRef).where(PolicyObservationRef.episode_id == episode.id, PolicyObservationRef.endpoint == point.endpoint, PolicyObservationRef.slot == point.slot))
        if existing is not None:
            same = (existing.source_type == point.source_type and existing.source_id == point.source_id and existing.source_revision == point.source_revision and existing.metric_version == point.metric_version and existing.value_json == json.dumps(point.value))
            if same:
                continue
            raise ApiException(409, "POLICY_VERSION_CONFLICT", "相同观察槽已有不同证据，请刷新后重试")
        db.add(PolicyObservationRef(user_id=user.id, episode_id=episode.id, endpoint=point.endpoint, slot=point.slot, source_type=point.source_type, source_id=point.source_id, source_revision=point.source_revision, value_json=json.dumps(point.value), observed_at=point.observed_at.replace(tzinfo=None), metric_version=point.metric_version, confirmed=point.confirmed, valid=True))
        inserted = True
    if inserted:
        episode.version += 1
    db.commit()
    return episode_view(db, episode)


@router.post("/episodes/{episode_id}/review-preview")
def review_preview(episode_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(PolicyEpisode, episode_id)
    if row is None or row.user_id != user.id:
        raise ApiException(404, "POLICY_NOT_FOUND", "周期不存在")
    from app.services.policy_learning.repository import _build_evidence
    from app.services.policy_learning.algorithm import adjudicate
    verdict = adjudicate(_build_evidence(db, row))
    return {"episode_id": episode_id, "preview": True, "execution_label": verdict.execution_label, "support_label": verdict.support_label, "availability_label": verdict.availability_label, "conclusion": verdict.conclusion, "reasons": list(verdict.reasons), "observed_score": verdict.observed_score}


@router.post("/episodes/{episode_id}/finish")
def finish(episode_id: str, body: EpisodeFinishRequest, user=Depends(current_user), db: Session = Depends(get_db)):
    try:
        payload = finish_episode(db, user.id, episode_id, body.episode_version); db.commit(); return payload
    except PolicyError as exc:
        db.rollback(); _raise(exc)


@router.post("/episodes/{episode_id}/finish-proposal")
def finish_proposal(episode_id: str, body: EpisodeFinishRequest, user=Depends(current_user), db: Session = Depends(get_db)):
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
    try:
        return rebuild_beliefs(db, user.id, strategy_id, context_key)
    except PolicyError as exc:
        _raise(exc)


@router.post("/memory/reset")
def reset_memory(body: ResetRequest, user=Depends(current_user), db: Session = Depends(get_db)):
    scope = body.strategy_id if body.scope == "strategy" and body.strategy_id else "*"
    row = current_control(db, user.id, scope)
    from app.core.time import utc_now
    row.epoch_counter += 1; row.reset_at = utc_now()
    db.commit()
    return {"reset": True, "scope": body.scope, "scope_key": scope, "learning_epoch": learning_epoch(db, user.id, scope)}


@router.post("/memory/reset-proposal")
def reset_memory_proposal(body: ResetRequest, user=Depends(current_user), db: Session = Depends(get_db)):
    """Create the auditable user-confirmation path for destructive learning reset."""
    from app.services.agent.action_proposals import propose_action, proposal_view
    arguments = {"scope": body.scope, "strategy_id": body.strategy_id, "version": body.version}
    proposal, _ = propose_action(db, user_id=user.id, action_key="policy.memory.reset", arguments=arguments, title="重置个人策略经验", risk_level="medium", requires_confirmation=True, user_visible_reason="这会让指定策略重新从中性先验开始，历史证据仍保留但不再参与当前学习。")
    return {"approval_required": True, **proposal_view(proposal)}
