"""Knowledge governance helpers (spec B3): conflict status is advisory-only.

Retrieval-time conflict handling NEVER lets the LLM adjudicate medical truth;
it only surfaces and blocks. Statuses follow the spec enumeration, and
``no_conflict_found_in_reviewed_claims`` never means "proven free of conflict".
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KnowledgeClaim, KnowledgeConflictReview

CONFLICT_STATUSES = frozenset({
    "not_assessed",
    "no_conflict_found_in_reviewed_claims",
    "potential_conflict",
    "reviewed_resolved",
    "insufficient_sources",
})

CONFLICT_NOTICES = {
    "not_assessed": "检索片段未经过语义冲突裁决；不得把多来源并列解释成共识。",
    "no_conflict_found_in_reviewed_claims": "已审阅的主张范围内未发现冲突；这不等同于证实无冲突。",
    "potential_conflict": "资料存在差异，涉及训练建议或特殊人群时请先咨询专业意见，不自行按单一来源决定。",
    "reviewed_resolved": "相关主张差异已由审核人裁定并记录适用范围。",
    "insufficient_sources": "已审核来源不足以支持该项判断，请缩小表述或询问专业意见。",
}

# Advisory notice attached when a potential conflict is present (spec B3).
POTENTIAL_CONFLICT_NOTICE = "资料存在差异，需专业咨询；不得生成看似确定的个体结论。"


def _claim_payload(row: KnowledgeClaim) -> dict:
    return {
        "claim_id": row.claim_id,
        "source_key": row.source_key,
        "subject_population": row.subject_population,
        "condition": row.condition,
        "behavior": row.behavior,
        "outcome": row.outcome,
        "direction": row.direction,
        "strength": row.strength,
        "qualifier": row.qualifier,
        "source_location": row.source_location,
        "review_state": row.review_state,
        "version_hash": row.version_hash,
    }


def claims_for_source_keys(db: Session, source_keys: list[str]) -> list[dict]:
    """Active atomic claims for the given reviewed chunks; empty when none."""
    if not source_keys:
        return []
    rows = db.scalars(select(KnowledgeClaim).where(
        KnowledgeClaim.source_key.in_(source_keys),
        KnowledgeClaim.active.is_(True),
    )).all()
    return [_claim_payload(row) for row in rows]


def conflict_status_for_claims(db: Session, claims: list[dict]) -> str:
    """Advisory conflict status among the retrieved claims.

    - no claims at all -> not_assessed (nothing to compare)
    - no human review exists for the claim pairs -> not_assessed
    - any unresolved potential_conflict on the pairs -> potential_conflict
    - otherwise the most recent review verdict wins (reviewed_resolved /
      insufficient_sources / no_conflict_found_in_reviewed_claims).
    """
    if not claims:
        return "not_assessed"
    ids = {int(c.get("id")) for c in claims if isinstance(c.get("id"), int)}
    if not ids:
        claim_rows = db.scalars(select(KnowledgeClaim).where(
            KnowledgeClaim.claim_id.in_([c["claim_id"] for c in claims]))).all()
        ids = {row.id for row in claim_rows}
    if not ids:
        return "not_assessed"
    reviews = db.scalars(select(KnowledgeConflictReview).where(
        KnowledgeConflictReview.claim_a_id.in_(ids),
        KnowledgeConflictReview.claim_b_id.in_(ids),
    ).order_by(KnowledgeConflictReview.reviewed_at.desc())).all()
    if not reviews:
        return "not_assessed"
    if any(row.conflict_status == "potential_conflict" for row in reviews):
        return "potential_conflict"
    return reviews[0].conflict_status
