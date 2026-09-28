from app.core.time import BUSINESS_TZ, business_day, business_today, naive_utc, utc_day_bounds
from datetime import datetime, time, timedelta, timezone
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


def _resting_energy(profile: HealthProfile | None) -> dict:
    """Return a transparent resting-energy estimate, never a diagnosis.

    Mifflin-St Jeor is only used when the user has supplied the inputs it needs.
    For an unspecified sex we use the midpoint of the two constants and name that
    limitation in the response instead of silently assuming one.
    """
    if not profile:
        return {
            "calories": None,
            "available": False,
            "method": "profile_required",
            "note": "完善年龄、身高与体重后可生成静息消耗估算。",
        }
    weight = float(profile.weight_kg or 0)
    height = float(profile.height_cm or 0)
    age = int(profile.age or 0)
    if weight <= 0 or height <= 0 or age <= 0:
        return {
            "calories": None,
            "available": False,
            "method": "profile_required",
            "note": "完善年龄、身高与体重后可生成静息消耗估算。",
        }
    gender = (profile.gender or "unspecified").lower()
    constant = 5 if gender == "male" else -161 if gender == "female" else -78
    value = round(10 * weight + 6.25 * height - 5 * age + constant)
    return {
        "calories": max(900, min(3000, value)),
        "available": True,
        "method": "mifflin_st_jeor",
        "note": "静息消耗为档案估算，不包含全部日常活动与食物热效应。",
    }


def _meal_bucket(record: DietRecord) -> str:
    meal_type = (record.meal_type or "other").lower()
    if meal_type in {"breakfast", "lunch", "dinner", "snack"}:
        return meal_type
    local_hour = (
        naive_utc(record.recorded_at)
        .replace(tzinfo=timezone.utc)
        .astimezone(BUSINESS_TZ)
        .hour
    )
    if local_hour < 10:
        return "breakfast"
    if local_hour < 15:
        return "lunch"
    if local_hour < 21:
        return "dinner"
    return "snack"


def energy_dashboard(db: Session, user_id: int, profile: HealthProfile | None):
    """Read model for the Records energy dashboard.

    The chart threshold starts from the user's saved calorie goal and only adds a
    small, capped fraction of recorded exercise. This makes the daily adjustment
    visible without treating missing exercise data as a reason to lower intake.
    """
    from app.services.health_data import daily_facts

    today = business_today()
    start = today - timedelta(days=6)
    facts = daily_facts(db, user_id, start, today)
    goals = get_goal_settings(db, user_id, profile)
    base_target = int(goals["calorie_target"])
    start_dt = utc_day_bounds(start)[0]
    end_dt = utc_day_bounds(today)[1]
    diets = db.scalars(
        select(DietRecord).where(
            DietRecord.user_id == user_id,
            DietRecord.recorded_at.between(start_dt, end_dt),
        )
    ).all()
    meals_by_day: dict[str, dict[str, float]] = {}
    for record in diets:
        key = business_day(record.recorded_at).isoformat()
        meal = _meal_bucket(record)
        values = meals_by_day.setdefault(
            key, {"breakfast": 0, "lunch": 0, "dinner": 0, "snack": 0}
        )
        values[meal] += float(record.calories or 0)

    days = []
    for row in facts:
        meals = meals_by_day.get(
            row["date"],
            {"breakfast": 0, "lunch": 0, "dinner": 0, "snack": 0},
        )
        exercise = round(float(row.get("exercise_calories") or 0))
        # A conservative activity adjustment: credit 25% of recorded workout
        # energy and cap it so a noisy estimate cannot move the goal abruptly.
        activity_adjustment = min(200, round(exercise * 0.25))
        recommended = base_target + activity_adjustment
        intake = round(sum(meals.values()))
        days.append(
            {
                "date": row["date"],
                "label": row["label"],
                "breakfast": round(meals["breakfast"]),
                "lunch": round(meals["lunch"]),
                "dinner": round(meals["dinner"]),
                "snack": round(meals["snack"]),
                "intake": intake,
                "exercise": exercise,
                "target": recommended,
                "lower": round(recommended * 0.9),
                "upper": round(recommended * 1.1),
                "observed": row.get("observed", {}),
            }
        )

    resting = _resting_energy(profile)
    current = days[-1]
    known_expenditure = (
        int(resting["calories"]) + current["exercise"]
        if resting["available"]
        else None
    )
    net = current["intake"] - known_expenditure if known_expenditure is not None else None
    if net is None:
        balance = "complete_profile"
    elif net > 80:
        balance = "positive"
    elif net < -80:
        balance = "negative"
    else:
        balance = "balanced"
    current.update(
        {
            "resting": resting["calories"],
            "known_expenditure": known_expenditure,
            "net": net,
            "balance": balance,
        }
    )
    return {
        "date": today.isoformat(),
        "target": {
            "base": base_target,
            "today": current["target"],
            "lower": current["lower"],
            "upper": current["upper"],
            "strategy": "profile_goal_activity_v1",
            "label": "智能建议区间",
            "note": "以当前健康目标为基线，结合已记录运动做小幅调整；漏记不会让目标自动降低。",
        },
        "resting": resting,
        "today": current,
        "days": days,
        "disclaimer": "能量收支仅包含已识别餐食、静息估算与已记录运动，供日常趋势参考。",
    }


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
