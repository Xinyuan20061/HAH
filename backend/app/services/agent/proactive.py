"""Proactive Health Guardian (Agent v3): deterministic forward-looking insights.

The Agent does not only answer questions — it watches the user's records and
surfaces risks *before* the user asks: exercise stalls, sleep deficits, weight
trends, motion-quality decline and record gaps. Everything here is a
deterministic rule over already-audited context; no model inference is claimed.
"""
from __future__ import annotations

PROACTIVE_VERSION = "v3.1"
PROACTIVE_CODES = {
    "exercise_stall",
    "sleep_deficit",
    "motion_decline",
    "weight_rise",
    "record_gap",
}

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _number(value) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _days_of(recent: dict) -> list[dict]:
    days = recent.get("days") if isinstance(recent.get("days"), list) else []
    return [d for d in days if isinstance(d, dict)]


def _consecutive_no_exercise(recent: dict) -> int:
    """Count trailing days without any exercise record (from today backwards)."""
    days = _days_of(recent)
    count = 0
    for day in reversed(days):
        observed = day.get("observed") if isinstance(day.get("observed"), dict) else {}
        if not observed.get("exercise") and _number(day.get("exercise_min")) in (None, 0):
            count += 1
        else:
            break
    return count


def _exercise_days(recent: dict) -> int:
    return sum(
        1
        for day in _days_of(recent)
        if (day.get("observed") or {}).get("exercise")
        or _number(day.get("exercise_min")) not in (None, 0)
    )


def _build_exercise_stall(context: dict) -> dict | None:
    recent = context.get("recent_7d") or {}
    goals = context.get("goals") or {}
    target = _number(goals.get("exercise_target"))
    if target is None or target <= 0:
        return None
    stalled = _consecutive_no_exercise(recent)
    exercised = _exercise_days(recent)
    if stalled >= 3 and exercised >= 1:
        return {
            "code": "exercise_stall",
            "severity": "medium",
            "title": "运动出现 3 天以上断档",
            "evidence": f"近 7 天仅 {exercised} 天有运动记录，最近已连续 {stalled} 天无运动；目标为每周 {target:g} 分钟。",
            "advice": "从 10-15 分钟轻量活动重新启动（快走/拉伸），恢复节奏后再回到计划，不必一次补回。",
            "source": "recent_7d + goals",
            "evidence_meta": {
                "facts": [
                    {"name": "exercise_days", "value": exercised, "unit": "天", "source": "confirmed_records"},
                    {"name": "consecutive_no_exercise_days", "value": stalled, "unit": "天", "source": "confirmed_records"},
                    {"name": "weekly_exercise_target", "value": target, "unit": "分钟", "source": "user_goal"},
                ],
                "knowledge_ids": [],
                "limitations": ["3 天没有运动记录，无法判断是否实际未运动；目标来自用户设定。"],
                "evidence_type": "record_observation",
            },
        }
    return None


def _build_sleep_deficit(context: dict) -> dict | None:
    recent = context.get("recent_7d") or {}
    days = _days_of(recent)
    short_days = [
        day
        for day in days
        if _number(day.get("sleep_hours")) is not None
        and _number(day.get("sleep_hours")) < 6
    ]
    latest_sleep = _number(days[-1].get("sleep_hours")) if days else None
    if len(short_days) >= 2 and latest_sleep is not None and latest_sleep < 6:
        return {
            "code": "sleep_deficit",
            "severity": "medium",
            "title": "连续睡眠不足",
            "evidence": f"近 7 天有 {len(short_days)} 天睡眠低于 6 小时，最新一天 {latest_sleep:g} 小时。",
            "advice": "今日训练优先轻量恢复或休息；把睡眠放在第一位，避免疲劳积累。",
            "source": "recent_7d",
            "evidence_meta": {
                "facts": [
                    {"name": "short_sleep_days", "value": len(short_days), "unit": "天", "source": "confirmed_records"},
                    {"name": "latest_sleep_hours", "value": latest_sleep, "unit": "小时", "source": "confirmed_records"},
                ],
                "knowledge_ids": [],
                "limitations": ["睡眠时长来自用户记录，缺失的天数不按零处理；未记录日无法判断实际睡眠。"],
                "evidence_type": "record_observation",
            },
        }
    return None


def _build_weight_rise(context: dict) -> dict | None:
    recent = context.get("recent_7d") or {}
    weights = [
        _number(day.get("weight_kg"))
        for day in _days_of(recent)
        if _number(day.get("weight_kg")) is not None
    ]
    if len(weights) < 3:
        return None
    last_three = weights[-3:]
    if last_three[2] > last_three[1] > last_three[0]:
        return {
            "code": "weight_rise",
            "severity": "low",
            "title": "体重呈连续上升趋势",
            "evidence": f"最近 3 次记录为 {last_three[0]:g} → {last_three[1]:g} → {last_three[2]:g} kg。",
            "advice": "先核对测量时间与条件是否一致；结合饮食与活动记录观察，不必焦虑单次波动。",
            "source": "recent_7d",
            "evidence_meta": {
                "facts": [
                    {"name": "weight_trend", "value": f"{last_three[0]:g}->{last_three[1]:g}->{last_three[2]:g}", "unit": "kg", "source": "confirmed_records"},
                ],
                "knowledge_ids": [],
                "limitations": ["体重受测量时间、着装和水分影响；连续 3 次上升不等于真实体脂趋势。"],
                "evidence_type": "record_observation",
            },
        }
    return None


