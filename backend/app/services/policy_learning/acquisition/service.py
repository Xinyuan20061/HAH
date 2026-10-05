"""Transactional orchestration for low-burden, revocable evidence decisions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.time import business_today, utc_now
from app.harness.plugins import authorize_capability, capability_snapshot_hash, record_capability_api_access
from app.models import (
    PersonalStrategyUnit, PolicyAcquisitionCommand, PolicyAcquisitionDailyUsage,
    PolicyAcquisitionEvent, PolicyAcquisitionFence, PolicyAcquisitionQuestion,
    PolicyAcquisitionSession, PolicyCertificateDependency, PolicyDecisionCertificate,
    PolicyAdjudication, PolicyDomainGeneration, PolicyEpisode, PolicyEvidenceRevision, PolicyExecutionOpportunity,
    PolicyObservationRef, PolicyReport,
)
from app.services.policy_learning.contracts import ExecutionReportRequest
from app.services.policy_learning.repository import PolicyError, episode_view, report_opportunity

from .contracts import (
    AcquisitionBudget, AnswerRequest, ObservationRepairRequest, RepairRequest,
    RereviewRequest, SelectedSource, StartAcquisitionRequest,
)
from .oracle import ExecutionProof, canonical_hash, execution_proof, proof_dict
from .planner import ExecutionDomain, Query, ResponsePrior, SupportPairDomain, choose_next
from .knowledge_contract import current_knowledge_contract


RULE_VERSION = "acquisition-rules-v1"


class AcquisitionError(ValueError):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _decode(raw: str, fallback):
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


def _authorize(db: Session, user_id: int, *, active: bool, phase: str = "new_work") -> dict:
    operation = "read" if active else "user_action"
    result = authorize_capability(db, user_id, "personal_policy", operation,
                                  ("policy.execution.read",), phase)
    record_capability_api_access(db, user_id, "personal_policy", route="policy_acquisition",
                                 allowed=bool(result.get("allowed")), reason=result.get("reason"))
    if not result.get("allowed"):
        reason = result.get("reason")
        code = "PLUGIN_DISABLED" if reason == "capability_paused" else "PLUGIN_SCOPE_NOT_GRANTED" if reason == "scope_not_granted" else "PLUGIN_UNAVAILABLE"
        raise AcquisitionError(409, code, "个人策略取证未获当前授权；请在能力设置中检查授权和审核版本")
    return result


def _fence(db: Session, user_id: int) -> PolicyAcquisitionFence:
    if db.get_bind().dialect.name == "sqlite":
        # SQLite ignores SELECT ... FOR UPDATE. Use a generation CAS as the
        # serialization point, including the first-row race. A concurrent
        # transaction either increments the next generation or receives a
        # bounded, retryable conflict; it can never silently lose an update.
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        try:
            # The caller may already have changed a source ORM object. Do not
            # let a query autoflush that source before the serialization point.
            with db.no_autoflush:
                db.execute(sqlite_insert(PolicyAcquisitionFence).values(
                    user_id=user_id, generation=0, updated_at=utc_now()
                ).on_conflict_do_nothing(index_elements=["user_id"]))
                for _ in range(3):
                    generation = db.scalar(select(PolicyAcquisitionFence.generation).where(
                        PolicyAcquisitionFence.user_id == user_id))
                    if generation is None:
                        continue
                    result = db.execute(update(PolicyAcquisitionFence).where(
                        PolicyAcquisitionFence.user_id == user_id,
                        PolicyAcquisitionFence.generation == generation
                    ).values(generation=generation + 1, updated_at=utc_now()))
                    if result.rowcount == 1:
                        return db.scalar(select(PolicyAcquisitionFence).where(
                            PolicyAcquisitionFence.user_id == user_id
                        ).execution_options(populate_existing=True))
            raise AcquisitionError(409, "ACQUISITION_WRITE_BUSY", "记录正在更新，请刷新后重试")
        except OperationalError:
            raise AcquisitionError(409, "ACQUISITION_WRITE_BUSY", "记录正在更新，请稍后安全重试") from None

    with db.no_autoflush:
        row = db.scalar(select(PolicyAcquisitionFence).where(
            PolicyAcquisitionFence.user_id == user_id).with_for_update())
    if row is None:
        row = PolicyAcquisitionFence(user_id=user_id, generation=1, updated_at=utc_now())
        db.add(row)
        try:
            db.flush([row])
        except IntegrityError:
            raise AcquisitionError(409, "ACQUISITION_WRITE_BUSY", "记录正在更新，请刷新后重试") from None
        return row
    row.generation += 1
    row.updated_at = utc_now()
    # Flush only the fence. Pending source edits are persisted after the lock
    # has been acquired, so a concurrent reader cannot cross the write fence.
    db.flush([row])
    return row


def _request_hash(method: str, route: str, body: dict) -> str:
    return canonical_hash({"schema_version": "acquisition-command-v1", "method": method,
                           "route": route, "body": body})


def _command(db: Session, user_id: int, key: str, method: str, route: str, body: dict):
    if not key or len(key) > 120:
        raise AcquisitionError(400, "IDEMPOTENCY_KEY_REQUIRED", "请求编号无效，请重试")
    digest = _request_hash(method, route, body)
    existing = db.scalar(select(PolicyAcquisitionCommand).where(
        PolicyAcquisitionCommand.user_id == user_id,
        PolicyAcquisitionCommand.idempotency_key == key))
    if existing is not None:
        if existing.request_hash != digest:
            raise AcquisitionError(409, "ACQUISITION_IDEMPOTENCY_CONFLICT", "相同请求编号不能用于不同内容")
        if existing.resource_kind == "session":
            if existing.response_json and existing.response_json != "{}":
                return digest, _decode(existing.response_json, {})
            session = db.scalar(select(PolicyAcquisitionSession).where(
                PolicyAcquisitionSession.user_id == user_id,
                PolicyAcquisitionSession.id == existing.resource_id))
            if session is None:
                raise AcquisitionError(409, "ACQUISITION_RESULT_UNAVAILABLE", "原操作已记录，请刷新周期状态")
            return digest, _session_view(db, session, mutate=False)
        return digest, _decode(existing.response_json, {})
    return digest, None


def _save_command(db: Session, *, user_id: int, key: str, method: str, route: str,
                  digest: str, session: PolicyAcquisitionSession | None = None,
                  resource_kind: str = "episode", resource_id: str = "",
                  response_json: str = "{}") -> None:
    if session is not None and response_json == "{}":
        response_json = _json(_session_view(db, session, mutate=False))
    db.add(PolicyAcquisitionCommand(
        id=uuid4().hex, user_id=user_id, idempotency_key=key, method=method,
        route_key=route, request_hash=digest,
        resource_kind="session" if session else resource_kind,
        resource_id=session.id if session else resource_id,
        response_json=response_json, created_at=utc_now()))
    db.flush()


def _episode(db: Session, user_id: int, episode_id: str) -> PolicyEpisode:
    row = db.scalar(select(PolicyEpisode).where(
        PolicyEpisode.id == episode_id, PolicyEpisode.user_id == user_id))
    if row is None:
        raise AcquisitionError(404, "ACQUISITION_NOT_FOUND", "周期不存在")
    return row


def _session(db: Session, user_id: int, session_id: str) -> PolicyAcquisitionSession:
    row = db.scalar(select(PolicyAcquisitionSession).where(
        PolicyAcquisitionSession.id == session_id, PolicyAcquisitionSession.user_id == user_id))
    if row is None:
        raise AcquisitionError(404, "ACQUISITION_NOT_FOUND", "取证记录不存在")
    return row


def _frozen_contract(db: Session, episode: PolicyEpisode) -> dict:
    unit = db.get(PersonalStrategyUnit, episode.unit_id)
    if unit is None or unit.user_id != episode.user_id:
        raise AcquisitionError(404, "ACQUISITION_NOT_FOUND", "周期协议不存在")
    protocol = _decode(episode.protocol_snapshot_json, {})
    template = protocol.get("template") if isinstance(protocol.get("template"), dict) else protocol
    scope = protocol.get("scope") if isinstance(protocol.get("scope"), dict) else {}
    if unit.template_id != "session_duration":
        raise AcquisitionError(409, "ACQUISITION_TEMPLATE_UNSUPPORTED", "当前仅开放训练时长周期的低负担取证")
    frozen_protocol_hash = hashlib.sha256(json.dumps(
        protocol, sort_keys=True, separators=(",", ":"), default=str
    ).encode()).hexdigest()
    protocol_version = str(scope.get("protocol_version") or protocol.get("protocol_version") or "")
    metric_version = str(scope.get("metric_version") or protocol.get("metric_version") or "")
    if (not protocol_version or not metric_version or frozen_protocol_hash != unit.protocol_hash or
            protocol_version != unit.protocol_version or metric_version != unit.metric_version):
        raise AcquisitionError(409, "ACQUISITION_CONTRACT_INVALID", "周期冻结协议与原始协议版本不一致，不能继续取证")
    expected_days = int(protocol.get("expected_days") or template.get("expected_days") or 0)
    target = float(protocol.get("execution_target") or template.get("execution_target") or 0)
    if not 1 <= expected_days <= 28 or not 0 < target <= 1:
        raise AcquisitionError(409, "ACQUISITION_CONTRACT_INVALID", "冻结协议不完整，暂时无法安全取证")
    knowledge = current_knowledge_contract(db, unit.template_id)
    if not knowledge["available"]:
        raise AcquisitionError(409, "KNOWLEDGE_CONTRACT_UNAVAILABLE",
                               "当前模板的审核知识依据缺失或已撤回，暂不能开展新的取证")
    return {
        "schema_version": "acquisition-contract-v1", "episode_id": episode.id,
        "strategy_unit_id": unit.id, "template_id": unit.template_id,
        "protocol_hash": frozen_protocol_hash, "protocol_version": protocol_version,
        "metric_version": metric_version, "context_key": episode.context_key,
        "metric_key": str(template.get("metric_key") or ""),
        "expected_days": expected_days, "execution_target": target,
        "minimum_days": int(protocol.get("minimum_days") or template.get("minimum_days") or 1),
        "minimum_coverage": float(protocol.get("minimum_coverage") or template.get("minimum_coverage") or 0),
        "aggregation": str(protocol.get("aggregation") or template.get("aggregation") or ""),
        "oracle_version": "exact-execution-v1", "planner_version": "bounded-lookahead-v1",
        "rule_version": RULE_VERSION,
        "knowledge_contract": knowledge["contract"],
        "knowledge_contract_hash": knowledge["contract_hash"],
    }


def _snapshot(db: Session, episode: PolicyEpisode, contract: dict) -> dict:
    expected = contract["expected_days"]
    raw_execution = _decode(episode.execution_json, [])
    if not isinstance(raw_execution, list) or len(raw_execution) != expected or any(
        x is not None and type(x) is not bool for x in raw_execution
    ):
        raise AcquisitionError(409, "ACQUISITION_EVIDENCE_CONFLICT", "执行记录与冻结周期长度不一致，请联系支持")
    opportunities = db.scalars(select(PolicyExecutionOpportunity).where(
        PolicyExecutionOpportunity.episode_id == episode.id,
        PolicyExecutionOpportunity.user_id == episode.user_id).order_by(PolicyExecutionOpportunity.slot)).all()
    if len(opportunities) != expected or {row.slot for row in opportunities} != set(range(expected)):
        raise AcquisitionError(409, "ACQUISITION_EVIDENCE_CONFLICT", "执行机会与冻结周期槽位不一致")
    reports = []
    due_slots = []
    now = utc_now()
    for opportunity in opportunities:
        if opportunity.slot < 0 or opportunity.slot >= expected:
            raise AcquisitionError(409, "ACQUISITION_EVIDENCE_CONFLICT", "执行机会超出冻结周期")
        if opportunity.scheduled_at <= now and raw_execution[opportunity.slot] is None:
            due_slots.append(opportunity.slot)
        if opportunity.current_report_id:
            report = db.get(PolicyReport, opportunity.current_report_id)
            if report is None or report.user_id != episode.user_id or report.episode_id != episode.id or report.opportunity_id != opportunity.id:
                raise AcquisitionError(409, "ACQUISITION_EVIDENCE_CONFLICT", "当前执行报告指针无效")
            report_value = True if report.execution == "completed" else False if report.execution == "explicitly_not_completed" else None
            if report.execution not in {"completed", "explicitly_not_completed", "unknown"} or raw_execution[opportunity.slot] is not report_value:
                raise AcquisitionError(409, "ACQUISITION_EVIDENCE_CONFLICT", "执行记录与当前报告指针不一致")
            reports.append({"opportunity_id": opportunity.id, "slot": opportunity.slot,
                            "report_id": report.id, "revision": report.revision,
                            "execution": report.execution, "payload_hash": report.payload_hash})
    refs = db.scalars(select(PolicyObservationRef).where(
        PolicyObservationRef.user_id == episode.user_id,
        PolicyObservationRef.episode_id == episode.id).order_by(
            PolicyObservationRef.endpoint, PolicyObservationRef.slot)).all()
    evidence_refs = []
    stale_sources = []
    from app.services.policy_learning.source_registry import SourceResolutionError, resolve_point
    for row in refs:
        value_hash = canonical_hash(row.value_json or "null")
        current = bool(row.valid)
        reason = "current" if current else "source_invalidated"
        if not row.valid:
            stale_sources.append({"observation_ref_id": row.id, "reason_code": reason})
        else:
            if row.metric_version != contract["metric_version"]:
                current, reason = False, "metric_version_changed"
            elif row.source_type == "user_report":
                try:
                    value = float(_decode(row.value_json, None))
                    report_window = ((episode.start_at - timedelta(days=30), episode.start_at)
                                     if row.endpoint == "baseline" else
                                     (episode.start_at, episode.end_at + timedelta(days=1)))
                    if (not row.confirmed or not 0 <= value <= 10 or
                            row.observed_at > now or
                            not report_window[0] <= row.observed_at <= report_window[1]):
                        raise ValueError("self-report no longer satisfies its evidence contract")
                except (TypeError, ValueError):
                    current, reason = False, "self_report_invalid"
            else:
                window = ((episode.start_at - timedelta(days=30), episode.start_at)
                          if row.endpoint == "baseline" else
                          (episode.start_at, episode.end_at + timedelta(days=1)))
                try:
                    resolved = resolve_point(db, episode.user_id, row, contract["metric_key"], window)
                    stored_value = float(_decode(row.value_json, None))
                    if (resolved.metric_version != row.metric_version or
                            not abs(resolved.value - stored_value) <= 1e-9 or
                            resolved.observed_at.replace(tzinfo=None) != row.observed_at.replace(tzinfo=None)):
                        raise SourceResolutionError("EVIDENCE_SOURCE_STALE", "来源记录与当前观察不一致")
                except (SourceResolutionError, TypeError, ValueError):
                    current, reason = False, "source_not_current"
            if not current:
                stale_sources.append({"observation_ref_id": row.id, "reason_code": reason})
        evidence_refs.append({"id": row.id, "endpoint": row.endpoint, "slot": row.slot,
                              "revision": int(getattr(row, "revision", 1) or 1), "valid": row.valid,
                              "current": current, "current_reason": reason,
                              "source_type": row.source_type, "source_id": row.source_id,
                              "source_revision": row.source_revision, "metric_version": row.metric_version,
                              "value_hash": value_hash})
    generations = db.scalars(select(PolicyDomainGeneration).where(
        PolicyDomainGeneration.user_id == episode.user_id).order_by(PolicyDomainGeneration.domain)).all()
    return {
        "execution": tuple(raw_execution), "due_slots": tuple(due_slots),
        "reports": reports, "observations": evidence_refs,
        "stale_sources": stale_sources,
        "episode_id": episode.id, "episode_version": episode.version,
        "protocol_hash": contract["protocol_hash"], "metric_version": contract["metric_version"],
        "context_key": episode.context_key, "learning_epoch": episode.learning_epoch,
        "window_closed": now >= episode.end_at,
        "source_generations": [{"domain": row.domain, "source": row.source_generation,
                                "processed": row.processed_generation} for row in generations],
    }


def _proof(snapshot: dict, contract: dict) -> ExecutionProof:
    return execution_proof(snapshot["execution"], contract["execution_target"])


def _fact_snapshot_hash(snapshot: dict) -> str:
    return canonical_hash({key: value for key, value in snapshot.items() if key != "due_slots"})


def _support_evidence_state(snapshot: dict, contract: dict) -> dict:
    required = max(int(contract["minimum_days"]),
                   math.ceil(contract["expected_days"] * contract["minimum_coverage"]))
    refs = {(row["endpoint"], row["slot"]) for row in snapshot["observations"]
            if row["valid"] and row["current"]}
    if contract.get("aggregation") == "paired_median":
        count = sum(("baseline", slot) in refs and ("followup", slot) in refs
                    for slot in range(contract["expected_days"]))
    else:
        count = sum(endpoint == "followup" for endpoint, _ in refs)
    state = ("needs_evidence" if count < required else
             "waiting_window" if not snapshot["window_closed"] else "awaiting_review")
    return {"label": None, "state": state, "valid_pair_count": count,
            "required_pair_count": required}


def _binding(db: Session, episode: PolicyEpisode, snapshot: dict, contract: dict) -> dict:
    knowledge = current_knowledge_contract(db, contract["template_id"])
    return {
        "user_id": episode.user_id, "episode_id": episode.id,
        "episode_version": episode.version, "protocol_hash": contract["protocol_hash"],
        "metric_version": contract["metric_version"], "context_key": episode.context_key,
        "learning_epoch": episode.learning_epoch, "rule_version": RULE_VERSION,
        "capability_snapshot_hash": capability_snapshot_hash(db, episode.user_id),
        "evidence_snapshot_hash": canonical_hash({k: snapshot[k] for k in (
            "execution", "reports", "observations", "source_generations", "episode_version")}),
        "source_generation_hash": canonical_hash(snapshot["source_generations"]),
        "knowledge_contract_hash": knowledge["contract_hash"],
        "knowledge_contract_status": knowledge["contract"]["status"],
        "window_closed": snapshot["window_closed"],
    }


def _invalidate_episode_certificates(db: Session, user_id: int, episode_id: str,
                                     *, except_question_id: str | None = None) -> None:
    rows = db.scalars(select(PolicyDecisionCertificate).where(
        PolicyDecisionCertificate.user_id == user_id,
        PolicyDecisionCertificate.episode_id == episode_id,
        PolicyDecisionCertificate.status == "valid")).all()
    for cert in rows:
        cert.status = "stale"
    session = db.scalar(select(PolicyAcquisitionSession).where(
        PolicyAcquisitionSession.user_id == user_id,
        PolicyAcquisitionSession.episode_id == episode_id))
    if session is None:
        return
    episode = db.get(PolicyEpisode, episode_id)
    closed_by_episode = bool(episode and episode.status in {"reviewed", "stopped"} and session.status != "closed")
    question = db.get(PolicyAcquisitionQuestion, session.active_question_id) if session.active_question_id else None
    if question and question.id != except_question_id and question.status == "issued":
        question.status = "obsolete"
        question.answered_at = utc_now()
        session.active_question_id = None
    if closed_by_episode:
        session.status = "closed"
        if question and question.status == "issued":
            question.status = "cancelled"
            question.answered_at = utc_now()
            session.active_question_id = None
        _new_event(db, session, "session_closed", "episode_closed")
    if rows or closed_by_episode or (question and question.status == "obsolete"):
        session.decision_state = "needs_repair"
        session.version += 1
        db.flush()


def invalidate_episode(db: Session, user_id: int, episode_id: str,
                       *, except_question_id: str | None = None) -> None:
    """Synchronous hook for report, observation, authorization and protocol writers."""
    _invalidate_episode_certificates(db, user_id, episode_id, except_question_id=except_question_id)


def invalidate_source(db: Session, user_id: int, source_type: str, source_id: str,
                      source_revision: int | None = None) -> int:
    stmt = select(PolicyCertificateDependency).where(
        PolicyCertificateDependency.user_id == user_id,
        PolicyCertificateDependency.source_type == source_type,
        PolicyCertificateDependency.source_id == source_id)
    deps = db.scalars(stmt).all()
    changed = 0
    affected_sessions: set[str] = set()
    for dep in deps:
        if source_revision is not None and dep.source_revision == source_revision:
            continue
        cert = db.get(PolicyDecisionCertificate, dep.certificate_id)
        if cert and cert.status == "valid":
            cert.status = "stale"
            affected_sessions.add(cert.session_id)
            changed += 1

    refs_query = select(PolicyObservationRef).where(
        PolicyObservationRef.user_id == user_id,
        PolicyObservationRef.source_type == source_type,
        PolicyObservationRef.source_id == source_id,
        PolicyObservationRef.valid.is_(True))
    if source_revision is not None:
        refs_query = refs_query.where(PolicyObservationRef.source_revision < source_revision)
    for ref in db.scalars(refs_query).all():
        session = db.scalar(select(PolicyAcquisitionSession).where(
            PolicyAcquisitionSession.user_id == user_id,
            PolicyAcquisitionSession.episode_id == ref.episode_id))
        if session:
            affected_sessions.add(session.id)

    for session_id in affected_sessions:
        session = db.get(PolicyAcquisitionSession, session_id)
        if session is None:
            continue
        session.decision_state = "needs_repair"
        session.version += 1
        question = db.get(PolicyAcquisitionQuestion, session.active_question_id) if session.active_question_id else None
        if question and question.status == "issued":
            question.status = "obsolete"
            question.answered_at = utc_now()
            session.active_question_id = None
        _new_event(db, session, "source_invalidated", "source_revision_changed",
                   question.id if question else None,
                   payload={"source_type": source_type, "source_id": source_id})
    if changed or affected_sessions:
        db.flush()
    return changed


def _issue_certificate(db: Session, session: PolicyAcquisitionSession, episode: PolicyEpisode,
                       contract: dict, snapshot: dict, proof: ExecutionProof) -> PolicyDecisionCertificate:
    if proof.label is None:
        raise AcquisitionError(409, "CERTIFICATE_NOT_READY", "执行记录尚不能确定冻结门槛")
    predecessor = session.latest_certificate_id
    expires = utc_now() + timedelta(minutes=10)
    purpose = "execution_endpoint" if snapshot["window_closed"] else "execution_progress"
    binding = _binding(db, episode, snapshot, contract)
    proof_json = proof_dict(proof)
    body_hash = canonical_hash({"binding": binding, "proof": proof_json,
                                "purpose": purpose, "expires_at": expires.isoformat()})
    cert = PolicyDecisionCertificate(
        id=uuid4().hex, user_id=episode.user_id, episode_id=episode.id,
        session_id=session.id,
        revision=(db.scalar(select(func.max(PolicyDecisionCertificate.revision)).where(
            PolicyDecisionCertificate.session_id == session.id)) or 0) + 1,
        purpose=purpose, endpoint="execution", label=proof.label, status="valid",
        contract_hash=session.contract_hash,
        evidence_snapshot_hash=binding["evidence_snapshot_hash"],
        binding_json=_json(binding), proof_json=_json(proof_json), body_hash=body_hash,
        predecessor_id=predecessor, expires_at=expires, created_at=utc_now())
    db.add(cert)
    session.latest_certificate_id = cert.id
    session.decision_state = "sufficient"
    db.flush()
    for report in snapshot["reports"]:
        db.add(PolicyCertificateDependency(
            user_id=episode.user_id, certificate_id=cert.id, dependency_kind="report",
            source_type="policy_report", source_id=report["opportunity_id"],
            source_revision=report["revision"], metric_version=contract["metric_version"],
            value_hash=report["payload_hash"], opportunity_id=report["opportunity_id"],
            current_report_id=report["report_id"]))
    for ref in snapshot["observations"]:
        if not ref["valid"] or not ref["current"]:
            continue
        db.add(PolicyCertificateDependency(
            user_id=episode.user_id, certificate_id=cert.id, dependency_kind="observation",
            source_type=ref["source_type"], source_id=ref["source_id"],
            source_revision=ref["source_revision"], metric_version=ref["metric_version"],
            value_hash=ref["value_hash"], observation_ref_id=ref["id"],
            observation_revision=ref["revision"]))
    db.flush()
    return cert


def _current_certificate(db: Session, session: PolicyAcquisitionSession,
                         episode: PolicyEpisode, contract: dict, snapshot: dict,
                         proof: ExecutionProof, now: datetime,
                         certificate_id: str | None = None) -> dict | None:
    cert_id = certificate_id or session.latest_certificate_id
    cert = db.get(PolicyDecisionCertificate, cert_id) if cert_id else None
    if cert is None:
        return None
    state = cert.status
    reason = "current" if state == "valid" else f"certificate_{state}"
    binding = _binding(db, episode, snapshot, contract)
    stored_binding = _decode(cert.binding_json, {})
    stored_proof = _decode(cert.proof_json, {})
    recomputed_proof = _decode(_json(proof_dict(proof)), {})
    body = canonical_hash({"binding": stored_binding, "proof": stored_proof,
                           "purpose": cert.purpose, "expires_at": cert.expires_at.isoformat()})
    authorization = authorize_capability(db, episode.user_id, "personal_policy", "read",
                                         ("policy.execution.read",), "new_work")
    if state == "valid" and (cert.user_id != episode.user_id or cert.episode_id != episode.id or
                              cert.session_id != session.id or cert.contract_hash != session.contract_hash):
        state, reason = "stale", "certificate_owner_or_contract_mismatch"
    elif state == "valid" and body != cert.body_hash:
        state, reason = "stale", "certificate_body_changed"
    elif state == "valid" and now >= cert.expires_at:
        state, reason = "expired", "certificate_expired"
    elif state == "valid" and not authorization.get("allowed"):
        state, reason = "stale", "authorization_changed"
    elif state == "valid" and snapshot.get("stale_sources"):
        state, reason = "stale", "source_not_current"
    elif state == "valid" and stored_binding != binding:
        state, reason = "stale", "certificate_binding_changed"
    elif state == "valid" and (proof.label != cert.label or stored_proof != recomputed_proof):
        state, reason = "stale", "oracle_recomputation_changed"
    elif state == "valid" and cert.purpose == "execution_endpoint" and not snapshot["window_closed"]:
        state, reason = "stale", "window_not_closed"
    return {"certificate_id": cert.id, "purpose": cert.purpose,
            "endpoint": cert.endpoint, "label": cert.label,
            "status": state, "effective_status": reason,
            "proof": stored_proof if state == "valid" else proof_dict(proof),
            "expires_at": cert.expires_at.isoformat() + "Z",
            "body_hash": cert.body_hash}


def _new_event(db: Session, session: PolicyAcquisitionSession, event_type: str,
               reason: str = "", question_id: str | None = None,
               elapsed_ms: int | None = None, payload: dict | None = None) -> None:
    sequence = (db.scalar(select(func.max(PolicyAcquisitionEvent.sequence)).where(
        PolicyAcquisitionEvent.session_id == session.id)) or 0) + 1
    db.add(PolicyAcquisitionEvent(
        id=uuid4().hex, user_id=session.user_id, session_id=session.id,
        question_id=question_id, sequence=sequence, event_type=event_type,
        reason_code=reason, elapsed_ms=elapsed_ms,
        payload_json=_json(payload or {}), created_at=utc_now()))
    db.flush()


def _add_measured_time(db: Session, user_id: int, elapsed_ms: int | None) -> int | None:
    """Accumulate client-reported duration for auditable cumulative-cost views.

    This is descriptive telemetry only: it never changes facts, labels, or the
    estimated-time prompt budget. The contract caps one operation at ten minutes.
    """
    if elapsed_ms is None:
        return None
    bounded = min(int(elapsed_ms), 600_000)
    usage = db.scalar(select(PolicyAcquisitionDailyUsage).where(
        PolicyAcquisitionDailyUsage.user_id == user_id,
        PolicyAcquisitionDailyUsage.business_date == business_today()).with_for_update())
    if usage is None:
        usage = PolicyAcquisitionDailyUsage(
            user_id=user_id, business_date=business_today(), prompt_count=0,
            estimated_ms=0, measured_ms=0, version=1)
        db.add(usage)
        db.flush()
    usage.measured_ms += bounded
    usage.version += 1
    return bounded


def _expire_question(db: Session, session: PolicyAcquisitionSession, now: datetime) -> None:
    if not session.active_question_id:
        return
    question = db.get(PolicyAcquisitionQuestion, session.active_question_id)
    if question is None or question.status != "issued":
        session.active_question_id = None
        return
    if now >= question.expires_at:
        question.status, question.answered_at = "timed_out", now
        question.answer_json = _json({"response": "no_response", "server_timeout": True})
        session.active_question_id = None
        session.version += 1
        _new_event(db, session, "question_timed_out", "server_timeout", question.id)


def _evidence_debt(session: PolicyAcquisitionSession, contract: dict,
                   snapshot: dict, proof: ExecutionProof,
                   support: dict) -> list[dict]:
    """User-readable projection of what evidence is still missing per endpoint.

    Every debt item carries a stable reason_code, source/window context,
    an executable action and an expiry rule; it is derived server-side and is
    NOT a certificate or a verdict. Debts are recomputed on every read.
    """
    stale = bool(snapshot.get("stale_sources"))
    debt: list[dict] = []
    if stale:
        debt.append({"endpoint": "execution", "state": "needs_repair",
                     "reason_code": "source_not_current",
                     "action": "repair", "expiry_rule": "until_source_revalidated"})
    elif proof.label is not None:
        debt.append({"endpoint": "execution", "state": "sufficient",
                     "reason_code": "endpoint_sufficient", "action": "none",
                     "expiry_rule": "binding_rechecked_on_read"})
    elif snapshot["due_slots"]:
        debt.append({"endpoint": "execution", "state": "askable",
                     "reason_code": "missing_due_report",
                     "action": "answer_or_skip", "expiry_rule": "due_slot_until_answer_or_skip"})
    else:
        debt.append({"endpoint": "execution", "state": "waiting_window",
                     "reason_code": "window_not_due", "action": "wait",
                     "expiry_rule": "until_slot_scheduled"})
    if stale:
        debt.append({"endpoint": "burden", "state": "needs_repair",
                     "reason_code": "source_not_current",
                     "action": "repair", "expiry_rule": "until_source_revalidated"})
    elif support["state"] in {"needs_evidence", "awaiting_review"} and snapshot.get("window_closed"):
        debt.append({"endpoint": "burden", "state": "askable",
                     "reason_code": "support_observation_may_complete_pair",
                     "action": "answer_or_skip", "expiry_rule": "until_pair_completed"})
    elif support["state"] == "needs_evidence":
        debt.append({"endpoint": "burden", "state": "waiting_window",
                     "reason_code": "followup_not_due", "action": "wait",
                     "expiry_rule": "until_completed_opportunity"})
    else:
        debt.append({"endpoint": "burden", "state": "sufficient",
                     "reason_code": "support_sufficient", "action": "none",
                     "expiry_rule": "binding_rechecked_on_read"})
    debt.append({"endpoint": "availability", "state": "independent_endpoint",
                 "reason_code": "independent_endpoint", "action": "none",
                 "expiry_rule": "not_applicable"})
    return debt


def _planner_meta(response_model: dict | None = None) -> dict:
    from .response_model import DEFAULT_RESPONSE_MODEL
    from .planner import PLANNER_VERSION
    model = response_model or DEFAULT_RESPONSE_MODEL.to_dict()
    return {
        "version": PLANNER_VERSION,
        "response_model_kind": model.get("kind", "declared_prior_or_consented_calibration"),
        "model_version": model.get("version", "declared-common-response-v2"),
    }


def _decision_response(db: Session, session: PolicyAcquisitionSession,
                       episode: PolicyEpisode, contract: dict, snapshot: dict,
                       proof: ExecutionProof, *, action: str, reason: str,
                       question: PolicyAcquisitionQuestion | None = None,
                       decision_state_override: str | None = None) -> dict:
    certificate = _current_certificate(db, session, episode, contract, snapshot, proof, utc_now())
    question_payload = None
    endpoint = "availability"
    estimated_burden_ms = 0
    if question is not None:
        prompt = _decode(question.prompt_json, {})
        question_payload = {"question_id": question.id, "kind": question.kind,
                            "prompt": prompt.get("prompt", ""), "why": prompt.get("why", ""),
                            "choices": prompt.get("choices", []),
                            "expires_at": question.expires_at.isoformat() + "Z"}
        endpoint = ("execution" if question.kind == "execution_confirmation"
                    else "burden")
        estimated_burden_ms = int(question.estimated_cost_ms or 0)
    elif action == "ask":
        endpoint = "execution"
        estimated_burden_ms = 3000  # next execution-confirmation question estimate
    support = _support_evidence_state(snapshot, contract)
    return {
        "schema_version": "acquisition-response-v1", "session_id": session.id,
        "session_version": session.version, "episode_id": episode.id,
        "episode_version": episode.version, "session_status": session.status,
        "decision_state": decision_state_override or session.decision_state,
        "decision": {"action": action, "reason_code": reason,
                     "explanation": _reason_message(reason),
                     "user_message": _reason_message(reason),
                     "endpoint": endpoint,
                     "confidence_kind": "exact_oracle_for_endpoint",
                     "estimated_burden_ms": estimated_burden_ms,
                     "allowed_actions": _allowed_actions(session, action),
                     "question": question_payload},
        "evidence_debt": _evidence_debt(session, contract, snapshot, proof, support),
        "planner_meta": _planner_meta(),
        "endpoints": {
            "execution": {"label": proof.label,
                          "state": "sufficient" if proof.label is not None else "needs_evidence",
                          "purpose": "execution_endpoint" if snapshot["window_closed"] else "execution_progress",
                          "lower_completed": proof.lower_completed,
                          "upper_completed": proof.upper_completed,
                          "minimum_completed": proof.minimum_completed,
                          "unknown_slots": list(proof.unknown_slots)},
            "support": support,
            "availability": {"label": None, "state": "independent_endpoint"},
        },
        "certificate": certificate,
        "budget": _budget_view(db, session),
        "snapshot_as_of": utc_now().isoformat() + "Z", "content_is_data": True,
    }


# Stable reason-code enum (spec P2). New codes are added append-only; unknown
# codes fall back to a conservative generic message in _reason_message, so an
# older client never crashes on a newer code.
REASON_CODES = frozenset({
    "endpoint_sufficient", "execution_endpoint_sufficient",
    "support_observation_may_complete_pair", "support_observation_wait",
    "execution_answer_may_change_label", "no_eligible_evidence",
    "daily_budget_exhausted", "episode_budget_exhausted", "no_positive_decision_value",
    "acquisition_paused", "execution_window_wait", "source_not_current",
    "evidence_changed", "knowledge_contract_changed", "question_expired",
    "server_timeout", "missing_due_report", "window_not_due",
    "support_sufficient", "question_issued", "followup_not_due",
    "independent_endpoint",
})


def _reason_message(code: str) -> str:
    return {
        "endpoint_sufficient": "现有记录已足以判断本周期的执行门槛，不再为这个判断补问。",
        "execution_endpoint_sufficient": "执行门槛已有充分依据；训练负担是独立端点，可继续补齐真实配对观察。",
        "support_observation_may_complete_pair": "已有完成记录和可配对的观察缺口；这条真实自评可能补足结果证据。",
        "support_observation_wait": "执行门槛已有依据；结果端点还需要已发生的配对观察，暂时没有可安全询问的记录。",
        "execution_answer_may_change_label": "这条已到期的执行记录可能改变门槛判断。",
        "no_eligible_evidence": "目前没有既已发生又可核验的缺失记录，稍后可再查看。",
        "daily_budget_exhausted": "今天的询问额度已用完，记录仍会保留。",
        "episode_budget_exhausted": "本周期的询问额度已用完，可以继续手动记录。",
        "no_positive_decision_value": "现有可问项目预计不能改善当前判断，暂不打扰。",
        "acquisition_paused": "取证已暂停，不会继续发出问题。",
        "execution_window_wait": "等待计划日期到达后，再询问尚未记录的执行情况。",
        "source_not_current": "当前记录来源版本不一致，旧判断已阻断；请先重新核验这条记录。",
        "evidence_changed": "证据已变化，原问题已失效；请刷新后查看新的核查状态。",
        "knowledge_contract_changed": "知识依据已更新，旧判定已阻断；请刷新后重新核验。",
        "question_expired": "上一条问题已超时，请重新获取核查状态。",
        "server_timeout": "问题因等待超时已关闭，记录已保留，可刷新后继续。",
        "missing_due_report": "执行门槛还缺少已到期的本人确认记录。",
        "window_not_due": "对应执行日期尚未到达，记录到期后会再次询问。",
        "support_sufficient": "负担配对证据已满足本周期的数据要求。",
        "question_issued": "一条待确认的问题正在等待本人回答。",
        "followup_not_due": "配对随访尚未到期或缺少已完成执行记录，暂时不能询问。",
        "independent_endpoint": "可用性为独立端点，不参与本次判定。",
    }.get(code, "当前无法核查，请刷新后重试。")


def _allowed_actions(session: PolicyAcquisitionSession, action: str) -> list[str]:
    if session.status == "paused":
        return ["resume", "history"]
    if session.status == "closed":
        return ["history"]
    if action == "ask":
        return ["answer", "pause", "history"]
    if action == "sufficient":
        return ["continue", "history", "pause"]
    return ["continue", "pause", "history"]


def _budget_view(db: Session, session: PolicyAcquisitionSession) -> dict:
    budget = _decode(session.budget_json, {})
    row = db.scalar(select(PolicyAcquisitionDailyUsage).where(
        PolicyAcquisitionDailyUsage.user_id == session.user_id,
        PolicyAcquisitionDailyUsage.business_date == business_today()))
    return {
        "daily_prompts_used": row.prompt_count if row else 0,
        "daily_prompt_limit": budget.get("daily_prompt_limit", 2),
        "episode_prompts_used": session.episode_prompt_count,
        "episode_prompt_limit": budget.get("episode_prompt_limit", 14),
        "estimated_daily_seconds_used": round((row.estimated_ms if row else 0) / 1000, 1),
        "estimated_daily_seconds_limit": budget.get("estimated_daily_seconds", 30),
    }


def _support_candidates(db: Session, session: PolicyAcquisitionSession,
                        episode: PolicyEpisode, contract: dict,
                        snapshot: dict) -> list[dict]:
    """Enumerate only real, pairable burden observations; never future points."""
    if (contract.get("metric_key") != "burden" or
            contract.get("aggregation") != "paired_median" or
            (episode.baseline_context_key or episode.context_key) != episode.context_key or
            (episode.followup_context_key or episode.context_key) != episode.context_key):
        return []
    required = max(int(contract["minimum_days"]),
                   math.ceil(contract["expected_days"] * contract["minimum_coverage"]))
    refs = {(row["endpoint"], row["slot"]): row for row in snapshot["observations"]
            if row["valid"] and row["current"]}
    paired = sum(("baseline", slot) in refs and ("followup", slot) in refs
                 for slot in range(contract["expected_days"]))
    if paired >= required:
        return []

    opportunities = db.scalars(select(PolicyExecutionOpportunity).where(
        PolicyExecutionOpportunity.user_id == episode.user_id,
        PolicyExecutionOpportunity.episode_id == episode.id).order_by(
            PolicyExecutionOpportunity.slot)).all()
    completed = []
    now = utc_now()
    for opportunity in opportunities:
        if opportunity.scheduled_at > now or not opportunity.current_report_id:
            continue
        report = db.get(PolicyReport, opportunity.current_report_id)
        if (report is not None and report.user_id == episode.user_id and
                report.episode_id == episode.id and report.opportunity_id == opportunity.id and
                report.execution == "completed" and report.burden is None):
            completed.append((opportunity, report))
    histories = db.scalars(select(PolicyAcquisitionQuestion).where(
        PolicyAcquisitionQuestion.user_id == session.user_id,
        PolicyAcquisitionQuestion.session_id == session.id)).all()
    by_target: dict[str, list[PolicyAcquisitionQuestion]] = {}
    for row in histories:
        by_target.setdefault(row.target_key, []).append(row)

    def available(kind: str, slot: int) -> bool:
        target = f"{episode.id}:{kind}:support:{slot}"
        prior = by_target.get(target, [])
        if any(row.status in {"unknown", "declined", "unavailable"} for row in prior):
            return False
        timeouts = [row for row in prior if row.status == "timed_out"]
        return (len(timeouts) < 2 and not any(
            row.answered_at and now - row.answered_at < timedelta(hours=24)
            for row in timeouts))

    candidates = []
    # Include a follow-up as a contingent second-step candidate only when its
    # baseline already exists or can itself be asked and answered.
    for opportunity, report in completed:
        slot = opportunity.slot
        has_baseline = ("baseline", slot) in refs
        has_followup = ("followup", slot) in refs
        baseline_available = has_baseline or available("burden_baseline", slot)
        if not has_baseline and baseline_available:
            candidates.append({"kind": "burden_baseline", "endpoint": "baseline", "slot": slot,
                              "opportunity": opportunity, "report": report,
                              "target_key": f"{episode.id}:burden_baseline:support:{slot}"})
        if not has_followup and baseline_available and available("burden_followup", slot):
            candidates.append({"kind": "burden_followup", "endpoint": "followup", "slot": slot,
                               "opportunity": opportunity, "report": report,
                               "target_key": f"{episode.id}:burden_followup:support:{slot}"})
    # Already-started pairs first, then stable slot/kind order. Production
    # planning has a hard candidate cap; this list is intentionally deterministic.
    candidates.sort(key=lambda item: (
        0 if item["kind"] == "burden_followup" else 1, item["slot"], item["kind"]))
    return candidates


def _support_candidate(db: Session, session: PolicyAcquisitionSession,
                       episode: PolicyEpisode, contract: dict,
                       snapshot: dict) -> dict | None:
    """Compatibility/read-preview helper for any available support candidate."""
    return next(iter(_support_candidates(db, session, episode, contract, snapshot)), None)


def _planned_support_candidate(db: Session, session: PolicyAcquisitionSession,
                               episode: PolicyEpisode, contract: dict,
                               snapshot: dict) -> dict | None:
    candidates = _support_candidates(db, session, episode, contract, snapshot)
    if not candidates:
        return None
    # Restrict the live horizon to at most six pair slots (12 candidate actions).
    slot_order = list(dict.fromkeys(item["slot"] for item in candidates))[:6]
    candidates = [item for item in candidates if item["slot"] in slot_order]
    candidate_map = {f"{item['kind']}:{item['slot']}": item for item in candidates}
    refs = {(row["endpoint"], row["slot"]): row for row in snapshot["observations"]
            if row["valid"] and row["current"]}
    state = tuple((1 if ("baseline", slot) in refs else 0) |
                  (2 if ("followup", slot) in refs else 0)
                  for slot in range(contract["expected_days"]))
    baseline_slots = tuple(sorted(item["slot"] for item in candidates
                                  if item["kind"] == "burden_baseline"))
    followup_slots = tuple(sorted(item["slot"] for item in candidates
                                  if item["kind"] == "burden_followup"))
    required = max(int(contract["minimum_days"]),
                   math.ceil(contract["expected_days"] * contract["minimum_coverage"]))
    budget = _decode(session.budget_json, {})
    usage = db.scalar(select(PolicyAcquisitionDailyUsage).where(
        PolicyAcquisitionDailyUsage.user_id == session.user_id,
        PolicyAcquisitionDailyUsage.business_date == business_today()))
    daily_used = usage.prompt_count if usage else 0
    estimated_used = usage.estimated_ms if usage else 0
    daily_limit = int(budget.get("daily_prompt_limit", 2))
    episode_limit = int(budget.get("episode_prompt_limit", 14))
    seconds_limit = int(budget.get("estimated_daily_seconds", 30))
    remaining_questions = min(2, daily_limit - daily_used,
                              episode_limit - session.episode_prompt_count)
    remaining_ms = max(0, seconds_limit * 1000 - estimated_used)
    # Endpoint value / pair value / defer penalty are configurable from the
    # session budget ("planner" block) so they can be frozen per episode and
    # ablated (e.g. defer_penalty=0 disables defer value); defaults match the
    # frozen v2 benchmark constants (cost_ms=4000, defer_penalty=20.0).
    planner_cfg = budget.get("planner", {}) if isinstance(budget, dict) else {}
    cost_ms = int(planner_cfg.get("support_cost_ms", 4000))
    defer_penalty = float(planner_cfg.get("support_defer_penalty", 20.0))
    domain = SupportPairDomain(
        state=state, eligible_baseline_slots=baseline_slots,
        eligible_followup_slots=followup_slots, required_pairs=required,
        cost_ms=cost_ms, defer_penalty=defer_penalty, prior=ResponsePrior())
    plan = choose_next(domain, state, remaining_questions=max(0, remaining_questions),
                       remaining_time_ms=remaining_ms, depth=2)
    return candidate_map.get(plan.query_key) if plan.action == "ask" else None


def _issue_support_question(db: Session, *, user_id: int,
                            session: PolicyAcquisitionSession,
                            episode: PolicyEpisode, contract: dict,
                            snapshot: dict, proof: ExecutionProof,
                            candidate: dict, idempotency_key: str,
                            route: str, digest: str) -> dict:
    budget = _decode(session.budget_json, {})
    usage = db.scalar(select(PolicyAcquisitionDailyUsage).where(
        PolicyAcquisitionDailyUsage.user_id == user_id,
        PolicyAcquisitionDailyUsage.business_date == business_today()).with_for_update())
    if usage is None:
        usage = PolicyAcquisitionDailyUsage(user_id=user_id, business_date=business_today(),
                                            prompt_count=0, estimated_ms=0, measured_ms=0, version=1)
        db.add(usage)
        db.flush()
    if (usage.prompt_count >= int(budget.get("daily_prompt_limit", 2)) or
            usage.estimated_ms + 4000 > int(budget.get("estimated_daily_seconds", 30)) * 1000):
        session.decision_state = "deferred"
        session.version += 1
        response = _decision_response(db, session, episode, contract, snapshot, proof,
                                      action="defer", reason="daily_budget_exhausted")
    elif session.episode_prompt_count >= int(budget.get("episode_prompt_limit", 14)):
        session.decision_state = "deferred"
        session.version += 1
        response = _decision_response(db, session, episode, contract, snapshot, proof,
                                      action="defer", reason="episode_budget_exhausted")
    else:
        slot, kind = candidate["slot"], candidate["kind"]
        session.version += 1
        question_id = uuid4().hex
        is_baseline = kind == "burden_baseline"
        prompt = {
            "prompt": (f"周期开始前 30 天内，第 {slot + 1} 条配对基线观察的训练负担是多少？"
                       if is_baseline else f"第 {slot + 1} 次已确认完成的训练，实际负担评分是多少？"),
            "why": "这条本人真实发生的 0–10 自评可能补足配对结果证据；执行门槛仍单独判断。",
            "choices": ["0–10 自评", "unknown", "declined"],
            "time_window": ("episode_start_minus_30d_to_episode_start" if is_baseline
                            else "completed_opportunity_to_now"),
        }
        question = PolicyAcquisitionQuestion(
            id=question_id, user_id=user_id, session_id=session.id,
            target_key=candidate["target_key"], kind=kind, endpoint=candidate["endpoint"],
            slot=slot, target_opportunity_id=candidate["opportunity"].id,
            fingerprint=canonical_hash({"contract": session.contract_hash, "kind": kind,
                                        "slot": slot, "report_id": candidate["report"].id,
                                        "observations": snapshot["observations"]}),
            expected_episode_version=episode.version,
            expected_snapshot_hash=_fact_snapshot_hash(snapshot),
            expected_session_version=session.version, status="issued",
            prompt_json=_json(prompt), estimated_cost_ms=4000,
            issued_at=utc_now(), expires_at=utc_now() + timedelta(minutes=10), answer_json="{}")
        db.add(question)
        session.active_question_id = question.id
        session.decision_state = "needs_evidence"
        session.episode_prompt_count += 1
        usage.prompt_count += 1
        usage.estimated_ms += question.estimated_cost_ms
        usage.version += 1
        _new_event(db, session, "question_issued", "support_observation_may_complete_pair",
                   question.id, payload={"target_key": candidate["target_key"],
                                         "estimated_cost_ms": question.estimated_cost_ms})
        response = _decision_response(db, session, episode, contract, snapshot, proof,
                                      action="ask", reason="support_observation_may_complete_pair",
                                      question=question)
    _save_command(db, user_id=user_id, key=idempotency_key, method="POST", route=route,
                  digest=digest, session=session, response_json=_json(response))
    return response


def _session_view(db: Session, session: PolicyAcquisitionSession, *, mutate: bool = True) -> dict:
    episode = _episode(db, session.user_id, session.episode_id)
    contract = _decode(session.contract_json, {})
    snapshot = _snapshot(db, episode, contract)
    proof = _proof(snapshot, contract)
    current_knowledge = current_knowledge_contract(db, contract.get("template_id", ""))
    knowledge_stale = current_knowledge["contract_hash"] != contract.get("knowledge_contract_hash")
    pending = db.get(PolicyAcquisitionQuestion, session.active_question_id) if session.active_question_id else None
    if pending and pending.status == "issued" and (
        pending.expected_episode_version != episode.version or
        pending.expected_snapshot_hash != _fact_snapshot_hash(snapshot)
    ):
        if mutate:
            pending.status = "obsolete"
            pending.answered_at = utc_now()
            session.active_question_id = None
            session.decision_state = "needs_repair"
            session.version += 1
            _new_event(db, session, "question_obsoleted", "evidence_changed", pending.id)
    now = utc_now()
    if mutate:
        _expire_question(db, session, now)
    current_question = db.get(PolicyAcquisitionQuestion, session.active_question_id) if session.active_question_id else None
    active = current_question if current_question and current_question.status == "issued" else None
    stale_question = bool(active and (active.expected_episode_version != episode.version or
                                      active.expected_snapshot_hash != _fact_snapshot_hash(snapshot)))
    expired_question = bool(active and now >= active.expires_at)
    if stale_question or expired_question:
        if mutate:
            active.status = "obsolete" if stale_question else "timed_out"
            active.answered_at = now
            if expired_question:
                active.answer_json = _json({"response": "no_response", "server_timeout": True})
            session.active_question_id = None
            session.decision_state = "needs_repair" if stale_question else "needs_evidence"
            session.version += 1
            _new_event(db, session, "question_obsoleted" if stale_question else "question_timed_out",
                       "evidence_changed" if stale_question else "server_timeout", active.id)
        active = None
    action = "paused" if session.status == "paused" else "ask" if active else "sufficient" if proof.label is not None else "defer"
    reason = "acquisition_paused" if session.status == "paused" else "endpoint_sufficient" if proof.label is not None else "execution_window_wait"
    decision_state = session.decision_state
    if (session.status == "active" and active is None and proof.label == 1 and
            not snapshot.get("stale_sources")):
        candidate = _support_candidate(db, session, episode, contract, snapshot)
        if candidate:
            reason = "execution_endpoint_sufficient"
            decision_state = "needs_evidence"
        elif not snapshot["window_closed"]:
            action, reason = "wait", "support_observation_wait"
            decision_state = "waiting_window"
        elif _support_evidence_state(snapshot, contract)["state"] == "needs_evidence":
            action, reason = "defer", "no_eligible_evidence"
            decision_state = "deferred"
        else:
            decision_state = "sufficient"
    if snapshot.get("stale_sources"):
        action, reason = "needs_repair", "source_not_current"
        decision_state = "needs_repair"
    elif knowledge_stale:
        action, reason = "needs_repair", "knowledge_contract_changed"
        decision_state = "needs_repair"
    elif stale_question:
        action, reason = "needs_repair", "evidence_changed"
        decision_state = "needs_repair"
    elif expired_question:
        action, reason = "defer", "question_expired"
        decision_state = "needs_evidence"
    if mutate:
        session.decision_state = decision_state
    return _decision_response(db, session, episode, contract, snapshot, proof,
                              action=action, reason=reason, question=active,
                              decision_state_override=decision_state)


def start_session(db: Session, *, user_id: int, episode_id: str,
                  request: StartAcquisitionRequest, idempotency_key: str) -> dict:
    route = f"/policy/episodes/{episode_id}/acquisition/sessions"
    body = request.model_dump(mode="json")
    _authorize(db, user_id, active=True)
    _fence(db, user_id)
    digest, replay = _command(db, user_id, idempotency_key, "POST", route, body)
    if replay is not None:
        return replay
    episode = _episode(db, user_id, episode_id)
    if episode.status != "active":
        raise AcquisitionError(409, "ACQUISITION_EPISODE_NOT_ACTIVE", "当前周期不接受新的主动取证")
    if episode.version != request.expected_episode_version:
        raise AcquisitionError(409, "ACQUISITION_VERSION_CONFLICT", "周期记录已变化，请刷新后重试")
    contract = _frozen_contract(db, episode)
    contract_hash = canonical_hash(contract)
    existing = db.scalar(select(PolicyAcquisitionSession).where(
        PolicyAcquisitionSession.user_id == user_id,
        PolicyAcquisitionSession.episode_id == episode_id))
    if existing is None:
        budget = request.budget.model_dump()
        existing = PolicyAcquisitionSession(
            id=uuid4().hex, user_id=user_id, episode_id=episode_id,
            contract_json=_json(contract), contract_hash=contract_hash,
            status="active", decision_state="needs_evidence", version=1,
            consented_at=utc_now(), budget_json=_json(budget),
            episode_prompt_count=0, created_at=utc_now(), updated_at=utc_now())
        db.add(existing)
        db.flush()
        _new_event(db, existing, "session_started", "explicit_consent", payload={"budget": budget})
    else:
        if existing.contract_hash != contract_hash:
            raise AcquisitionError(409, "ACQUISITION_CONTRACT_CHANGED", "冻结协议与取证契约不一致，请先修复")
        if existing.status == "closed":
            raise AcquisitionError(409, "ACQUISITION_SESSION_CLOSED", "本周期取证会话已关闭")
        existing.status = "active"
        existing.consented_at = utc_now()
        existing.version += 1
        existing.updated_at = utc_now()
        _new_event(db, existing, "consent_renewed", "explicit_consent")
    current_contract = _decode(existing.contract_json, {})
    current_snapshot = _snapshot(db, episode, current_contract)
    current_proof = _proof(current_snapshot, current_contract)
    if current_proof.label is not None and not current_snapshot.get("stale_sources"):
        current_cert = _current_certificate(db, existing, episode, current_contract,
                                            current_snapshot, current_proof, utc_now())
        if current_cert is None or current_cert.get("effective_status") != "current":
            previous = db.get(PolicyDecisionCertificate, existing.latest_certificate_id) if existing.latest_certificate_id else None
            if previous and previous.status == "valid":
                previous.status = "stale"
            _issue_certificate(db, existing, episode, current_contract, current_snapshot, current_proof)
    response = _session_view(db, existing)
    _save_command(db, user_id=user_id, key=idempotency_key, method="POST", route=route,
                  digest=digest, session=existing, response_json=_json(response))
    return response


def read_session(db: Session, *, user_id: int, session_id: str) -> dict:
    _authorize(db, user_id, active=False, phase="read_history")
    session = _session(db, user_id, session_id)
    return _session_view(db, session, mutate=False)


def preview_episode(db: Session, *, user_id: int, episode_id: str) -> dict:
    """Read-only preview for Harness; it never creates a session/question/cert."""
    _authorize(db, user_id, active=True)
    episode = _episode(db, user_id, episode_id)
    try:
        contract = _frozen_contract(db, episode)
    except AcquisitionError as exc:
        if exc.code != "KNOWLEDGE_CONTRACT_UNAVAILABLE":
            raise
        return {"episode_id": episode.id, "action": "blocked",
                "reason_code": "knowledge_contract_unavailable",
                "requires_explicit_consent": True, "content_is_data": True}
    snapshot = _snapshot(db, episode, contract)
    proof = _proof(snapshot, contract)
    existing = db.scalar(select(PolicyAcquisitionSession).where(
        PolicyAcquisitionSession.user_id == user_id,
        PolicyAcquisitionSession.episode_id == episode.id))
    if existing is not None:
        state = _session_view(db, existing, mutate=False)
        return {"episode_id": episode.id, "action": state["decision"]["action"],
                "reason_code": state["decision"]["reason_code"],
                "decision_state": state["decision_state"],
                "execution": state["endpoints"]["execution"],
                "support": state["endpoints"]["support"],
                "requires_explicit_consent": False, "content_is_data": True}
    if snapshot.get("stale_sources"):
        action, reason = "needs_repair", "source_not_current"
    elif proof.label is not None:
        action, reason = "sufficient", "execution_endpoint_sufficient"
    elif snapshot["due_slots"]:
        action, reason = "candidate_available", "execution_answer_may_change_label"
    else:
        action, reason = "wait", "execution_window_wait"
    return {"episode_id": episode.id, "action": action, "reason_code": reason,
            "execution": {"label": proof.label, "state": "sufficient" if proof.label is not None else "needs_evidence",
                          "lower_completed": proof.lower_completed,
                          "upper_completed": proof.upper_completed,
                          "unknown_slots": list(proof.unknown_slots)},
            "eligible_due_slot_count": len(snapshot["due_slots"]),
            "requires_explicit_consent": True, "content_is_data": True}


def issue_next_question(db: Session, *, user_id: int, session_id: str,
                        expected_session_version: int, expected_episode_version: int,
                        idempotency_key: str) -> dict:
    route = f"/policy/acquisition/sessions/{session_id}/next"
    body = {"expected_session_version": expected_session_version,
            "expected_episode_version": expected_episode_version}
    _authorize(db, user_id, active=True)
    _fence(db, user_id)
    digest, replay = _command(db, user_id, idempotency_key, "POST", route, body)
    if replay is not None:
        return replay
    session = _session(db, user_id, session_id)
    episode = _episode(db, user_id, session.episode_id)
    if session.status != "active" or episode.status != "active":
        raise AcquisitionError(409, "ACQUISITION_SESSION_NOT_ACTIVE", "取证已暂停或结束")
    if session.version != expected_session_version or episode.version != expected_episode_version:
        raise AcquisitionError(409, "ACQUISITION_VERSION_CONFLICT", "记录已变化，请刷新后继续")
    if not session.consented_at:
        raise AcquisitionError(422, "ACQUISITION_CONSENT_REQUIRED", "需要本人明确同意后才能询问")
    _expire_question(db, session, utc_now())
    contract = _decode(session.contract_json, {})
    snapshot = _snapshot(db, episode, contract)
    proof = _proof(snapshot, contract)
    live_knowledge = current_knowledge_contract(db, contract.get("template_id", ""))
    if live_knowledge["contract_hash"] != contract.get("knowledge_contract_hash"):
        prior_state = session.decision_state
        prior_version = session.version
        _invalidate_episode_certificates(db, user_id, episode.id)
        session.decision_state = "needs_repair"
        if session.version == prior_version:
            session.version += 1
        if prior_state != "needs_repair":
            _new_event(db, session, "knowledge_contract_invalidated", "reviewed_knowledge_changed",
                       payload={"template_id": contract.get("template_id")})
        response = _decision_response(db, session, episode, contract, snapshot, proof,
                                      action="needs_repair", reason="knowledge_contract_changed")
        _save_command(db, user_id=user_id, key=idempotency_key, method="POST", route=route,
                      digest=digest, session=session, response_json=_json(response))
        return response
    pending = db.get(PolicyAcquisitionQuestion, session.active_question_id) if session.active_question_id else None
    if pending and pending.status == "issued" and (
        pending.expected_episode_version != episode.version or
        pending.expected_snapshot_hash != _fact_snapshot_hash(snapshot)
    ):
        pending.status = "obsolete"
        pending.answered_at = utc_now()
        session.active_question_id = None
        session.decision_state = "needs_repair"
        session.version += 1
        _new_event(db, session, "question_obsoleted", "evidence_changed", pending.id)
    if snapshot.get("stale_sources"):
        session.decision_state = "needs_repair"
        response = _decision_response(db, session, episode, contract, snapshot, proof,
                                      action="needs_repair", reason="source_not_current")
        _save_command(db, user_id=user_id, key=idempotency_key, method="POST", route=route,
                      digest=digest, session=session, response_json=_json(response))
        return response
    if session.active_question_id:
        question = db.get(PolicyAcquisitionQuestion, session.active_question_id)
        if question and question.status == "issued":
            response = _decision_response(db, session, episode, contract, snapshot, proof,
                                          action="ask", reason=("execution_answer_may_change_label"
                                          if question.kind == "execution_confirmation"
                                          else "support_observation_may_complete_pair"), question=question)
            _save_command(db, user_id=user_id, key=idempotency_key, method="POST", route=route,
                          digest=digest, session=session, response_json=_json(response))
            return response
    if proof.label is not None and not snapshot.get("stale_sources"):
        current_cert = _current_certificate(db, session, episode, contract, snapshot, proof, utc_now())
        if current_cert is None or current_cert.get("effective_status") != "current":
            previous = db.get(PolicyDecisionCertificate, session.latest_certificate_id) if session.latest_certificate_id else None
            if previous and previous.status == "valid":
                previous.status = "stale"
            _issue_certificate(db, session, episode, contract, snapshot, proof)
        if proof.label == 1:
            candidate = _planned_support_candidate(db, session, episode, contract, snapshot)
            if candidate:
                return _issue_support_question(
                    db, user_id=user_id, session=session, episode=episode,
                    contract=contract, snapshot=snapshot, proof=proof,
                    candidate=candidate, idempotency_key=idempotency_key,
                    route=route, digest=digest)
        if proof.label == 0:
            session.decision_state = "sufficient"
            action, reason = "sufficient", "endpoint_sufficient"
        elif not snapshot["window_closed"]:
            session.decision_state = "waiting_window"
            action, reason = "wait", "support_observation_wait"
        else:
            support_state = _support_evidence_state(snapshot, contract)["state"]
            if support_state == "needs_evidence":
                session.decision_state = "deferred"
                action, reason = "defer", "no_eligible_evidence"
            else:
                session.decision_state = "sufficient"
                action, reason = "sufficient", "endpoint_sufficient"
        session.version += 1
        response = _decision_response(db, session, episode, contract, snapshot, proof,
                                      action=action, reason=reason)
    else:
        budget = _decode(session.budget_json, {})
        usage = db.scalar(select(PolicyAcquisitionDailyUsage).where(
            PolicyAcquisitionDailyUsage.user_id == user_id,
            PolicyAcquisitionDailyUsage.business_date == business_today()).with_for_update())
        if usage is None:
            usage = PolicyAcquisitionDailyUsage(user_id=user_id, business_date=business_today(),
                                                prompt_count=0, estimated_ms=0, measured_ms=0, version=1)
            db.add(usage)
            db.flush()
        daily_limit = int(budget.get("daily_prompt_limit", 2))
        episode_limit = int(budget.get("episode_prompt_limit", 14))
        seconds_limit = int(budget.get("estimated_daily_seconds", 30))
        if usage.prompt_count >= daily_limit or usage.estimated_ms >= seconds_limit * 1000:
            session.decision_state = "deferred"
            session.version += 1
            response = _decision_response(db, session, episode, contract, snapshot, proof,
                                          action="defer", reason="daily_budget_exhausted")
        elif session.episode_prompt_count >= episode_limit:
            session.decision_state = "deferred"
            session.version += 1
            response = _decision_response(db, session, episode, contract, snapshot, proof,
                                          action="defer", reason="episode_budget_exhausted")
        else:
            histories = db.scalars(select(PolicyAcquisitionQuestion).where(
                PolicyAcquisitionQuestion.user_id == user_id,
                PolicyAcquisitionQuestion.session_id == session.id)).all()
            by_target: dict[str, list[PolicyAcquisitionQuestion]] = {}
            for prior_question in histories:
                by_target.setdefault(prior_question.target_key, []).append(prior_question)
            eligible = []
            for slot in snapshot["due_slots"]:
                target = f"{episode.id}:execution:execution:{slot}"
                prior_rows = by_target.get(target, [])
                if any(row.status in {"unknown", "declined", "unavailable"} for row in prior_rows):
                    continue
                timeouts = [row for row in prior_rows if row.status == "timed_out"]
                if len(timeouts) >= 2 or any(row.answered_at and utc_now() - row.answered_at < timedelta(hours=24) for row in timeouts):
                    continue
                eligible.append(slot)
            domain = ExecutionDomain(target=contract["execution_target"],
                                     eligible_slots=tuple(eligible),
                                     defer_penalty=20.0, prior=ResponsePrior())
            # A small per-unknown-slot cost keeps two-step planning willing to
            # gather evidence when the exact threshold needs more than two answers.
            original_loss = domain.terminal_loss
            domain.terminal_loss = lambda state: (0.0 if domain.resolved(state)
                                                   else original_loss(state) + 5.0 * sum(v is None for v in state))
            remaining_ms = max(0, seconds_limit * 1000 - usage.estimated_ms)
            plan = choose_next(domain, snapshot["execution"],
                               remaining_questions=min(2, daily_limit - usage.prompt_count,
                                                       episode_limit - session.episode_prompt_count),
                               remaining_time_ms=remaining_ms, depth=2)
            if plan.query_key is None:
                reason = "no_eligible_evidence" if not eligible else "no_positive_decision_value"
                if not eligible and not snapshot["due_slots"]:
                    session.decision_state = "waiting_window"
                    action = "wait"
                    reason = "execution_window_wait"
                else:
                    session.decision_state = "deferred"
                    action = "defer"
                session.version += 1
                response = _decision_response(db, session, episode, contract, snapshot, proof,
                                              action=action, reason=reason)
            else:
                slot = int(plan.query_key.split(":", 1)[1])
                opportunity = db.scalar(select(PolicyExecutionOpportunity).where(
                    PolicyExecutionOpportunity.user_id == user_id,
                    PolicyExecutionOpportunity.episode_id == episode.id,
                    PolicyExecutionOpportunity.slot == slot))
                if opportunity is None:
                    raise AcquisitionError(409, "ACQUISITION_EVIDENCE_CONFLICT", "执行机会不存在")
                target_key = f"{episode.id}:execution:execution:{slot}"
                history = db.scalars(select(PolicyAcquisitionQuestion).where(
                    PolicyAcquisitionQuestion.user_id == user_id,
                    PolicyAcquisitionQuestion.session_id == session.id,
                    PolicyAcquisitionQuestion.target_key == target_key).order_by(
                        PolicyAcquisitionQuestion.issued_at.desc())).all()
                if any(q.status in {"unknown", "declined", "unavailable"} for q in history):
                    session.decision_state = "deferred"
                    session.version += 1
                    response = _decision_response(db, session, episode, contract, snapshot, proof,
                                                  action="defer", reason="no_positive_decision_value")
                elif sum(q.status == "timed_out" for q in history) >= 2 or any(
                    q.status == "timed_out" and q.answered_at and utc_now() - q.answered_at < timedelta(hours=24)
                    for q in history):
                    session.decision_state = "deferred"
                    session.version += 1
                    response = _decision_response(db, session, episode, contract, snapshot, proof,
                                                  action="defer", reason="no_positive_decision_value")
                else:
                    session.version += 1
                    question_id = uuid4().hex
                    prompt = {
                        "prompt": f"计划日期已到的第 {slot + 1} 次训练，你实际完成了吗？",
                        "why": "这条已发生的执行记录可能改变本周期的预设门槛判断。",
                        "choices": ["completed", "explicitly_not_completed", "unknown", "declined"],
                    }
                    question = PolicyAcquisitionQuestion(
                        id=question_id, user_id=user_id, session_id=session.id,
                        target_key=target_key, kind="execution_confirmation", endpoint="execution",
                        slot=slot, target_opportunity_id=opportunity.id,
                        fingerprint=canonical_hash({"contract": session.contract_hash,
                                                    "episode_version": episode.version,
                                                    "slot": slot, "execution": snapshot["execution"]}),
                        expected_episode_version=episode.version,
                expected_snapshot_hash=_fact_snapshot_hash(snapshot),
                        expected_session_version=session.version, status="issued",
                        prompt_json=_json(prompt), estimated_cost_ms=3000,
                        issued_at=utc_now(), expires_at=utc_now() + timedelta(minutes=10),
                        answer_json="{}")
                    db.add(question)
                    session.active_question_id = question.id
                    session.decision_state = "needs_evidence"
                    session.episode_prompt_count += 1
                    usage.prompt_count += 1
                    usage.estimated_ms += question.estimated_cost_ms
                    usage.version += 1
                    usage.estimated_ms = min(usage.estimated_ms, seconds_limit * 1000)
                    _new_event(db, session, "question_issued", "execution_answer_may_change_label", question.id,
                               payload={"target_key": target_key, "estimated_cost_ms": question.estimated_cost_ms})
                    response = _decision_response(db, session, episode, contract, snapshot, proof,
                                                  action="ask", reason="execution_answer_may_change_label", question=question)
    _save_command(db, user_id=user_id, key=idempotency_key, method="POST", route=route,
                  digest=digest, session=session, response_json=_json(response))
    return response


def answer_question(db: Session, *, user_id: int, session_id: str, question_id: str,
                    request: AnswerRequest, idempotency_key: str) -> dict:
    route = f"/policy/acquisition/sessions/{session_id}/questions/{question_id}/answer"
    body = request.model_dump(mode="json")
    _authorize(db, user_id, active=True)
    _fence(db, user_id)
    digest, replay = _command(db, user_id, idempotency_key, "POST", route, body)
    if replay is not None:
        return replay
    session = _session(db, user_id, session_id)
    episode = _episode(db, user_id, session.episode_id)
    question = db.scalar(select(PolicyAcquisitionQuestion).where(
        PolicyAcquisitionQuestion.id == question_id,
        PolicyAcquisitionQuestion.session_id == session.id,
        PolicyAcquisitionQuestion.user_id == user_id))
    if question is None:
        raise AcquisitionError(404, "ACQUISITION_QUESTION_NOT_FOUND", "询问不存在")
    if question.status != "issued" or session.active_question_id != question.id:
        raise AcquisitionError(410, "ACQUISITION_QUESTION_EXPIRED", "这条询问已失效，请刷新后查看最新状态")
    if utc_now() >= question.expires_at:
        raise AcquisitionError(410, "ACQUISITION_QUESTION_EXPIRED", "这条询问已超时，请刷新后查看最新状态")
    if session.version != request.expected_session_version or episode.version != request.expected_episode_version or question.expected_session_version != session.version or question.expected_episode_version != episode.version:
        raise AcquisitionError(409, "ACQUISITION_VERSION_CONFLICT", "周期或询问已变化，请刷新后重新核对")
    contract = _decode(session.contract_json, {})
    snapshot = _snapshot(db, episode, contract)
    if _fact_snapshot_hash(snapshot) != question.expected_snapshot_hash:
        raise AcquisitionError(409, "ACQUISITION_QUESTION_STALE", "询问依据已变化；没有写入答复，请先刷新")
    now = utc_now()
    result_report_id = None
    result_observation_ref_id = None
    if request.response == "answered" and question.kind == "execution_confirmation":
        if request.execution_value is None or request.burden_value is not None or request.selected_source is not None:
            raise AcquisitionError(422, "ACQUISITION_ANSWER_KIND_MISMATCH", "答复类型与当前询问不匹配")
        report_id = f"acq-{question.id}"
        report = ExecutionReportRequest(
            episode_version=episode.version, report_id=report_id,
            opportunity_id=str(question.target_opportunity_id),
            execution=str(request.execution_value), perceived_burden=None, confounder_codes=[])
        try:
            report_opportunity(db, user_id, episode.id, report,
                               actor_question_id=question.id, fence_acquired=True)
        except PolicyError as exc:
            raise AcquisitionError(409, exc.code, exc.message) from exc
        current = db.scalar(select(PolicyExecutionOpportunity).where(
            PolicyExecutionOpportunity.id == question.target_opportunity_id,
            PolicyExecutionOpportunity.user_id == user_id,
            PolicyExecutionOpportunity.episode_id == episode.id))
        result_report_id = current.current_report_id if current else None
    elif request.response == "answered" and question.kind in {"burden_baseline", "burden_followup"}:
        if (request.burden_value is None or request.observed_at is None or
                request.execution_value is not None or request.selected_source is not None or
                contract.get("metric_key") != "burden"):
            raise AcquisitionError(422, "ACQUISITION_ANSWER_KIND_MISMATCH", "答复类型与当前询问不匹配")
        observed_at = request.observed_at.astimezone(timezone.utc).replace(tzinfo=None)
        opportunity = db.scalar(select(PolicyExecutionOpportunity).where(
            PolicyExecutionOpportunity.id == question.target_opportunity_id,
            PolicyExecutionOpportunity.user_id == user_id,
            PolicyExecutionOpportunity.episode_id == episode.id,
            PolicyExecutionOpportunity.slot == question.slot))
        if opportunity is None or not opportunity.current_report_id:
            raise AcquisitionError(409, "ACQUISITION_QUESTION_STALE", "对应训练记录已变化，请刷新后重新核对")
        current_report = db.get(PolicyReport, opportunity.current_report_id)
        if (current_report is None or current_report.user_id != user_id or
                current_report.execution != "completed" or current_report.burden is not None):
            raise AcquisitionError(409, "ACQUISITION_QUESTION_STALE", "对应训练记录已变化或已有负担值，请刷新后重新核对")
        if observed_at > now:
            raise AcquisitionError(422, "EVIDENCE_SOURCE_IN_FUTURE", "不能提前提交尚未发生的观察")
        if question.kind == "burden_baseline":
            left, right = episode.start_at - timedelta(days=30), episode.start_at
        else:
            left, right = episode.start_at, min(episode.end_at + timedelta(days=1), now)
            if observed_at < opportunity.scheduled_at:
                raise AcquisitionError(422, "EVIDENCE_SOURCE_OUTSIDE_WINDOW", "周期内观察必须发生在对应训练计划之后")
        if not left <= observed_at <= right:
            raise AcquisitionError(422, "EVIDENCE_SOURCE_OUTSIDE_WINDOW", "观察时间不在该问题允许的真实发生窗口内")
        repeated_observation = db.scalar(select(PolicyObservationRef.id).where(
            PolicyObservationRef.user_id == user_id,
            PolicyObservationRef.episode_id == episode.id,
            PolicyObservationRef.endpoint == question.endpoint,
            PolicyObservationRef.slot != question.slot,
            PolicyObservationRef.observed_at == observed_at,
            PolicyObservationRef.valid.is_(True)))
        if repeated_observation is not None:
            raise AcquisitionError(409, "EVIDENCE_OBSERVATION_DUPLICATE",
                                   "同一真实发生时间不能重复占用不同观察槽")
        ref = db.scalar(select(PolicyObservationRef).where(
            PolicyObservationRef.user_id == user_id,
            PolicyObservationRef.episode_id == episode.id,
            PolicyObservationRef.endpoint == question.endpoint,
            PolicyObservationRef.slot == question.slot))
        if ref is not None and ref.valid:
            raise AcquisitionError(409, "ACQUISITION_QUESTION_STALE", "该观察槽已有有效证据，请刷新后核对")
        if ref is None:
            ref = PolicyObservationRef(
                user_id=user_id, episode_id=episode.id, endpoint=question.endpoint,
                slot=question.slot, source_type="user_report", source_id=question.id,
                source_revision=1, revision=1, value_json=_json(float(request.burden_value)),
                observed_at=observed_at, metric_version=contract["metric_version"],
                confirmed=True, valid=True)
            db.add(ref)
            db.flush()
        else:
            old_revision = int(ref.revision or 1)
            db.add(PolicyEvidenceRevision(
                user_id=user_id, observation_ref_id=ref.id,
                observation_revision=old_revision, source_type=ref.source_type,
                source_id=ref.source_id, source_revision=ref.source_revision,
                metric_version=ref.metric_version, endpoint=ref.endpoint, slot=ref.slot,
                observed_at=ref.observed_at, value_json=ref.value_json,
                value_hash=canonical_hash(ref.value_json or "null"), confirmed=ref.confirmed,
                created_at=now))
            ref.revision = old_revision + 1
            ref.source_type, ref.source_id, ref.source_revision = "user_report", question.id, 1
            ref.value_json, ref.observed_at = _json(float(request.burden_value)), observed_at
            ref.metric_version, ref.confirmed, ref.valid = contract["metric_version"], True, True
        result_observation_ref_id = ref.id
        episode.version += 1
    elif request.response == "answered":
        raise AcquisitionError(422, "ACQUISITION_ANSWER_KIND_MISMATCH", "答复类型与当前询问不匹配")
    elif request.execution_value is not None or request.burden_value is not None or request.selected_source is not None or request.observed_at is not None:
        raise AcquisitionError(422, "ACQUISITION_ANSWER_KIND_MISMATCH", "未知或拒答不能携带个人事实")
    question.status = request.response
    question.answered_at = now
    question.answer_json = _json({"response": request.response,
                                  "execution_value": request.execution_value if request.response == "answered" else None,
                                  "resulting_observation_ref_id": result_observation_ref_id,
                                  "confirmation": request.confirmation})
    question.resulting_report_id = result_report_id
    question.resulting_observation_ref_id = result_observation_ref_id
    session.active_question_id = None
    session.version += 1
    session.updated_at = now
    if request.client_elapsed_ms is not None:
        usage = db.scalar(select(PolicyAcquisitionDailyUsage).where(
            PolicyAcquisitionDailyUsage.user_id == user_id,
            PolicyAcquisitionDailyUsage.business_date == business_today()).with_for_update())
        if usage:
            usage.measured_ms += min(request.client_elapsed_ms, 600_000)
            usage.version += 1
    _invalidate_episode_certificates(db, user_id, episode.id, except_question_id=question.id)
    contract = _decode(session.contract_json, {})
    snapshot = _snapshot(db, episode, contract)
    proof = _proof(snapshot, contract)
    _new_event(db, session, "question_answered", request.response, question.id,
               request.client_elapsed_ms, {
                   "execution_value": request.execution_value if question.kind == "execution_confirmation" and request.response == "answered" else None,
                   "observation_ref_id": result_observation_ref_id,
                   "burden_endpoint": question.endpoint if result_observation_ref_id is not None else None,
               })
    if proof.label is not None and not snapshot.get("stale_sources"):
        _issue_certificate(db, session, episode, contract, snapshot, proof)
        if proof.label == 1:
            candidate = _support_candidate(db, session, episode, contract, snapshot)
            support_state = _support_evidence_state(snapshot, contract)["state"]
            if candidate:
                session.decision_state = "needs_evidence"
            elif not snapshot["window_closed"]:
                session.decision_state = "waiting_window"
            elif support_state == "needs_evidence":
                session.decision_state = "deferred"
    else:
        session.decision_state = "needs_repair" if snapshot.get("stale_sources") else "needs_evidence"
    response_reason = ("source_not_current" if snapshot.get("stale_sources") else
                       "execution_endpoint_sufficient" if proof.label == 1 else
                       "endpoint_sufficient" if proof.label is not None else "no_eligible_evidence")
    response = _decision_response(db, session, episode, contract, snapshot, proof,
                                  action="needs_repair" if snapshot.get("stale_sources") else "sufficient" if proof.label is not None else "defer",
                                  reason=response_reason)
    _save_command(db, user_id=user_id, key=idempotency_key, method="POST", route=route,
                  digest=digest, session=session, response_json=_json(response))
    return response


def pause_session(db: Session, *, user_id: int, session_id: str,
                  expected_session_version: int, idempotency_key: str) -> dict:
    route = f"/policy/acquisition/sessions/{session_id}/pause"
    body = {"expected_session_version": expected_session_version}
    _authorize(db, user_id, active=False, phase="close_existing")
    _fence(db, user_id)
    digest, replay = _command(db, user_id, idempotency_key, "POST", route, body)
    if replay is not None:
        return replay
    session = _session(db, user_id, session_id)
    if session.version != expected_session_version:
        raise AcquisitionError(409, "ACQUISITION_VERSION_CONFLICT", "取证状态已变化，请刷新后重试")
    if session.status != "closed":
        session.status = "paused"
        session.version += 1
        if session.active_question_id:
            question = db.get(PolicyAcquisitionQuestion, session.active_question_id)
            if question and question.status == "issued":
                question.status = "cancelled"
                question.answered_at = utc_now()
            session.active_question_id = None
        _new_event(db, session, "session_paused", "user_requested")
    response = _session_view(db, session)
    _save_command(db, user_id=user_id, key=idempotency_key, method="POST", route=route,
                  digest=digest, session=session, response_json=_json(response))
    return response


def resume_session(db: Session, *, user_id: int, session_id: str,
                   expected_session_version: int, expected_episode_version: int,
                   idempotency_key: str) -> dict:
    route = f"/policy/acquisition/sessions/{session_id}/resume"
    body = {"expected_session_version": expected_session_version,
            "expected_episode_version": expected_episode_version}
    _authorize(db, user_id, active=True)
    _fence(db, user_id)
    digest, replay = _command(db, user_id, idempotency_key, "POST", route, body)
    if replay is not None:
        return replay
    session = _session(db, user_id, session_id)
    episode = _episode(db, user_id, session.episode_id)
    if session.version != expected_session_version or episode.version != expected_episode_version:
        raise AcquisitionError(409, "ACQUISITION_VERSION_CONFLICT", "周期记录已变化，请刷新后再恢复")
    if session.status != "paused" or episode.status != "active":
        raise AcquisitionError(409, "ACQUISITION_SESSION_NOT_ACTIVE", "只有进行中的已暂停取证可以恢复")
    session.status = "active"
    session.consented_at = utc_now()
    session.version += 1
    session.updated_at = utc_now()
    _new_event(db, session, "session_resumed", "explicit_user_action")
    response = _session_view(db, session)
    _save_command(db, user_id=user_id, key=idempotency_key, method="POST", route=route,
                  digest=digest, session=session, response_json=_json(response))
    return response


def repair_session(db: Session, *, user_id: int, session_id: str,
                   request: RepairRequest, idempotency_key: str) -> dict:
    route = f"/policy/acquisition/sessions/{session_id}/repair"
    body = request.model_dump(mode="json")
    _authorize(db, user_id, active=False, phase="close_existing")
    _fence(db, user_id)
    digest, replay = _command(db, user_id, idempotency_key, "POST", route, body)
    if replay is not None:
        return replay
    session = _session(db, user_id, session_id)
    episode = _episode(db, user_id, session.episode_id)
    if session.version != request.expected_session_version or episode.version != request.expected_episode_version:
        raise AcquisitionError(409, "ACQUISITION_VERSION_CONFLICT", "周期状态已变化，请刷新后修复")
    contract = _frozen_contract(db, episode)
    snapshot = _snapshot(db, episode, contract)
    proof = _proof(snapshot, contract)
    session.contract_json = _json(contract)
    session.contract_hash = canonical_hash(contract)
    session.status = "active" if session.status != "closed" else "closed"
    session.version += 1
    session.decision_state = "needs_repair" if snapshot.get("stale_sources") else "sufficient" if proof.label is not None else "needs_evidence"
    reusable = None
    if proof.label is not None and not snapshot.get("stale_sources"):
        reusable = _current_certificate(db, session, episode, contract, snapshot, proof, utc_now())
    if reusable is None or reusable.get("effective_status") != "current":
        _invalidate_episode_certificates(db, user_id, episode.id)
        if proof.label is not None and not snapshot.get("stale_sources"):
            _issue_certificate(db, session, episode, contract, snapshot, proof)
    measured_ms = _add_measured_time(db, user_id, request.client_elapsed_ms)
    _new_event(db, session, "session_repaired", "current_evidence_reloaded",
               elapsed_ms=measured_ms)
    response = _decision_response(db, session, episode, contract, snapshot, proof,
                                  action="needs_repair" if snapshot.get("stale_sources") else "sufficient" if proof.label is not None else "defer",
                                  reason="source_not_current" if snapshot.get("stale_sources") else "endpoint_sufficient" if proof.label is not None else "no_eligible_evidence")
    _save_command(db, user_id=user_id, key=idempotency_key, method="POST", route=route,
                  digest=digest, session=session, response_json=_json(response))
    return response


def read_certificate(db: Session, *, user_id: int, certificate_id: str) -> dict:
    _authorize(db, user_id, active=False, phase="read_history")
    cert = db.scalar(select(PolicyDecisionCertificate).where(
        PolicyDecisionCertificate.id == certificate_id,
        PolicyDecisionCertificate.user_id == user_id))
    if cert is None:
        raise AcquisitionError(404, "ACQUISITION_NOT_FOUND", "判断依据不存在")
    session = _session(db, user_id, cert.session_id)
    episode = _episode(db, user_id, cert.episode_id)
    contract = _decode(session.contract_json, {})
    snapshot = _snapshot(db, episode, contract)
    proof = _proof(snapshot, contract)
    view = _current_certificate(db, session, episode, contract, snapshot, proof, utc_now(),
                                certificate_id=cert.id)
    return view or {"certificate_id": cert.id, "status": "stale", "effective_status": "certificate_unavailable"}


def read_history(db: Session, *, user_id: int, episode_id: str, cursor: int | None = None,
                 limit: int = 20) -> dict:
    _authorize(db, user_id, active=False, phase="read_history")
    episode = _episode(db, user_id, episode_id)
    session = db.scalar(select(PolicyAcquisitionSession).where(
        PolicyAcquisitionSession.user_id == user_id,
        PolicyAcquisitionSession.episode_id == episode.id))
    if session is None:
        return {"items": [], "next_cursor": None}
    stmt = select(PolicyAcquisitionEvent).where(
        PolicyAcquisitionEvent.user_id == user_id,
        PolicyAcquisitionEvent.session_id == session.id)
    if cursor is not None:
        stmt = stmt.where(PolicyAcquisitionEvent.sequence < cursor)
    rows = db.scalars(stmt.order_by(PolicyAcquisitionEvent.sequence.desc()).limit(min(50, max(1, limit)) + 1)).all()
    has_more = len(rows) > min(50, max(1, limit))
    selected = rows[:min(50, max(1, limit))]
    return {"session_id": session.id, "items": [{"sequence": row.sequence, "type": row.event_type,
                       "reason_code": row.reason_code, "created_at": row.created_at.isoformat() + "Z",
                       "elapsed_ms": row.elapsed_ms} for row in selected],
            "next_cursor": selected[-1].sequence if has_more and selected else None}


def repair_observation(db: Session, *, user_id: int, episode_id: str,
                       request: ObservationRepairRequest, idempotency_key: str) -> dict:
    """Replace only a reviewed cycle's existing self-reported burden slot."""
    route = f"/policy/episodes/{episode_id}/observation-repairs"
    body = request.model_dump(mode="json")
    _authorize(db, user_id, active=False, phase="close_existing")
    _fence(db, user_id)
    digest, replay = _command(db, user_id, idempotency_key, "POST", route, body)
    if replay is not None:
        return replay
    episode = _episode(db, user_id, episode_id)
    if episode.status not in {"active", "reviewed"} or episode.version != request.expected_episode_version:
        raise AcquisitionError(409, "POLICY_REREVIEW_NOT_READY", "只有进行中或已复查周期可以按原观察槽修复")
    ref = db.scalar(select(PolicyObservationRef).where(
        PolicyObservationRef.id == request.observation_ref_id,
        PolicyObservationRef.user_id == user_id,
        PolicyObservationRef.episode_id == episode.id))
    if ref is None:
        raise AcquisitionError(404, "ACQUISITION_NOT_FOUND", "可修复的观察记录不存在")
    if request.burden_value is not None and ref.source_type != "user_report":
        raise AcquisitionError(422, "EVIDENCE_REPAIR_GRADE_MISMATCH", "只有原本人自报观察可以更正为本人自报数值")
    old_revision = int(getattr(ref, "revision", 1) or 1)
    old_hash = canonical_hash(ref.value_json or "null")
    if old_revision != request.expected_observation_revision or ref.source_revision != request.expected_source_revision or old_hash != request.expected_observation_hash:
        raise AcquisitionError(409, "ACQUISITION_VERSION_CONFLICT", "这条观察已被修改，请刷新后再修复")
    if not db.scalar(select(PolicyEvidenceRevision.id).where(
        PolicyEvidenceRevision.user_id == user_id,
        PolicyEvidenceRevision.observation_ref_id == ref.id,
        PolicyEvidenceRevision.observation_revision == old_revision)):
        db.add(PolicyEvidenceRevision(
            user_id=user_id, observation_ref_id=ref.id, observation_revision=old_revision,
            source_type=ref.source_type, source_id=ref.source_id,
            source_revision=ref.source_revision, metric_version=ref.metric_version,
            endpoint=ref.endpoint, slot=ref.slot, observed_at=ref.observed_at,
            value_json=ref.value_json, value_hash=old_hash, confirmed=ref.confirmed,
            created_at=utc_now()))
    if request.burden_value is not None:
        if request.observed_at is None:
            raise AcquisitionError(422, "EVIDENCE_SOURCE_INVALID", "自报观察时间无效")
        left, right = ((episode.start_at - timedelta(days=30), episode.start_at)
                       if ref.endpoint == "baseline" else (episode.start_at, episode.end_at + timedelta(days=1)))
        observed_at = request.observed_at.astimezone(timezone.utc).replace(tzinfo=None)
        if observed_at > utc_now():
            raise AcquisitionError(422, "EVIDENCE_SOURCE_IN_FUTURE", "不能提前提交尚未发生的观察")
        if not left <= observed_at <= right:
            raise AcquisitionError(422, "EVIDENCE_SOURCE_OUTSIDE_WINDOW", "修复时间必须留在原观察窗口")
        ref.value_json = _json(float(request.burden_value))
        ref.observed_at = observed_at
        ref.confirmed = True
    else:
        source = request.replacement_source
        unit = db.get(PersonalStrategyUnit, episode.unit_id)
        protocol = _decode(episode.protocol_snapshot_json, {})
        metric = str((protocol.get("template") or {}).get("metric_key") or "burden")
        left, right = ((episode.start_at - timedelta(days=30), episode.start_at)
                       if ref.endpoint == "baseline" else (episode.start_at, episode.end_at + timedelta(days=1)))
        from app.services.policy_learning.source_registry import SourceResolutionError, resolve_point
        try:
            resolved = resolve_point(db, user_id, source, metric, (left, right))
        except SourceResolutionError as exc:
            raise AcquisitionError(409 if exc.code.endswith("STALE") else 422, exc.code, exc.message) from exc
        if resolved.metric_version != unit.metric_version:
            raise AcquisitionError(409, "EVIDENCE_METRIC_VERSION_MISMATCH", "来源指标版本与冻结周期不同")
        ref.source_type, ref.source_id, ref.source_revision = resolved.source_type, resolved.source_id, resolved.source_revision
        ref.metric_version, ref.value_json = resolved.metric_version, _json(float(resolved.value))
        ref.observed_at, ref.confirmed = resolved.observed_at, True
    ref.revision = old_revision + 1
    ref.valid = True
    episode.version += 1
    _invalidate_episode_certificates(db, user_id, episode.id)
    if episode.status == "reviewed" and episode.effective_adjudication_revision:
        prior = db.scalar(select(PolicyAdjudication).where(
            PolicyAdjudication.user_id == user_id,
            PolicyAdjudication.episode_id == episode.id,
            PolicyAdjudication.revision == episode.effective_adjudication_revision))
        if prior is not None:
            prior.stale = True
    result = episode_view(db, episode)
    _new_session = db.scalar(select(PolicyAcquisitionSession).where(
        PolicyAcquisitionSession.user_id == user_id,
        PolicyAcquisitionSession.episode_id == episode.id))
    if _new_session:
        _new_session.version += 1
        _new_session.decision_state = "needs_repair"
        measured_ms = _add_measured_time(db, user_id, request.client_elapsed_ms)
        _new_event(db, _new_session, "observation_repaired", "user_confirmed_revision",
                   elapsed_ms=measured_ms,
                   payload={"observation_ref_id": ref.id, "revision": ref.revision})
    else:
        measured_ms = _add_measured_time(db, user_id, request.client_elapsed_ms)
    response = {"episode": result, "observation_revision": ref.revision,
                "requires_rereview": episode.status == "reviewed", "prior_revision_preserved": True}
    _save_command(db, user_id=user_id, key=idempotency_key, method="POST", route=route,
                  digest=digest, session=_new_session, resource_kind="episode",
                  resource_id=episode.id, response_json=_json(response))
    return response


