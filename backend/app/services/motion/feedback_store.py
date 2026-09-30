# -*- coding: utf-8 -*-
"""Per-feedback user annotations store (V2, contract §7 / spec §3.3 R13).

Work package F. User feedback (useful / wrong_label / wrong_frame /
unhelpful_advice) is written to ``motion_user_feedback`` as its OWN row per
submission, NEVER embedded inside the computed ``motion_analysis_feedback.result_json``.

Rationale (contract §7): the result snapshot is rebuilt by the pipeline on every
(Re)analysis; embedding feedback there meant a re-analysis / category change
silently overwrote the user's correction. Keeping them in a dedicated table makes
feedback durable, queryable and independent of the 30-day score/profile tables.

This module imports the E-package model ``MotionUserFeedback`` (read-only use; it
does NOT edit models.py) and validates ``frame_id`` membership against the run's
evidence pool (``motion_evidence_frames``) with a fallback to the materialised
result ``timeline.frames[].id`` so partial/legacy runs are still accepted.
"""

from __future__ import annotations

from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MotionAnalysisFeedback, MotionAnalysisRun, MotionEvidenceFrame, MotionUserFeedback

# R13 / contract §6: closed kind vocabulary.
FEEDBACK_KINDS = frozenset(
    {"useful", "wrong_label", "wrong_frame", "unhelpful_advice"}
)


class FeedbackValidationError(ValueError):
    """Raised when a feedback payload fails structural / frame-membership checks.

    ``retryable`` tells the API layer whether the client should retry (e.g. a
    transient frame lookup) vs. correct the payload (e.g. an invalid kind).
    """

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def _snapshot_frame_ids(result: dict) -> set[str]:
    """Frame ids from a materialised V2 result (timeline.frames[].id)."""
    ids: set[str] = set()
    timeline = result.get("timeline") or {}
    for frame in timeline.get("frames") or []:
        if isinstance(frame, dict) and isinstance(frame.get("id"), str):
            ids.add(frame["id"])
    # Legacy V1 keyframes fallback.
    for frame in result.get("keyframes") or []:
        if isinstance(frame, dict) and isinstance(frame.get("id"), str):
            ids.add(frame["id"])
    return ids


def run_frame_ids(db: Session, run: MotionAnalysisRun) -> set[str]:
    """All frame ids that legitimately belong to this run.

    Source of truth: ``motion_evidence_frames`` rows for the run. Fallback to the
    materialised result timeline when evidence rows have not been ingested yet
    (partial runs / legacy V1 snapshots).
    """
    rows = db.scalars(
        select(MotionEvidenceFrame.frame_id).where(
            MotionEvidenceFrame.run_id == run.id
        )
    ).all()
    ids = {str(f) for f in rows if f}
    feedback = db.scalar(
        select(MotionAnalysisFeedback).where(
            MotionAnalysisFeedback.run_id == run.id
        )
    )
    if feedback is not None:
        result = feedback.result if isinstance(feedback.result, dict) else {}
        ids |= _snapshot_frame_ids(result)
    return ids


def validate_kind(kind: str) -> str:
    if kind not in FEEDBACK_KINDS:
        raise FeedbackValidationError(
            "INVALID_KIND",
            f"kind 必须是 {sorted(FEEDBACK_KINDS)} 之一",
            retryable=False,
        )
    return kind


def validate_frame_membership(db: Session, run: MotionAnalysisRun, frame_id: Optional[str]) -> Optional[str]:
    """Return the frame_id when it belongs to the run, else raise.

    A missing/empty frame_id is allowed (feedback may concern the whole result);
    a supplied frame_id MUST reference a real frame of THIS run — never another
    run's frame, and never an arbitrary string.
    """
    if not frame_id:
        return None
    frame_id = str(frame_id).strip()
    if not frame_id:
        return None
    if frame_id in run_frame_ids(db, run):
        return frame_id
    raise FeedbackValidationError(
        "FRAME_NOT_IN_RUN",
        "所选帧不属于本次分析；请在当前结果的时间轴上选择帧",
        retryable=False,
    )


def record_feedback(
    db: Session,
    *,
    run: MotionAnalysisRun,
    user_id: int,
    kind: str,
    frame_id: Optional[str] = None,
    corrected_label: Optional[str] = None,
    comment: Optional[str] = None,
) -> MotionUserFeedback:
    """Insert ONE durable feedback row. Never overwrites the result snapshot."""
    kind = validate_kind(kind)
    frame_id = validate_frame_membership(db, run, frame_id)
    corrected_label = (
        str(corrected_label).strip()[:120] if corrected_label else None
    )
    comment = str(comment).strip()[:2000] if comment else None
    row = MotionUserFeedback(
        run_id=run.id,
        user_id=user_id,
        kind=kind,
        frame_id=frame_id,
        corrected_label=corrected_label,
        comment=comment,
    )
    db.add(row)
    db.flush()
    return row


def list_feedback(db: Session, *, run_id: int | None = None, user_id: int | None = None) -> list[dict]:
    """Read-side for quality governance / harness (never used to rewrite facts)."""
    stmt = select(MotionUserFeedback).order_by(MotionUserFeedback.created_at.desc())
    if run_id is not None:
        stmt = stmt.where(MotionUserFeedback.run_id == run_id)
    if user_id is not None:
        stmt = stmt.where(MotionUserFeedback.user_id == user_id)
    out: list[dict] = []
    for row in db.scalars(stmt).all():
        out.append(
            {
                "id": row.id,
                "run_id": row.run_id,
                "kind": row.kind,
                "frame_id": row.frame_id,
                "corrected_label": row.corrected_label,
                "comment": row.comment,
                "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
            }
        )
    return out


def coerce_legacy_feedback(
    *,
    kind: Optional[str],
    useful: Optional[bool],
    label_correction: Optional[str],
) -> str:
    """Map the V1 boolean feedback shape (R13: frontend sent ``useful=true``)
    onto the V2 closed kind vocabulary, keeping old clients working.
    """
    if kind:
        return validate_kind(kind)
    if useful is True:
        return "useful"
    if label_correction:
        return "wrong_label"
    return "unhelpful_advice"


__all__: Iterable[str] = [
    "FEEDBACK_KINDS",
    "FeedbackValidationError",
    "run_frame_ids",
    "validate_kind",
    "validate_frame_membership",
    "record_feedback",
    "list_feedback",
    "coerce_legacy_feedback",
]
