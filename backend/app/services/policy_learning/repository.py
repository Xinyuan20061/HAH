from __future__ import annotations

import hashlib
import json
import math
from datetime import timedelta
from types import SimpleNamespace
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
from .source_registry import SourceResolutionError, resolve_point


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


def learning_epoch(
    db: Session, user_id: int, scope_key: str, *, create: bool = True
) -> str:
    row = current_control(db, user_id, scope_key, create=create)
    # A global reset must invalidate every strategy, not just the wildcard
    # memory row itself.  Keep both counters in the epoch so old adjudications
    # can never leak back after an account-wide reset.
    global_row = (
        current_control(db, user_id, "*", create=create) if scope_key != "*" else row
    )
    return f"{scope_key}:g{global_row.epoch_counter if global_row else 0}:s{row.epoch_counter if row else 0}"


def _scope_from_unit(row: PersonalStrategyUnit) -> Scope:
    payload = json.loads(row.protocol_json)
    return Scope(row.user_id, row.strategy_id, row.protocol_version, row.metric_version, row.context_key)


def _protocol_from_unit(row: PersonalStrategyUnit) -> Protocol:
    payload = json.loads(row.protocol_json)
    return Protocol(_scope_from_unit(row), expected_days=payload["expected_days"], minimum_days=payload["minimum_days"], minimum_coverage=payload["minimum_coverage"], execution_target=payload["execution_target"], mode=payload["mode"], direction=payload["direction"], target=payload["target"], ambiguity_band=payload["ambiguity_band"], changed_variable=payload["changed_variable"], aggregation=payload.get("aggregation", "paired_median"))


def _active_slot(db: Session, user_id: int):
    return db.scalar(select(PolicyActiveSlot).where(PolicyActiveSlot.user_id == user_id))


def start_episode(
    db: Session,
    user_id: int,
    unit_id: str,
    protocol_hash: str,
    version: int,
    decision_id: str | None = None,
    *,
    state_snapshot_hash: str,
    capability_snapshot_hash: str,
) -> dict:
    unit = db.get(PersonalStrategyUnit, unit_id)
    if unit is None or unit.user_id != user_id:
        raise PolicyError("POLICY_NOT_FOUND", "策略不存在或不属于当前用户")
    if unit.protocol_hash != protocol_hash:
        raise PolicyError("POLICY_PROTOCOL_CHANGED", "策略协议已变化，需要重新确认")
    if unit.state_snapshot_hash != state_snapshot_hash:
        raise PolicyError("POLICY_STATE_CHANGED", "健康状态快照已变化，请重新查看并确认协议")
    if unit.status != "compiled":
        raise PolicyError("POLICY_UNVERIFIABLE", "策略还有必需信息未补齐")
    from app.harness.plugins import (
        capability_snapshot_hash as current_capability_snapshot_hash,
        health_state_excluded_sources,
    )
    from app.services.health_state import build_snapshot, frozen_state_hash

    current_state = build_snapshot(
        db,
        user_id,
        window_days=7,
        persist=False,
        excluded_sources=health_state_excluded_sources(db, user_id),
    )
    if frozen_state_hash(current_state) != state_snapshot_hash:
        raise PolicyError("POLICY_STATE_CHANGED", "健康记录或约束已变化，请重新查看并确认协议")
    if current_capability_snapshot_hash(db, user_id) != capability_snapshot_hash:
        raise PolicyError("POLICY_CAPABILITY_CHANGED", "能力授权已变化，请刷新后重新确认")
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
    changed_variable = protocol.get("changed_variable")
    changed_value = (protocol.get("parameters") or {}).get("session_minutes")
    changed_variables = (
        [{"name": changed_variable, "value": changed_value, "unit": "min"}]
        if changed_variable and changed_value is not None
        else ([changed_variable] if changed_variable else [])
    )
    episode = PolicyEpisode(id=episode_id, user_id=user_id, unit_id=unit.id, decision_id=decision_id, learning_epoch=learning_epoch(db, user_id, unit.strategy_id), status="active", start_at=now, end_at=now + timedelta(days=int(protocol["expected_days"])), version=version, protocol_snapshot_json=unit.protocol_json, execution_json=_json([None] * int(protocol["expected_days"])), context_key=unit.context_key, baseline_context_key=unit.context_key, followup_context_key=unit.context_key, baseline_state_snapshot_hash=unit.state_snapshot_hash, followup_state_snapshot_hash=unit.state_snapshot_hash, changed_variables_json=_json(changed_variables))
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
    observations = db.scalars(select(PolicyObservationRef).where(
        PolicyObservationRef.episode_id == episode.id,
    ).order_by(PolicyObservationRef.endpoint, PolicyObservationRef.slot)).all()
    return {"episode_id": episode.id, "strategy_unit_id": episode.unit_id, "status": episode.status, "version": episode.version, "start_at": episode.start_at.isoformat() + "Z", "end_at": episode.end_at.isoformat() + "Z", "learning_epoch": episode.learning_epoch, "stop_reason": episode.stop_reason, "review_revision": episode.review_revision, "effective_adjudication_revision": episode.effective_adjudication_revision, "context_snapshot": {"baseline_context_key": episode.baseline_context_key or episode.context_key, "followup_context_key": episode.followup_context_key or episode.context_key, "baseline_state_snapshot_hash": episode.baseline_state_snapshot_hash, "followup_state_snapshot_hash": episode.followup_state_snapshot_hash, "changed_variables": json.loads(episode.changed_variables_json or "[]")}, "opportunities": [{"id": x.id, "slot": x.slot, "scheduled_at": x.scheduled_at.isoformat() + "Z", "report_id": x.current_report_id} for x in opportunities], "reports": [{"id": x.id, "opportunity_id": x.opportunity_id, "revision": x.revision, "execution": x.execution, "burden": x.burden, "confounders": json.loads(x.confounders_json)} for x in reports], "observations": [{"endpoint": x.endpoint, "slot": x.slot, "source_type": x.source_type, "source_revision": x.source_revision, "observed_at": x.observed_at.isoformat() + "Z", "value": json.loads(x.value_json or "null"), "valid": x.valid} for x in observations], "adjudications": [{"revision": x.revision, "execution_label": x.execution_label, "support_label": x.support_label, "availability_label": x.availability_label, "conclusion": x.conclusion, "reasons": json.loads(x.reasons_json), "stale": x.stale, "valid": x.valid} for x in adjudications]}