def _session_for_episode(db: Session, user_id: int, episode_id: str) -> PolicyAcquisitionSession:
    session = db.scalar(select(PolicyAcquisitionSession).where(
        PolicyAcquisitionSession.user_id == user_id,
        PolicyAcquisitionSession.episode_id == episode_id))
    if session is None:
        raise AcquisitionError(409, "ACQUISITION_SESSION_REQUIRED", "该周期尚无取证历史")
    return session


def rereview_preview(db: Session, *, user_id: int, episode_id: str,
                     request: RereviewRequest) -> dict:
    from app.services.policy_learning.algorithm import adjudicate
    from app.services.policy_learning.repository import _build_evidence
    _authorize(db, user_id, active=False, phase="close_existing")
    episode = _episode(db, user_id, episode_id)
    current = db.scalar(select(PolicyAdjudication).where(
        PolicyAdjudication.user_id == user_id,
        PolicyAdjudication.episode_id == episode.id,
        PolicyAdjudication.revision == episode.effective_adjudication_revision))
    if (episode.status != "reviewed" or utc_now() < episode.end_at or current is None or not current.stale or
            episode.version != request.expected_episode_version or
            episode.effective_adjudication_revision != request.expected_adjudication_revision):
        raise AcquisitionError(409, "POLICY_REREVIEW_NOT_READY", "周期或复查版本已变化")
    snapshot = _snapshot_for_rereview(db, episode)
    if canonical_hash(snapshot) != request.expected_evidence_hash:
        raise AcquisitionError(409, "ACQUISITION_VERSION_CONFLICT", "观察证据已变化，请重新读取预览")
    verdict = adjudicate(_build_evidence(db, episode, invalidate_stale=False))
    return {"episode_id": episode.id, "episode_version": episode.version,
            "adjudication_revision": episode.effective_adjudication_revision,
            "evidence_hash": canonical_hash(snapshot), "preview": True,
            "execution_label": verdict.execution_label, "support_label": verdict.support_label,
            "availability_label": verdict.availability_label, "conclusion": verdict.conclusion,
            "reasons": list(verdict.reasons), "requires_user_confirmation": True}


