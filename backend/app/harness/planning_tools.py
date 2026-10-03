"""Planning and decision Harness tools (capability plan §7.4/§8.2).

Two properties matter more than the feature surface:

* **simulation is read-only** — ``plan.simulate`` never writes a plan and never
  creates a proposal, so an agent can explore freely;
* **decisions are not invented** — ``decision.next_best_action`` returns the
  deterministic ranking with its reasons and filtered candidates, so the agent
  explains a computed choice instead of choosing one.
"""

from __future__ import annotations

from typing import Any

from app.harness.contracts import ToolContext, ToolSpec
from app.harness.plugins import health_state_excluded_sources

# Read-only state/planning tools are usable by every persona, including
# the planner, which is not part of the default allowed-agent tuple.
ALL_PERSONAS: tuple[str, ...] = ("xiaojian", "xiaokang", "steward", "planner")
from app.services.agent.capability_graph import capability_graph, unavailable_reasons
from app.services.agent.decision import decide, next_best_action
from app.services.agent.proactive import PROACTIVE_CODES, build_proactive_insights
from app.services.agent.tools import read_context
from app.services.planning.contracts import PlanContext, PlanRequest
from app.services.planning.solver import simulate

TOOL_CAPABILITIES_READ = "harness.capabilities.read"
TOOL_PLAN_SIMULATE = "plan.simulate"
TOOL_DECISION_CONTRACT = "decision.contract"
TOOL_NBA = "decision.next_best_action"


def _owner(context: ToolContext) -> int | None:
    user = getattr(context, "user", None)
    uid = getattr(user, "id", None)
    return uid if isinstance(uid, int) else None


def read_capabilities(context: ToolContext, arguments: dict) -> dict[str, Any]:
    uid = _owner(context)
    if uid is None:
        return {"found": False, "reason": "no_owner"}
    graph = capability_graph(context.db, user_id=uid)
    return {
        "found": True,
        "available": sorted(key for key, value in graph.items() if value.available),
        "unavailable": unavailable_reasons(graph),
        "content_is_data": True,
    }


def _plan_request(arguments: dict) -> PlanRequest:
    equipment = arguments.get("equipment")
    if isinstance(equipment, str):
        equipment = [item.strip() for item in equipment.split(",") if item.strip()]
    if not isinstance(equipment, list) or not equipment:
        equipment = ["bodyweight"]
    preferred = arguments.get("preferred_days")
    if isinstance(preferred, str):
        preferred = [int(item) for item in preferred.split(",") if item.strip().isdigit()]
    if not isinstance(preferred, list):
        preferred = []
    excluded = arguments.get("excluded_exercises")
    if isinstance(excluded, str):
        excluded = [item.strip() for item in excluded.split(",") if item.strip()]
    if not isinstance(excluded, list):
        excluded = []

    def _int(value, default, low, high):
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return max(low, min(high, parsed))

    return PlanRequest(
        goal=str(arguments.get("goal") or "fitness"),  # type: ignore[arg-type]
        days_per_week=_int(arguments.get("days_per_week"), 3, 1, 7),
        minutes_per_session=_int(arguments.get("minutes_per_session"), 30, 10, 120),
        equipment=set(str(item) for item in equipment),
        preferred_days=[int(item) for item in preferred if 0 <= int(item) <= 6],
        excluded_exercises=set(str(item) for item in excluded),
        intensity_preference=(
            "gentle" if str(arguments.get("intensity_preference")) == "gentle" else "standard"
        ),
    )


def _plan_context(context: ToolContext) -> PlanContext:
    data = read_context(context.db, context.user)
    insights = data.get("proactive_insights") or build_proactive_insights(data)
    state = data.get("state") or {}
    snapshot = None
    from app.services.health_state import build_snapshot

    snapshot = build_snapshot(
        context.db, context.user.id, persist=False,
        excluded_sources=health_state_excluded_sources(context.db, context.user.id),
    )
    recovery: list[str] = []
    debt = snapshot.numeric("sleep_debt_7d")
    if debt is not None and debt >= 5:
        recovery.append("sleep_debt")
    adherence = snapshot.numeric("plan_adherence_7d")
    focus: list[str] = []
    for item in insights.get("insights", []):
        for key in item.get("evidence", []) or []:
            if isinstance(key, str) and key.endswith("_consistency"):
                focus.append(key)
    del state
    return PlanContext(
        health_state=snapshot,
        motion_focus=sorted(set(focus)),
        recovery_constraints=recovery,
        adherence_history=(
            {"plan_adherence_7d": adherence} if adherence is not None else {}
        ),
    )


