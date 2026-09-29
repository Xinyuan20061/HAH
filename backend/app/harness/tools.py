from __future__ import annotations

from functools import lru_cache

from app.harness.contracts import ToolContext, ToolSpec
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

