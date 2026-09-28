# -*- coding: utf-8 -*-
"""Stage-1 patch: structured evidence annotations in proactive.py."""
import io

PATH = r"D:\学习资料\计算机应用大赛\health-assistant\backend\app\services\agent\proactive.py"

s = io.open(PATH, encoding="utf-8").read()

# exercise_stall evidence
OLD = '''    if stalled >= 3 and exercised >= 1:
        return {
            "code": "exercise_stall",
            "severity": "medium",
            "title": "运动出现 3 天以上断档",
            "evidence": f"近 7 天仅 {exercised} 天有运动记录，最近已连续 {stalled} 天无运动；目标为每周 {target:g} 分钟。",
            "advice": "从 10-15 分钟轻量活动重新启动（快走/拉伸），恢复节奏后再回到计划，不必一次补回。",
            "source": "recent_7d + goals",
        }
    return None'''
NEW = '''    if stalled >= 3 and exercised >= 1:
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
    return None'''
assert s.count(OLD) == 1
s = s.replace(OLD, NEW)

# sleep_deficit evidence
OLD = '''    if len(short_days) >= 2 and latest_sleep is not None and latest_sleep < 6:
        return {
            "code": "sleep_deficit",
            "severity": "medium",
            "title": "连续睡眠不足",
            "evidence": f"近 7 天有 {len(short_days)} 天睡眠低于 6 小时，最新一天 {latest_sleep:g} 小时。",
            "advice": "今日训练优先轻量恢复或休息；把睡眠放在第一位，避免疲劳积累。",
            "source": "recent_7d",
        }
    return None'''
NEW = '''    if len(short_days) >= 2 and latest_sleep is not None and latest_sleep < 6:
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
    return None'''
assert s.count(OLD) == 1
s = s.replace(OLD, NEW)

# weight_rise evidence
OLD = '''    if last_three[2] > last_three[1] > last_three[0]:
        return {
            "code": "weight_rise",
            "severity": "low",
            "title": "体重呈连续上升趋势",
            "evidence": f"最近 3 次记录为 {last_three[0]:g} → {last_three[1]:g} → {last_three[2]:g} kg。",
            "advice": "先核对测量时间与条件是否一致；结合饮食与活动记录观察，不必焦虑单次波动。",
            "source": "recent_7d",
        }
    return None'''
NEW = '''    if last_three[2] > last_three[1] > last_three[0]:
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
    return None'''
assert s.count(OLD) == 1
s = s.replace(OLD, NEW)

# motion_decline evidence
OLD = '''    return {
        "code": "motion_decline",
        "severity": "medium",
        "title": f"{exercise.get('exercise_name', '动作')}近期表现下滑",
        "evidence": f"近期平均分较前段下降 {abs(change):g} 分，样本 {exercise.get('sessions')} 次。",
        "advice": "本周减少该动作训练量，复核机位、疲劳与动作技术，优先质量而不是次数。",
        "source": "motion_profile",
    }'''
NEW = '''    return {
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
    }'''
assert s.count(OLD) == 1
s = s.replace(OLD, NEW)

# record_gap evidence
OLD = '''    if not any_record:
        return {
            "code": "record_gap",
            "severity": "low",
            "title": "近 7 天还没有健康记录",
            "evidence": "近 7 天没有睡眠/饮水/体重/运动任何一项记录。",
            "advice": "从最简单的每日打卡开始（睡眠、饮水量），让智能体可以为你提供个性化建议。",
            "source": "recent_7d",
        }
    return None'''
NEW = '''    if not any_record:
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
    return None'''
assert s.count(OLD) == 1
s = s.replace(OLD, NEW)

io.open(PATH, "w", encoding="utf-8", newline="").write(s)
print("OK proactive.py evidence annotations added")
