"""DeepSeek summary generation for food / motion analysis results.

Called lazily when a finished job's result is first fetched. The summary ties
the raw analysis to the user's daily goals and training intent, so the UI can
show one calm, explainable paragraph instead of raw numbers.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.ai.gateway import get_provider
from app.services.health import get_goal_settings, today_summary
from app.services.training_semantics import get_training_intent

SUMMARY_SYSTEM = (
    "你是一位熟悉的朋友兼健身教练/营养师，点评要自然、有人味、像聊天，"
    "不要用AI腔、不要罗列术语。只根据提供的分析结果写一段中文点评（80-140字）："
    "先聚焦食物/动作本身说人话，再视情况对照用户真实目标给1句建议。"
    "不要编造数据、不要做疾病诊断、不要给出无法执行的话。"
    "如果用户还没有设置目标，就不要拿默认数值对比，只需一句带过：建议先在计划页设置目标。"
)


def _food_facts(result: dict) -> str:
    return (
        f"识别餐食：{result.get('dish_name') or '未命名'}；"
        f"热量 {result.get('calories')} kcal（区间 {result.get('calorie_range_low')}-{result.get('calorie_range_high')}）；"
        f"蛋白质 {result.get('protein')} g、碳水 {result.get('carbs')} g、脂肪 {result.get('fat')} g；"
        f"置信度 {result.get('confidence')}。"
    )


def _motion_facts(result: dict) -> str:
    score = result.get("score") or {}
    recognition = result.get("recognition") or {}
    method = str(recognition.get("method") or result.get("method") or "")
    source = "DeepSeek 视觉参与识别" if "deepseek" in method else "本地规则识别"
    pose = result.get("pose") or {}
    return (
        f"动作类型：{pose.get('exercise_type') or recognition.get('selected_type') or '未确认'}（{source}）；"
        f"次数 {pose.get('reps')}；"
        f"评分：完成度 {score.get('completeness')}、稳定性 {score.get('stability')}、"
        f"节奏 {score.get('rhythm_control')}、需调整 {score.get('risk_index')}、总分 {score.get('overall')}；"
        f"识别说明：{recognition.get('reason') or ''}"
    )


def _goal_context(db: Session, user_id: int, profile) -> str:
    from app.models import HealthGoalSetting
    has_goals = (
        db.scalar(select(HealthGoalSetting).where(HealthGoalSetting.user_id == user_id))
        is not None
    )
    if not has_goals:
        parts = ["用户尚未设置饮食/运动目标（不要用默认数值对比）。"]
    else:
        goals = get_goal_settings(db, user_id, profile)
        parts = [
            "用户已设置目标："
            f"热量 {goals['calorie_target']} kcal、蛋白质 {goals['protein_target']} g、"
            f"运动 {goals['exercise_target']} 分钟、饮水 {goals['water_target']} ml。"
        ]
        try:
            summary = today_summary(db, user_id, profile)
        except Exception:
            summary = None
        if summary:
            parts.append(
                "今日实际："
                f"热量 {summary['calories']} kcal、蛋白质 {summary['protein']} g、"
                f"运动 {summary['exercise_min']} 分钟、饮水 {summary['water_ml']} ml。"
            )
    intent = get_training_intent(db, user_id)
    if intent and intent.get("confirmed"):
        targets = "、".join(intent.get("target_body_parts") or []) or "未设定"
        goals_ = "、".join(intent.get("goals") or []) or "未设定"
        parts.append(f"训练意图：目标部位 {targets}，训练目标 {goals_}。")
    else:
        parts.append("训练意图：用户尚未确认。")
    return " ".join(parts)


async def generate_result_summary(db: Session, user, result: dict) -> str | None:
    """Return a 80-140 character Chinese summary, or None when unavailable."""
    if not isinstance(result, dict):
        return None
    if result.get("dish_name"):
        facts = _food_facts(result)
        prompt = (
            f"请总结这餐饮食：{facts}\n\n{_goal_context(db, user.id, user.profile)}\n\n"
            "写 80-140 字中文点评，像朋友聊天：这顿饭营养结构怎么样、份量如何、"
            "哪些可以顺手改进（如少油少盐、加蔬菜）；若用户已设置目标，对照给1句进展；"
            "若未设置，只用一句提醒可以先设置饮食目标。"
        )
    elif result.get("pose") or result.get("recognition"):
        facts = _motion_facts(result)
        prompt = (
            f"请总结这次动作分析：{facts}\n\n{_goal_context(db, user.id, user.profile)}\n\n"
            "写 80-140 字中文点评，像教练聊天：动作做得到不到位（姿态、节奏、稳定）、"
            "这次练得怎么样；若用户已确认训练意图或设置运动目标，对照给1句；"
            "若未设置，只用一句提醒可以先设置训练意图。若动作未确认，如实说一句并建议手动选择动作。"
        )
    else:
        return None
    try:
        provider = get_provider(user)
        ai = await provider.chat(SUMMARY_SYSTEM, prompt)
        text = (ai.text or "").strip()
        return text if len(text) >= 10 else None
    except Exception:
        return None


def attach_summary_into_result(result: dict, summary: str | None) -> None:
    """Inline the summary into the cached result dict (mutates in place)."""
    if summary:
        result["summary"] = summary
