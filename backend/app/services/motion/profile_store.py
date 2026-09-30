# -*- coding: utf-8 -*-
"""Health-profile closed loop (V2, spec §9.4).

Work package F. A completed unified run must land in the 30-day motion profile
(``motion_scores`` / ``motion_events``) the same way the UI shows it — otherwise
the user sees a score on screen but the 30-day profile is empty.

Write rules (contract §7, spec §6.4 / T05):

* ``motion_scores`` is written ONLY when ALL hold:
    1. the run recognised a real action (recognition.canonical_id set, state in
       {identified, likely});
    2. the measurement actually belongs to that action — i.e. the measured
       exercise matches the recognised canonical_id. A category change (local
       squat -> vision bicep_curl) must NOT inherit the squat score/reps (T05);
    3. a quality scorer is REGISTERED for that action in the catalog
       (capabilities.quality_scorer is a non-empty version string) AND the metric
       carries a scorer_version. Until package G registers real scorers this is
       intentionally never true — we never fabricate a numerical score for an
       action that only has visual coaching.

* Visual coaching observations (frame phase/observation/explanation/next_step)
  are written as ``motion_events`` with severity ``info`` — they are OBSERVATIONS,
  never measurements, and must not masquerade as a score.

The call site (after ``orchestrator.apply_post_review`` / worker receipt
materialises the result) is wired by the E-package post-processing consumer at
integration; this module is the idempotent writer both paths share.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MotionAnalysisRun, MotionEvent, MotionScore

from . import catalog

_RECOGNIZED_STATES = {"identified", "likely"}


def _quality_metric(metrics: list[dict]) -> Optional[dict]:
    for m in metrics or []:
        if isinstance(m, dict) and m.get("id") in {"overall", "quality"}:
            return m
    return None


def _scorer_registered(canonical_id: str) -> bool:
    """True only when the catalog registers a real quality-scorer version.

    Reads through the catalog accessor (never a hand-maintained mapping).
    """
    action = catalog.get_action(canonical_id)
    if not action:
        return False
    scorer = (action.get("capabilities") or {}).get("quality_scorer")
    return isinstance(scorer, str) and bool(scorer.strip())


def should_write_score(result: dict) -> tuple[bool, str]:
    """Decide whether this result earns a motion_scores row.

    Returns (write_score, reason). Reason explains why NOT, so the profile write
    is auditable and the 30-day profile never silently drops a valid score.
    """
    recognition = result.get("recognition") or {}
    canonical_id = recognition.get("canonical_id")
    state = recognition.get("state")
    if not canonical_id or state not in _RECOGNIZED_STATES:
        return False, "no_recognized_action"

    metrics = result.get("metrics") or []
    quality = _quality_metric(metrics)
    if quality is None:
        # No quality metric: visual-coaching-only action (e.g. bicep_curl before G
        # ships a scorer). Observations still go to motion_events; no score.
        return False, "no_quality_metric"

    if not _scorer_registered(canonical_id):
        # A scorer field exists in the receipt but the catalog has not registered
        # a verified scorer version for this action. Do not fabricate a score.
        return False, "scorer_not_registered"

    if not quality.get("scorer_version"):
        return False, "scorer_version_unregistered"

    return True, "ok"


def _build_score_row(*, run: MotionAnalysisRun, result: dict, quality: dict) -> MotionScore:
    recognition = result.get("recognition") or {}
    canonical_id = recognition.get("canonical_id")
    metrics = {m.get("id"): m.get("value") for m in result.get("metrics") or [] if isinstance(m, dict)}
    return MotionScore(
        user_id=run.user_id,
        job_id=run.ai_job_id,
        exercise_type=canonical_id,
        requested_exercise_type=run.requested_type or "auto",
        recognition_method=recognition.get("source") or "unified_v2",
        recognition_confidence=float(recognition.get("confidence") or 0.0),
        completeness=float(metrics.get("completeness") or 0.0),
        stability=float(metrics.get("stability") or 0.0),
        rhythm_control=float(metrics.get("rhythm_control") or 0.0),
        risk_index=float(metrics.get("risk_index") or 0.0),
        overall=float(quality.get("value") or 0.0),
        confidence=float(quality.get("confidence") or recognition.get("confidence") or 0.0),
        evidence_json=json.dumps(
            {
                "basis": ["unified_v2", recognition.get("source") or "vision"],
                "scorer_version": quality.get("scorer_version"),
                "pipeline_version": result.get("pipeline_version"),
                "result_version": result.get("result_version"),
            },
            ensure_ascii=False,
        ),
    )


def _observation_events(result: dict) -> list[dict]:
    """Visual coaching observations -> motion_events payloads (severity info)."""
    timeline = result.get("timeline") or {}
    events: list[dict] = []
    for frame in timeline.get("frames") or []:
        if not isinstance(frame, dict):
            continue
        t_ms = frame.get("timestamp_ms")
        try:
            t_seconds = round(float(t_ms) / 1000.0, 3)
        except (TypeError, ValueError):
            t_seconds = 0.0
        events.append(
            {
                "event_type": "visual_observation",
                "timestamp_seconds": t_seconds,
                "evidence": {
                    "frame_id": frame.get("id"),
                    "phase": frame.get("phase"),
                    "observation": frame.get("observation"),
                    "explanation": frame.get("explanation"),
                    "next_step": frame.get("next_step"),
                    "advice_kind": frame.get("advice_kind"),
                },
            }
        )
    return events


def record_run_profile(db: Session, *, run: MotionAnalysisRun, result: dict) -> dict[str, Any]:
    """Idempotently write motion_scores (+motion_events) for a finished run.

    Safe to call repeatedly (e.g. on re-analysis / post-review rebuild): the
    score row is keyed by ai_job_id (unique) and prior observation events for the
    job are replaced so a category change never leaves stale observations behind.
    """
    outcome: dict[str, Any] = {
        "run_id": run.id,
        "score_written": False,
        "score_reason": "",
        "events_written": 0,
    }
    if not isinstance(result, dict) or not result:
        outcome["score_reason"] = "no_result"
        return outcome

    # --- observation events (always refreshed; info severity, not a score) --- #
    if run.ai_job_id is not None:
        db.execute(
            MotionEvent.__table__.delete().where(  # type: ignore[attr-defined]
                MotionEvent.job_id == run.ai_job_id
            )
        )
        for index, ev in enumerate(_observation_events(result)):
            db.add(
                MotionEvent(
                    user_id=run.user_id,
                    job_id=run.ai_job_id,
                    event_index=index,
                    event_type=ev["event_type"],
                    timestamp_seconds=ev["timestamp_seconds"],
                    severity="info",
                    evidence_json=json.dumps(ev["evidence"], ensure_ascii=False),
                )
            )
            outcome["events_written"] += 1

    # --- score row (only when the closed-loop conditions hold) ---------------- #
    write_score, reason = should_write_score(result)
    outcome["score_reason"] = reason
    if not write_score:
        return outcome
    if run.ai_job_id is None:
        outcome["score_reason"] = "no_job_id"
        return outcome

    existing = db.scalar(
        select(MotionScore).where(MotionScore.job_id == run.ai_job_id)
    )
    quality = _quality_metric(result.get("metrics") or []) or {}
    row = _build_score_row(run=run, result=result, quality=quality)
    if existing is None:
        db.add(row)
    else:
        # Re-analysis of the same job: refresh the recognised action + metrics.
        existing.exercise_type = row.exercise_type
        existing.overall = row.overall
        existing.stability = row.stability
        existing.completeness = row.completeness
        existing.rhythm_control = row.rhythm_control
        existing.risk_index = row.risk_index
        existing.evidence_json = row.evidence_json
    db.flush()
    outcome["score_written"] = True
    return outcome


__all__ = [
    "should_write_score",
    "record_run_profile",
]
