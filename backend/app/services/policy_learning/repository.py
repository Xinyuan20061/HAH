from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import (
    AgentMicroExperiment,
    PersonalPolicyBelief, PersonalStrategyUnit, PolicyActiveSlot, PolicyAdjudication,
    PolicyDomainGeneration, PolicyEpisode, PolicyExecutionOpportunity, PolicyLearningControl,
    PolicyObservationRef, PolicyOutbox, PolicyReport,
)

from .algorithm import Adjudication, BetaBelief, BeliefSet, EpisodeEvidence, Point, Protocol, Scope, adjudicate


class PolicyError(ValueError):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def current_control(db: Session, user_id: int, scope_key: str, *, create: bool = True) -> PolicyLearningControl | None:
    row = db.scalar(select(PolicyLearningControl).where(PolicyLearningControl.user_id == user_id, PolicyLearningControl.scope_key == scope_key))
    if row is None and create:
        row = PolicyLearningControl(user_id=user_id, scope_key=scope_key, epoch_counter=0, learning_enabled=True)
        db.add(row); db.flush()
    return row


def learning_epoch(db: Session, user_id: int, scope_key: str) -> str:
    row = current_control(db, user_id, scope_key)
    # A global reset must invalidate every strategy, not just the wildcard
    # memory row itself.  Keep both counters in the epoch so old adjudications
    # can never leak back after an account-wide reset.
    global_row = current_control(db, user_id, "*") if scope_key != "*" else row
    return f"{scope_key}:g{global_row.epoch_counter if global_row else 0}:s{row.epoch_counter if row else 0}"


def _scope_from_unit(row: PersonalStrategyUnit) -> Scope:
    payload = json.loads(row.protocol_json)
    return Scope(row.user_id, row.strategy_id, row.protocol_version, row.metric_version, row.context_key)


def _protocol_from_unit(row: PersonalStrategyUnit) -> Protocol:
    payload = json.loads(row.protocol_json)
    return Protocol(_scope_from_unit(row), expected_days=payload["expected_days"], minimum_days=payload["minimum_days"], minimum_coverage=payload["minimum_coverage"], execution_target=payload["execution_target"], mode=payload["mode"], direction=payload["direction"], target=payload["target"], ambiguity_band=payload["ambiguity_band"], changed_variable=payload["changed_variable"], aggregation=payload.get("aggregation", "paired_median"))


def _active_slot(db: Session, user_id: int):
    return db.scalar(select(PolicyActiveSlot).where(PolicyActiveSlot.user_id == user_id))


def start_episode(db: Session, user_id: int, unit_id: str, protocol_hash: str, version: int, decision_id: str | None = None) -> dict:
    unit = db.get(PersonalStrategyUnit, unit_id)
    if unit is None or unit.user_id != user_id:
        raise PolicyError("POLICY_NOT_FOUND", "策略不存在或不属于当前用户")
    if unit.protocol_hash != protocol_hash:
        raise PolicyError("POLICY_PROTOCOL_CHANGED", "策略协议已变化，需要重新确认")
    if unit.status != "compiled":
        raise PolicyError("POLICY_UNVERIFIABLE", "策略还有必需信息未补齐")
    control = current_control(db, user_id, unit.strategy_id)
    if not control.learning_enabled:
        raise PolicyError("POLICY_LEARNING_DISABLED", "个人策略学习已关闭")
    if _active_slot(db, user_id) is not None:
        raise PolicyError("POLICY_EPISODE_ACTIVE", "已有一个进行中的策略周期")
    # The legacy micro-experiment lane and the policy-learning lane share one
    # user-facing active slot.  They must not run concurrently.
    legacy_active = db.scalar(select(AgentMicroExperiment).where(AgentMicroExperiment.user_id == user_id, AgentMicroExperiment.status == "active"))
    if legacy_active is not None:
        raise PolicyError("POLICY_EPISODE_ACTIVE", "已有一个进行中的策略周期")
    now = utc_now(); protocol = json.loads(unit.protocol_json)
    episode_id = uuid4().hex
    episode = PolicyEpisode(id=episode_id, user_id=user_id, unit_id=unit.id, decision_id=decision_id, learning_epoch=learning_epoch(db, user_id, unit.strategy_id), status="active", start_at=now, end_at=now + timedelta(days=int(protocol["expected_days"])), version=version, protocol_snapshot_json=unit.protocol_json, execution_json=_json([None] * int(protocol["expected_days"])), context_key=unit.context_key)
    db.add(episode)
    db.add(PolicyActiveSlot(user_id=user_id, episode_kind="policy", episode_id=episode_id, version=version))
    for slot in range(int(protocol["expected_days"])):
        db.add(PolicyExecutionOpportunity(id=uuid4().hex, user_id=user_id, episode_id=episode_id, scheduled_at=now + timedelta(days=slot), slot=slot, frozen_action_json=_json({"strategy_id": unit.strategy_id, "protocol_hash": protocol_hash})))
    try:
        db.flush()
    except IntegrityError:
        # Two requests can pass the read check at the same time; the unique
        # active-slot key is the final arbiter and must become a safe 409.
        db.rollback()
        raise PolicyError("POLICY_EPISODE_ACTIVE", "已有一个进行中的策略周期") from None
    return episode_view(db, episode)