def _snapshot_for_rereview(db: Session, episode: PolicyEpisode) -> list[dict]:
    rows = db.scalars(select(PolicyObservationRef).where(
        PolicyObservationRef.user_id == episode.user_id,
        PolicyObservationRef.episode_id == episode.id).order_by(
            PolicyObservationRef.endpoint, PolicyObservationRef.slot)).all()
    return [{"id": row.id, "revision": row.revision, "source_type": row.source_type,
             "source_id": row.source_id, "source_revision": row.source_revision,
             "endpoint": row.endpoint, "slot": row.slot,
             "value_hash": canonical_hash(row.value_json or "null"), "valid": row.valid}
            for row in rows]


def observation_repair_targets(db: Session, *, user_id: int, episode_id: str) -> dict:
    """Return owner-scoped optimistic-concurrency tokens for existing slots only."""
    _authorize(db, user_id, active=False, phase="close_existing")
    episode = _episode(db, user_id, episode_id)
    if episode.status not in {"active", "reviewed"}:
        raise AcquisitionError(409, "POLICY_REREVIEW_NOT_READY", "已停止周期不能修复并重新复查")
    rows = db.scalars(select(PolicyObservationRef).where(
        PolicyObservationRef.user_id == user_id,
        PolicyObservationRef.episode_id == episode.id).order_by(
            PolicyObservationRef.endpoint, PolicyObservationRef.slot)).all()
    return {"episode_id": episode.id, "episode_version": episode.version,
            "items": [{"observation_ref_id": row.id,
                       "observation_revision": int(getattr(row, "revision", 1) or 1),
                       "source_revision": row.source_revision,
                       "observation_hash": canonical_hash(row.value_json or "null"),
                       "endpoint": row.endpoint, "slot": row.slot,
                       "source_type": row.source_type, "source_id": row.source_id,
                       "metric_version": row.metric_version,
                       "observed_at": row.observed_at.isoformat() + "Z",
                       "value": _decode(row.value_json, None), "valid": row.valid,
                       "confirmed": row.confirmed,
                       "can_replace_with_self_report": row.source_type == "user_report"}
                      for row in rows]}


