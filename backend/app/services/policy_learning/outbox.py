"""Reliable policy adjudication outbox consumer and source invalidation."""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import PolicyAdjudication, PolicyDomainGeneration, PolicyEpisode, PolicyObservationRef, PolicyOutbox
from app.harness.plugins import authorize_capability, record_capability_api_access

from .repository import PolicyError, rebuild_beliefs


def _advance_processed_generation(db: Session, user_id: int, source_type: str) -> int | None:
    generation = db.scalar(select(PolicyDomainGeneration).where(
        PolicyDomainGeneration.user_id == user_id,
        PolicyDomainGeneration.domain == source_type,
    ))
    if generation is None:
        return None
    events = db.scalars(select(PolicyOutbox).where(
        PolicyOutbox.user_id == user_id,
        PolicyOutbox.event_type == "policy.source_invalidated",
        PolicyOutbox.revision > generation.processed_generation,
        PolicyOutbox.revision <= generation.source_generation,
    ).order_by(PolicyOutbox.revision)).all()
    cursor = generation.processed_generation
    for pending in events:
        if pending.revision != cursor + 1 or pending.status != "processed":
            break
        try:
            payload = json.loads(pending.payload or "{}")
        except (TypeError, ValueError):
            break
        if payload.get("source_type") != source_type:
            break
        cursor = pending.revision
    generation.processed_generation = cursor
    return cursor


def consume_policy_event(db: Session, event_id: str, *, claimed: bool = False) -> dict:
    event = db.get(PolicyOutbox, event_id)
    if event is None:
        return {"processed": False, "reason": "not_found"}
    if event.status == "processed":
        return {"processed": True, "idempotent": True, "event_id": event_id}
    if event.status == "processing" and not claimed:
        return {"processed": False, "reason": "claimed", "event_id": event_id}
    try:
        maintenance_phase = "delete" if event.event_type == "policy.source_invalidated" else "close_existing"
        capability = authorize_capability(
            db, event.user_id, "personal_policy", "system_maintenance", (), maintenance_phase,
        )
        record_capability_api_access(
            db, event.user_id, "personal_policy", route="outbox_maintenance",
            allowed=bool(capability.get("allowed")), reason=capability.get("reason"),
        )
        if not capability.get("allowed"):
            event.status = "pending"
            event.attempts += 1
            event.next_retry_at = utc_now() + timedelta(
                seconds=min(300, 2 ** min(event.attempts, 8))
            )
            db.flush()
            return {"processed": False, "reason": "capability_unavailable", "event_id": event_id}
        payload = json.loads(event.payload or "{}")
        if event.event_type == "policy.source_invalidated":
            strategy_contexts = payload.get("strategy_contexts") or []
            for pair in strategy_contexts:
                rebuild_beliefs(
                    db,
                    event.user_id,
                    pair["strategy_id"],
                    pair["context_key"],
                )
            event.status, event.attempts = "processed", event.attempts + 1
            db.flush()
            processed_generation = _advance_processed_generation(
                db, event.user_id, payload.get("source_type") or ""
            )
            db.flush()
            return {
                "processed": True,
                "event_id": event_id,
                "invalidated": True,
                "beliefs_rebuilt": len(strategy_contexts),
                "processed_generation": processed_generation,
            }
        episode = db.get(PolicyEpisode, payload.get("episode_id") or event.ref_id)
        if episode is None or episode.user_id != event.user_id:
            event.status = "failed"
            event.attempts += 1
            db.flush()
            return {"processed": False, "reason": "episode_not_found"}
        unit = episode.unit_id
        from app.models import PersonalStrategyUnit
        strategy = db.get(PersonalStrategyUnit, unit)
        if strategy is None:
            raise PolicyError("POLICY_NOT_FOUND", "策略协议不存在")
        rebuild_beliefs(db, event.user_id, strategy.strategy_id, strategy.context_key)
        event.status, event.attempts = "processed", event.attempts + 1
        db.flush()
        return {"processed": True, "event_id": event_id, "episode_id": episode.id}
    except Exception:
        event.status, event.attempts = "pending", event.attempts + 1
        event.next_retry_at = utc_now() + timedelta(seconds=min(300, 2 ** min(event.attempts, 8)))
        db.flush()
        raise


