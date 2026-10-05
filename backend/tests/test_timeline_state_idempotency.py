"""Timeline state events are idempotent per business record (spec §13.5):
re-submitting the same state adds nothing; flipping the state rewrites the
single latest event instead of piling up duplicates."""

from uuid import uuid4
from app.services.timeline import add_state_event, add_event
from app.models import User, HealthTimelineEvent
from sqlalchemy import select


def _events(db, user_id):
    return db.scalars(
        select(HealthTimelineEvent)
        .where(HealthTimelineEvent.user_id == user_id)
        .order_by(HealthTimelineEvent.id)
    ).all()


def _new_user(db):
    user = User(openid="timeline-" + uuid4().hex)
    db.add(user)
    db.commit()
    return user.id


def test_state_event_same_state_is_idempotent(db):
    uid = _new_user(db)
    add_state_event(
        db, uid, "daily_plan_completed", {"task_key": "k", "date": "2026-10-05"},
        source="user", ref_type="plan_task", ref_id=101,
    )
    add_state_event(
        db, uid, "daily_plan_completed", {"task_key": "k", "date": "2026-10-05"},
        source="user", ref_type="plan_task", ref_id=101,
    )
    rows = _events(db, uid)
    assert len(rows) == 1, "重复提交相同状态不得新增事件"
    assert rows[0].event_type == "daily_plan_completed"


def test_state_event_flip_rewrites_the_same_row(db):
    uid = _new_user(db)
    add_state_event(
        db, uid, "daily_plan_completed", {"task_key": "k", "date": "2026-10-05"},
        source="user", ref_type="plan_task", ref_id=202,
    )
    add_state_event(
        db, uid, "daily_plan_reopened", {"task_key": "k", "date": "2026-10-05"},
        source="user", ref_type="plan_task", ref_id=202,
    )
    add_state_event(
        db, uid, "daily_plan_completed", {"task_key": "k", "date": "2026-10-05"},
        source="user", ref_type="plan_task", ref_id=202,
    )
    rows = _events(db, uid)
    assert len(rows) == 1, "状态切换必须改写同一条事件，不得堆积"
    assert rows[0].event_type == "daily_plan_completed"


def test_state_event_different_refs_do_not_collide(db):
    uid = _new_user(db)
    add_state_event(
        db, uid, "daily_plan_completed", {}, source="user",
        ref_type="plan_task", ref_id=301,
    )
    add_state_event(
        db, uid, "daily_plan_completed", {}, source="user",
        ref_type="plan_task", ref_id=302,
    )
    assert len(_events(db, uid)) == 2


def test_plain_add_event_still_appends(db):
    uid = _new_user(db)
    add_event(db, uid, "checkin", {"weight_kg": 60}, ref_type="checkin", ref_id=401)
    add_event(db, uid, "checkin", {"weight_kg": 61}, ref_type="checkin", ref_id=401)
    assert len(_events(db, uid)) == 2, "非状态类事件保持追加语义"
