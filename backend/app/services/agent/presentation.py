"""Deterministic UI instructions for the HealthMate companion surface.

The language model may suggest health content, but it never chooses an
arbitrary client route or claims that a write has happened.  This module turns
the already validated orchestration result into a small, versioned presentation
contract that the mini-program can safely interpret.
"""

from __future__ import annotations

from enum import Enum
import re
from typing import Any, Mapping


PRESENTATION_VERSION = "healthmate.presentation.v1"


class NavigationTarget(str, Enum):
    """Every destination the Agent presentation layer may request.

    These are semantic targets rather than paths.  The client owns the target
    to route mapping, so neither a provider response nor stored free text can
    navigate to an arbitrary URL.
    """

    PLAN_PREVIEW = "plan_preview"
    CAPABILITY_SETUP = "capability_setup"
    RECORDS = "records"
    WORKOUT = "workout"
    HEALTH_STATE = "health_state"
    PLAN_HOME = "plan_home"
    FOOD_SCAN = "food_scan"
    MOTION_ANALYSIS = "motion_analysis"
    TRENDS = "trends"
    INSIGHTS = "insights"
    GOALS = "goals"
    CHECKIN = "checkin"
    REPORT = "report"
    PROFILE = "profile"
    PROFILE_EDIT = "profile_edit"
    SETTINGS = "settings"
    AI_SETTINGS = "ai_settings"
    PRIVACY_SETTINGS = "privacy_settings"
    CAPABILITY_CENTER = "capability_center"
    EVALUATION = "evaluation"
    DIET_RECORDS = "diet_records"
    EXERCISE_RECORDS = "exercise_records"
    POLICY_CENTER = "policy_center"
    POLICY_PROTOCOL = "policy_protocol"
    POLICY_HISTORY = "policy_history"
    HOME = "home"
    STEWARD = "steward"


# Longer/specific names precede generic ones ("饮食记录" before "记录").
# These are navigation commands only; health questions still use the Agent loop.
_PAGE_ALIASES: tuple[tuple[NavigationTarget, tuple[str, ...]], ...] = (
    (NavigationTarget.AI_SETTINGS, ("AI设置", "ai设置", "API设置", "api设置", "语音设置", "模型设置")),
    (NavigationTarget.PRIVACY_SETTINGS, ("隐私设置", "隐私管理", "数据隐私")),
    (NavigationTarget.CAPABILITY_CENTER, ("能力中心", "能力设置", "插件设置")),
    (NavigationTarget.PROFILE_EDIT, ("编辑档案", "修改档案", "健康档案编辑")),
    (NavigationTarget.DIET_RECORDS, ("饮食记录", "餐食记录", "吃饭记录")),
    (NavigationTarget.EXERCISE_RECORDS, ("运动记录", "训练记录")),
    # Detail screens need a selected exercise / policy event ID. Route to
    # their safe parent when the user has not supplied that page context.
    (NavigationTarget.WORKOUT, ("动作详情", "训练详情")),
    (NavigationTarget.POLICY_HISTORY, ("策略复盘", "策略事件", "策略详情")),
    (NavigationTarget.MOTION_ANALYSIS, ("动作分析", "视频分析", "动作识别")),
    (NavigationTarget.FOOD_SCAN, ("拍照识别", "饮食拍照", "食物识别", "扫描食物")),
    (NavigationTarget.TRENDS, ("七日趋势", "能量趋势", "趋势图")),
    (NavigationTarget.HEALTH_STATE, ("健康状态", "状态页")),
    (NavigationTarget.POLICY_PROTOCOL, ("策略协议", "策略规则")),
    (NavigationTarget.POLICY_HISTORY, ("策略历史", "策略记录")),
    (NavigationTarget.POLICY_CENTER, ("个人策略", "策略中心")),
    (NavigationTarget.EVALUATION, ("能力评估", "模型评估", "评估页")),
    (NavigationTarget.CHECKIN, ("今日打卡", "打卡页", "打卡")),
    (NavigationTarget.INSIGHTS, ("健康洞察", "健康提醒", "洞察")),
    (NavigationTarget.GOALS, ("健康目标", "目标页", "目标")),
    (NavigationTarget.REPORT, ("健康报告", "周报", "报告")),
    (NavigationTarget.PLAN_HOME, ("计划页", "我的计划", "计划")),
    (NavigationTarget.RECORDS, ("记录页", "健康记录", "记录")),
    (NavigationTarget.WORKOUT, ("训练页", "训练", "锻炼")),
    (NavigationTarget.PROFILE, ("个人中心", "我的页面", "我的", "档案")),
    (NavigationTarget.SETTINGS, ("设置页", "设置")),
    (NavigationTarget.HOME, ("健身房", "养生馆", "首页")),
    (NavigationTarget.STEWARD, ("小管家", "管家页面")),
)

