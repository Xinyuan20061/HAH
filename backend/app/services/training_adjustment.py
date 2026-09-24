from __future__ import annotations

from copy import deepcopy
import math


def _number(value) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


def build_training_adjustment(context: dict) -> dict:
    today = context.get("today") if isinstance(context.get("today"), dict) else {}
    goals = context.get("goals") if isinstance(context.get("goals"), dict) else {}
    recent = context.get("recent_7d") if isinstance(context.get("recent_7d"), dict) else {}
    motion = context.get("motion_profile") if isinstance(context.get("motion_profile"), dict) else {}
    weekly = context.get("weekly_facts") if isinstance(context.get("weekly_facts"), dict) else {}
    observed = today.get("observed") if isinstance(today.get("observed"), dict) else {}
    target = int(_number(goals.get("exercise_target")) or 30)
    reasons: list[dict] = []
    coaching_focus: list[dict] = []
    max_session_minutes = min(45, max(15, target))
    intensity = "moderate"
    recovery_priority = False
    progression_pct = 0

    sleep = _number(today.get("sleep_hours"))
    if observed.get("checkin") and sleep is not None and 0 < sleep < 6:
        recovery_priority = True
        intensity = "light"
        max_session_minutes = min(max_session_minutes, 20)
        reasons.append(
            {
                "code": "short_sleep_today",
                "label": "今日恢复优先",
                "evidence": f"今日记录睡眠 {sleep:g} 小时，低于6小时保守阈值。",
                "effect": "今日训练改为轻活动或恢复，单次不超过20分钟。",
            }
        )
    mood = str(today.get("mood") or "")
    if observed.get("checkin") and mood in {"tired", "low"}:
        intensity = "light"
        max_session_minutes = min(max_session_minutes, 25)
        reasons.append(
            {
                "code": "low_readiness_today",
                "label": "主观状态偏低",
                "evidence": f"今日心情/状态记录为 {mood}。",
                "effect": "降低训练复杂度和时长，并保留停止训练的自主判断。",
            }
        )

    days = recent.get("days") if isinstance(recent.get("days"), list) else []
    exercise_values = [
        float(day["exercise_min"])
        for day in days
        if isinstance(day, dict) and _number(day.get("exercise_min")) is not None
    ]
    if len(exercise_values) >= 5 and sum(exercise_values) >= target * 4:
        max_session_minutes = min(max_session_minutes, 30)
        reasons.append(
            {
                "code": "high_recent_frequency",
                "label": "近期训练频率较高",
                "evidence": f"近7天有 {len(exercise_values)} 天记录训练，共 {round(sum(exercise_values))} 分钟。",
                "effect": "本周保留恢复间隔，单次计划时长上限30分钟。",
            }
        )

    sample_count = int(motion.get("sample_count") or 0)
    dimensions = motion.get("dimensions") if isinstance(motion.get("dimensions"), dict) else {}
    if sample_count >= 3:
        for key, threshold, label, advice in [
            ("stability", 65, "稳定性练习", "降低动作速度，加入核心稳定和控制练习"),
            ("rhythm_control", 65, "节奏控制", "使用可控下放和短暂停顿，不追求快速次数"),
            ("completion", 65, "动作幅度控制", "在无痛范围内分级练习完整动作幅度"),
        ]:
            value = _number(dimensions.get(key))
            if value is not None and value < threshold:
                coaching_focus.append(
                    {
                        "code": key,
                        "label": label,
                        "evidence": f"近30天 {label} 维度为 {value:g} 分，样本 {sample_count} 次。",
                        "advice": advice,
                    }
                )
        deviation = _number(dimensions.get("deviation_index"))
        if deviation is not None and deviation >= 35:
            intensity = "light" if intensity == "light" else "moderate"
            max_session_minutes = min(max_session_minutes, 30)
            reasons.append(
                {
                    "code": "motion_deviation_attention",
                    "label": "先纠正动作再增加负荷",
                    "evidence": f"近30天二维规则偏差均值为 {deviation:g}，基于 {sample_count} 次视频分析。",
                    "effect": "不增加复杂度或负荷，优先技术练习。",
                }
            )
        for finding in motion.get("frequent_findings", [])[:2]:
            if isinstance(finding, dict) and int(finding.get("count") or 0) >= 2:
                coaching_focus.append(
                    {
                        "code": "frequent_finding",
                        "label": "高频动作问题",
                        "evidence": f"“{str(finding.get('finding', ''))[:80]}”出现 {finding['count']} 次。",
                        "advice": "本周训练中降低速度并逐组复核该问题",
                    }
                )
        for exercise in motion.get("by_exercise", []):
            if not isinstance(exercise, dict) or int(exercise.get("sessions") or 0) < 3:
                continue
            change = _number(exercise.get("recent_change_points"))
            if change is not None and change <= -8:
                reasons.append(
                    {
                        "code": "recent_motion_decline",
                        "label": f"{exercise.get('exercise_name', '动作')}近期表现下降",
                        "evidence": f"近期平均分较前段下降 {abs(change):g} 分。",
                        "effect": "减少该动作训练量，先复核机位、疲劳和动作技术。",
                    }
                )

    data_quality = motion.get("data_quality") if isinstance(motion.get("data_quality"), dict) else {}
    overall = _number(dimensions.get("motion_quality"))
    stability = _number(dimensions.get("stability"))
    rhythm = _number(dimensions.get("rhythm_control"))
    if (
        data_quality.get("level") == "high"
        and overall is not None
        and overall >= 85
        and (stability or 0) >= 80
        and (rhythm or 0) >= 80
        and not recovery_priority
        and not coaching_focus
    ):
        progression_pct = 5
        reasons.append(
            {
                "code": "stable_motion_progression",
                "label": "动作表现稳定",
                "evidence": f"近30天动作质量 {overall:g} 分，稳定性 {stability:g}，节奏 {rhythm:g}。",
                "effect": "仅建议增加约5%的训练量，并继续观察下一周期。",
            }
        )

    weekly_quality = (
        weekly.get("data_quality", {}).get("confidence")
        if isinstance(weekly.get("data_quality"), dict)
        else "low"
    )
    reliable_sources = int(sample_count >= 3) + int(weekly_quality in {"medium", "high"}) + int(bool(observed.get("checkin")))
    confidence = "high" if reliable_sources >= 3 else "medium" if reliable_sources >= 1 else "low"
    mode = "adaptive" if reasons or coaching_focus else "baseline"
    if mode == "baseline":
        summary = "现有记录不足以支持个性化增减量，保持基础计划并继续记录。"
    else:
        labels = [item["label"] for item in reasons[:2]] + [item["label"] for item in coaching_focus[:2]]
        summary = "；".join(labels) + "。"
    return {
        "version": "1.0",
        "mode": mode,
        "confidence": confidence,
        "summary": summary,
        "constraints": {
            "intensity": intensity,
            "max_session_minutes": max_session_minutes,
            "recovery_priority_today": recovery_priority,
            "progression_pct": progression_pct,
        },
        "reasons": reasons,
        "coaching_focus": coaching_focus[:4],
        "policy": "仅依据有记录数据进行保守调整；不诊断疲劳、损伤或疾病，用户不适时应停止训练。",
    }


