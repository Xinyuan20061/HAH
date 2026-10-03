"""Phase 1 acceptance — the Health State Engine (capability plan §4, §13.1).

The plan's §13.1 checklist is the specification for these tests:

* the same input produces a stable snapshot;
* a missing day is never turned into zero;
* editing or deleting records invalidates exactly the affected features;
* motion scores from different model versions are never compared;
* every derived value carries evidence refs and a feature version.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import (
    DietRecord,
    ExerciseRecord,
    HealthCheckIn,
    HealthGoalSetting,
    HealthStateFeature,
    MotionAnalysisFeedback,
    MotionAnalysisRun,
    MediaAsset,
    User,
)
from app.services.health_state import (
    FEATURE_KEYS,
    build_snapshot,
    feature_history,
    features_for_sources,
    invalidate,
    load_snapshot,
)


def _checkin(db: Session, user_id: int, day: date, **kwargs) -> HealthCheckIn:
    row = HealthCheckIn(user_id=user_id, record_date=day.isoformat(), **kwargs)
    db.add(row)
    return row


def test_snapshot_is_deterministic_for_the_same_input(api, db):
    user = db.get(User, api.user_id)
    _checkin(db, user.id, date.today(), sleep_hours=6.0, weight_kg=70.0, steps=8000)
    db.commit()

    first = build_snapshot(db, user.id, persist=False)
    second = build_snapshot(db, user.id, persist=False)
    assert first.snapshot_hash == second.snapshot_hash, "同一输入必须产生同一 snapshot_hash"

    # A genuinely different state must change the hash.
    _checkin(db, user.id, date.today() - timedelta(days=1), sleep_hours=5.0)
    db.commit()
    third = build_snapshot(db, user.id, persist=False)
    assert third.snapshot_hash != first.snapshot_hash


def test_missing_days_are_counted_not_zeroed(api, db):
    """§4.3: a 3-of-7-day average must never be presented as a full week."""
    user = db.get(User, api.user_id)
    _checkin(db, user.id, date.today(), sleep_hours=6.0)
    _checkin(db, user.id, date.today() - timedelta(days=2), sleep_hours=6.0)
    db.commit()

    snapshot = build_snapshot(db, user.id, persist=False)
    debt = snapshot.value("sleep_debt_7d")
    assert debt is not None
    assert debt.observed_days == 2, "只统计有记录的日子"
    assert debt.window_days == 7
    assert any("2/7" in item for item in debt.limitations), debt.limitations
    # The seven-day sleep debt is 2 nights of debt, not 7 nights assuming zero.
    assert snapshot.numeric("sleep_debt_7d") == 4.0

    missing = snapshot.missingness
    assert missing["checkin"] == 5, missing


def test_no_records_yields_unavailable_not_zero(api, db):
    snapshot = build_snapshot(db, api.user_id, persist=False)
    debt = snapshot.value("sleep_debt_7d")
    assert debt is not None
    assert debt.value is None, "没有睡眠记录时不得给出 0 睡眠债"
    assert debt.confidence_level == "unavailable"
    assert debt.limitations

    avg = snapshot.value("diet_calories_avg")
    assert avg.value is None
    assert "不按 0 处理" in " ".join(avg.limitations)


def test_every_derived_value_carries_evidence_and_version(api, db):
    user = db.get(User, api.user_id)
    _checkin(db, user.id, date.today(), sleep_hours=7.0, steps=9000)
    db.add(
        DietRecord(
            user_id=user.id,
            name="午餐",
            meal_type="lunch",
            calories=500,
            recorded_at=utc_now(),
        )
    )
    db.add(
        ExerciseRecord(
            user_id=user.id,
            name="慢跑",
            duration_min=30,
            calories_burned=220,
            recorded_at=utc_now(),
        )
    )
    db.commit()

    snapshot = build_snapshot(db, user.id, persist=False)
    assert set(snapshot.values) == set(FEATURE_KEYS)
    for key, value in snapshot.sorted_items():
        assert value.feature_version, f"{key} 缺少 feature_version"
        assert value.evidence_type in {
            "observed",
            "derived",
            "model_inferred",
            "user_confirmed",
        }
        assert value.confidence_level in {"high", "medium", "low", "unavailable"}
        assert value.window_days == 7
        if value.value is not None and value.evidence_type != "derived":
            assert value.evidence, f"{key} 有值但没有 evidence refs"
        for ref in value.evidence:
            assert ref.source_type and ref.source_id
            assert ref.observed_at is not None


def test_invalidation_targets_only_affected_features(api, db):
    """§4.5: a diet edit must not recompute sleep debt."""
    user = db.get(User, api.user_id)
    _checkin(db, user.id, date.today(), sleep_hours=7.0)
    diet = DietRecord(
        user_id=user.id,
        name="午餐",
        meal_type="lunch",
        calories=500,
        recorded_at=utc_now(),
    )
    db.add(diet)
    db.commit()

    build_snapshot(db, user.id)
    before = {
        row.feature_key
        for row in db.scalars(
            select(HealthStateFeature).where(HealthStateFeature.user_id == user.id)
        ).all()
    }
    assert "sleep_debt_7d" in before and "diet_calories_avg" in before

    report = invalidate(db, user.id, ["diet_record"], recompute=False)
    assert "diet_calories_avg" in report["affected"]
    assert "sleep_debt_7d" not in report["affected"], "无关特征不得被失效"

    remaining = {
        row.feature_key
        for row in db.scalars(
            select(HealthStateFeature).where(HealthStateFeature.user_id == user.id)
        ).all()
    }
    assert "diet_calories_avg" not in remaining
    assert "sleep_debt_7d" in remaining, "未被影响的特征必须保留"


def test_invalidation_map_comes_from_declarations():
    assert features_for_sources(["diet_record"]) == ["diet_calories_avg", "diet_record_coverage_7d", "data_reliability_score"] or set(
        features_for_sources(["diet_record"])
    ) >= {"diet_calories_avg", "diet_record_coverage_7d"}
    # An unknown source invalidates nothing rather than everything.
    assert features_for_sources(["not_a_real_source"]) == []


def test_motion_trend_refuses_to_mix_model_versions(api, db):
    """§4.5: after a model version change the old scores are not comparable."""
    user = db.get(User, api.user_id)
    asset = MediaAsset(
        user_id=user.id, storage_key=f"trend-{user.id}", media_type="video"
    )
    db.add(asset)
    db.flush()

    def _run(version: str, score: float, index: int) -> None:
        run = MotionAnalysisRun(
            user_id=user.id,
            media_asset_id=asset.id,
            requested_type="squat",
            pipeline_version="motion-unified-v2",
            effective_pipeline_version=version,
            status="completed",
        )
        db.add(run)
        db.flush()
        db.add(
            MotionAnalysisFeedback(
                run_id=run.id,
                user_id=user.id,
                result_json=json.dumps(
                    {
                        "recognition": {"canonical_id": "squat", "state": "identified"},
                        "score": {"available": True, "overall": score},
                    }
                ),
            )
        )

    # Two runs on v1 and one on v2: no single version reaches 3 samples.
    for index, score in enumerate([60.0, 70.0]):
        _run("pipeline-v1", score, index)
    _run("pipeline-v2", 90.0, 2)
    db.commit()

    snapshot = build_snapshot(db, user.id, persist=False)
    trend = snapshot.value("motion_quality_trend")
    assert trend.value is None, "混合版本不得给出进步结论"
    assert "至少 3 次" in " ".join(trend.limitations) or "3 次" in " ".join(
        trend.limitations
    )

    # Three same-version samples do produce a comparable trend.
    _run("pipeline-v1", 80.0, 3)
    db.commit()
    snapshot = build_snapshot(db, user.id, persist=False)
    trend = snapshot.value("motion_quality_trend")
    assert trend.value == 20.0, trend.limitations
    assert "同模型版本" in " ".join(trend.limitations)


def test_snapshot_round_trips_through_persistence(api, db):
    user = db.get(User, api.user_id)
    _checkin(db, user.id, date.today(), sleep_hours=6.5, steps=7000)
    db.commit()

    built = build_snapshot(db, user.id)
    from app.services.health_state import latest_snapshot

    row = latest_snapshot(db, user.id)
    assert row is not None
    restored = load_snapshot(row)
    assert restored.snapshot_hash == built.snapshot_hash
    assert restored.numeric("sleep_debt_7d") == built.numeric("sleep_debt_7d")
    assert len(restored.constraints) == len(built.constraints)
    assert restored.missingness == built.missingness


def test_low_coverage_becomes_a_hard_constraint(api, db):
    """Insufficient coverage must block automatic planning, not be ignored."""
    snapshot = build_snapshot(db, api.user_id, persist=False)
    assert "insufficient_record_coverage" in snapshot.block_keys()
    assert snapshot.hard_constraints()

    user = db.get(User, api.user_id)
    for offset in range(7):
        _checkin(
            db,
            user.id,
            date.today() - timedelta(days=offset),
            sleep_hours=7.0,
            steps=8000,
        )
        db.add(
            DietRecord(
                user_id=user.id,
                name="餐",
                meal_type="lunch",
                calories=500,
                recorded_at=utc_now() - timedelta(days=offset),
            )
        )
        db.add(
            ExerciseRecord(
                user_id=user.id,
                name="慢跑",
                duration_min=20,
                calories_burned=150,
                recorded_at=utc_now() - timedelta(days=offset),
            )
        )
    db.commit()
    snapshot = build_snapshot(db, user.id, persist=False)
    assert "insufficient_record_coverage" not in snapshot.block_keys()
    assert snapshot.numeric("data_reliability_score") >= 0.9


def test_unsafe_rule_hit_blocks_auto_planning(api, db):
    from app.models import SafetyEvent

    db.add(
        SafetyEvent(
            user_id=api.user_id,
            category="chest_pain",
            severity="high",
            action="warn",
            matched_rule="chest_pain",
        )
    )
    db.commit()
    snapshot = build_snapshot(db, api.user_id, persist=False)
    assert any(item.startswith("safety_rule:") for item in snapshot.block_keys())


def test_excluded_exercise_is_a_hard_constraint(api, db):
    from app.models import UserTrainingIntent

    db.add(
        UserTrainingIntent(
            user_id=api.user_id,
            constraints_json=json.dumps(["squat", {"exercise_id": "pushup"}]),
        )
    )
    db.commit()
    snapshot = build_snapshot(db, api.user_id, persist=False)
    assert "user_excluded_exercise:squat" in snapshot.block_keys()
    assert "user_excluded_exercise:pushup" in snapshot.block_keys()


def test_state_api_exposes_evidence_and_policy(api, db):
    user = db.get(User, api.user_id)
    _checkin(db, user.id, date.today(), sleep_hours=6.0, steps=7500)
    db.commit()

    res = api.get("/api/v1/health/state", params={"window_days": 7})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["snapshot_hash"]
    assert body["window_days"] == 7
    assert "sleep_debt_7d" in body["values"]
    assert body["values"]["sleep_debt_7d"]["observed_days"] == 1
    assert "policy" in body

    constraints = api.get("/api/v1/health/state/constraints").json()
    assert "blocks_auto_planning" in constraints

    features = api.get("/api/v1/health/state/features").json()
    assert {item["key"] for item in features["features"]} == set(FEATURE_KEYS)
    assert features["invalidation_map"]

    history = api.get(
        "/api/v1/health/state/history", params={"key": "sleep_debt_7d"}
    )
    assert history.status_code == 200
    assert history.json()["items"]

    bad = api.get("/api/v1/health/state/history", params={"key": "nope"})
    assert bad.status_code == 404
    assert bad.json()["error"]["code"] == "FEATURE_NOT_FOUND"

    assert api.get("/api/v1/health/state", params={"window_days": 999}).status_code == 422


def test_signals_endpoint_uses_the_state_layer(api, db):
    res = api.get("/api/v1/health/signals")
    assert res.status_code == 200, res.text
    body = res.json()
    assert "signals" in body and "data_quality" in body
    assert body["state_snapshot_hash"], "信号必须来自状态层快照"
    assert "policy" in body


def test_feature_history_is_append_only_and_minimal(api, db):
    user = db.get(User, api.user_id)
    row = _checkin(db, user.id, date.today(), sleep_hours=6.0)
    db.commit()
    build_snapshot(db, user.id)
    # A correction to the same day genuinely changes the state; the history keeps
    # both points so a recomputation is auditable.
    row.sleep_hours = 5.0
    db.add(row)
    db.commit()
    build_snapshot(db, user.id)

    items = feature_history(db, user.id, "sleep_debt_7d")
    assert len(items) >= 2, "每次快照都应留下可回看的历史点"
    values = {item["value"] for item in items}
    assert {2.0, 3.0} <= values, values
    for item in items:
        assert set(item) >= {
            "as_of",
            "snapshot_hash",
            "value",
            "confidence_level",
            "observed_days",
            "feature_version",
            "limitations",
        }


def test_read_context_consumes_the_state_layer(api, db):
    """§4.4: the old flat keys become projections of the snapshot."""
    from app.services.agent.tools import read_context

    user = db.get(User, api.user_id)
    _checkin(db, user.id, date.today(), sleep_hours=6.0)
    db.commit()
    context = read_context(db, user)
    assert "state" in context
    assert context["state"]["snapshot_hash"]
    assert "constraints" in context
    assert "state_blocked" in context
    # The legacy keys must still be present for compatibility.
    assert {"profile", "today", "goals", "recent_7d"} <= set(context)


def test_state_api_exposes_display_titles(api):
    """Internal feature keys must not leak to users as-is.

    Titles come from the feature registry and are returned alongside the snapshot;
    they are deliberately NOT part of ``StateValue``, so rewording a label can never
    change a persisted snapshot or its hash (which is what invalidates caches).
    """
    res = api.get("/api/v1/health/state")
    assert res.status_code == 200, res.text
    body = res.json()
    assert "titles" in body
    titles = body["titles"]
    assert titles, "必须提供展示标题"
    for key, title in titles.items():
        assert title and title != key, f"{key} 的标题没有配置中文名"
    for key in body["values"]:
        assert key in titles, f"{key} 缺少展示标题"


def test_titles_are_not_part_of_the_snapshot_contract(db, api):
    from app.services.health_state import StateValue, build_snapshot

    assert "title" not in StateValue.model_fields, (
        "标题属于展示层，放进 StateValue 会让改文案改变 snapshot_hash"
    )
    before = build_snapshot(db, api.user_id, persist=False).snapshot_hash
    after = build_snapshot(db, api.user_id, persist=False).snapshot_hash
    assert before == after
    assert len(before) == 64, "snapshot_hash 应为稳定的十六进制摘要"