_PAGE_LABELS = {
    NavigationTarget.PLAN_HOME: "计划", NavigationTarget.FOOD_SCAN: "拍照识别",
    NavigationTarget.MOTION_ANALYSIS: "动作分析", NavigationTarget.TRENDS: "七日趋势",
    NavigationTarget.INSIGHTS: "健康洞察", NavigationTarget.GOALS: "健康目标",
    NavigationTarget.CHECKIN: "今日打卡", NavigationTarget.REPORT: "健康报告",
    NavigationTarget.PROFILE: "我的", NavigationTarget.PROFILE_EDIT: "档案编辑",
    NavigationTarget.SETTINGS: "设置", NavigationTarget.AI_SETTINGS: "AI 设置",
    NavigationTarget.PRIVACY_SETTINGS: "隐私设置", NavigationTarget.CAPABILITY_CENTER: "能力中心",
    NavigationTarget.EVALUATION: "能力评估", NavigationTarget.DIET_RECORDS: "饮食记录",
    NavigationTarget.EXERCISE_RECORDS: "运动记录", NavigationTarget.POLICY_CENTER: "个人策略",
    NavigationTarget.POLICY_PROTOCOL: "策略协议", NavigationTarget.POLICY_HISTORY: "策略历史",
    NavigationTarget.HOME: "健身房", NavigationTarget.STEWARD: "小管家",
    NavigationTarget.RECORDS: "记录", NavigationTarget.WORKOUT: "训练",
    NavigationTarget.HEALTH_STATE: "健康状态",
}

_NAV_COMMAND = re.compile(r"打开|进入|带我去|跳转到?|前往|去往|切换到?|回到|返回|查看|看看|(?<!过)去")


def navigation_label(target: NavigationTarget, message: str = "") -> str:
    if target == NavigationTarget.HOME and "养生馆" in message:
        return "养生馆"
    return _PAGE_LABELS.get(target, "对应页面")


def requested_navigation_target(message: str) -> NavigationTarget | None:
    """Recognise explicit page-opening requests, never arbitrary model URLs."""
    text = str(message or "").strip()
    command = _NAV_COMMAND.search(text)
    if not command or any(word in text[max(0, command.start() - 2):command.start()] for word in ("不要", "别", "不想", "无需")):
        return None
    if any(word in text[:command.start()] for word in ("制定", "生成", "规划", "安排")):
        return None
    destination_text = re.sub(r"\s+", "", text[command.end():]).lower()
    for target, aliases in _PAGE_ALIASES:
        if any(re.sub(r"\s+", "", alias).lower() in destination_text for alias in aliases):
            return target
    return None


_ACTORS = frozenset({"xiaojian", "xiaokang", "steward"})


def is_plan_result_eligible(intent: str, result: Mapping[str, Any]) -> bool:
    """Whether a reviewed result may be exposed as a confirmable plan draft."""

    if not isinstance(result, Mapping):
        return False
    if intent != "plan" or str(result.get("safety_level") or "normal") != "normal":
        return False
    plan = result.get("plan")
    return (
        isinstance(plan, Mapping)
        and isinstance(plan.get("items"), list)
        and bool(plan.get("items"))
    )