def rereview_context(db: Session, *, user_id: int, episode_id: str) -> dict:
    _authorize(db, user_id, active=False, phase="close_existing")
    episode = _episode(db, user_id, episode_id)
    current = db.scalar(select(PolicyAdjudication).where(
        PolicyAdjudication.user_id == user_id,
        PolicyAdjudication.episode_id == episode.id,
        PolicyAdjudication.revision == episode.effective_adjudication_revision))
    if (episode.status != "reviewed" or utc_now() < episode.end_at or
            current is None or not current.stale):
        raise AcquisitionError(409, "POLICY_REREVIEW_NOT_READY", "当前没有需要重新复查的旧结论")
    evidence = _snapshot_for_rereview(db, episode)
    return {"episode_id": episode.id, "episode_version": episode.version,
            "expected_adjudication_revision": episode.effective_adjudication_revision,
            "expected_evidence_hash": canonical_hash(evidence),
            "prior_conclusion": current.conclusion, "prior_revision_stale": True}


def propose_rereview(db: Session, *, user_id: int, episode_id: str,
                     request: RereviewRequest, idempotency_key: str) -> dict:
    route = f"/policy/episodes/{episode_id}/rereview-proposal"
    body = request.model_dump(mode="json")
    _authorize(db, user_id, active=False, phase="close_existing")
    _fence(db, user_id)
    digest, replay = _command(db, user_id, idempotency_key, "POST", route, body)
    if replay is not None:
        return replay
    preview = rereview_preview(db, user_id=user_id, episode_id=episode_id, request=request)
    from app.services.agent.action_proposals import propose_action, proposal_view
    proposal, _ = propose_action(
        db, user_id=user_id, action_key="policy.episode.rereview",
        arguments={"episode_id": episode_id,
                   "episode_version": request.expected_episode_version,
                   "expected_adjudication_revision": request.expected_adjudication_revision,
                   "expected_evidence_hash": request.expected_evidence_hash},
        title="按修复后的记录重新复查", risk_level="low", requires_confirmation=True,
        user_visible_reason="你更正了原观察记录。确认后会保留旧复查，并按当前证据生成新版本。")
    response = {"approval_required": True, "preview": preview, **proposal_view(proposal)}
    _save_command(db, user_id=user_id, key=idempotency_key, method="POST", route=route,
                  digest=digest, resource_kind="proposal",
                  resource_id=str(response.get("proposal_id") or ""),
                  response_json=_json(response))
    return response


