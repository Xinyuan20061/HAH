from __future__ import annotations
from app.core.time import business_today, business_day, utc_day_bounds
from datetime import date, datetime, time, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import DietRecord, ExerciseRecord, HealthCheckIn, PlanTaskState


def _day_range(d: date):
    return utc_day_bounds(d)


def daily_facts(
    db: Session, user_id: int, start: date, end: date, *, include_plan: bool = True
) -> list[dict]:
    """Canonical health time-series projection.

    Missing observations stay None instead of silently becoming zero. Consumers that
    need display-friendly zeros can convert at their presentation boundary.
    """
    start_dt = utc_day_bounds(start)[0]
    end_dt = utc_day_bounds(end)[1]
    diets = db.scalars(
        select(DietRecord).where(
            DietRecord.user_id == user_id,
            DietRecord.recorded_at.between(start_dt, end_dt),
        )
    ).all()
    exercises = db.scalars(
        select(ExerciseRecord).where(
            ExerciseRecord.user_id == user_id,
            ExerciseRecord.recorded_at.between(start_dt, end_dt),
        )
    ).all()
    checks = db.scalars(
        select(HealthCheckIn).where(
            HealthCheckIn.user_id == user_id,
            HealthCheckIn.record_date >= start.isoformat(),
            HealthCheckIn.record_date <= end.isoformat(),
        )
    ).all()
    task_states = (
        db.scalars(
            select(PlanTaskState).where(
                PlanTaskState.user_id == user_id,
                PlanTaskState.record_date >= start.isoformat(),
                PlanTaskState.record_date <= end.isoformat(),
            )
        ).all()
        if include_plan
        else []
    )

    diet_by: dict[str, list] = {}
    exercise_by: dict[str, list] = {}
    check_by = {x.record_date: x for x in checks}
    tasks_by: dict[str, list] = {}
    for x in diets:
        diet_by.setdefault(business_day(x.recorded_at).isoformat(), []).append(x)
    for x in exercises:
        exercise_by.setdefault(business_day(x.recorded_at).isoformat(), []).append(x)
    for x in task_states:
        tasks_by.setdefault(x.record_date, []).append(x)

    rows = []
    d = start
    while d <= end:
        key = d.isoformat()
        c = check_by.get(key)
        ds = diet_by.get(key, [])
        es = exercise_by.get(key, [])
        ts = tasks_by.get(key, [])
        fact = {
            "date": key,
            "label": f"{d.month}/{d.day}",
            "water_ml": c.water_ml if c else None,
            "sleep_hours": float(c.sleep_hours) if c else None,
            "weight_kg": float(c.weight_kg) if c and c.weight_kg else None,
            "steps": c.steps if c else None,
            "mood": c.mood if c else None,
            "calories": round(sum(float(x.calories or 0) for x in ds), 1)
            if ds
            else None,
            "protein": round(sum(float(x.protein or 0) for x in ds), 1)
            if ds
            else None,
            "carbs": round(sum(float(x.carbs or 0) for x in ds), 1) if ds else None,
            "fat": round(sum(float(x.fat or 0) for x in ds), 1) if ds else None,
            "exercise_min": int(sum(int(x.duration_min or 0) for x in es))
            if es
            else None,
            "exercise_calories": round(
                sum(float(x.calories_burned or 0) for x in es), 1
            )
            if es
            else None,
            "observed": {
                "checkin": bool(c),
                "diet": bool(ds),
                "exercise": bool(es),
            },
        }
        if include_plan:
            fact["plan_done"] = sum(1 for x in ts if x.done)
            fact["plan_total"] = len(ts)
            fact["observed"]["plan"] = bool(ts)
        rows.append(fact)
        d += timedelta(days=1)
    return rows


def display_daily(row: dict) -> dict:
    result = dict(row)
    for k in (
        "water_ml",
        "sleep_hours",
        "weight_kg",
        "steps",
        "calories",
        "protein",
        "carbs",
        "fat",
        "exercise_min",
        "exercise_calories",
    ):
        if result.get(k) is None:
            result[k] = 0
    return result


def period_snapshot(
    db: Session, user_id: int, days: int = 7, end: date | None = None,
    *, include_plan: bool = True,
) -> dict:
    end = end or business_today()
    start = end - timedelta(days=days - 1)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": daily_facts(db, user_id, start, end, include_plan=include_plan),
    }