def report_opportunity(db: Session, user_id: int, episode_id: str, req) -> dict:
    episode = db.get(PolicyEpisode, episode_id)
    if episode is None or episode.user_id != user_id:
        raise PolicyError("POLICY_NOT_FOUND", "周期不存在")
    payload = {"opportunity_id": req.opportunity_id, "execution": req.execution, "burden": req.perceived_burden, "confounders": req.confounder_codes}
    digest = hashlib.sha256(_json(payload).encode()).hexdigest()
    existing = db.scalar(select(PolicyReport).where(PolicyReport.user_id == user_id, PolicyReport.client_report_id == req.report_id))
    if existing is not None:
        if existing.episode_id != episode.id or existing.payload_hash != digest:
            raise PolicyError("POLICY_VERSION_CONFLICT", "相同报告编号的内容不同")
        return episode_view(db, episode)
    if episode.status != "active":
        raise PolicyError("POLICY_NOT_READY", "周期已关闭，不能追加执行报告")
    if episode.version != req.episode_version:
        raise PolicyError("POLICY_VERSION_CONFLICT", "周期版本已变化，请刷新后重试")
    opportunity = db.get(PolicyExecutionOpportunity, req.opportunity_id)
    if opportunity is None or opportunity.episode_id != episode.id or opportunity.user_id != user_id:
        raise PolicyError("POLICY_NOT_FOUND", "执行机会不存在")
    if opportunity.scheduled_at > utc_now():
        raise PolicyError("POLICY_OPPORTUNITY_NOT_DUE", "还没到这次记录的日期，不能提前填写执行情况")
    revision = (db.scalar(select(PolicyReport.revision).where(PolicyReport.opportunity_id == opportunity.id).order_by(PolicyReport.revision.desc()).limit(1)) or 0) + 1
    row = PolicyReport(id=uuid4().hex, client_report_id=req.report_id, user_id=user_id, episode_id=episode.id, opportunity_id=opportunity.id, revision=revision, execution=req.execution, burden=req.perceived_burden, confounders_json=_json(req.confounder_codes), received_at=utc_now(), payload_hash=digest)
    db.add(row); opportunity.current_report_id = row.id
    execution = json.loads(episode.execution_json or "[]")
    execution[opportunity.slot] = True if req.execution == "completed" else False if req.execution == "explicitly_not_completed" else None
    episode.execution_json = _json(execution)
    episode.version += 1
    db.flush()
    return episode_view(db, episode)


