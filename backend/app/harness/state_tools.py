"""Health-state Harness tools (capability plan §4.4).

Read-only tools over the versioned state layer. They exist so an agent reads a
*value with provenance* instead of concatenating dictionaries itself, and so the
constraints that must block an action are visible to the reasoner rather than
implicit in a prompt.

Every handler is scoped to the calling user and returns structured data only: no
raw media, no prompts, no private reasoning.
"""

from __future__ import annotations

from typing import Any

from app.harness.contracts import ToolContext, ToolSpec
from app.services.health_state import (
    FEATURE_KEYS,
    build_snapshot,
    feature_history,
)
from app.services.health_state.builder import latest_snapshot, load_snapshot

# Every persona may read the state layer; the planner persona also needs the
# planning tools, so the allowed-agent set is explicit rather than the default.
ALL_PERSONAS: tuple[str, ...] = ("xiaojian", "xiaokang", "steward", "planner")

TOOL_STATE_READ = "health.state.read"
TOOL_STATE_HISTORY = "health.state.history"
TOOL_SIGNALS_READ = "health.signals.read"
TOOL_CONSTRAINTS_READ = "health.constraints.read"
TOOL_OUTCOMES_COMPARE = "health.outcomes.compare"

MAX_WINDOW_DAYS = 90


def _window(context: ToolContext, arguments: dict) -> int:
    raw = arguments.get("window_days")
    try:
        value = int(raw) if raw not in (None, "") else 7
    except (TypeError, ValueError):
        value = 7
    return max(1, min(MAX_WINDOW_DAYS, value))


def _owner(context: ToolContext) -> int | None:
    user = getattr(context, "user", None)
    uid = getattr(user, "id", None)
    return uid if isinstance(uid, int) else None


def _snapshot(context: ToolContext, arguments: dict):
    uid = _owner(context)
    if uid is None:
        return None
    key = ("state_snapshot", _window(context, arguments))
    cache: dict = context.state.setdefault("state_cache", {})
    if key not in cache:
        cache[key] = build_snapshot(
            context.db, uid, window_days=key[1], persist=False
        )
    return cache[key]


def read_state(context: ToolContext, arguments: dict) -> dict[str, Any]:
    """Current values with evidence type, confidence, coverage and limitations."""
    snapshot = _snapshot(context, arguments)
    if snapshot is None:
        return {"found": False, "reason": "no_owner"}
    return {
        "found": True,
        "snapshot_hash": snapshot.snapshot_hash,
        "state_version": snapshot.version,
        "as_of": snapshot.as_of.isoformat() + "Z",
        "window_days": snapshot.window_days,
        "values": {
            key: {
                "value": value.value,
                "unit": value.unit,
                "evidence_type": value.evidence_type,
                "confidence_level": value.confidence_level,
                "observed_days": value.observed_days,
                "window_days": value.window_days,
                "feature_version": value.feature_version,
                "limitations": value.limitations,
                "evidence_count": len(value.evidence),
            }
            for key, value in snapshot.sorted_items()
        },
        "missingness": snapshot.missingness,
        "active_actions": snapshot.active_actions,
        "how_to_read": (
            "value=null 表示数据不足而不是 0；confidence_level 由记录覆盖度决定，"
            "不采用模型自报置信度；observed_days 是实际贡献的天数。"
        ),
        "content_is_data": True,
    }


def read_state_history(context: ToolContext, arguments: dict) -> dict[str, Any]:
    uid = _owner(context)
    if uid is None:
        return {"found": False, "reason": "no_owner"}
    key = str(arguments.get("key") or "").strip()
    if key not in FEATURE_KEYS:
        return {
            "found": False,
            "reason": "unknown_feature",
            "available_keys": list(FEATURE_KEYS),
        }
    try:
        days = int(arguments.get("days") or 30)
    except (TypeError, ValueError):
        days = 30
    days = max(1, min(365, days))
    items = feature_history(context.db, uid, key, limit=min(days, 90))
    return {
        "found": bool(items),
        "key": key,
        "days": days,
        "items": items,
        "note": "只返回数值、版本与限制，不返回原始媒体或模型私有推理。",
        "content_is_data": True,
    }


def read_constraints(context: ToolContext, arguments: dict) -> dict[str, Any]:
    snapshot = _snapshot(context, arguments)
    if snapshot is None:
        return {"found": False, "reason": "no_owner"}
    return {
        "found": True,
        "blocks_auto_planning": bool(snapshot.hard_constraints()),
        "hard": [item.model_dump() for item in snapshot.hard_constraints()],
        "soft": [
            item.model_dump()
            for item in snapshot.constraints
            if item.severity == "soft"
        ],
        "policy": (
            "hard 约束不可被模型替换或忽略；只可解释并向用户说明为何不能自动执行。"
        ),
        "content_is_data": True,
    }


