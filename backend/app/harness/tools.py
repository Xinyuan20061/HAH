from __future__ import annotations

from functools import lru_cache

from app.harness.contracts import ToolContext, ToolSpec
from app.harness.motion_evidence import (
    TOOL_ANALYSIS_READ,
    TOOL_FEEDBACK_READ,
    TOOL_HISTORY_COMPARE,
    TOOL_TIMELINE_READ,
    compare_history,
    owner_id,
    read_analyses,
    read_feedback_signals,
    read_timeline,
)
from app.harness.registry import ToolRegistry
from app.services.agent.actions import list_actions
from app.services.agent.tools import read_context
from app.services.exercise_resources import recommend_resources
from app.services.rag.service import search_knowledge


def _context(context: ToolContext, arguments: dict):
    if "health_context" not in context.state:
        context.state["health_context"] = read_context(context.db, context.user)
    return context.state["health_context"]


def _knowledge(context: ToolContext, arguments: dict):
    query = str(arguments.get("query") or "").strip()[:500]
    limit = max(1, min(5, int(arguments.get("limit") or 3)))
    cache_key = (query, limit)
    cache = context.state.setdefault("knowledge", {})
    if cache_key not in cache:
        cache[cache_key] = search_knowledge(context.db, query, limit)
    return cache[cache_key]


def _resources(context: ToolContext, arguments: dict):
    query = str(arguments.get("query") or "").strip()[:500]
    return recommend_resources(context.db, query)


def _action_catalog(context: ToolContext, arguments: dict):
    return list_actions()


def _proposal_only(context: ToolContext, arguments: dict):
    return {"proposal": arguments, "status": "awaiting_user_confirmation"}


def _motion_analysis_read(context: ToolContext, arguments: dict):
    """Read-only unified motion result; scoped to the calling user (spec 8.2)."""
    uid = owner_id(context)
    if uid is None:
        return {"found": False, "reason": "no_owner"}
    raw = arguments.get("analysis_id")
    try:
        analysis_id = int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        analysis_id = None
    return read_analyses(
        context.db, uid, analysis_id=analysis_id, limit=int(arguments.get("limit") or 3)
    )


def _motion_feedback_read(context: ToolContext, arguments: dict):
    """Read-only quality-correction signals from the calling user (spec 8.4)."""
    uid = owner_id(context)
    if uid is None:
        return {"found": False, "count": 0, "signals": []}
    return read_feedback_signals(context.db, uid)


def _motion_timeline_read(context: ToolContext, arguments: dict):
    """Read-only frame timeline with concrete times (V2, spec §9.4)."""
    uid = owner_id(context)
    if uid is None:
        return {"found": False, "reason": "no_owner"}
    raw = arguments.get("analysis_id")
    try:
        analysis_id = int(raw)
    except (TypeError, ValueError):
        return {"found": False, "reason": "analysis_id_required"}
    return read_timeline(context.db, uid, analysis_id=analysis_id)


def _motion_history_compare(context: ToolContext, arguments: dict):
    """Read-only history comparison; only same-exercise + same rubric (V2)."""
    uid = owner_id(context)
    if uid is None:
        return {"found": False, "reason": "no_owner"}
    exercise_type = arguments.get("exercise_type") or None
    return compare_history(context.db, uid, exercise_type=exercise_type)


@lru_cache
def get_tool_registry() -> ToolRegistry:
    registry = ToolRegistry(
        [
            ToolSpec(
                name="health.context.read",
                title="读取健康上下文",
                description="读取用户档案、今日记录、目标、七日事实、动作画像和主动提醒。",
                handler=_context,
                input_schema={},
            ),
            ToolSpec(
                name="health.knowledge.search",
                title="检索审核知识",
                description="从已审核健康知识库检索支持当前结论的片段。",
                handler=_knowledge,
                input_schema={"query": "string", "limit": "integer 1-5"},
            ),
            ToolSpec(
                name="health.resources.search",
                title="查找训练资源",
                description="从已审核资源库匹配动作教学内容。",
                handler=_resources,
                input_schema={"query": "string"},
            ),
            ToolSpec(
                name="harness.actions.list",
                title="查看可申请操作",
                description="列出 Harness 中已注册、受权限控制的写操作。",
                handler=_action_catalog,
                input_schema={},
            ),
            ToolSpec(
                name=TOOL_ANALYSIS_READ,
                title="读取动作分析结果",
                description=(
                    "按当前用户范围读取统一动作分析结果（识别结论、关键帧证据、"
                    "测量评分、点评摘要、来源与 trace_id）。只读，不会触发模型调用，"
                    "也不能修改任何测量值；analysis_id 缺省时返回最近若干次。"
                ),
                handler=_motion_analysis_read,
                kind="read",
                input_schema={"analysis_id": "integer 可选", "limit": "integer 1-5"},
            ),
            ToolSpec(
                name=TOOL_FEEDBACK_READ,
                title="读取动作纠错反馈",
                description=(
                    "按当前用户范围读取其对动作分析的纠错/确认信号（类别纠正、"
                    "关键帧问题、建议是否有用）；仅用于质量治理与引用，不直接改写测量事实。"
                ),
                handler=_motion_feedback_read,
                kind="read",
                input_schema={},
            ),
            ToolSpec(
                name=TOOL_TIMELINE_READ,
                title="读取动作时间轴帧",
                description=(
                    "按当前用户范围读取某次动作分析的时间轴，返回带具体时间点的帧"
                    "（时间戳、阶段、观察、讲解、下一步），便于回答时精确引用某一秒；"
                    "只读，图片字节不外泄。"
                ),
                handler=_motion_timeline_read,
                kind="read",
                input_schema={"analysis_id": "integer 必填"},
            ),
            ToolSpec(
                name=TOOL_HISTORY_COMPARE,
                title="对比动作历史",
                description=(
                    "仅在同动作、同评分口径、且可比对时比较用户历史评分；机位不可比"
                    "或样本不足时说明缺什么证据，不编造进步百分比；只读，不修改训练目标。"
                ),
                handler=_motion_history_compare,
                kind="read",
                input_schema={"exercise_type": "string 可选"},
            ),
        ]
    )
    for action in list_actions():
        registry.register(
            ToolSpec(
                name=action["key"],
                title=action["title"],
                description=action["description"],
                handler=_proposal_only,
                kind="action",
                risk_level=action["risk_level"],
                requires_confirmation=action["requires_confirmation"],
                proposal_only=True,
                input_schema={"proposal": "object"},
            )
        )
    return registry

