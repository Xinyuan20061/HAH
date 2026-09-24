from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.core.database import Base
from app.models import DietRecord, ExerciseRecord, HealthCheckIn
from app.services.health_data import daily_facts
from app.services.dynamic_goals import calculate_suggestions


def test_daily_facts_preserves_missing_values():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    with Session(engine) as db:
        db.add(
            DietRecord(
                user_id=1,
                name="test",
                meal_type="other",
                calories=500,
                protein=30,
                carbs=50,
                fat=10,
                recorded_at=datetime.combine(yesterday, datetime.min.time()),
            )
        )
        db.add(
            HealthCheckIn(
                user_id=1,
                record_date=today.isoformat(),
                water_ml=1600,
                sleep_hours=7.5,
                weight_kg=65,
                steps=7000,
                mood="normal",
            )
        )
        db.commit()
        rows = daily_facts(db, 1, yesterday, today)
    assert rows[0]["calories"] == 500
    assert rows[0]["water_ml"] is None
    assert rows[1]["water_ml"] == 1600
    assert rows[1]["calories"] is None


def test_dynamic_goal_rule_reduces_only_with_enough_observations():
    goals = {"water_target": 2000, "exercise_target": 60, "steps_target": 10000}
    low = [{"water_ml": 1000, "exercise_min": 20, "steps": 4000} for _ in range(10)]
    result = calculate_suggestions(low, goals, 14)
    by = {x["target_key"]: x for x in result}
    assert by["exercise_target"]["decision"] == "reduce"
    assert by["exercise_target"]["recommended_target"] < 60
    sparse = [{"water_ml": 1000, "exercise_min": 20, "steps": 4000} for _ in range(4)]
    result = calculate_suggestions(sparse, goals, 14)
    assert all(x["decision"] == "insufficient_data" for x in result)