def _build_motion_decline(context: dict) -> dict | None:
    motion = context.get("motion_profile") or {}
    worst = None
    for exercise in motion.get("by_exercise", []):
        if not isinstance(exercise, dict):
            continue
        change = _number(exercise.get("recent_change_points"))
        if change is not None and change <= -8:
            candidate = (change, exercise)
            if worst is None or candidate[0] < worst[0]:
                worst = candidate
    if worst is None:
        return None
    change, exercise = worst
    return {
        "code": "motion_decline",
        "severity": "medium",
        "title": f"{exercise.get('exercise_name', '动作')}近期表现下滑",
        "evidence": f"近期平均分较前段下降 {abs(change):g} 分，样本 {exercise.get('sessions')} 次。",
        "advice": "本周减少该动作训练量，复核机位、疲劳与动作技术，优先质量而不是次数。",
        "source": "motion_profile",
        "evidence_meta": {
            "facts": [
                {"name": "recent_change_points", "value": change, "unit": "分", "source": "motion_scores"},
                {"name": "session_count", "value": exercise.get("sessions"), "unit": "次", "source": "motion_scores"},
            ],
            "knowledge_ids": [],
            "limitations": ["动作评分来自视频规则基线，只对登记固定集口径有效；样本过少时不代表能力下降。"],
            "evidence_type": "record_observation",
        },
    }


def _build_record_gap(context: dict) -> dict | None:
    recent = context.get("recent_7d") or {}
    days = _days_of(recent)
    if not days:
        return None
    any_record = any(
        any((day.get("observed") or {}).values()) for day in days
    )
    if not any_record:
        return {
            "code": "record_gap",
            "severity": "low",
            "title": "近 7 天还没有健康记录",
            "evidence": "近 7 天没有睡眠/饮水/体重/运动任何一项记录。",
            "advice": "从最简单的每日打卡开始（睡眠、饮水量），让智能体可以为你提供个性化建议。",
            "source": "recent_7d",
            "evidence_meta": {
                "facts": [
                    {"name": "recorded_days", "value": 0, "unit": "天", "source": "confirmed_records"},
                ],
                "knowledge_ids": [],
                "limitations": ["没有任何记录时无法判断真实活动情况；该提醒只反映记录覆盖，不代表实际行为。"],
                "evidence_type": "record_observation",
            },
        }
    return None


_BUILDERS = [
    _build_exercise_stall,
    _build_sleep_deficit,
    _build_motion_decline,
    _build_weight_rise,
    _build_record_gap,
]


def _data_quality(context: dict) -> dict:
    days = _days_of(context.get("recent_7d") or {})
    recorded_days = 0
    categories: set[str] = set()
    for day in days:
        observed = day.get("observed") if isinstance(day.get("observed"), dict) else {}
        present = [str(key) for key, value in observed.items() if value]
        if present:
            recorded_days += 1
            categories.update(present)
    expected_days = len(days) or 7
    ratio = round(recorded_days / expected_days * 100) if expected_days else 0
    if recorded_days >= 5:
        level, label = "high", "记录较充分"
    elif recorded_days >= 2:
        level, label = "medium", "记录一般"
    else:
        level, label = "low", "记录较少"
    return {
        "recorded_days": recorded_days,
        "expected_days": expected_days,
        "coverage_pct": ratio,
        "level": level,
        "label": label,
        "observed_categories": sorted(categories),
        "message": f"近 {expected_days} 天有 {recorded_days} 天包含健康记录；数据越完整，提醒越有针对性。",
    }


def build_proactive_insights(context: dict, max_items: int = 4) -> dict:
    """Return forward-looking insights sorted by severity (high first)."""
    insights = [builder(context) for builder in _BUILDERS]
    insights = [item for item in insights if item is not None]
    insights.sort(key=lambda item: SEVERITY_ORDER.get(item["severity"], 9))
    insights = insights[:max_items]
    if insights:
        summary = "；".join(item["title"] for item in insights) + "。"
    else:
        summary = "当前没有需要主动干预的信号，保持记录即可。"
    return {
        "version": PROACTIVE_VERSION,
        "summary": summary,
        "count": len(insights),
        "insights": insights,
        "data_quality": _data_quality(context),
        "policy": "仅依据有记录的数据做保守预警；不诊断疾病，用户不适时应停止训练并就医。",
    }