def apply_plan_guardrails(plan: dict | None, adjustment: dict) -> tuple[dict | None, list[str]]:
    if not isinstance(plan, dict) or not isinstance(plan.get("items"), list):
        return plan, []
    result = deepcopy(plan)
    items = result["items"]
    constraints = adjustment.get("constraints", {})
    limit = int(constraints.get("max_session_minutes") or 45)
    changes: list[str] = []
    for item in items:
        if item.get("category") not in {"exercise", "recovery"}:
            continue
        target = item.get("target") if isinstance(item.get("target"), dict) else {}
        for key in ("duration_min", "exercise_min"):
            value = _number(target.get(key))
            if value is None:
                continue
            target[key] = max(0, min(round(value), limit))
            if value > limit:
                changes.append(f"将“{item.get('title', '训练')}”时长限制为{limit}分钟")
            elif value < 0:
                changes.append(f"移除“{item.get('title', '训练')}”中的负时长")
        item["target"] = target

    if constraints.get("recovery_priority_today"):
        today_item = next(
            (item for item in items if item.get("date_offset") == 0 and item.get("category") == "exercise"),
            None,
        )
        if today_item:
            today_item.update(
                category="recovery",
                title="轻量恢复与活动度",
                description="今日睡眠或主观状态记录提示恢复优先：选择轻松步行、呼吸和无痛活动度练习，不追求训练量。",
                target={"duration_min": min(limit, 20)},
            )
            changes.append("将今日训练调整为轻量恢复")
        elif not any(item.get("date_offset") == 0 and item.get("category") == "recovery" for item in items):
            items.insert(
                0,
                {
                    "date_offset": 0,
                    "category": "recovery",
                    "title": "轻量恢复与活动度",
                    "description": "今日以轻松步行、呼吸和无痛活动度练习为主。",
                    "target": {"duration_min": min(limit, 20)},
                },
            )
            changes.append("新增今日轻量恢复")

    focus = adjustment.get("coaching_focus") or []
    first_training = next((item for item in items if item.get("category") == "exercise"), None)
    if first_training and focus:
        focus_text = "；".join(str(item.get("advice") or item.get("label")) for item in focus[:2])
        description = str(first_training.get("description") or "")
        if focus_text and focus_text not in description:
            first_training["description"] = (description + " 本次重点：" + focus_text + "。").strip()
            changes.append("将动作画像中的薄弱项加入首个训练日")
    result["items"] = items[:10]
    return result, changes
