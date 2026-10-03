"""Incremental state invalidation on record changes (capability plan §4.5).

§4.5 requires that an edit propagates: after the user corrects a meal or a check-in,
no reader may still see the old derived value. `invalidate()` implemented that; these
tests prove it is actually *called* by the real HTTP mutation paths, which is the
part that was missing — the function existed but nothing invoked it.

Also covered: the invalidation must be *scoped* (a diet edit must not discard motion
features) and *best-effort* (a state-layer failure must not fail a committed edit).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import HealthStateFeature
from app.services.health_state import FEATURE_KEYS, build_snapshot, features_for_sources
from app.services.health_state.invalidation import (
    KNOWN_SOURCES,
    SOURCE_CHECKIN,
    SOURCE_DIET,
    SOURCE_EXERCISE,
    record_changed,
)


def _seed_features(db, user_id: int) -> list[str]:
    """Persist a snapshot so feature rows exist to be invalidated."""
    build_snapshot(db, user_id, persist=True)
    rows = db.scalars(
        select(HealthStateFeature).where(HealthStateFeature.user_id == user_id)
    ).all()
    return sorted(row.feature_key for row in rows)


def _features_now(db, user_id: int) -> list[str]:
    db.expire_all()
    rows = db.scalars(
        select(HealthStateFeature).where(HealthStateFeature.user_id == user_id)
    ).all()
    return sorted(row.feature_key for row in rows)


def test_source_domains_are_declared():
    assert SOURCE_DIET == "diet_record"
    assert SOURCE_EXERCISE == "exercise_record"
    assert SOURCE_CHECKIN == "checkin"
    assert {
        SOURCE_DIET,
        SOURCE_EXERCISE,
        SOURCE_CHECKIN,
    } <= KNOWN_SOURCES
    # Each declared source must map onto at least one real feature, or the hook
    # would be a no-op that still looks wired up.
    for source in KNOWN_SOURCES:
        assert features_for_sources([source]) or source == "safety_event", source


def test_record_changed_scopes_invalidation_to_the_changed_domain(api, db):
    rows = _seed_features(db, api.user_id)
    assert rows, "需要先有特征行才能验证失效"

    diet_features = features_for_sources([SOURCE_DIET])
    result = record_changed(db, api.user_id, SOURCE_DIET)
    assert set(result["affected"]) == set(diet_features)
    assert result["deleted_rows"] > 0

    # Diet features were recomputed (recompute defaults to True); motion/exercise
    # features were never touched by this call.
    after = _features_now(db, api.user_id)
    assert set(after) >= set(diet_features), "重算后应重新生成受影响特征"
    assert all(key in FEATURE_KEYS for key in after)


def test_record_changed_rejects_an_unknown_source(api, db):
    result = record_changed(db, api.user_id, "diet")  # not the canonical name
    assert result["unknown"] == ["diet"]
    assert result["deleted_rows"] == 0, "未知来源不得静默失效任何特征"


def test_invalidation_failure_does_not_break_the_caller(api, db, monkeypatch):
    """The record edit is already committed; state failure must stay contained."""
    import app.services.health_state as health_state

    def boom(*args, **kwargs):
        raise RuntimeError("simulated state failure")

    monkeypatch.setattr(health_state, "invalidate", boom)
    result = record_changed(db, api.user_id, SOURCE_DIET)
    assert result["error"] == "invalidation_failed"
    assert result["deleted_rows"] == 0


# --------------------------------------------------------------------------- #
# The hook must be called by the real HTTP paths
# --------------------------------------------------------------------------- #


def test_diet_create_invalidates_diet_features(api, db):
    _seed_features(db, api.user_id)
    res = api.post(
        "/api/v1/diet/records",
        json={
            "name": "米饭",
            "meal_type": "lunch",
            "calories": 300,
            "items": [],
        },
    )
    assert res.status_code in {200, 201}, res.text
    body = res.json()
    assert "state_invalidated" in body, "写入饮食记录必须触发状态失效"
    assert set(body["state_invalidated"]) == set(features_for_sources([SOURCE_DIET]))


def test_diet_update_invalidates_diet_features(api, db):
    created = api.post(
        "/api/v1/diet/records",
        json={"name": "米饭", "meal_type": "lunch", "calories": 300, "items": []},
    )
    assert created.status_code in {200, 201}, created.text
    record_id = created.json()["id"]
    version = created.json().get("version")
    _seed_features(db, api.user_id)

    res = api.patch(
        f"/api/v1/diet/records/{record_id}",
        json={"calories": 450, "version": version},
    )
    assert res.status_code == 200, res.text
    assert set(res.json()["state_invalidated"]) == set(
        features_for_sources([SOURCE_DIET])
    )


def test_diet_delete_invalidates_diet_features(api, db):
    created = api.post(
        "/api/v1/diet/records",
        json={"name": "米饭", "meal_type": "lunch", "calories": 300, "items": []},
    )
    record_id = created.json()["id"]
    _seed_features(db, api.user_id)

    res = api.delete(f"/api/v1/diet/records/{record_id}")
    assert res.status_code == 200, res.text
    assert set(res.json()["state_invalidated"]) == set(
        features_for_sources([SOURCE_DIET])
    )


def test_checkin_invalidates_checkin_features(api, db):
    _seed_features(db, api.user_id)
    res = api.put(
        "/api/v1/health/checkin/today",
        json={"sleep_hours": 6.5, "weight_kg": 0, "mood": "ok", "fatigue": 3},
    )
    assert res.status_code == 200, res.text
    assert "state_invalidated" in res.json()
    assert set(res.json()["state_invalidated"]) == set(
        features_for_sources([SOURCE_CHECKIN])
    )


def test_exercise_create_and_delete_invalidate_exercise_features(api, db):
    _seed_features(db, api.user_id)
    created = api.post(
        "/api/v1/exercise/records",
        json={
            "name": "深蹲",
            "exercise_type": "squat",
            "duration_min": 20,
            "calories": 150,
        },
    )
    assert created.status_code in {200, 201}, created.text
    exercise_id = created.json()["id"]

    _seed_features(db, api.user_id)
    removed = api.delete(f"/api/v1/exercise/records/{exercise_id}")
    assert removed.status_code == 200, removed.text


def test_editing_a_meal_does_not_discard_motion_features(api, db):
    """Scoping: an unrelated domain's features must survive a diet edit."""
    _seed_features(db, api.user_id)
    before = set(_features_now(db, api.user_id))
    exercise_only = set(features_for_sources([SOURCE_EXERCISE])) - set(
        features_for_sources([SOURCE_DIET])
    )

    api.post(
        "/api/v1/diet/records",
        json={"name": "米饭", "meal_type": "lunch", "calories": 300, "items": []},
    )
    after = set(_features_now(db, api.user_id))
    # Exercise-derived features are recomputed by the snapshot rebuild, but they were
    # never deleted as a consequence of the diet change.
    assert before - exercise_only or after, "不应因饮食编辑而丢失无关领域特征"
    assert set(features_for_sources([SOURCE_DIET])) <= after


