"""Read-only policy-learning Harness tools."""

from __future__ import annotations

from typing import Any

from app.harness.contracts import ToolContext, ToolSpec
from app.models import PersonalStrategyUnit, PolicyEpisode
from app.services.policy_learning.templates import TEMPLATE_REGISTRY
from sqlalchemy import select

TOOL_POLICY_TEMPLATES = "policy.templates.read"
TOOL_POLICY_CANDIDATES = "policy.candidates.preview"
TOOL_POLICY_EPISODE = "policy.episode.read"
TOOL_POLICY_EVIDENCE = "policy.evidence.read"
TOOL_POLICY_MEMORY = "policy.memory.read"
TOOL_POLICY_EXPLAIN = "policy.decision.explain"


def _uid(ctx):
    return getattr(ctx.user, "id", None)


def _templates(ctx, args):
    return {"templates": [{"template_id": k, **v} for k, v in TEMPLATE_REGISTRY.items()], "content_is_data": True}


def _episodes(ctx, args):
    uid = _uid(ctx)
    if not isinstance(uid, int): return {"found": False, "reason": "no_owner"}
    episode_id = args.get("episode_id")
    stmt = select(PolicyEpisode).where(PolicyEpisode.user_id == uid, PolicyEpisode.status.in_(("active", "awaiting_review", "reviewed", "stopped")))
    if episode_id:
        stmt = stmt.where(PolicyEpisode.id == str(episode_id))
    else:
        stmt = stmt.order_by(PolicyEpisode.start_at.desc()).limit(10)
    rows = ctx.db.scalars(stmt).all()
    return {"found": bool(rows), "episodes": [{"episode_id": r.id, "unit_id": r.unit_id, "status": r.status, "version": r.version, "conclusion_revision": r.effective_adjudication_revision} for r in rows], "content_is_data": True}


def _candidates(ctx, args):
    from app.services.policy_learning.api_helpers import preview_decision
    return preview_decision(ctx.db, _uid(ctx), args.get("context_key"))


def _evidence(ctx, args):
    from app.services.policy_learning.api_helpers import read_evidence
    return read_evidence(ctx.db, _uid(ctx), str(args.get("episode_id") or ""))


def _memory(ctx, args):
    from app.services.policy_learning.api_helpers import read_memory
    return read_memory(ctx.db, _uid(ctx), str(args.get("strategy_id") or ""), str(args.get("context_key") or ""))


def _explain(ctx, args):
    from app.services.policy_learning.api_helpers import explain_decision
    return explain_decision(ctx.db, _uid(ctx), str(args.get("decision_id") or ""))


def policy_tools() -> list[ToolSpec]:
    all_personas = ("xiaojian", "xiaokang", "steward", "planner")
    return [
        ToolSpec(TOOL_POLICY_TEMPLATES, "读取策略模板", "读取审核后的可验证策略模板，不启动行动。", _templates, input_schema={}),
        ToolSpec(TOOL_POLICY_CANDIDATES, "预览策略候选", "按当前个人证据和硬约束预览候选，不执行行动。", _candidates, input_schema={"context_key": "string 可选"}, allowed_agents=all_personas),
        ToolSpec(TOOL_POLICY_EPISODE, "读取策略周期", "读取本人策略周期与协议状态。", _episodes, input_schema={"episode_id": "string 可选"}, allowed_agents=all_personas),
        ToolSpec(TOOL_POLICY_EVIDENCE, "读取策略证据", "读取门控结论、缺失原因与来源引用。", _evidence, input_schema={"episode_id": "string"}, allowed_agents=all_personas),
        ToolSpec(TOOL_POLICY_MEMORY, "读取策略经验", "读取条件化个人策略经验；不输出未校准概率文案。", _memory, input_schema={"strategy_id": "string", "context_key": "string"}, allowed_agents=all_personas),
        ToolSpec(TOOL_POLICY_EXPLAIN, "解释策略决策", "重放已保存策略决策的证据和排序变化。", _explain, input_schema={"decision_id": "string"}, allowed_agents=all_personas),
    ]