def episode_view(db: Session, episode: PolicyEpisode) -> dict:
    reports = db.scalars(select(PolicyReport).where(PolicyReport.episode_id == episode.id).order_by(PolicyReport.opportunity_id, PolicyReport.revision)).all()
    opportunities = db.scalars(select(PolicyExecutionOpportunity).where(PolicyExecutionOpportunity.episode_id == episode.id).order_by(PolicyExecutionOpportunity.slot)).all()
    adjudications = db.scalars(select(PolicyAdjudication).where(PolicyAdjudication.episode_id == episode.id).order_by(PolicyAdjudication.revision)).all()
    return {"episode_id": episode.id, "strategy_unit_id": episode.unit_id, "status": episode.status, "version": episode.version, "start_at": episode.start_at.isoformat() + "Z", "end_at": episode.end_at.isoformat() + "Z", "learning_epoch": episode.learning_epoch, "stop_reason": episode.stop_reason, "review_revision": episode.review_revision, "effective_adjudication_revision": episode.effective_adjudication_revision, "opportunities": [{"id": x.id, "slot": x.slot, "scheduled_at": x.scheduled_at.isoformat() + "Z", "report_id": x.current_report_id} for x in opportunities], "reports": [{"id": x.id, "opportunity_id": x.opportunity_id, "revision": x.revision, "execution": x.execution, "burden": x.burden, "confounders": json.loads(x.confounders_json)} for x in reports], "adjudications": [{"revision": x.revision, "execution_label": x.execution_label, "support_label": x.support_label, "availability_label": x.availability_label, "conclusion": x.conclusion, "reasons": json.loads(x.reasons_json), "stale": x.stale, "valid": x.valid} for x in adjudications]}


def report_opportunity(db: Session, user_id: int, episode_id: str, req) -> dict:
    episode = db.get(PolicyEpisode, episode_id)
    if episode is None or episode.user_id != user_id:
        raise PolicyError("POLICY_NOT_FOUND", "周期不存在")
    if episode.status != "active":
        raise PolicyError("POLICY_NOT_READY", "周期已关闭，不能追加执行报告")
    if episode.version != req.episode_version:
        raise PolicyError("POLICY_VERSION_CONFLICT", "周期版本已变化，请刷新后重试")
    opportunity = db.get(PolicyExecutionOpportunity, req.opportunity_id)
    if opportunity is None or opportunity.episode_id != episode.id or opportunity.user_id != user_id:
        raise PolicyError("POLICY_NOT_FOUND", "执行机会不存在")
    payload = {"opportunity_id": opportunity.id, "execution": req.execution, "burden": req.perceived_burden, "confounders": req.confounder_codes}
    digest = hashlib.sha256(_json(payload).encode()).hexdigest()
    existing = db.scalar(select(PolicyReport).where(PolicyReport.user_id == user_id, PolicyReport.client_report_id == req.report_id))
    if existing is not None:
        if existing.payload_hash != digest:
            raise PolicyError("POLICY_VERSION_CONFLICT", "相同报告编号的内容不同")
        return episode_view(db, episode)
    revision = (db.scalar(select(PolicyReport.revision).where(PolicyReport.opportunity_id == opportunity.id).order_by(PolicyReport.revision.desc()).limit(1)) or 0) + 1
    row = PolicyReport(id=uuid4().hex, client_report_id=req.report_id, user_id=user_id, episode_id=episode.id, opportunity_id=opportunity.id, revision=revision, execution=req.execution, burden=req.perceived_burden, confounders_json=_json(req.confounder_codes), received_at=utc_now(), payload_hash=digest)
    db.add(row); opportunity.current_report_id = row.id
    execution = json.loads(episode.execution_json or "[]")
    execution[opportunity.slot] = True if req.execution == "completed" else False if req.execution == "explicitly_not_completed" else None
    episode.execution_json = _json(execution)
    episode.version += 1
    db.flush()
    return episode_view(db, episode)


