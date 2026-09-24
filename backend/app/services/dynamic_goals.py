from __future__ import annotations
from app.core.time import business_today
from app.core.time import utc_now, utc_iso
import json
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import HealthGoalAdjustment, HealthGoalSetting
from app.services.health_data import daily_facts
from app.services.health import get_goal_settings

RULES = {
    "water_target": {
        "metric": "water_ml",
        "source": "checkin",
        "min": 1200,
        "max": 3000,
        "step": 100,
        "up": 1.05,
        "down": 0.90,
        "label": "每日饮水",
    },
    "exercise_target": {
        "metric": "exercise_min",
        "source": "exercise",
        "min": 15,
        "max": 90,
        "step": 5,
        "up": 1.10,
        "down": 0.90,
        "label": "每日运动",
    },
    "steps_target": {
        "metric": "steps",
        "source": "checkin",
        "min": 4000,
        "max": 15000,
        "step": 500,
        "up": 1.10,
        "down": 0.90,
        "label": "每日步数",
    },
}


def _round_step(v, step):
    return round(v / step) * step


def calculate_suggestions(rows: list[dict], goals: dict, window_days: int = 14):
    suggestions = []
    for target_key, r in RULES.items():
        if target_key == "exercise_target":
            vals = [
                float(x.get("exercise_min") or 0)
                for x in rows
                if x.get("exercise_min") is not None
                or x.get("observed", {}).get("checkin")
            ]
        else:
            vals = [
                float(x[r["metric"]]) for x in rows if x.get(r["metric"]) is not None
            ]
        if len(vals) < 5:
            suggestions.append(
                {
                    "target_key": target_key,
                    "metric": r["metric"],
                    "label": r["label"],
                    "observed_days": len(vals),
                    "completion_rate": None,
                    "previous_target": goals[target_key],
                    "recommended_target": goals[target_key],
                    "decision": "insufficient_data",
                    "reason": "至少需要 5 个有记录日期后再调整，避免把漏记误判为未完成。",
                }
            )
            continue
        target = float(goals[target_key])
        rate = sum(1 for v in vals if v >= target * 0.9) / len(vals)
        avg = sum(vals) / len(vals)
        new = target
        decision = "keep"
        reason = "当前目标与实际执行水平基本匹配。"
        if rate < 0.35:
            new = max(r["min"], _round_step(target * r["down"], r["step"]))
            decision = "reduce" if new < target else "keep"
            reason = f"近 {window_days} 天仅 {rate:.0%} 的有记录日期达到目标，先小幅降低难度以提高可持续性。"
        elif rate >= 0.85 and avg >= target * 0.95:
            new = min(r["max"], _round_step(target * r["up"], r["step"]))
            decision = "increase" if new > target else "keep"
            reason = f"近 {window_days} 天有 {rate:.0%} 的有记录日期稳定达标，可小幅递增而不是一次提高过多。"
        suggestions.append(
            {
                "target_key": target_key,
                "metric": r["metric"],
                "label": r["label"],
                "observed_days": len(vals),
                "completion_rate": round(rate, 3),
                "average": round(avg, 1),
                "previous_target": target,
                "recommended_target": new,
                "decision": decision,
                "reason": reason,
            }
        )
    return suggestions


def evaluate_dynamic_goals(db: Session, user, window_days: int = 14, persist=True):
    end = business_today()
    start = end - timedelta(days=window_days - 1)
    rows = daily_facts(db, user.id, start, end)
    goals = get_goal_settings(db, user.id, user.profile)
    items = calculate_suggestions(rows, goals, window_days)
    if persist:
        for x in items:
            if x["decision"] not in {"increase", "reduce"}:
                continue
            existing = db.scalar(
                select(HealthGoalAdjustment)
                .where(
                    HealthGoalAdjustment.user_id == user.id,
                    HealthGoalAdjustment.metric == x["target_key"],
                    HealthGoalAdjustment.window_days == window_days,
                    HealthGoalAdjustment.status == "pending",
                )
                .order_by(HealthGoalAdjustment.created_at.desc())
            )
            if (
                existing
                and float(existing.recommended_target) == float(x["recommended_target"])
                and float(existing.previous_target) == float(x["previous_target"])
            ):
                x["adjustment_id"] = existing.id
                continue
            if existing:
                existing.status = "superseded"
                db.add(existing)
            item = HealthGoalAdjustment(
                user_id=user.id,
                metric=x["target_key"],
                window_days=window_days,
                observed_days=x["observed_days"],
                completion_rate=x["completion_rate"] or 0,
                previous_target=float(x["previous_target"]),
                recommended_target=float(x["recommended_target"]),
                facts_json=json.dumps(x, ensure_ascii=False),
                status="pending",
            )
            db.add(item)
            db.flush()
            x["adjustment_id"] = item.id
        db.commit()
    return {
        "window_days": window_days,
        "policy": "规则算法只调整运动、步数和饮水；睡眠、热量、蛋白质不由完成率机械上调。单次调整不超过约 10%。",
        "items": items,
    }


def apply_adjustment(db: Session, user_id: int, adjustment_id: int):
    a = db.get(HealthGoalAdjustment, adjustment_id)
    if not a or a.user_id != user_id:
        return None
    if a.status == "applied":
        return a
    if a.status != "pending":
        return None
    setting = db.scalar(
        select(HealthGoalSetting).where(HealthGoalSetting.user_id == user_id)
    ) or HealthGoalSetting(user_id=user_id)
    setattr(
        setting,
        a.metric,
        int(a.recommended_target)
        if a.metric != "sleep_target"
        else float(a.recommended_target),
    )
    a.status = "applied"
    a.applied_at = utc_now()
    db.add(setting)
    db.add(a)
    db.commit()
    db.refresh(a)
    return a
