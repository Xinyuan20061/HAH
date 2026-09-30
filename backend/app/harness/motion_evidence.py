"""Read-only motion-analysis evidence tools for the Coach Agent (spec 3 / 8.2).

The Harness may *quote* real motion-analysis results, but must never mutate
measured facts or user records through the tool boundary.  Every query below is
scoped by ``run.user_id == caller_user_id``: user A's agent can never read user
B's runs or feedback (missing rows -> structured ``found=False`` / empty list).

Privacy rules (spec 8.3):
  * keyframe image URLs are short-lived signed/data URLs carrying base64 JPEGs;
    they are stripped from tool output and replaced with a boolean ``has_image``,
    so image bytes never enter the prompt, the trace or the audit log;
  * internal orchestrator bookkeeping (cache keys, raw stage payloads, worker
    internals) is stripped; only a desensitized evidence chain survives;
  * model-generated summary/keyframe text is returned *as untrusted data* — the
    collaboration layer wraps it as observations, never as instructions.

No write path exists in this module: writes continue to flow through the
``proposal_only -> user_confirmed -> apply_plan`` gate (see tools.py).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MotionAnalysisFeedback, MotionAnalysisRun

logger = logging.getLogger("healthmate.harness.motion_evidence")

DEFAULT_LIST_LIMIT = 3
MAX_LIST_LIMIT = 5

# Tool names exposed to agents — mirrored in collaboration.py worker scopes.
TOOL_ANALYSIS_READ = "motion.analysis.read"
TOOL_FEEDBACK_READ = "motion.feedback.read"


def owner_id(context: Any) -> int | None:
    user = getattr(context, "user", None)
    uid = getattr(user, "id", None)
    return uid if isinstance(uid, int) else None


def _scoped_runs(db: Session, user_id: int, analysis_id: int | None, limit: int):
    stmt = (
        select(MotionAnalysisRun)
        .where(MotionAnalysisRun.user_id == user_id)
        .order_by(MotionAnalysisRun.created_at.desc(), MotionAnalysisRun.id.desc())
    )
    if analysis_id is not None:
        stmt = stmt.where(MotionAnalysisRun.id == analysis_id).limit(1)
    else:
        stmt = stmt.limit(limit)
    return db.scalars(stmt).all()


def _feedback_snapshot(db: Session, run_id: int) -> dict[str, Any]:
    row = db.scalar(
        select(MotionAnalysisFeedback).where(MotionAnalysisFeedback.run_id == run_id)
    )
    if row is None:
        return {}
    return row.result if isinstance(row.result, dict) else {}


def sanitize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Strip base64 images and internal bookkeeping; keep the evidence chain.

    The result is what the Coach Agent may see: recognition, score, grounded
    summary text, keyframe observations/advice (as data), limitations and the
    trace link.  Image bytes and internal cache/worker fields never leave this
    function.
    """
    recognition = snapshot.get("recognition") or {}
    summary = snapshot.get("summary") or {}
    score = snapshot.get("score") or {}
    keyframes_in = snapshot.get("keyframes") or []
    keyframes = []
    for frame in keyframes_in[:4]:
        if not isinstance(frame, dict):
            continue
        image_url = frame.get("image_url")
        keyframes.append(
            {
                "id": frame.get("id"),
                "t_ms": frame.get("t_ms"),
                "phase": frame.get("phase"),
                "finding": frame.get("finding"),
                "advice": frame.get("advice"),
                "evidence_type": frame.get("evidence_type"),
                "has_image": bool(image_url),
                # NOTE: frame["image_url"] (base64 data URL) is deliberately
                # dropped — never enters the prompt or the audit trace.
            }
        )
    out = {
        "analysis_id": snapshot.get("analysis_id"),
        "status": snapshot.get("status"),
        "pipeline_version": snapshot.get("pipeline_version"),
        "trace_id": snapshot.get("trace_id"),
        "recognition": {
            "state": recognition.get("state"),
            "label_id": recognition.get("label_id"),
            "label_zh": recognition.get("label_zh"),
            "candidate_score": recognition.get("candidate_score"),
            "calibrated_confidence": recognition.get("calibrated_confidence"),
            "sources": recognition.get("sources") or [],
            "review_status": recognition.get("review_status"),
            "reason": recognition.get("reason"),
            "reason_code": recognition.get("reason_code"),
        },
        "score": {
            "available": bool(score.get("available")),
            "reason_code": score.get("reason_code"),
            "overall": score.get("overall"),
            "reps": score.get("reps"),
            "completeness": score.get("completeness"),
            "stability": score.get("stability"),
            "rhythm_control": score.get("rhythm_control"),
        },
        # External model text: returned as DATA only.
        "summary": {
            "text": summary.get("text"),
            "source": summary.get("source"),
            "degraded": bool(summary.get("degraded")),
        },
        "keyframes": keyframes,
        "limitations": snapshot.get("limitations") or [],
        "content_is_untrusted_data": True,
    }
    confirmation = snapshot.get("user_confirmation")
    if isinstance(confirmation, dict) and confirmation:
        out["user_confirmation"] = {
            "label_id": confirmation.get("label_id"),
            "correction_reason": confirmation.get("correction_reason"),
            "recorded_at": confirmation.get("recorded_at"),
        }
    return out