def _build_evidence(db: Session, episode: PolicyEpisode) -> EpisodeEvidence:
    protocol = _protocol_from_unit(db.get(PersonalStrategyUnit, episode.unit_id))
    reports = db.scalars(select(PolicyReport).where(PolicyReport.episode_id == episode.id)).all()
    by_slot = {}
    for report in reports:
        opportunity = db.get(PolicyExecutionOpportunity, report.opportunity_id)
        if opportunity is not None:
            by_slot[opportunity.slot] = report
    execution = tuple(True if by_slot.get(i) and by_slot[i].execution == "completed" else False if by_slot.get(i) and by_slot[i].execution == "explicitly_not_completed" else None for i in range(protocol.expected_days))
    refs = db.scalars(select(PolicyObservationRef).where(PolicyObservationRef.episode_id == episode.id, PolicyObservationRef.valid.is_(True))).all()
    points = tuple(Point(r.slot, float(json.loads(r.value_json)), f"{r.source_type}:{r.source_id}", r.source_revision, r.metric_version, r.confirmed) for r in refs)
    confounders = tuple(code for row in reports for code in json.loads(row.confounders_json))
    baseline = tuple(point for row, point in zip(refs, points) if row.endpoint == "baseline")
    followup = tuple(point for row, point in zip(refs, points) if row.endpoint == "followup")
    return EpisodeEvidence(episode.id, episode.review_revision + 1, protocol, execution, baseline, followup, baseline_context=episode.context_key, followup_context=episode.context_key, confounders=confounders, adverse_event=episode.status == "stopped", window_closed=True)


def finish_episode(db: Session, user_id: int, episode_id: str, version: int) -> dict:
    episode = db.get(PolicyEpisode, episode_id)
    if episode is None or episode.user_id != user_id:
        raise PolicyError("POLICY_NOT_FOUND", "周期不存在")
    if episode.version != version:
        raise PolicyError("POLICY_VERSION_CONFLICT", "周期版本已变化，请刷新后重试")
    if episode.status not in {"active", "awaiting_review"}:
        raise PolicyError("POLICY_NOT_READY", "周期已经结束")
    if utc_now() < episode.end_at:
        raise PolicyError("POLICY_NOT_READY", "观察窗口尚未结束，不能生成最终裁决")
    evidence = _build_evidence(db, episode)
    verdict = adjudicate(evidence)
    revision = episode.review_revision + 1
    episode.review_revision = revision; episode.effective_adjudication_revision = revision; episode.status = "reviewed"; episode.version += 1
    db.add(PolicyAdjudication(id=uuid4().hex, user_id=user_id, episode_id=episode.id, revision=revision, learning_epoch=episode.learning_epoch, execution_label=verdict.execution_label, support_label=verdict.support_label, availability_label=verdict.availability_label, conclusion=verdict.conclusion, reasons_json=_json(verdict.reasons), evidence_refs_json="[]", source_hash=hashlib.sha256(_json(verdict.__dict__).encode()).hexdigest(), algorithm_version="egpl-v1.0.0", gate_version="evidence-gate-v1.0.0"))
    db.execute(delete(PolicyActiveSlot).where(PolicyActiveSlot.user_id == user_id))
    db.add(PolicyOutbox(event_id=uuid4().hex, user_id=user_id, event_type="policy.adjudicated", ref_id=episode.id, revision=revision, payload=_json({"episode_id": episode.id, "revision": revision}), status="pending"))
    db.flush()
    return {"episode": episode_view(db, episode), "adjudication": {"revision": revision, "execution_label": verdict.execution_label, "support_label": verdict.support_label, "availability_label": verdict.availability_label, "conclusion": verdict.conclusion, "reasons": list(verdict.reasons), "observed_score": verdict.observed_score}}


