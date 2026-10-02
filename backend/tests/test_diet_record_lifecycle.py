"""WP0 red tests — FOOD-01/02/03, API-03 (spec §5.3/§5.4/§6).

Confirmed defects covered here:

* FOOD-02: ``/diet/records`` had POST/GET/DELETE only — no detail, no PATCH, so
  the product copy "直接编辑饮食记录" pointed at an endpoint that did not exist.
* API-03: the list was hard-capped at 50 rows with no cursor, filter or detail.
* FOOD-03: ``/vision/food-analysis/{id}/finalize`` accepted ``meal_type=other``
  silently and returned only a record id, so the client never knew what landed.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import create_access_token
from app.models import (
    AgentActionAudit,
    DietRecord,
    EvaluationEvent,
    FoodAnalysisSession,
    HealthTimelineEvent,
    User,
)

RECORDS = "/api/v1/diet/records"


@pytest.fixture
def db(migrated_engine):
    """A session bound to the migrated test database (never the dev database)."""
    with Session(migrated_engine) as session:
        yield session


def _create(api, **overrides):
    body = {
        "name": "鸡胸肉蔬菜饭",
        "meal_type": "lunch",
        "calories": 520,
        "protein": 42,
        "carbs": 58,
        "fat": 12,
        "fiber": 8,
        "portion": "1 盘",
        "source": "manual",
    }
    body.update(overrides)
    res = api.post(RECORDS, json=body)
    assert res.status_code in (200, 201), res.text
    return res.json()


def test_diet_record_detail_returns_version(api):
    """FOOD-02/API-03: a single record is readable and carries its version."""
    created = _create(api)
    assert created["version"] == 1
    res = api.get(f"{RECORDS}/{created['id']}")
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == created["id"]
    assert body["version"] == 1
    assert body["meal_type"] == "lunch"
    assert body["source_label"] == "手动记录"


def test_diet_record_detail_is_scoped_to_the_owner(api, migrated_engine):
    """Cross-user reads must answer 404, never 403 (no resource enumeration)."""
    created = _create(api)
    owner_header = api.headers["Authorization"]
    with Session(migrated_engine) as db:
        other = User(openid="other-" + uuid4().hex)
        db.add(other)
        db.commit()
        token = create_access_token(str(other.id))
    api.headers["Authorization"] = "Bearer " + token
    try:
        assert api.get(f"{RECORDS}/{created['id']}").status_code == 404
        assert (
            api.patch(
                f"{RECORDS}/{created['id']}", json={"version": 1, "calories": 1}
            ).status_code
            == 404
        )
        assert api.delete(f"{RECORDS}/{created['id']}").status_code == 404
    finally:
        api.headers["Authorization"] = owner_header


def test_diet_record_patch_bumps_version(api):
    """API-03/§5.4: PATCH requires the current version and increments it."""
    created = _create(api)
    res = api.patch(
        f"{RECORDS}/{created['id']}",
        json={"version": 1, "calories": 390, "portion": "半盘"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["version"] == 2
    assert body["calories"] == 390
    assert body["portion"] == "半盘"
    assert body["source"] == created["source"]


def test_diet_record_patch_version_conflict(api):
    """§5.4: a stale version must answer 409 and never silently overwrite."""
    created = _create(api)
    first = api.patch(
        f"{RECORDS}/{created['id']}", json={"version": 1, "calories": 400}
    )
    assert first.status_code == 200
    stale = api.patch(
        f"{RECORDS}/{created['id']}", json={"version": 1, "calories": 100}
    )
    assert stale.status_code == 409
    error = stale.json()["error"]
    assert error["code"] == "DIET_RECORD_VERSION_CONFLICT"
    assert error["details"]["current_version"] == 2
    assert api.get(f"{RECORDS}/{created['id']}").json()["calories"] == 400


def test_diet_record_patch_updates_timeline_instead_of_appending(api, db):
    """§6.3.3: the edit rewrites the existing timeline row, no duplicate event."""
    created = _create(api)
    api.patch(
        f"{RECORDS}/{created['id']}", json={"version": 1, "calories": 111}
    )
    rows = db.scalars(
        select(HealthTimelineEvent).where(
            HealthTimelineEvent.user_id == api.user_id,
            HealthTimelineEvent.ref_type == "diet",
            HealthTimelineEvent.ref_id == created["id"],
            HealthTimelineEvent.event_type == "diet",
        )
    ).all()
    assert len(rows) == 1, f"时间线出现重复事件: {len(rows)}"
    assert json.loads(rows[0].payload_json)["calories"] == 111


def test_diet_record_patch_records_field_names_only(api, db):
    """§6.3.3: the evaluation event stores changed field names, not content."""
    created = _create(api)
    api.patch(
        f"{RECORDS}/{created['id']}",
        json={"version": 1, "name": "完全不同的名字", "calories": 222},
    )
    events = db.scalars(
        select(EvaluationEvent).where(
            EvaluationEvent.user_id == api.user_id,
            EvaluationEvent.metric_name == "diet_record_updated",
        )
    ).all()
    assert events, "缺少 diet_record_updated 评测事件"
    meta = json.loads(events[-1].meta_json)
    assert set(meta["changed_fields"]) >= {"name", "calories"}
    assert "完全不同的名字" not in events[-1].meta_json


def test_diet_items_are_validated_and_limited(api):
    """§6.2: items_json goes through the FoodItem schema, max 12 items."""
    items = [
        {"name": "鸡胸肉", "weight_g": 150, "calories": 250, "protein": 40},
        {"name": "米饭", "weight_g": 200, "calories": 260, "carbs": 56},
    ]
    created = _create(api, items=items)
    assert created["items"][0]["name"] == "鸡胸肉"
    assert (
        api.post(
            RECORDS,
            json={
                "name": "超量",
                "calories": 100,
                "items": [{"name": f"项{i}", "calories": 10} for i in range(13)],
            },
        ).status_code
        == 422
    )
    assert (
        api.post(
            RECORDS,
            json={
                "name": "未知字段",
                "calories": 100,
                "items": [{"name": "饭", "calories": 10, "invented": True}],
            },
        ).status_code
        == 422
    )


def test_diet_meal_type_is_whitelisted(api):
    """§6.2: meal_type only accepts the five frozen values."""
    assert (
        api.post(
            RECORDS,
            json={"name": "宵夜", "meal_type": "midnight", "calories": 100},
        ).status_code
        == 422
    )


def test_diet_list_supports_cursor_and_filters(api):
    """API-03: cursor pagination plus date and meal filters."""
    ids = [
        _create(api, name=f"餐{i}", meal_type="lunch" if i % 2 else "dinner")["id"]
        for i in range(5)
    ]
    listed = api.get(RECORDS, params={"limit": 2})
    assert listed.status_code == 200
    body = listed.json()
    assert set(body) == {"items", "next_cursor", "has_more"}
    assert len(body["items"]) == 2
    assert body["has_more"] is True
    assert body["next_cursor"]

    page2 = api.get(
        RECORDS, params={"limit": 2, "cursor": body["next_cursor"]}
    ).json()
    page1_ids = {item["id"] for item in body["items"]}
    page2_ids = {item["id"] for item in page2["items"]}
    assert page1_ids.isdisjoint(page2_ids), "游标分页出现重复记录"

    filtered = api.get(RECORDS, params={"meal_type": "lunch", "limit": 50}).json()
    assert {item["meal_type"] for item in filtered["items"]} == {"lunch"}
    assert {item["id"] for item in filtered["items"]} <= set(ids)

    assert api.get(RECORDS, params={"limit": 51}).status_code == 422
    assert api.get(RECORDS, params={"cursor": "not-a-cursor"}).status_code == 422
    assert api.get(RECORDS, params={"meal_type": "brunch"}).status_code == 422
    assert api.get(RECORDS, params={"date_from": "02/10/2026"}).status_code == 422


def test_diet_list_default_limit_is_20(api):
    """§5.3: default limit 20, ordered by recorded_at DESC, id DESC."""
    for i in range(25):
        _create(api, name=f"批量{i}")
    body = api.get(RECORDS).json()
    assert len(body["items"]) == 20
    assert body["has_more"] is True
    ids = [item["id"] for item in body["items"]]
    assert ids == sorted(ids, reverse=True)


def test_diet_delete_removes_timeline_and_marks_session(api, db):
    """§6.3.4: delete clears the timeline row and blocks a later finalize."""
    created = _create(api)
    session = FoodAnalysisSession(
        user_id=api.user_id, status="finalized", finalized_record_id=created["id"]
    )
    db.add(session)
    db.commit()
    session_id = session.id

    assert api.delete(f"{RECORDS}/{created['id']}").status_code == 200

    rows = db.scalars(
        select(HealthTimelineEvent).where(
            HealthTimelineEvent.user_id == api.user_id,
            HealthTimelineEvent.ref_type == "diet",
            HealthTimelineEvent.ref_id == created["id"],
            HealthTimelineEvent.event_type == "diet",
        )
    ).all()
    assert rows == [], "删除后仍残留饮食时间线事件"
    assert db.get(DietRecord, created["id"]) is None
    db.expire_all()
    marked = db.get(FoodAnalysisSession, session_id)
    assert marked.status == "record_deleted"
    assert marked.finalized_record_id is None


def test_diet_delete_is_recorded_in_business_audit(api, db):
    """§6.3.4: the delete lands in the unified business audit."""
    created = _create(api)
    api.delete(f"{RECORDS}/{created['id']}")
    audits = db.scalars(
        select(AgentActionAudit).where(
            AgentActionAudit.user_id == api.user_id,
            AgentActionAudit.action_key == "diet.record.delete",
        )
    ).all()
    assert audits, "删除操作未写入业务审计"
    assert audits[-1].status == "executed"