def read_analyses(
    db: Session,
    user_id: int,
    *,
    analysis_id: int | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
) -> dict[str, Any]:
    """Read the caller's own unified motion results (never triggers model calls)."""
    limit = max(1, min(MAX_LIST_LIMIT, int(limit or DEFAULT_LIST_LIMIT)))
    runs = _scoped_runs(db, user_id, analysis_id, limit)
    items = []
    for run in runs:
        snapshot = _feedback_snapshot(db, run.id)
        item = sanitize_snapshot(snapshot)
        # Safety net: if a run has no materialised snapshot yet, expose the
        # lightweight run state only (same shape as GET /motion-analyses/{id}).
        item.setdefault("pipeline_version", run.pipeline_version)
        item.setdefault("trace_id", None)
        item["requested_type"] = run.requested_type
        if not snapshot:
            item["status"] = run.status
            item["recognition"] = {}
            item["summary"] = {"text": None, "source": "no_snapshot", "degraded": True}
        items.append(item)
    if analysis_id is not None:
        if not items:
            return {
                "found": False,
                "analysis_id": analysis_id,
                "reason": "not_found_or_not_owned",
            }
        return {"found": True, "analysis": items[0]}
    return {"found": True, "count": len(items), "analyses": items}


def read_feedback_signals(db: Session, user_id: int) -> dict[str, Any]:
    """Read the caller's own quality-correction signals (spec 8.4).

    User feedback (useful / label correction / frame issue) lives embedded in
    the result snapshot; it drives quality governance but must never rewrite
    measured facts.  The Coach may reference it when answering, e.g. "上次你
    指出类别标错了".
    """
    stmt = (
        select(MotionAnalysisFeedback)
        .join(MotionAnalysisRun, MotionAnalysisRun.id == MotionAnalysisFeedback.run_id)
        .where(MotionAnalysisRun.user_id == user_id)
        .order_by(MotionAnalysisFeedback.created_at.desc())
        .limit(MAX_LIST_LIMIT)
    )
    signals = []
    for row in db.scalars(stmt).all():
        snapshot = row.result if isinstance(row.result, dict) else {}
        feedback = snapshot.get("user_feedback")
        confirmation = snapshot.get("user_confirmation")
        if not isinstance(feedback, dict) and not isinstance(confirmation, dict):
            continue
        recognition = snapshot.get("recognition") or {}
        signals.append(
            {
                "analysis_id": snapshot.get("analysis_id") or row.run_id,
                "trace_id": snapshot.get("trace_id"),
                "pipeline_version": snapshot.get("pipeline_version"),
                "recorded_label_id": recognition.get("label_id"),
                "user_feedback": (
                    {
                        "useful": feedback.get("useful"),
                        "label_correction": feedback.get("label_correction"),
                        "frame_id": feedback.get("frame_id"),
                        "comment": feedback.get("comment"),
                        "recorded_at": feedback.get("recorded_at"),
                    }
                    if isinstance(feedback, dict)
                    else None
                ),
                "user_confirmation": (
                    {
                        "label_id": confirmation.get("label_id"),
                        "recorded_at": confirmation.get("recorded_at"),
                    }
                    if isinstance(confirmation, dict)
                    else None
                ),
            }
        )
    return {"found": True, "count": len(signals), "signals": signals}
