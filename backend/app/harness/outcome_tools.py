"""Outcome-learning Harness tools — plan §9.5.

The plan names four tools explicitly:

    outcomes.history.read
    preferences.read
    experiment.result.read
    next_action.rank

``next_action.rank`` lives in ``planning_tools`` as ``decision.next_best_action``
(it ranks by the Decision Contract's weights). The other three are here.

Every one of them is a *read*: memory can be inspected and cleared, but it is never
written by a model, and an unobserved result is reported as unobserved rather than
turned into evidence (plan §9.5: ``insufficient_data`` 不得被当作正向证据).
"""

from __future__ import annotations

from typing import Any

from app.harness.contracts import ToolContext, ToolSpec
from app.services.agent.outcome import (
    NEVER_LEARNED,
    memory_view,
    outcome_history,
    policy_snapshot,
    rank_variants,
)

TOOL_PREFERENCES_READ = "preferences.read"
TOOL_OUTCOMES_HISTORY = "outcomes.history.read"
TOOL_EXPERIMENT_RESULT = "experiment.result.read"
TOOL_NEXT_ACTION_RANK = "next_action.rank"

# Read-only memory/outcome tools are usable by every persona, including the planner.
ALL_PERSONAS: tuple[str, ...] = ("xiaojian", "xiaokang", "steward", "planner")

MAX_HISTORY_DAYS = 365


def _owner(context: ToolContext) -> int | None:
    user = getattr(context, "user", None)
    uid = getattr(user, "id", None)
    return uid if isinstance(uid, int) else None


def _days(arguments: dict, default: int = 30) -> int:
    try:
        value = int(arguments.get("days") or default)
    except (TypeError, ValueError):
        value = default
    return max(1, min(MAX_HISTORY_DAYS, value))


def read_preferences_tool(context: ToolContext, arguments: dict) -> dict[str, Any]:
    """Read long-term structured memory with its provenance and boundaries."""
    uid = _owner(context)
    if uid is None:
        return {"found": False, "reason": "no_owner"}
    view = memory_view(context.db, uid)
    return {**view, "content_is_data": True}


def read_outcomes_history(context: ToolContext, arguments: dict) -> dict[str, Any]:
    """Read what actually happened after past actions."""
    uid = _owner(context)
    if uid is None:
        return {"found": False, "reason": "no_owner"}
    days = _days(arguments)
    action_key = str(arguments.get("action_key") or "").strip()
    payload = outcome_history(context.db, uid, days=days)
    policies = policy_snapshot(context.db, uid)
    if action_key:
        payload["items"] = [
            item for item in payload["items"] if item["action_key"] == action_key
        ]
        policies = [row for row in policies if row["action_family"] == action_key]
    return {
        "found": bool(payload["items"]),
        "days": days,
        "action_key": action_key or None,
        "outcomes": payload,
        "policy": policies,
        "never_learned": list(NEVER_LEARNED),
        "note": (
            "只报告观察到的结果与可解释的 Beta 统计；insufficient_data 不算正向证据，"
            "不据此声称因果，也不训练模型权重。"
        ),
        "content_is_data": True,
    }


def read_experiment_result(context: ToolContext, arguments: dict) -> dict[str, Any]:
    """Read a micro-experiment's own conclusion, including "not enough data"."""
    uid = _owner(context)
    if uid is None:
        return {"found": False, "reason": "no_owner"}
    from app.models import AgentMicroExperiment
    from app.services.agent.experiments import list_experiments, serialize_experiment

    raw_id = arguments.get("experiment_id")
    if raw_id not in (None, ""):
        try:
            experiment_id = int(raw_id)
        except (TypeError, ValueError):
            return {"found": False, "reason": "invalid_experiment_id"}
        row = context.db.get(AgentMicroExperiment, experiment_id)
        if row is None or row.user_id != uid:
            return {"found": False, "reason": "experiment_not_found"}
        payload = serialize_experiment(context.db, row)
        items = [payload]
    else:
        items = list_experiments(context.db, uid, limit=10)

    # An experiment with no follow-up data carries `insufficient_data`; it is
    # reported as such and never presented as support for the hypothesis.
    for item in items:
        outcome = item.get("outcome") or {}
        conclusion = str(outcome.get("conclusion") or "").strip()
        item["is_conclusive"] = conclusion in {"supports_hypothesis", "not_supported_yet"}
        if conclusion == "insufficient_data":
            item["reading_note"] = (
                "记录不足，无法判断该做法是否有效；这不是负面结果，也不是支持证据。"
            )
        elif conclusion in {"supports_hypothesis", "not_supported_yet"}:
            item["reading_note"] = (
                "这是单变量观察结果，不构成因果证明；只用于决定下一步是否继续。"
            )
        else:
            item["reading_note"] = "实验尚未结束或尚无结论。"

    return {
        "found": bool(items),
        "experiments": items,
        "count": len(items),
        "policy": (
            "微实验一次只改一个变量；结论来自真实记录，记录不足时明确说明不判断，"
            "不得把 insufficient_data 表达为有效。"
        ),
        "content_is_data": True,
    }