def process_pending_policy_events(db: Session, limit: int = 20) -> dict:
    now = utc_now()
    rows = db.scalars(select(PolicyOutbox).where(PolicyOutbox.status == "pending", (PolicyOutbox.next_retry_at.is_(None) | (PolicyOutbox.next_retry_at <= now))).order_by(PolicyOutbox.created_at).limit(max(1, min(limit, 100))).with_for_update(skip_locked=True)).all()
    processed = failed = 0
    for row in rows:
        try:
            # Keep each event's belief rebuild atomic. A single malformed
            # event or transient database error must not poison the shared
            # batch transaction and strand the remaining claimed work.
            with db.begin_nested():
                row.status = "processing"
                db.flush()
                result = consume_policy_event(db, row.event_id, claimed=True)
            if result.get("processed"):
                processed += 1
            else:
                failed += 1
        except Exception:
            row.status = "pending"
            row.attempts += 1
            row.next_retry_at = utc_now() + timedelta(
                seconds=min(300, 2 ** min(row.attempts, 8))
            )
            db.flush()
            failed += 1
    db.commit()
    return {"claimed": len(rows), "processed": processed, "failed": failed}


def invalidate_source(
    db: Session,
    user_id: int,
    source_type: str,
    source_id: str,
    *,
    source_revision: int | None = None,
    deleted: bool = False,
) -> dict:
    ref_query = select(PolicyObservationRef).where(
        PolicyObservationRef.user_id == user_id,
        PolicyObservationRef.source_type == source_type,
        PolicyObservationRef.source_id == source_id,
        PolicyObservationRef.valid.is_(True),
    )
    if not deleted and source_revision is not None:
        ref_query = ref_query.where(PolicyObservationRef.source_revision < source_revision)
    refs = db.scalars(ref_query).all()
    affected = {row.episode_id for row in refs}
    from app.services.policy_learning.acquisition.service import invalidate_source as invalidate_acquisition_source
    acquisition_certificates = invalidate_acquisition_source(
        db, user_id, source_type, source_id,
        source_revision=source_revision if not deleted else None,
    )
    if not refs and not acquisition_certificates:
        return {"affected_episode_ids": [], "source_generation": None,
                "acquisition_certificates_invalidated": 0, "deleted": deleted}
    for ref in refs:
        ref.valid = False
    generation = db.scalar(select(PolicyDomainGeneration).where(PolicyDomainGeneration.user_id == user_id, PolicyDomainGeneration.domain == source_type))
    if generation is None:
        generation = PolicyDomainGeneration(user_id=user_id, domain=source_type, source_generation=0, processed_generation=0)
        db.add(generation)
    generation.source_generation += 1
    if affected:
        rows = db.scalars(select(PolicyAdjudication).where(PolicyAdjudication.user_id == user_id, PolicyAdjudication.episode_id.in_(affected), PolicyAdjudication.valid.is_(True))).all()
        for row in rows:
            row.stale = True
    from app.models import PersonalStrategyUnit
    units = db.scalars(select(PersonalStrategyUnit).join(PolicyEpisode, PolicyEpisode.unit_id == PersonalStrategyUnit.id).where(PersonalStrategyUnit.user_id == user_id, PolicyEpisode.id.in_(affected))).all() if affected else []
    strategy_contexts = sorted(
        { (unit.strategy_id, unit.context_key) for unit in units }
    )
    db.add(PolicyOutbox(
        event_id=__import__("uuid").uuid4().hex,
        user_id=user_id,
        event_type="policy.source_invalidated",
        ref_id=f"{source_type}:{source_id}",
        revision=generation.source_generation,
        payload=json.dumps({
            "source_type": source_type,
            "source_id": source_id,
            "source_revision": source_revision,
            "deleted": deleted,
            "strategy_contexts": [
                {"strategy_id": strategy_id, "context_key": context_key}
                for strategy_id, context_key in strategy_contexts
            ],
        }, ensure_ascii=False),
        status="pending",
    ))
    db.flush()
    return {"affected_episode_ids": sorted(affected), "source_generation": generation.source_generation,
            "acquisition_certificates_invalidated": acquisition_certificates, "deleted": deleted}