def _actor(agent_id: str) -> str:
    return agent_id if agent_id in _ACTORS else "steward"


def _mood(agent_id: str, *, safety_blocked: bool) -> str:
    if safety_blocked:
        return "calm"
    return {
        "xiaojian": "focused",
        "xiaokang": "warm",
        "steward": "calm",
    }.get(agent_id, "calm")


def build_presentation(
    *,
    intent: str,
    specialist: str,
    agent_id: str,
    run_id: int,
    result: Mapping[str, Any],
    message: str = "",
) -> dict[str, Any]:
    """Build a safe UI directive from trusted orchestration state only.

    The function deliberately ignores any ``presentation`` or ``ui_directive``
    value returned by the model.  Navigation is inferred from validated intent,
    the selected specialist and the post-sanitisation result.
    """

    safety_blocked = (
        intent == "safety"
        or str(result.get("safety_level") or "normal") != "normal"
    )
    has_plan = is_plan_result_eligible(intent, result)

    cue = "safety.pause" if safety_blocked else "answer.present"
    target: NavigationTarget | None = None
    mode = "none"
    params: dict[str, int] = {}

    requested_page = requested_navigation_target(message) if not safety_blocked else None
    if requested_page is not None:
        target = requested_page
        mode = "after_animation"
    elif has_plan and not safety_blocked:
        cue = "plan.compose"
        target = NavigationTarget.PLAN_PREVIEW
        mode = "after_animation"
        params = {"run_id": int(run_id)}
    elif intent == "plan" and not safety_blocked:
        # A plan request without a reviewed structured draft is normally a
        # capability boundary. Expose a deterministic setup action instead of
        # leaving the user with an unexplained text-only answer.
        target = NavigationTarget.CAPABILITY_SETUP
        mode = "on_user_action"
    elif (
        specialist == "coach"
        and intent == "exercise_knowledge"
        and not safety_blocked
    ):
        cue = "workout.guide"
        target = NavigationTarget.WORKOUT
        mode = "on_user_action"

    actions = result.get("actions")
    confirmation_required = has_plan or (isinstance(actions, list) and bool(actions))
    return {
        "version": PRESENTATION_VERSION,
        "actor": _actor(agent_id),
        "cue": cue,
        "mood": _mood(agent_id, safety_blocked=safety_blocked),
        "navigation": {
            "target": target.value if target is not None else None,
            "mode": mode,
            "params": params,
        },
        # This is a statement of observable state, not a suggestion from the
        # model.  Only the existing proposal/confirm endpoint may change it.
        "write": {
            "status": "not_applied",
            "automatic": False,
            "confirmation_required": bool(confirmation_required),
        },
    }


def build_plan_preview(
    run_id: int,
    result: Mapping[str, Any],
    *,
    applied: bool = False,
) -> dict[str, Any] | None:
    """Return the minimal read-only draft needed by a plan preview screen."""

    plan = result.get("plan")
    if not isinstance(plan, Mapping):
        return None
    raw_items = plan.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return None

    items: list[dict[str, Any]] = []
    for raw in raw_items[:10]:
        if not isinstance(raw, Mapping):
            continue
        try:
            date_offset = max(0, min(6, int(raw.get("date_offset", 0))))
        except (TypeError, ValueError):
            date_offset = 0
        title = str(raw.get("title") or "").strip()[:160]
        if not title:
            continue
        target = raw.get("target")
        items.append(
            {
                "date_offset": date_offset,
                "category": str(raw.get("category") or "habit")[:30],
                "title": title,
                "description": str(raw.get("description") or "")[:600],
                "target": dict(target) if isinstance(target, Mapping) else {},
            }
        )
    if not items:
        return None
    return {
        "run_id": int(run_id),
        "status": "applied" if applied else "draft",
        "read_only": True,
        "title": str(plan.get("title") or "本周健康计划")[:160],
        "items": items,
        "write": {
            "status": "applied" if applied else "not_applied",
            "automatic": False,
            "confirmation_required": not applied,
        },
    }