def plan_simulate(context: ToolContext, arguments: dict) -> dict[str, Any]:
    """Read-only what-if. Never writes a plan or a proposal."""
    uid = _owner(context)
    if uid is None:
        return {"found": False, "reason": "no_owner"}
    try:
        request = _plan_request(arguments)
    except Exception as exc:  # noqa: BLE001
        return {
            "found": False,
            "reason": "invalid_arguments",
            "detail": type(exc).__name__,
        }

    # Capability plan §9.2: explicit arguments win; only the fields the caller left
    # unset are filled from long-term memory. Memory may tune shape/volume, never an
    # exclusion and never a safety input.
    from app.services.agent.decision import planning_preferences

    memory = planning_preferences(context.db, uid)
    updates: dict[str, Any] = {}
    if memory:
        if "intensity_preference" not in arguments and "intensity_preference" in memory:
            updates["intensity_preference"] = memory["intensity_preference"]
        if "minutes_per_session" not in arguments and "minutes_per_session" in memory:
            updates["minutes_per_session"] = memory["minutes_per_session"]
        if "days_per_week" not in arguments and "days_per_week" in memory:
            updates["days_per_week"] = memory["days_per_week"]
        if "equipment" not in arguments and "equipment" in memory:
            updates["equipment"] = set(memory["equipment"])
    applied: dict[str, str] = {}
    if updates:
        request = request.model_copy(update=updates)
        applied = {key: str(value) for key, value in updates.items()}

    payload = simulate(request, _plan_context(context))
    payload["found"] = True
    payload["memory_applied"] = applied
    payload["memory_note"] = (
        "已按长期记忆补齐未指定的形态/强度参数；显式参数优先，记忆不能改变硬约束。"
        if applied
        else "本次没有需要长期记忆补齐的参数。"
    )
    payload["content_is_data"] = True
    return payload


def decision_contract(context: ToolContext, arguments: dict) -> dict[str, Any]:
    uid = _owner(context)
    if uid is None:
        return {"found": False, "reason": "no_owner"}
    data = read_context(context.db, context.user)
    insights = data.get("proactive_insights") or build_proactive_insights(data)
    signals = [
        item for item in insights.get("insights", []) if item.get("code") in PROACTIVE_CODES
    ]
    payload = decide(context.db, uid, signals=signals).as_dict()
    payload["found"] = True
    payload["content_is_data"] = True
    return payload


def decision_next_best_action(context: ToolContext, arguments: dict) -> dict[str, Any]:
    uid = _owner(context)
    if uid is None:
        return {"found": False, "reason": "no_owner"}
    payload = next_best_action(context.db, uid)
    payload["found"] = payload.get("next_best_action") is not None
    payload["content_is_data"] = True
    return payload


def planning_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name=TOOL_CAPABILITIES_READ,
            title="读取能力图",
            description=(
                "读取当前真正可用的能力与不可用原因。未测量一律视为不可用；"
                "动作 Gold 级只有在评测报告通过后才可用。只读。"
            ),
            handler=read_capabilities,
            kind="read",
            allowed_agents=ALL_PERSONAS,
            input_schema={},
        ),
        ToolSpec(
            name=TOOL_PLAN_SIMULATE,
            title="模拟一周计划",
            description=(
                "在给定目标/天数/时长/器械/偏好下求解一份合法计划并返回约束检查结果；"
                "只读，不写入计划、不创建提案。真正应用必须走 plan.apply 并由用户确认。"
            ),
            handler=plan_simulate,
            kind="read",
            allowed_agents=ALL_PERSONAS,
            input_schema={
                "goal": "fat_loss|strength|fitness|posture|maintain",
                "days_per_week": "integer 1-7",
                "minutes_per_session": "integer 10-120",
                "equipment": "string[] 如 ['bodyweight','dumbbell']",
                "preferred_days": "integer[] 0-6",
                "excluded_exercises": "string[]",
                "intensity_preference": "gentle|standard",
            },
        ),
        ToolSpec(
            name=TOOL_DECISION_CONTRACT,
            title="读取决策契约",
            description=(
                "读取确定性候选集合、硬约束过滤原因、排序权重明细与次优选项；"
                "用于解释为什么建议某一件事，而不是由模型自行选择。只读。"
            ),
            handler=decision_contract,
            kind="read",
            allowed_agents=ALL_PERSONAS,
            input_schema={},
        ),
        ToolSpec(
            name=TOOL_NBA,
            title="读取下一个最佳行动",
            description=(
                "返回排序后的下一个最佳行动及其依据与被过滤的候选。样本不足或存在"
                "硬约束时明确说明，不强行给建议。只读，写操作仍需用户确认。"
            ),
            handler=decision_next_best_action,
            kind="read",
            allowed_agents=ALL_PERSONAS,
            input_schema={},
        ),
    ]


def planning_tool_names() -> tuple[str, ...]:
    return tuple(spec.name for spec in planning_tools())


__all__ = [
    "TOOL_CAPABILITIES_READ",
    "TOOL_DECISION_CONTRACT",
    "TOOL_NBA",
    "TOOL_PLAN_SIMULATE",
    "decision_contract",
    "decision_next_best_action",
    "plan_simulate",
    "planning_tool_names",
    "planning_tools",
    "read_capabilities",
]
