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
TOOL_POLICY_ACQUISITION_PREVIEW = "policy.acquisition.preview"
TOOL_POLICY_CERTIFICATE = "policy.certificate.read"
TOOL_POLICY_KNOWLEDGE = "policy.knowledge.read"


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


def _acquisition_preview(ctx, args):
    uid = _uid(ctx)
    if not isinstance(uid, int):
        return {"found": False, "content_is_data": True}
    from app.models import PolicyAcquisitionSession
    from app.services.policy_learning.acquisition.service import read_session
    session_id = str(args.get("session_id") or "")
    if not session_id and args.get("episode_id"):
        row = ctx.db.scalar(select(PolicyAcquisitionSession).where(
            PolicyAcquisitionSession.user_id == uid,
            PolicyAcquisitionSession.episode_id == str(args["episode_id"])))
        if row is None:
            from app.services.policy_learning.acquisition.service import preview_episode
            return {"found": True, "preview_only": True,
                    "decision": preview_episode(ctx.db, user_id=uid,
                                                 episode_id=str(args["episode_id"])),
                    "content_is_data": True}
        session_id = row.id
    if not session_id:
        return {"found": False, "reason": "session_or_episode_required", "content_is_data": True}
    return {"found": True, "decision": read_session(ctx.db, user_id=uid, session_id=session_id),
            "content_is_data": True}


def _certificate(ctx, args):
    from app.services.policy_learning.acquisition.service import read_certificate
    return {"certificate": read_certificate(ctx.db, user_id=_uid(ctx),
                                            certificate_id=str(args.get("certificate_id") or "")),
            "content_is_data": True}


def _knowledge(ctx, args):
    from app.services.policy_learning.acquisition.knowledge_contract import current_knowledge_contract
    from app.services.rag.governance import (
        CONFLICT_NOTICES,
        claims_for_source_keys,
        conflict_status_for_claims,
        POTENTIAL_CONFLICT_NOTICE,
    )
    from app.services.rag.service import retrieval_meta, search_knowledge
    query = str(args.get("query") or "").strip()[:300]
    results = search_knowledge(ctx.db, query, 3) if query else []
    template_id = str(args.get("template_id") or "session_duration")
    binding = current_knowledge_contract(ctx.db, template_id)
    claims = claims_for_source_keys(ctx.db, [item["source_key"] for item in results]) if results else []
    conflict_status = conflict_status_for_claims(ctx.db, claims) if claims else (
        "no_sources_to_compare" if not results else "not_assessed")
    notice = (POTENTIAL_CONFLICT_NOTICE if conflict_status == "potential_conflict"
              else CONFLICT_NOTICES.get(conflict_status, ""))
    return {"contract": binding["contract"], "contract_hash": binding["contract_hash"],
            "knowledge": results, "content_is_data": True,
            "retrieval_status": "matched" if results else "no_reviewed_match",
            "conflict_status": conflict_status,
            "conflict_notice": (notice if results
                                else "没有匹配到已审核知识；不补写依据、不推测结论。"),
            "retrieval": retrieval_meta(ctx.db),
            "claims": [
                {"claim_id": claim["claim_id"], "source_key": claim["source_key"],
                 "review_state": claim["review_state"], "version_hash": claim["version_hash"],
                 "subject_population": claim["subject_population"],
                 "qualifier": claim["qualifier"]}
                for claim in claims
            ],
            "allowed_use": "explanation_only",
            "may_fill_personal_facts": False,
            "limits": ["知识检索不补写个人事实", "不能改变已冻结协议门槛"]}


def policy_tools() -> list[ToolSpec]:
    all_personas = ("xiaojian", "xiaokang", "steward", "planner")
    return [
        ToolSpec(TOOL_POLICY_TEMPLATES, "读取策略模板", "读取审核后的可验证策略模板，不启动行动。", _templates, input_schema={}),
        ToolSpec(TOOL_POLICY_CANDIDATES, "预览策略候选", "按当前个人证据和硬约束预览候选，不执行行动。", _candidates, input_schema={"context_key": "string 可选"}, allowed_agents=all_personas),
        ToolSpec(TOOL_POLICY_EPISODE, "读取策略周期", "读取本人策略周期与协议状态。", _episodes, input_schema={"episode_id": "string 可选"}, allowed_agents=all_personas),
        ToolSpec(TOOL_POLICY_EVIDENCE, "读取策略证据", "读取门控结论、缺失原因与来源引用。", _evidence, input_schema={"episode_id": "string"}, allowed_agents=all_personas),
        ToolSpec(TOOL_POLICY_MEMORY, "读取策略经验", "读取条件化个人策略经验；不输出未校准概率文案。", _memory, input_schema={"strategy_id": "string", "context_key": "string"}, allowed_agents=all_personas),
        ToolSpec(TOOL_POLICY_EXPLAIN, "解释策略决策", "重放已保存策略决策的证据和排序变化。", _explain, input_schema={"decision_id": "string"}, allowed_agents=all_personas),
        ToolSpec(TOOL_POLICY_ACQUISITION_PREVIEW, "读取低负担取证状态", "只读取已有取证会话与当前有效判断，不创建问题或写入证书。", _acquisition_preview, input_schema={"session_id": "string 可选", "episode_id": "string 可选"}, allowed_agents=all_personas),
        ToolSpec(TOOL_POLICY_CERTIFICATE, "读取判断依据", "核验本人证书当前是否有效；不会签发新证书。", _certificate, input_schema={"certificate_id": "string"}, allowed_agents=all_personas),
        ToolSpec(TOOL_POLICY_KNOWLEDGE, "读取审核知识依据", "通过审核知识检索提供说明；知识不填入个人记录、不修改冻结门槛。", _knowledge, input_schema={"query": "string", "template_id": "string 可选"}, allowed_agents=all_personas),
    ]