def rank_next_actions(context: ToolContext, arguments: dict) -> dict[str, Any]:
    """Rank caller-supplied *safe* options; never widens the option set."""
    uid = _owner(context)
    if uid is None:
        return {"found": False, "reason": "no_owner"}
    family = str(arguments.get("action_family") or "").strip()
    raw = arguments.get("variants")
    if isinstance(raw, str):
        variants = [item.strip() for item in raw.split(",") if item.strip()]
    elif isinstance(raw, list):
        variants = [str(item) for item in raw]
    else:
        variants = []
    if not family or not variants:
        return {
            "found": False,
            "reason": "missing_arguments",
            "detail": "需要 action_family 与 variants（候选必须由调用方认定安全）",
        }
    payload = rank_variants(context.db, uid, family, variants)
    payload["found"] = True
    payload["content_is_data"] = True
    return payload


def outcome_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name=TOOL_PREFERENCES_READ,
            title="读取长期记忆",
            description=(
                "读取结构化长期记忆：每个键带来源（explicit/confirmed_action/"
                "repeated_choice）、证据次数、置信度与是否过期，并标明它是否能影响行为。"
                "过期或低置信度的记忆会被列出但不生效；不得由模型写入。只读。"
            ),
            handler=read_preferences_tool,
            kind="read",
            allowed_agents=ALL_PERSONAS,
            input_schema={},
        ),
        ToolSpec(
            name=TOOL_OUTCOMES_HISTORY,
            title="读取历史行动结果",
            description=(
                "读取过去行动的观察结果与可解释 Beta 统计，用于回答"
                "「上次那样做之后发生了什么」。样本不足时明确说明，不推断因果。只读。"
            ),
            handler=read_outcomes_history,
            kind="read",
            allowed_agents=ALL_PERSONAS,
            input_schema={"action_key": "string 可选", "days": "integer 1-365 可选"},
        ),
        ToolSpec(
            name=TOOL_EXPERIMENT_RESULT,
            title="读取微实验结果",
            description=(
                "读取微实验自身的结论（含 supports_hypothesis/not_supported_yet/"
                "insufficient_data）。记录不足时必须如实回答无法判断，不得表达为有效。只读。"
            ),
            handler=read_experiment_result,
            kind="read",
            allowed_agents=ALL_PERSONAS,
            input_schema={"experiment_id": "integer 可选，缺省返回最近若干次"},
        ),
        ToolSpec(
            name=TOOL_NEXT_ACTION_RANK,
            title="排序候选行动",
            description=(
                "按个人历史对调用方给定的候选做贝叶斯重排。只重排、不新增候选、"
                "不改变安全边界；样本不足时返回未个性化。只读。"
            ),
            handler=rank_next_actions,
            kind="read",
            allowed_agents=ALL_PERSONAS,
            input_schema={
                "action_family": "string 必填",
                "variants": "string[] 必填（必须已由调用方认定为安全）",
            },
        ),
    ]


def outcome_tool_names() -> tuple[str, ...]:
    return tuple(spec.name for spec in outcome_tools())


__all__ = [
    "TOOL_EXPERIMENT_RESULT",
    "TOOL_NEXT_ACTION_RANK",
    "TOOL_OUTCOMES_HISTORY",
    "TOOL_PREFERENCES_READ",
    "outcome_tool_names",
    "outcome_tools",
    "rank_next_actions",
    "read_experiment_result",
    "read_outcomes_history",
    "read_preferences_tool",
]