def read_signals(context: ToolContext, arguments: dict) -> dict[str, Any]:
    """Active proactive signals, deterministic only."""
    from app.services.agent.proactive import PROACTIVE_CODES, build_proactive_insights
    from app.services.agent.tools import read_context

    uid = _owner(context)
    if uid is None:
        return {"found": False, "reason": "no_owner"}
    context_data = read_context(context.db, context.user)
    payload = context_data.get("proactive_insights") or build_proactive_insights(
        context_data
    )
    insights = [
        item
        for item in payload.get("insights", [])
        if item.get("code") in PROACTIVE_CODES
    ]
    return {
        "found": bool(insights),
        "state_snapshot_hash": (context_data.get("state") or {}).get("snapshot_hash"),
        "data_quality": payload.get("data_quality", {}),
        "signals": [
            {
                "code": item.get("code"),
                "title": item.get("title"),
                "detail": item.get("detail"),
                "evidence": item.get("evidence"),
            }
            for item in insights
        ],
        "content_is_data": True,
    }


def compare_outcomes(context: ToolContext, arguments: dict) -> dict[str, Any]:
    """Observed outcomes for a given action family, if any exist."""
    from app.services.agent.outcome import outcome_history, policy_snapshot

    uid = _owner(context)
    if uid is None:
        return {"found": False, "reason": "no_owner"}
    try:
        days = int(arguments.get("days") or 30)
    except (TypeError, ValueError):
        days = 30
    days = max(1, min(365, days))
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
        "action_key": action_key or None,
        "outcomes": payload,
        "policy": policies,
        "note": (
            "只报告观察到的结果与可解释的先验统计；insufficient_data 不算正向证据，"
            "不据此声称因果或进行模型训练。"
        ),
        "content_is_data": True,
    }


def state_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name=TOOL_STATE_READ,
            title="读取健康状态",
            description=(
                "读取版本化个人健康状态：每个值都带 evidence_type（observed/derived/"
                "model_inferred/user_confirmed）、覆盖度置信度、实际贡献天数与限制。"
                "value=null 表示数据不足，不得当作 0；只读，不触发模型调用。"
            ),
            handler=read_state,
            kind="read",
            allowed_agents=ALL_PERSONAS,
            input_schema={"window_days": "integer 1-90 可选"},
        ),
        ToolSpec(
            name=TOOL_STATE_HISTORY,
            title="读取状态历史",
            description=(
                "按特征键读取最近若干次快照值与其版本，用于回答“最近怎么样”；"
                "版本不同的值不可直接比较。只读。"
            ),
            handler=read_state_history,
            kind="read",
            allowed_agents=ALL_PERSONAS,
            input_schema={"key": "string 必填", "days": "integer 1-365 可选"},
        ),
        ToolSpec(
            name=TOOL_CONSTRAINTS_READ,
            title="读取健康约束",
            description=(
                "读取 hard/soft 约束。hard 约束（安全规则命中、覆盖不足、"
                "没有测量器的动作、用户排除项）不可被忽略，只能解释为何不能自动执行。只读。"
            ),
            handler=read_constraints,
            kind="read",
            allowed_agents=ALL_PERSONAS,
            input_schema={"window_days": "integer 1-90 可选"},
        ),
        ToolSpec(
            name=TOOL_SIGNALS_READ,
            title="读取主动信号",
            description=(
                "读取由确定性事实与数据覆盖产生的活跃信号（运动断档、睡眠不足等）；"
                "不使用模型自报置信度。只读。"
            ),
            handler=read_signals,
            kind="read",
            allowed_agents=ALL_PERSONAS,
            input_schema={},
        ),
        ToolSpec(
            name=TOOL_OUTCOMES_COMPARE,
            title="读取行动结果",
            description=(
                "读取已确认行动/微实验的观察结果与可解释偏好统计，用于回答"
                "“上次那样做之后发生了什么”。样本不足时明确说明，不推断因果。只读。"
            ),
            handler=compare_outcomes,
            kind="read",
            allowed_agents=ALL_PERSONAS,
            input_schema={"action_key": "string 可选", "days": "integer 1-365 可选"},
        ),
    ]


def state_tool_names() -> tuple[str, ...]:
    return tuple(spec.name for spec in state_tools())


__all__ = [
    "TOOL_CONSTRAINTS_READ",
    "TOOL_OUTCOMES_COMPARE",
    "TOOL_SIGNALS_READ",
    "TOOL_STATE_HISTORY",
    "TOOL_STATE_READ",
    "compare_outcomes",
    "read_constraints",
    "read_signals",
    "read_state",
    "read_state_history",
    "state_tool_names",
    "state_tools",
]
