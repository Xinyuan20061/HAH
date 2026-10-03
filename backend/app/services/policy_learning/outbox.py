"""Reliable policy adjudication outbox consumer and source invalidation."""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import PolicyAdjudication, PolicyDomainGeneration, PolicyEpisode, PolicyObservationRef, PolicyOutbox

from .repository import PolicyError, rebuild_beliefs


def consume_policy_event(db: Session, event_id: str, *, claimed: bool = False) -> dict:
    event = db.get(PolicyOutbox, event_id)
    if event is None:
        return {"processed": False, "reason": "not_found"}
    if event.status == "processed":
        return {"processed": True, "idempotent": True, "event_id": event_id}
    if event.status == "processing" and not claimed:
        return {"processed": False, "reason": "claimed", "event_id": event_id}
    try:
        payload = json.loads(event.payload or "{}")
        if event.event_type == "policy.source_invalidated":
            strategy_ids = set(payload.get("strategy_ids") or [])
            contexts = set(payload.get("context_keys") or [])
            from app.models import PersonalStrategyUnit
            units = db.scalars(select(PersonalStrategyUnit).where(PersonalStrategyUnit.user_id == event.user_id, PersonalStrategyUnit.strategy_id.in_(strategy_ids) if strategy_ids else True, PersonalStrategyUnit.context_key.in_(contexts) if contexts else True)).all()
            for unit in units:
                rebuild_beliefs(db, event.user_id, unit.strategy_id, unit.context_key)
            event.status, event.attempts = "processed", event.attempts + 1
            db.flush()
            return {"processed": True, "event_id": event_id, "invalidated": True}
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
            row.status = "processing"
            db.flush()
            consume_policy_event(db, row.event_id, claimed=True); processed += 1
        except Exception:
            row.status = "pending"
            failed += 1
    db.commit()
    return {"claimed": len(rows), "processed": processed, "failed": failed}


def invalidate_source(db: Session, user_id: int, source_type: str, source_id: str, *, deleted: bool = False) -> dict:
    refs = db.scalars(select(PolicyObservationRef).where(PolicyObservationRef.user_id == user_id, PolicyObservationRef.source_type == source_type, PolicyObservationRef.source_id == source_id, PolicyObservationRef.valid.is_(True))).all()
    affected = {row.episode_id for row in refs}
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
    db.add(PolicyOutbox(event_id=__import__("uuid").uuid4().hex, user_id=user_id, event_type="policy.source_invalidated", ref_id=f"{source_type}:{source_id}", revision=generation.source_generation, payload=json.dumps({"source_type": source_type, "source_id": source_id, "deleted": deleted, "strategy_ids": sorted({u.strategy_id for u in units}), "context_keys": sorted({u.context_key for u in units})}, ensure_ascii=False), status="pending"))
    db.flush()
    return {"affected_episode_ids": sorted(affected), "source_generation": generation.source_generation, "deleted": deleted}
