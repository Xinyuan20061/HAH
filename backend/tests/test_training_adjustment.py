from sqlalchemy.orm import Session

from app.core.time import business_today
from app.models import AIJob, HealthCheckIn, MotionScore
from app.services.training_adjustment import (
    apply_plan_guardrails,
    build_training_adjustment,
)


def base_context():
    return {
        "today": {
            "sleep_hours": 0,
            "mood": "normal",
            "observed": {"checkin": False},
        },
        "goals": {"exercise_target": 35},
        "recent_7d": {"days": []},
        "weekly_facts": {"data_quality": {"confidence": "low"}},
        "motion_profile": {
            "sample_count": 0,
            "data_quality": {"level": "low"},
            "dimensions": {},
            "by_exercise": [],
            "frequent_findings": [],
        },
    }


def test_training_adjustment_does_not_personalize_missing_data():
    adjustment = build_training_adjustment(base_context())
    assert adjustment["mode"] == "baseline"
    assert adjustment["confidence"] == "low"
    assert adjustment["reasons"] == []
    assert adjustment["constraints"]["progression_pct"] == 0
    assert "记录不足" in adjustment["summary"]


def test_training_adjustment_combines_recovery_and_motion_evidence():
    context = base_context()
    context["today"].update(
        sleep_hours=5.0,
        mood="tired",
        observed={"checkin": True},
    )
    context["motion_profile"] = {
        "sample_count": 4,
        "data_quality": {"level": "medium"},
        "dimensions": {
            "motion_quality": 61,
            "completion": 62,
            "stability": 55,
            "rhythm_control": 58,
            "deviation_index": 40,
        },
        "by_exercise": [
            {
                "exercise_name": "深蹲",
                "sessions": 4,
                "recent_change_points": -10,
            }
        ],
        "frequent_findings": [{"finding": "躯干倾角较大", "count": 3}],
    }
    adjustment = build_training_adjustment(context)
    assert adjustment["mode"] == "adaptive"
    assert adjustment["constraints"] == {
        "intensity": "light",
        "max_session_minutes": 20,
        "recovery_priority_today": True,
        "progression_pct": 0,
    }
    assert {item["code"] for item in adjustment["coaching_focus"]} >= {
        "stability",
        "rhythm_control",
    }
    assert any(item["code"] == "recent_motion_decline" for item in adjustment["reasons"])


def test_plan_guardrails_cap_duration_and_convert_today_to_recovery():
    context = base_context()
    context["today"].update(sleep_hours=5, observed={"checkin": True})
    context["motion_profile"].update(
        sample_count=3,
        dimensions={"stability": 50, "rhythm_control": 80, "completion": 80},
    )
    adjustment = build_training_adjustment(context)
    plan = {
        "title": "测试计划",
        "items": [
            {
                "date_offset": 0,
                "category": "exercise",
                "title": "高强度训练",
                "description": "原计划",
                "target": {"duration_min": 50},
            },
            {
                "date_offset": 2,
                "category": "exercise",
                "title": "力量训练",
                "description": "原计划",
                "target": {"duration_min": "45"},
            },
        ],
    }
    adjusted, changes = apply_plan_guardrails(plan, adjustment)
    assert plan["items"][0]["category"] == "exercise"
    assert adjusted["items"][0]["category"] == "recovery"
    assert adjusted["items"][0]["target"]["duration_min"] == 20
    assert adjusted["items"][1]["target"]["duration_min"] == 20
    assert "本次重点" in adjusted["items"][1]["description"]
    assert len(changes) >= 3


def test_training_adjustment_api_and_agent_fallback_apply_real_user_data(
    api, migrated_engine
):
    with Session(migrated_engine) as db:
        db.add(
            HealthCheckIn(
                user_id=api.user_id,
                record_date=business_today().isoformat(),
                sleep_hours=5,
                mood="tired",
            )
        )
        for overall in [58, 60, 62]:
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

    adjustment_response = api.get("/api/v1/fitness/training-adjustment")
    assert adjustment_response.status_code == 200
    adjustment = adjustment_response.json()
    assert adjustment["mode"] == "adaptive"
    assert adjustment["constraints"]["max_session_minutes"] == 20

    response = api.post(
        "/api/v1/agent/respond",
        json={"message": "结合我最近的数据安排本周训练计划"},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["provider"] == "rules-fallback"
    assert result["plan_adjustment"]["applied_changes"]
    assert "training_adjustment" in result["facts_used"]
    assert result["plan"]["items"][0]["category"] == "recovery"
    for item in result["plan"]["items"]:
        duration = item.get("target", {}).get("duration_min")
        if duration is not None:
            assert duration <= 20
