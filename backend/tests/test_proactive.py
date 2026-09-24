"""Proactive Health Guardian (Agent v3) evaluation.

Verifies the deterministic forward-looking rules:
  exercise_stall / sleep_deficit / weight_rise / motion_decline / record_gap
and the guarantee that a well-recorded user gets no spurious alerts.
All checks run with constructed record data; no model inference is involved.
"""
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.time import utc_now, business_today
from app.models import AIJob, ExerciseRecord, HealthCheckIn, MotionScore
from app.services.agent.proactive import build_proactive_insights


def _codes(result: dict) -> list[str]:
    return [item["code"] for item in result["insights"]]


def _seed_exercise(db: Session, user_id: int, days_ago: int, minutes: int = 20):
    db.add(
        ExerciseRecord(
            user_id=user_id,
            name="快走",
            duration_min=minutes,
            calories_burned=80,
            recorded_at=utc_now() - timedelta(days=days_ago, hours=1),
        )
    )
    db.commit()


def _seed_checkin(db: Session, user_id: int, days_ago: int, **kwargs):
    date = (business_today() - timedelta(days=days_ago)).isoformat()
    db.add(HealthCheckIn(user_id=user_id, record_date=date, **kwargs))
    db.commit()


def _snapshot(db: Session, user_id: int) -> dict:
    from app.services.health_data import period_snapshot

    return period_snapshot(db, user_id, 7)


def _motion(db: Session, user_id: int) -> dict:
    from app.services.motion_profile import motion_profile

    return motion_profile(db, user_id, 30)


def test_insights_exercise_stall(api, migrated_engine):
    with Session(migrated_engine) as db:
        _seed_exercise(db, api.user_id, days_ago=6)
        result = build_proactive_insights(
            {"recent_7d": _snapshot(db, api.user_id), "goals": {"exercise_target": 30}}
        )
    assert "exercise_stall" in _codes(result)


def test_insights_sleep_deficit(api, migrated_engine):
    with Session(migrated_engine) as db:
        _seed_checkin(db, api.user_id, days_ago=1, sleep_hours=5.0)
        _seed_checkin(db, api.user_id, days_ago=0, sleep_hours=5.5)
        result = build_proactive_insights({"recent_7d": _snapshot(db, api.user_id), "goals": {}})
    assert "sleep_deficit" in _codes(result)


def test_insights_weight_rise(api, migrated_engine):
    with Session(migrated_engine) as db:
        for days_ago, weight in [(2, 62.0), (1, 62.5), (0, 63.0)]:
            _seed_checkin(db, api.user_id, days_ago=days_ago, weight_kg=weight)
        result = build_proactive_insights({"recent_7d": _snapshot(db, api.user_id), "goals": {}})
    assert "weight_rise" in _codes(result)


def test_insights_motion_decline(api, migrated_engine):
    with Session(migrated_engine) as db:
        for overall in [70, 64, 58]:
            job = AIJob(
                user_id=api.user_id,
                job_type="motion_pose",
                status="done",
                payload_json='{"exercise_type":"squat"}',
            )
            db.add(job)
            db.flush()
            db.add(
                MotionScore(
                    user_id=api.user_id,
                    job_id=job.id,
                    exercise_type="squat",
                    completeness=62,
                    stability=54,
                    rhythm_control=59,
                    risk_index=38,
                    overall=overall,
                    confidence=0.8,
                    evidence_json="{}",
                )
            )
        db.commit()
        result = build_proactive_insights({"motion_profile": _motion(db, api.user_id)})
    assert "motion_decline" in _codes(result)


def test_insights_record_gap(api, migrated_engine):
    with Session(migrated_engine) as db:
        result = build_proactive_insights({"recent_7d": _snapshot(db, api.user_id), "goals": {}})
    assert "record_gap" in _codes(result)


def test_insights_clean_user_no_spurious_alerts(api, migrated_engine):
    with Session(migrated_engine) as db:
        for days_ago in range(0, 7):
            _seed_exercise(db, api.user_id, days_ago=days_ago, minutes=25)
            _seed_checkin(db, api.user_id, days_ago=days_ago, sleep_hours=7.5, weight_kg=62.0)
        result = build_proactive_insights(
            {"recent_7d": _snapshot(db, api.user_id), "goals": {"exercise_target": 30}}
        )
    assert result["count"] == 0
    assert "没有需要主动干预" in result["summary"]


def test_insights_endpoint_returns_trace(api):
    response = api.get("/api/v1/agent/insights")
    assert response.status_code == 200
    body = response.json()
    assert body["trace"]["specialist"] == "proactive_guardian"
    assert body["trace"]["routing"]
    assert isinstance(body["insights"], list)
    assert body["data_quality"]["expected_days"] == 7


def test_insights_report_record_coverage(api, migrated_engine):
    with Session(migrated_engine) as db:
        _seed_checkin(db, api.user_id, days_ago=1, sleep_hours=7.0)
        _seed_checkin(db, api.user_id, days_ago=0, sleep_hours=7.5)
        result = build_proactive_insights(
            {"recent_7d": _snapshot(db, api.user_id), "goals": {}}
        )
    quality = result["data_quality"]
    assert quality["recorded_days"] == 2
    assert quality["expected_days"] == 7
    assert quality["coverage_pct"] == 29
    assert quality["level"] == "medium"


def test_insight_feedback_is_human_controlled_and_auditable(api):
    response = api.post(
        "/api/v1/agent/insights/record_gap/feedback",
        json={"verdict": "helpful"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "helpful"
    assert "不会自动用于训练" in body["policy"]

    stats = api.get("/api/v1/agent/stats?days=30").json()["insight_feedback"]
    assert stats["sample_size"] == 1
    assert stats["distribution"] == {"helpful": 1}
    assert stats["helpful_rate_pct"] == 100.0

    refreshed = api.get("/api/v1/agent/insights").json()
    record_gap = next(x for x in refreshed["insights"] if x["code"] == "record_gap")
    assert record_gap["user_feedback"]["verdict"] == "helpful"
    assert record_gap["user_feedback"]["recorded_at"]


def test_insight_feedback_keeps_latest_daily_judgement(api):
    for verdict in ("helpful", "inaccurate"):
        response = api.post(
            "/api/v1/agent/insights/record_gap/feedback", json={"verdict": verdict}
        )
        assert response.status_code == 200
    stats = api.get("/api/v1/agent/stats?days=30").json()["insight_feedback"]
    assert stats["sample_size"] == 1
    assert stats["distribution"] == {"inaccurate": 1}
    assert stats["helpful_rate_pct"] == 0.0


def test_insight_feedback_rejects_unknown_inactive_and_invalid_values(api):
    unknown = api.post(
        "/api/v1/agent/insights/not_a_signal/feedback", json={"verdict": "helpful"}
    )
    assert unknown.status_code == 404
    inactive = api.post(
        "/api/v1/agent/insights/sleep_deficit/feedback", json={"verdict": "helpful"}
    )
    assert inactive.status_code == 409
    invalid = api.post(
        "/api/v1/agent/insights/record_gap/feedback", json={"verdict": "train_on_it"}
    )
    assert invalid.status_code == 422