def stop_episode(db: Session, user_id: int, episode_id: str, version: int, reason_code: str) -> dict:
    episode = db.get(PolicyEpisode, episode_id)
    if episode is None or episode.user_id != user_id:
        raise PolicyError("POLICY_NOT_FOUND", "周期不存在")
    if episode.version != version:
        raise PolicyError("POLICY_VERSION_CONFLICT", "周期版本已变化，请刷新后重试")
    if episode.status not in {"active", "awaiting_review"}:
        raise PolicyError("POLICY_NOT_READY", "周期已经结束")
    episode.status, episode.stop_reason, episode.version = "stopped", reason_code, episode.version + 1
    db.execute(delete(PolicyActiveSlot).where(PolicyActiveSlot.user_id == user_id))
    evidence = _build_evidence(db, episode)
    verdict = adjudicate(evidence)
    revision = episode.review_revision + 1
    episode.review_revision, episode.effective_adjudication_revision = revision, revision
    db.add(PolicyAdjudication(id=uuid4().hex, user_id=user_id, episode_id=episode.id, revision=revision, learning_epoch=episode.learning_epoch, execution_label=verdict.execution_label, support_label=None, availability_label=None, conclusion="stopped", reasons_json=_json((reason_code,) + verdict.reasons), evidence_refs_json="[]", source_hash=hashlib.sha256(_json(verdict.__dict__).encode()).hexdigest(), algorithm_version="egpl-v1.0.0", gate_version="evidence-gate-v1.0.0"))
    db.add(PolicyOutbox(event_id=uuid4().hex, user_id=user_id, event_type="policy.adjudicated", ref_id=episode.id, revision=revision, payload=_json({"episode_id": episode.id, "revision": revision}), status="pending"))
    db.flush()
    return {"episode": episode_view(db, episode), "adjudication": {"revision": revision, "execution_label": verdict.execution_label, "support_label": None, "availability_label": None, "conclusion": "stopped", "reasons": [reason_code, *verdict.reasons]}}


def rebuild_beliefs(db: Session, user_id: int, strategy_id: str, context_key: str) -> dict:
    unit = db.scalar(select(PersonalStrategyUnit).where(PersonalStrategyUnit.user_id == user_id, PersonalStrategyUnit.strategy_id == strategy_id, PersonalStrategyUnit.context_key == context_key).order_by(PersonalStrategyUnit.created_at.desc()))
    if unit is None:
        raise PolicyError("POLICY_NOT_FOUND", "策略经验不存在")
    epoch = learning_epoch(db, user_id, strategy_id)
    unit_ids = select(PersonalStrategyUnit.id).where(PersonalStrategyUnit.user_id == user_id, PersonalStrategyUnit.strategy_id == strategy_id, PersonalStrategyUnit.context_key == context_key)
    adjudications = db.scalars(select(PolicyAdjudication).join(PolicyEpisode, PolicyEpisode.id == PolicyAdjudication.episode_id).where(PolicyAdjudication.user_id == user_id, PolicyEpisode.unit_id.in_(unit_ids), PolicyAdjudication.learning_epoch == epoch, PolicyAdjudication.valid.is_(True), PolicyAdjudication.stale.is_(False), PolicyAdjudication.revision == PolicyEpisode.effective_adjudication_revision)).all()
    labels = {"execution": [], "support": [], "availability": []}
    for row in adjudications:
        labels["execution"].append(row.execution_label); labels["support"].append(row.support_label); labels["availability"].append(row.availability_label)
    out = {}
    for endpoint, values in labels.items():
        pos, neg = sum(v == 1 for v in values), sum(v == 0 for v in values)
        out[endpoint] = {"alpha": 1 + pos, "beta": 1 + neg, "positive_count": pos, "negative_count": neg, "episodes": pos + neg}
        row = db.scalar(select(PersonalPolicyBelief).where(PersonalPolicyBelief.user_id == user_id, PersonalPolicyBelief.strategy_id == strategy_id, PersonalPolicyBelief.protocol_version == unit.protocol_version, PersonalPolicyBelief.metric_version == unit.metric_version, PersonalPolicyBelief.context_key == context_key, PersonalPolicyBelief.endpoint == endpoint, PersonalPolicyBelief.learning_epoch == epoch))
        if row is None:
            row = PersonalPolicyBelief(user_id=user_id, strategy_id=strategy_id, protocol_version=unit.protocol_version, metric_version=unit.metric_version, context_key=context_key, endpoint=endpoint, learning_epoch=epoch)
            db.add(row)
        row.alpha, row.beta, row.positive_count, row.negative_count = 1 + pos, 1 + neg, pos, neg
    db.flush()
    return {"strategy_id": strategy_id, "context_key": context_key, "learning_epoch": epoch, "beliefs": out, "eligible_episodes": len(adjudications)}
