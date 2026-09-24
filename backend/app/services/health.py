from app.core.time import business_today
from datetime import datetime, time, timedelta
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.models import (
    DietRecord,
    ExerciseRecord,
    HealthProfile,
    HealthCheckIn,
    HealthGoalSetting,
)


def calorie_target(profile: HealthProfile | None):
    if not profile:
        return 2000
    base = 2000
    if profile.goal_type == "lose":
        base -= 300
    elif profile.goal_type == "gain":
        base += 300
    return max(1400, min(3200, base))


def get_goal_settings(db: Session, user_id: int, profile: HealthProfile | None = None):
    g = db.scalar(select(HealthGoalSetting).where(HealthGoalSetting.user_id == user_id))
    if g:
        return {
            "water_target": int(g.water_target),
            "sleep_target": float(g.sleep_target),
            "exercise_target": int(g.exercise_target),
            "steps_target": int(g.steps_target),
            "protein_target": float(g.protein_target),
            "calorie_target": int(g.calorie_target),
            "weekly_checkin_target": int(g.weekly_checkin_target),
        }
    return {
        "water_target": 1800,
        "sleep_target": 8,
        "exercise_target": 30,
        "steps_target": 8000,
        "protein_target": 90,
        "calorie_target": calorie_target(profile),
        "weekly_checkin_target": 5,
    }


def today_summary(db: Session, user_id: int, profile: HealthProfile | None):
    from app.services.health_data import daily_facts, display_daily

    today = business_today()
    row = display_daily(daily_facts(db, user_id, today, today)[0])
    goals = get_goal_settings(db, user_id, profile)
    return {
        "calories": round(float(row["calories"]), 1),
        "calorie_target": goals["calorie_target"],
        "protein": round(float(row["protein"]), 1),
        "protein_target": goals["protein_target"],
        "exercise_min": int(row["exercise_min"]),
        "exercise_target": goals["exercise_target"],
        "burned": round(float(row["exercise_calories"]), 1),
        "water_ml": int(row["water_ml"]),
        "water_target": goals["water_target"],
        "sleep_hours": float(row["sleep_hours"]),
        "sleep_target": goals["sleep_target"],
        "weight_kg": float(row["weight_kg"] or (profile.weight_kg if profile else 0)),
        "steps": int(row["steps"]),
        "steps_target": goals["steps_target"],
        "mood": row.get("mood") or "normal",
        "observed": row.get("observed", {}),
    }


def trend_7d(db: Session, user_id: int):
    from app.services.health_data import daily_facts

    today = business_today()
    start = today - timedelta(days=6)
    return daily_facts(db, user_id, start, today)


def streak_summary(db: Session, user_id: int):
    rows = db.scalars(
        select(HealthCheckIn)
        .where(HealthCheckIn.user_id == user_id)
        .order_by(HealthCheckIn.record_date.asc())
    ).all()
    dates = sorted({datetime.strptime(x.record_date, "%Y-%m-%d").date() for x in rows})
    date_set = set(dates)
    today = business_today()
    anchor = today if today in date_set else today - timedelta(days=1)
    current = 0
    cursor = anchor
    while cursor in date_set:
        current += 1
        cursor -= timedelta(days=1)
    longest = 0
    run = 0
    prev = None
    for d in dates:
        run = run + 1 if prev and d == prev + timedelta(days=1) else 1
        longest = max(longest, run)
        prev = d
    last7 = []
    for offset in range(6, -1, -1):
        d = today - timedelta(days=offset)
        last7.append(
            {
                "date": d.isoformat(),
                "label": "一二三四五六日"[d.weekday()],
                "done": d in date_set,
            }
        )
    return {
        "current": current,
        "longest": longest,
        "checked_today": today in date_set,
        "last7": last7,
    }