def _build_evidence(
    db: Session, episode: PolicyEpisode, *, invalidate_stale: bool = True
) -> EpisodeEvidence:
    unit = db.get(PersonalStrategyUnit, episode.unit_id)
    protocol = _protocol_from_unit(unit)
    try:
        payload = json.loads(unit.protocol_json or "{}")
        expected_metric = str((payload.get("template") or {}).get("metric_key") or "")
    except (TypeError, ValueError):
        expected_metric = ""
    opportunities = db.scalars(select(PolicyExecutionOpportunity).where(PolicyExecutionOpportunity.episode_id == episode.id)).all()
    reports = []
    by_slot = {}
    for opportunity in opportunities:
        report = db.get(PolicyReport, opportunity.current_report_id) if opportunity.current_report_id else None
        if report is not None:
            reports.append(report)
            by_slot[opportunity.slot] = report
    execution = tuple(True if by_slot.get(i) and by_slot[i].execution == "completed" else False if by_slot.get(i) and by_slot[i].execution == "explicitly_not_completed" else None for i in range(protocol.expected_days))
    refs = db.scalars(select(PolicyObservationRef).where(
        PolicyObservationRef.episode_id == episode.id,
        PolicyObservationRef.valid.is_(True),
        PolicyObservationRef.metric_version == protocol.scope.metric_version,
    )).all()
    resolved_points = []
    for ref in refs:
        window = (
            (episode.start_at - timedelta(days=30), episode.start_at)
            if ref.endpoint == "baseline"
            else (episode.start_at, episode.end_at + timedelta(days=1))
        )
        try:
            value = float(json.loads(ref.value_json))
            if not math.isfinite(value):
                raise ValueError("non-finite")
            if ref.source_type == "user_report":
                if expected_metric != "burden" or not 0 <= value <= 10:
                    raise ValueError("self-report value outside the reviewed burden scale")
                observed_at = ref.observed_at.replace(tzinfo=None)
                if observed_at > utc_now():
                    raise ValueError("self-report timestamp is in the future")
                if observed_at < window[0] or observed_at > window[1]:
                    raise ValueError("self-report outside observation window")
                evidence_value = value
            else:
                current = resolve_point(
                    db,
                    episode.user_id,
                    SimpleNamespace(
                        source_type=ref.source_type,
                        source_id=ref.source_id,
                        source_revision=ref.source_revision,
                        metric_version=ref.metric_version,
                    ),
                    expected_metric,
                    window,
                )
                if (
                    current.metric_key != expected_metric
                    or current.metric_version != ref.metric_version
                    or not math.isclose(current.value, value, rel_tol=0.0, abs_tol=1e-9)
                    or current.observed_at.replace(tzinfo=None) != ref.observed_at.replace(tzinfo=None)
                ):
                    raise SourceResolutionError("EVIDENCE_SOURCE_STALE", "这条记录已更新，请重新选择")
                evidence_value = current.value
            resolved_points.append(
                (
                    ref,
                    Point(
                        ref.slot,
                        evidence_value,
                        f"{ref.source_type}:{ref.source_id}",
                        ref.source_revision,
                        ref.metric_version,
                        ref.confirmed,
                    ),
                )
            )
        except (SourceResolutionError, TypeError, ValueError):
            if invalidate_stale:
                ref.valid = False
            continue
    confounders = tuple(code for row in reports for code in json.loads(row.confounders_json))
    baseline = tuple(point for ref, point in resolved_points if ref.endpoint == "baseline")
    followup = tuple(point for ref, point in resolved_points if ref.endpoint == "followup")
    return EpisodeEvidence(episode.id, episode.review_revision + 1, protocol, execution, baseline, followup, baseline_context=episode.baseline_context_key or episode.context_key, followup_context=episode.followup_context_key or episode.context_key, confounders=confounders, adverse_event=episode.status == "stopped", window_closed=True)


def _freeze_followup_context(db: Session, episode: PolicyEpisode) -> None:
    """Capture the state boundary used by a final review without persisting a read."""
    from app.harness.plugins import health_state_excluded_sources
    from app.services.health_state import build_snapshot, frozen_state_hash
    snapshot = build_snapshot(
        db,
        episode.user_id,
        window_days=7,
        persist=False,
        excluded_sources=health_state_excluded_sources(db, episode.user_id),
    )
    episode.followup_state_snapshot_hash = frozen_state_hash(snapshot)