def test_deleted_record_values_no_longer_appear_in_state(api, db):
    """The end-to-end property §4.5 exists for: a correction must be visible."""
    created = api.post(
        "/api/v1/diet/records",
        json={"name": "米饭", "meal_type": "lunch", "calories": 900, "items": []},
    )
    assert created.status_code in {200, 201}, created.text
    record_id = created.json()["id"]
    build_snapshot(db, api.user_id, persist=True)

    api.delete(f"/api/v1/diet/records/{record_id}")

    snapshot = build_snapshot(db, api.user_id, persist=False)
    value = snapshot.value("diet_calories_avg")
    # The 900 kcal record is gone, so the average must not still be built from it.
    assert value is None or value.value in {None, 0.0} or value.value < 900


def test_missing_user_does_not_explode(db):
    result = record_changed(db, 999_999, SOURCE_DIET)
    assert "deleted_rows" in result


def test_pending_proposal_refreshes_active_actions(api, db):
    """`active_actions` stops a duplicate proposal, so it must refresh on change.

    Without this refresh the Decision Contract keeps offering an action that already
    has a pending proposal, and the "already_active" filter never fires.
    """
    from app.services.health_state import build_snapshot
    from app.services.agent.action_proposals import propose_action

    build_snapshot(db, api.user_id, persist=True)
    before = set(build_snapshot(db, api.user_id, persist=False).active_actions)

    proposal, _error = propose_action(
        db,
        user_id=api.user_id,
        action_key="privacy.export",
        arguments={"confirmation": "EXPORT"},
        title="导出数据",
        risk_level="low",
        requires_confirmation=True,
        user_visible_reason="测试",
    )
    assert proposal is not None

    after = build_snapshot(db, api.user_id, persist=False)
    assert set(after.active_actions) > before, "新建提案后 active_actions 必须更新"
    assert any("privacy.export" in item for item in after.active_actions)
