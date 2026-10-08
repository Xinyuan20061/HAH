from __future__ import annotations
from app.core.time import business_today
import json
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import WeeklyReportSnapshot
from app.services.health_data import daily_facts
from app.services.health import get_goal_settings, streak_summary

METRICS = {
    "water_ml": ("checkin", "water_target"),
    "sleep_hours": ("checkin", "sleep_target"),
    "steps": ("checkin", "steps_target"),
    "calories": ("diet", "calorie_target"),
    "protein": ("diet", "protein_target"),
    "exercise_min": ("exercise", "exercise_target"),
}


def _avg(rows, metric):
    vals = [float(x[metric]) for x in rows if x.get(metric) is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _delta(now, prev):
    if now is None or prev is None:
        return None
    return round(now - prev, 1)


def _completion(rows, metric, target):
    vals = [float(x[metric]) for x in rows if x.get(metric) is not None]
    if not vals or not target:
        return {"observed_days": len(vals), "rate": None, "hit_days": 0}
    hit = sum(1 for v in vals if v >= float(target) * 0.9)
    return {
        "observed_days": len(vals),
        "rate": round(hit / len(vals), 3),
        "hit_days": hit,
    }


def build_weekly_facts(
    user, db: Session, days: int = 7, end_date=None, persist: bool = True,
    include_plan: bool = True, include_goal_targets: bool = True,
):
    end = end_date or business_today()
    start = end - timedelta(days=days - 1)
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    rows = daily_facts(db, user.id, start, end, include_plan=include_plan)
    prev = daily_facts(db, user.id, prev_start, prev_end, include_plan=include_plan)
    goals = (
        get_goal_settings(db, user.id, user.profile) if include_goal_targets else {}
    )
    averages = {k: _avg(rows, k) for k in METRICS}
    previous_averages = {k: _avg(prev, k) for k in METRICS}
    changes = {k: _delta(averages[k], previous_averages[k]) for k in METRICS}
    completion = (
        {
            k: _completion(rows, k, goals[target])
            for k, (_, target) in METRICS.items()
        }
        if include_goal_targets
        else {}
    )
    coverage = {
        "checkin_days": sum(1 for x in rows if x["observed"]["checkin"]),
        "diet_days": sum(1 for x in rows if x["observed"]["diet"]),
        "exercise_days": sum(1 for x in rows if x["observed"]["exercise"]),
        "total_days": days,
    }
    available_rates = [
        v["rate"]
        for k, v in completion.items()
        if k in {"water_ml", "sleep_hours", "steps", "protein", "exercise_min"}
        and v["rate"] is not None
    ]
    score = (
        round(sum(available_rates) / len(available_rates) * 100)
        if available_rates
        else None
    )
    facts = {
        "facts_version": "1.0",
        "period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "label": f"{start.month}/{start.day} - {end.month}/{end.day}",
        },
        "previous_period": {
            "start": prev_start.isoformat(),
            "end": prev_end.isoformat(),
        },
        "score": score,
        "averages": averages,
        "previous_averages": previous_averages,
        "changes": changes,
        "completion": completion,
        "coverage": coverage,
        "goals": goals,
        "days": rows,
        "streak": streak_summary(db, user.id),
        "data_quality": {
            "confidence": "high"
            if coverage["checkin_days"] >= 5
            and (coverage["diet_days"] >= 4 or coverage["exercise_days"] >= 3)
            else "medium"
            if coverage["checkin_days"] >= 3
            else "low",
            "note": "平均值仅按有记录的日期计算。",
        },
    }
    facts["highlights"] = programmatic_highlights(facts)
    if persist:
        snap = db.scalar(
            select(WeeklyReportSnapshot).where(
                WeeklyReportSnapshot.user_id == user.id,
                WeeklyReportSnapshot.period_start == start.isoformat(),
                WeeklyReportSnapshot.period_end == end.isoformat(),
            )
        ) or WeeklyReportSnapshot(
            user_id=user.id, period_start=start.isoformat(), period_end=end.isoformat()
        )
        snap.facts_version = "1.0"
        snap.facts_json = json.dumps(facts, ensure_ascii=False)
        db.add(snap)
        db.commit()
    return facts


def programmatic_highlights(facts):
    a = facts["averages"]
    c = facts["changes"]
    g = facts["goals"]
    out = []
    if (
        g.get("sleep_target") is not None
        and a["sleep_hours"] is not None
        and a["sleep_hours"] >= g["sleep_target"] * 0.9
    ):
        out.append("有记录日期的平均睡眠接近个人目标")
    if (
        g.get("exercise_target") is not None
        and a["exercise_min"] is not None
        and a["exercise_min"] >= g["exercise_target"] * 0.8
    ):
        out.append("有记录日期的运动时长接近个人目标")
    if c["exercise_min"] is not None and c["exercise_min"] >= 5:
        out.append(f"运动日均较上一周期增加 {c['exercise_min']} 分钟")
    if (
        g.get("water_target") is not None
        and a["water_ml"] is not None
        and a["water_ml"] < g["water_target"] * 0.75
    ):
        out.append("有记录日期的饮水完成度仍有提升空间")
    if (
        g.get("protein_target") is not None
        and a["protein"] is not None
        and a["protein"] < g["protein_target"] * 0.7
    ):
        out.append("有记录日期的蛋白质摄入可更规律")
    if not out:
        out.append("当前数据更适合先观察稳定性，而不是追求单项极值")
    return out[:3]