def _evidence_refs_json(db: Session, episode: PolicyEpisode) -> str:
    """Persist replayable source references, never an unverifiable bare value."""
    # `_build_evidence` may have just invalidated stale refs. Sessions use
    # autoflush=False, so flush those validity changes before selecting the
    # immutable adjudication snapshot below.
    db.flush()
    refs = db.scalars(select(PolicyObservationRef).where(
        PolicyObservationRef.episode_id == episode.id,
        PolicyObservationRef.valid.is_(True),
    ).order_by(PolicyObservationRef.endpoint, PolicyObservationRef.slot)).all()
    payload = [{
        "endpoint": ref.endpoint,
        "slot": ref.slot,
        "source_type": ref.source_type,
        "source_id": ref.source_id,
        "source_revision": ref.source_revision,
        "metric_version": ref.metric_version,
        "observed_at": ref.observed_at.isoformat(),
        "value_hash": hashlib.sha256((ref.value_json or "").encode()).hexdigest(),
    } for ref in refs]
    return _json(payload)


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
    _freeze_followup_context(db, episode)
    evidence = _build_evidence(db, episode)
    verdict = adjudicate(evidence)
    revision = episode.review_revision + 1
    episode.review_revision = revision; episode.effective_adjudication_revision = revision; episode.status = "reviewed"; episode.version += 1
    db.add(PolicyAdjudication(id=uuid4().hex, user_id=user_id, episode_id=episode.id, revision=revision, learning_epoch=episode.learning_epoch, execution_label=verdict.execution_label, support_label=verdict.support_label, availability_label=verdict.availability_label, conclusion=verdict.conclusion, reasons_json=_json(verdict.reasons), evidence_refs_json=_evidence_refs_json(db, episode), source_hash=hashlib.sha256(_json(verdict.__dict__).encode()).hexdigest(), algorithm_version="egpl-v1.0.0", gate_version="evidence-gate-v1.0.0"))
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
    _freeze_followup_context(db, episode)
    evidence = _build_evidence(db, episode)
    verdict = adjudicate(evidence)
    revision = episode.review_revision + 1
    episode.review_revision, episode.effective_adjudication_revision = revision, revision
    db.add(PolicyAdjudication(id=uuid4().hex, user_id=user_id, episode_id=episode.id, revision=revision, learning_epoch=episode.learning_epoch, execution_label=verdict.execution_label, support_label=None, availability_label=None, conclusion="stopped", reasons_json=_json((reason_code,) + verdict.reasons), evidence_refs_json=_evidence_refs_json(db, episode), source_hash=hashlib.sha256(_json(verdict.__dict__).encode()).hexdigest(), algorithm_version="egpl-v1.0.0", gate_version="evidence-gate-v1.0.0"))
    db.add(PolicyOutbox(event_id=uuid4().hex, user_id=user_id, event_type="policy.adjudicated", ref_id=episode.id, revision=revision, payload=_json({"episode_id": episode.id, "revision": revision}), status="pending"))
    db.flush()
    return {"episode": episode_view(db, episode), "adjudication": {"revision": revision, "execution_label": verdict.execution_label, "support_label": None, "availability_label": None, "conclusion": "stopped", "reasons": [reason_code, *verdict.reasons]}}


def rebuild_beliefs(db: Session, user_id: int, strategy_id: str, context_key: str) -> dict:
    unit = db.scalar(select(PersonalStrategyUnit).where(PersonalStrategyUnit.user_id == user_id, PersonalStrategyUnit.strategy_id == strategy_id, PersonalStrategyUnit.context_key == context_key).order_by(PersonalStrategyUnit.created_at.desc()))
    if unit is None:
        raise PolicyError("POLICY_NOT_FOUND", "策略经验不存在")
    epoch = learning_epoch(db, user_id, strategy_id)
    unit_ids = select(PersonalStrategyUnit.id).where(
        PersonalStrategyUnit.user_id == user_id,
        PersonalStrategyUnit.strategy_id == strategy_id,
        PersonalStrategyUnit.protocol_version == unit.protocol_version,
        PersonalStrategyUnit.metric_version == unit.metric_version,
        PersonalStrategyUnit.context_key == context_key,
    )
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
