from __future__ import annotations

import json
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PersonalPolicyBelief, PersonalStrategyUnit, PolicyAdjudication, PolicyDecision, PolicyDomainGeneration, PolicyEpisode
from .repository import learning_epoch, rebuild_beliefs


def preview_decision(db: Session, user_id: int | None, context_key: str | None = None):
    if not isinstance(user_id, int): return {"found": False, "reason": "no_owner", "content_is_data": True}
    rows = db.scalars(select(PersonalStrategyUnit).where(PersonalStrategyUnit.user_id == user_id, PersonalStrategyUnit.status == "compiled").order_by(PersonalStrategyUnit.created_at.desc()).limit(20)).all()
    if context_key: rows = [r for r in rows if r.context_key == context_key]
    return {"found": bool(rows), "candidates": [{"strategy_unit_id": r.id, "strategy_id": r.strategy_id, "context_key": r.context_key, "protocol_version": r.protocol_version} for r in rows], "content_is_data": True}


def read_evidence(db: Session, user_id: int | None, episode_id: str):
    row = db.get(PolicyEpisode, episode_id)
    if row is None or row.user_id != user_id: return {"found": False, "reason": "not_found", "content_is_data": True}
    verdicts = db.scalars(select(PolicyAdjudication).where(PolicyAdjudication.episode_id == row.id).order_by(PolicyAdjudication.revision.desc())).all()
    return {"found": True, "episode_id": row.id, "status": row.status, "adjudications": [{"revision": x.revision, "execution_label": x.execution_label, "support_label": x.support_label, "availability_label": x.availability_label, "conclusion": x.conclusion, "reasons": json.loads(x.reasons_json), "stale": x.stale} for x in verdicts], "content_is_data": True}


def read_memory(db: Session, user_id: int | None, strategy_id: str, context_key: str):
    if not isinstance(user_id, int): return {"found": False, "reason": "no_owner", "content_is_data": True}
    generations = db.scalars(select(PolicyDomainGeneration).where(PolicyDomainGeneration.user_id == user_id)).all()
    if any(row.source_generation > row.processed_generation for row in generations):
        return {"found": False, "reason": "source_correction_replay_pending", "note": "来源更正正在复核，旧经验暂不提供。", "content_is_data": True}
    epoch = learning_epoch(db, user_id, strategy_id, create=False)
    rows = db.scalars(select(PersonalPolicyBelief).where(PersonalPolicyBelief.user_id == user_id, PersonalPolicyBelief.strategy_id == strategy_id, PersonalPolicyBelief.context_key == context_key, PersonalPolicyBelief.learning_epoch == epoch)).all()
    return {"found": bool(rows), "strategy_id": strategy_id, "context_key": context_key, "beliefs": [{"endpoint": r.endpoint, "eligible_positive": r.positive_count, "eligible_negative": r.negative_count, "episodes": r.positive_count + r.negative_count} for r in rows], "note": "仅展示已验证周期计数与适用条件，不输出未经校准的有效率。", "content_is_data": True}


def explain_decision(db: Session, user_id: int | None, decision_id: str):
    row = db.get(PolicyDecision, decision_id)
    if row is None or row.user_id != user_id: return {"found": False, "reason": "not_found", "content_is_data": True}
    return {"found": True, "decision_id": row.id, "state_hash": row.state_hash, "belief_generation": row.belief_generation, "selected_id": row.selected_id, "policy_mode": row.policy_mode, "candidates": json.loads(row.candidate_json or "[]"), "propensity": json.loads(row.propensity_json or "{}"), "content_is_data": True}
