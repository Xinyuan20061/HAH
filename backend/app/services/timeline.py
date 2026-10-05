from app.core.time import business_today
from app.core.time import utc_now, utc_iso
import json
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import (
    HealthTimelineEvent,
    DietRecord,
    ExerciseRecord,
    HealthCheckIn,
    HealthGoalSetting,
    PlanTaskState,
)
from app.services.health_data import daily_facts


def add_event(
    db: Session,
    user_id: int,
    event_type: str,
    payload: dict,
    source="manual",
    ref_type="",
    ref_id=None,
    occurred_at=None,
):
    e = HealthTimelineEvent(
        user_id=user_id,
        event_type=event_type,
        occurred_at=occurred_at or utc_now(),
        source=source,
        ref_type=ref_type,
        ref_id=ref_id,
        payload_json=json.dumps(payload, ensure_ascii=False, default=str),
    )
    db.add(e)
    return e


def add_state_event(
    db: Session,
    user_id: int,
    event_type: str,
    payload: dict,
    source="manual",
    ref_type="",
    ref_id=None,
    occurred_at=None,
):
    """State-toggle event (done/reopened/checkin …) is idempotent per business
    record: re-submitting the same state adds nothing, and flipping the state
    rewrites the single latest event instead of piling up duplicates
    (spec §13.5 — editing should rewrite the same event, not append).
    """
    if ref_type and ref_id is not None:
        existing = db.scalar(
            select(HealthTimelineEvent)
            .where(
                HealthTimelineEvent.user_id == user_id,
                HealthTimelineEvent.ref_type == ref_type,
                HealthTimelineEvent.ref_id == ref_id,
            )
            .order_by(HealthTimelineEvent.occurred_at.desc())
        )
        if existing is not None:
            if existing.event_type == event_type:
                return existing  # same state re-submitted: nothing new to record
            existing.event_type = event_type
            existing.source = source
            existing.payload_json = json.dumps(payload, ensure_ascii=False, default=str)
            existing.occurred_at = occurred_at or utc_now()
            return existing
    return add_event(
        db, user_id, event_type, payload, source=source,
        ref_type=ref_type, ref_id=ref_id, occurred_at=occurred_at,
    )


def unified_timeline(db: Session, user_id: int, days: int = 7):
    since = utc_now() - timedelta(days=days)
    rows = db.scalars(
        select(HealthTimelineEvent)
        .where(
            HealthTimelineEvent.user_id == user_id,
            HealthTimelineEvent.occurred_at >= since,
        )
        .order_by(HealthTimelineEvent.occurred_at.desc())
        .limit(500)
    ).all()
    items = []
    for r in rows:
        try:
            payload = json.loads(r.payload_json or "{}")
        except Exception:
            payload = {}
        items.append(
            {
                "id": r.id,
                "type": r.event_type,
                "occurred_at": utc_iso(r.occurred_at),
                "source": r.source,
                "ref_type": r.ref_type,
                "ref_id": r.ref_id,
                "payload": payload,
            }
        )
    return items


def timeline_daily(db: Session, user_id: int, days: int = 7):
    end = business_today()
    start = end - timedelta(days=days - 1)
    return daily_facts(db, user_id, start, end)


def backfill_legacy_events(db: Session, user_id: int):
    """Idempotently create timeline events for records created before Timeline existed."""
    existing = {
        (x.event_type, x.ref_type, x.ref_id)
        for x in db.scalars(
            select(HealthTimelineEvent).where(
                HealthTimelineEvent.user_id == user_id,
                HealthTimelineEvent.ref_id.is_not(None),
            )
        ).all()
    }
    count = 0
    diets = db.scalars(select(DietRecord).where(DietRecord.user_id == user_id)).all()
    for x in diets:
        key = ("diet", "diet", x.id)
        if key not in existing:
            add_event(
                db,
                user_id,
                "diet",
                {
                    "name": x.name,
                    "meal_type": x.meal_type,
                    "calories": x.calories,
                    "protein": x.protein,
                    "carbs": x.carbs,
                    "fat": x.fat,
                    "source": x.source,
                },
                x.source,
                "diet",
                x.id,
                x.recorded_at,
            )
            count += 1
    exercises = db.scalars(
        select(ExerciseRecord).where(ExerciseRecord.user_id == user_id)
    ).all()
    for x in exercises:
        key = ("exercise", "exercise", x.id)
        if key not in existing:
            add_event(
                db,
                user_id,
                "exercise",
                {
                    "name": x.name,
                    "duration_min": x.duration_min,
                    "calories_burned": x.calories_burned,
                    "intensity": x.intensity,
                },
                "legacy",
                "exercise",
                x.id,
                x.recorded_at,
            )
            count += 1
    checks = db.scalars(
        select(HealthCheckIn).where(HealthCheckIn.user_id == user_id)
    ).all()
    for x in checks:
        key = ("checkin", "checkin", x.id)
        if key not in existing:
            try:
                occurred = datetime.fromisoformat(x.record_date + "T12:00:00")
            except Exception:
                occurred = x.created_at
            add_event(
                db,
                user_id,
                "checkin",
                {
                    "water_ml": x.water_ml,
                    "sleep_hours": x.sleep_hours,
                    "weight_kg": x.weight_kg,
                    "steps": x.steps,
                    "mood": x.mood,
                },
                "legacy",
                "checkin",
                x.id,
                occurred,
            )
            count += 1
    db.commit()
    return count
