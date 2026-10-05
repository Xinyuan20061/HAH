"""Bounded, no-new-prompts maintenance for acquisition sessions."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import (
    PolicyAcquisitionQuestion, PolicyAcquisitionSession,
    PolicyDecisionCertificate, PolicyEpisode,
)


def expire_issued_questions(db: Session, *, now: datetime | None = None,
                            limit: int = 100) -> dict:
    """Reconcile a bounded batch of issued questions and live certificates.

    Caller owns the transaction. Every selected row is re-read after acquiring
    its owner's fence so an answer racing the sweeper wins or safely retries.
    The worker only obsoletes/times out prompts and stales certificates; it
    never creates prompts, restores a refusal, or increases a budget.
    """
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("ACQUISITION_MAINTENANCE_LIMIT_INVALID")
    now = now or utc_now()
    ids = list(db.scalars(select(PolicyAcquisitionQuestion.id).join(
        PolicyAcquisitionSession,
        PolicyAcquisitionSession.id == PolicyAcquisitionQuestion.session_id,
    ).join(PolicyEpisode, PolicyEpisode.id == PolicyAcquisitionSession.episode_id).where(
        PolicyAcquisitionQuestion.status == "issued",
        or_(PolicyAcquisitionQuestion.expires_at <= now,
            PolicyAcquisitionQuestion.expected_episode_version != PolicyEpisode.version),
    ).order_by(PolicyAcquisitionQuestion.expires_at,
               PolicyAcquisitionQuestion.id).limit(limit)))
    from app.services.policy_learning.acquisition.service import (
        _current_certificate, _decode, _fact_snapshot_hash, _fence, _json,
        _new_event, _proof, _snapshot,
    )
    from app.services.policy_learning.acquisition.knowledge_contract import current_knowledge_contract

    timed_out = 0
    obsoleted = 0
    skipped = 0
    for question_id in ids:
        candidate = db.get(PolicyAcquisitionQuestion, question_id)
        if candidate is None:
            skipped += 1
            continue
        _fence(db, candidate.user_id)
        question = db.scalar(select(PolicyAcquisitionQuestion).where(
            PolicyAcquisitionQuestion.id == question_id,
            PolicyAcquisitionQuestion.user_id == candidate.user_id,
        ).with_for_update())
        if question is None or question.status != "issued":
            skipped += 1
            continue
        session = db.scalar(select(PolicyAcquisitionSession).where(
            PolicyAcquisitionSession.id == question.session_id,
            PolicyAcquisitionSession.user_id == question.user_id,
        ).with_for_update())
        if session is None:
            skipped += 1
            continue
        episode = db.scalar(select(PolicyEpisode).where(
            PolicyEpisode.id == session.episode_id,
            PolicyEpisode.user_id == session.user_id,
        ))
        if episode is None:
            skipped += 1
            continue
        contract = _decode(session.contract_json, {})
        snapshot = _snapshot(db, episode, contract)
        knowledge = current_knowledge_contract(db, contract.get("template_id", ""))
        source_stale = (
            question.expected_episode_version != episode.version or
            question.expected_snapshot_hash != _fact_snapshot_hash(snapshot) or
            knowledge["contract_hash"] != contract.get("knowledge_contract_hash")
        )
        if source_stale:
            question.status = "obsolete"
            question.answered_at = now
            session.active_question_id = None if session.active_question_id == question.id else session.active_question_id
            session.decision_state = "needs_repair"
            session.version += 1
            _new_event(db, session, "question_obsoleted", "evidence_changed", question.id)
            obsoleted += 1
        elif question.expires_at <= now:
            question.status = "timed_out"
            question.answered_at = now
            question.answer_json = _json({"response": "no_response", "server_timeout": True})
            if session.active_question_id == question.id:
                session.active_question_id = None
                if session.status == "active":
                    session.decision_state = "needs_evidence"
                session.version += 1
            _new_event(db, session, "question_timed_out", "server_timeout", question.id)
            timed_out += 1
        else:
            skipped += 1

    # A synchronous writer normally stales certificates in its own
    # transaction. This bounded sweep is the repair path for missed hooks,
    # upgraded rules/knowledge, expiration, or old rows imported from backup.
    certificate_ids = list(db.scalars(select(PolicyDecisionCertificate.id).where(
        PolicyDecisionCertificate.status == "valid",
    ).order_by(PolicyDecisionCertificate.expires_at,
               PolicyDecisionCertificate.id).limit(limit)))
    certificates_staled = 0
    for certificate_id in certificate_ids:
        candidate = db.get(PolicyDecisionCertificate, certificate_id)
        if candidate is None:
            skipped += 1
            continue
        _fence(db, candidate.user_id)
        cert = db.scalar(select(PolicyDecisionCertificate).where(
            PolicyDecisionCertificate.id == certificate_id,
            PolicyDecisionCertificate.user_id == candidate.user_id,
        ).with_for_update())
        if cert is None or cert.status != "valid":
            skipped += 1
            continue
        session = db.scalar(select(PolicyAcquisitionSession).where(
            PolicyAcquisitionSession.id == cert.session_id,
            PolicyAcquisitionSession.user_id == cert.user_id,
        ).with_for_update())
        episode = db.scalar(select(PolicyEpisode).where(
            PolicyEpisode.id == cert.episode_id,
            PolicyEpisode.user_id == cert.user_id,
        ))
        if session is None or episode is None:
            cert.status = "stale"
            certificates_staled += 1
            continue
        contract = _decode(session.contract_json, {})
        snapshot = _snapshot(db, episode, contract)
        proof = _proof(snapshot, contract)
        current = _current_certificate(db, session, episode, contract,
                                       snapshot, proof, now,
                                       certificate_id=cert.id)
        if current and current.get("effective_status") == "current":
            continue
        reason = (current or {}).get("effective_status") or "certificate_unavailable"
        cert.status = "stale"
        if session.status != "closed":
            session.decision_state = "needs_repair"
            session.version += 1
            question = db.get(PolicyAcquisitionQuestion, session.active_question_id) if session.active_question_id else None
            if question is not None and question.status == "issued":
                question.status = "obsolete"
                question.answered_at = now
                session.active_question_id = None
            _new_event(db, session, "certificate_compensated_stale",
                       str(reason)[:80], question.id if question else None,
                       payload={"certificate_id": cert.id})
        certificates_staled += 1
    db.flush()
    return {"timed_out": timed_out, "obsoleted": obsoleted,
            "certificates_staled": certificates_staled,
            "raced_or_skipped": skipped, "limit": limit}