def execute_rereview(db: Session, *, user_id: int, episode_id: str,
                     episode_version: int, expected_adjudication_revision: int,
                     expected_evidence_hash: str) -> dict:
    """Called only by the typed user-confirmed Action executor."""
    from app.services.policy_learning.algorithm import adjudicate
    from app.services.policy_learning.repository import _build_evidence, _evidence_refs_json, _freeze_followup_context, _json as policy_json
    from app.models import PolicyAdjudication, PolicyOutbox
    from uuid import uuid4
    _authorize(db, user_id, active=False, phase="close_existing")
    _fence(db, user_id)
    episode = _episode(db, user_id, episode_id)
    current_adjudication = db.scalar(select(PolicyAdjudication).where(
        PolicyAdjudication.user_id == user_id,
        PolicyAdjudication.episode_id == episode.id,
        PolicyAdjudication.revision == episode.effective_adjudication_revision))
    if (episode.status != "reviewed" or utc_now() < episode.end_at or current_adjudication is None or
            not current_adjudication.stale or episode.version != episode_version or
            episode.effective_adjudication_revision != expected_adjudication_revision):
        raise AcquisitionError(409, "POLICY_REREVIEW_NOT_READY", "周期或复查版本已变化，请重新预览")
    current_snapshot = _snapshot_for_rereview(db, episode)
    if canonical_hash(current_snapshot) != expected_evidence_hash:
        raise AcquisitionError(409, "POLICY_REREVIEW_NOT_READY", "观察证据已变化，请重新预览")
    _freeze_followup_context(db, episode)
    verdict = adjudicate(_build_evidence(db, episode))
    revision = episode.review_revision + 1
    episode.review_revision = revision
    episode.effective_adjudication_revision = revision
    episode.version += 1
    db.add(PolicyAdjudication(
        id=uuid4().hex, user_id=user_id, episode_id=episode.id, revision=revision,
        learning_epoch=episode.learning_epoch, execution_label=verdict.execution_label,
        support_label=verdict.support_label, availability_label=verdict.availability_label,
        conclusion=verdict.conclusion, reasons_json=policy_json(verdict.reasons),
        evidence_refs_json=_evidence_refs_json(db, episode),
        source_hash=hashlib.sha256(policy_json(verdict.__dict__).encode()).hexdigest(),
        algorithm_version="egpl-v1.0.0", gate_version="evidence-gate-v1.0.0"))
    db.add(PolicyOutbox(event_id=uuid4().hex, user_id=user_id,
                        event_type="policy.adjudicated", ref_id=episode.id,
                        revision=revision,
                        payload=policy_json({"episode_id": episode.id, "revision": revision,
                                             "rereview": True}), status="pending"))
    session = db.scalar(select(PolicyAcquisitionSession).where(
        PolicyAcquisitionSession.user_id == user_id,
        PolicyAcquisitionSession.episode_id == episode.id))
    if session:
        session.version += 1
        session.decision_state = "sufficient"
        _new_event(db, session, "episode_rereviewed", "user_confirmed_new_revision",
                   payload={"adjudication_revision": revision})
    db.flush()
    return {"episode": episode_view(db, episode),
            "adjudication": {"revision": revision,
                             "execution_label": verdict.execution_label,
                             "support_label": verdict.support_label,
                             "availability_label": verdict.availability_label,
                             "conclusion": verdict.conclusion,
                             "reasons": list(verdict.reasons)}}
