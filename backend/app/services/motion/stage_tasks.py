"""Persistent post-processing stage queue (V2, contract section 8 / spec 9.1).

The worker receipt HTTP transaction only:
  1. authenticates + checks the lease,
  2. validates the receipt schema,
  3. stores local evidence (motion_evidence_frames),
  4. flips the run to the local-done stage,
  5. enqueues post-processing stage rows (vision_review, feedback_generation),
  6. commits and returns immediately.

DeepSeek / Tencent calls happen in a background consumer that claims these rows
and commits at every status transition, so polling observes real progress. This
is NOT an in-process BackgroundTask: rows survive a process restart.

Compare-and-set by (run_id, stage, version): a consumer holding an older lease
cannot complete a stage that has been re-enqueued at a newer version, and a
duplicate / out-of-order complete cannot overwrite a newer result.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Callable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now
from app.models import (
    AIJob,
    MotionAnalysisFeedback,
    MotionAnalysisRun,
    MotionEvidenceFrame,
    MotionStageTask,
)
from app.schemas.worker import (
    MOTION_WORKER_RESULT_SCHEMA_VERSION,
    MOTION_WORKER_RESULT_V2_SCHEMA_VERSION,
    MotionResultSchemaError,
)

logger = logging.getLogger("healthmate.motion.stage_tasks")

STAGE_VISION_REVIEW = "vision_review"
STAGE_FEEDBACK = "feedback_generation"

# Re-exported for the callers and tests that address the V2 adapter by its
# canonical name (spec §7.3).
from app.services.motion.evidence_v2 import (  # noqa: E402
    MotionEvidenceBundle,
    evidence_from_worker_v2,
)

__all__ = [
    "STAGE_VISION_REVIEW",
    "STAGE_FEEDBACK",
    "MotionEvidenceBundle",
    "evidence_from_worker_v2",
    "enqueue_postprocessing_stages",
    "process_pending_stages",
]

STATUS_QUEUED = "queued"
STATUS_PROCESSING = "processing"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

# Lease for a claimed stage; a crashed consumer lets another claim after expiry.
DEFAULT_LEASE_SECONDS = 120


class StageTaskNotFound(RuntimeError):
    pass


def _json(payload: dict | None) -> str:
    return json.dumps(payload or {}, ensure_ascii=False, default=str)


def next_version(db: Session, *, run_id: int, stage: str) -> int:
    current = db.scalar(
        select(MotionStageTask.version)
        .where(
            MotionStageTask.run_id == run_id,
            MotionStageTask.stage == stage,
        )
        .order_by(MotionStageTask.version.desc())
        .limit(1)
    )
    return int(current or 0) + 1


def enqueue_stage(
    db: Session,
    *,
    run_id: int,
    stage: str,
    payload: dict | None = None,
    version: int | None = None,
) -> MotionStageTask:
    """Insert a queued stage task. Auto-bumps version when omitted.

    A re-analysis / retry re-enqueues the same (run, stage) at a higher version;
    the unique (run_id, stage, version) key keeps every attempt as its own row.
    """
    if version is None:
        version = next_version(db, run_id=run_id, stage=stage)
    task = MotionStageTask(
        run_id=run_id,
        stage=stage,
        version=version,
        status=STATUS_QUEUED,
        lease_token="",
        attempts=0,
        available_at=utc_now(),
        error_code=None,
        payload_json=_json(payload),
    )
    db.add(task)
    db.flush()
    return task


def enqueue_postprocessing_stages(
    db: Session,
    *,
    run_id: int,
    payload: dict | None = None,
) -> list[MotionStageTask]:
    """Enqueue the standard V2 post-processing stages after a worker receipt."""
    stages: list[MotionStageTask] = []
    for stage in (STAGE_VISION_REVIEW, STAGE_FEEDBACK):
        stages.append(enqueue_stage(db, run_id=run_id, stage=stage, payload=payload))
    return stages


def claim_stage(
    db: Session,
    *,
    run_id: int,
    stage: str,
    version: int,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> MotionStageTask | None:
    """Atomically claim a queued stage for processing.

    Returns the row if this call won the claim, else None (already claimed by
    another consumer, already done, or not yet available). Uses a CAS UPDATE so
    two racing consumers cannot both own the row.
    """
    now = utc_now()
    lease_token = uuid.uuid4().hex
    changed = db.execute(
        update(MotionStageTask)
        .where(
            MotionStageTask.run_id == run_id,
            MotionStageTask.stage == stage,
            MotionStageTask.version == version,
            MotionStageTask.status == STATUS_QUEUED,
        )
        .values(
            status=STATUS_PROCESSING,
            lease_token=lease_token,
            attempts=MotionStageTask.attempts + 1,
            available_at=now + timedelta(seconds=lease_seconds),
        ),
        execution_options={"synchronize_session": False},
    ).rowcount
    db.flush()
    if not changed:
        return None
    return db.scalar(
        select(MotionStageTask).where(
            MotionStageTask.run_id == run_id,
            MotionStageTask.stage == stage,
            MotionStageTask.version == version,
        )
    )


def complete_stage(
    db: Session,
    *,
    run_id: int,
    stage: str,
    version: int,
    error_code: str | None = None,
) -> bool:
    """Compare-and-set finish. Only a row currently PROCESSING at this version
    can move to done/failed. A consumer holding an older lease cannot finish a
    stage that has been superseded by a newer version (re-analysis), so it cannot
    overwrite a newer result.
    """
    latest = db.scalar(
        select(MotionStageTask.version)
        .where(
            MotionStageTask.run_id == run_id,
            MotionStageTask.stage == stage,
        )
        .order_by(MotionStageTask.version.desc())
        .limit(1)
    )
    if latest is not None and version < latest:
        return False
    new_status = STATUS_FAILED if error_code else STATUS_DONE
    changed = db.execute(
        update(MotionStageTask)
        .where(
            MotionStageTask.run_id == run_id,
            MotionStageTask.stage == stage,
            MotionStageTask.version == version,
            MotionStageTask.status == STATUS_PROCESSING,
        )
        .values(status=new_status, error_code=error_code, lease_token=""),
        execution_options={"synchronize_session": False},
    ).rowcount
    db.flush()
    return bool(changed)


def fail_stage(
    db: Session,
    *,
    run_id: int,
    stage: str,
    version: int,
    error_code: str,
    retry_seconds: int = 60,
) -> bool:
    """CAS a processing stage to failed with a short backoff before retry."""
    now = utc_now()
    changed = db.execute(
        update(MotionStageTask)
        .where(
            MotionStageTask.run_id == run_id,
            MotionStageTask.stage == stage,
            MotionStageTask.version == version,
            MotionStageTask.status == STATUS_PROCESSING,
        )
        .values(
            status=STATUS_QUEUED,
            error_code=error_code,
            lease_token="",
            available_at=now + timedelta(seconds=retry_seconds),
        ),
        execution_options={"synchronize_session": False},
    ).rowcount
    db.flush()
    return bool(changed)


def pending_tasks(
    db: Session,
    *,
    run_id: int | None = None,
    limit: int = 20,
) -> list[MotionStageTask]:
    """Queued tasks available right now, oldest first. Scoped to one run when given."""
    now = utc_now()
    stmt = select(MotionStageTask).where(
        MotionStageTask.status == STATUS_QUEUED,
        MotionStageTask.available_at.is_(None) | (MotionStageTask.available_at <= now),
    )
    if run_id is not None:
        stmt = stmt.where(MotionStageTask.run_id == run_id)
    stmt = stmt.order_by(MotionStageTask.run_id, MotionStageTask.id).limit(limit)
    return list(db.scalars(stmt))


StageHandler = Callable[[Session, MotionStageTask], None]


def drain_pending_stages(
    db: Session,
    *,
    run_id: int | None = None,
    stage_handler: StageHandler | None = None,
    limit: int = 10,
) -> int:
    """Claim and run pending stage tasks. Each transition commits independently.

    ``stage_handler(db, task)`` performs the external work (DeepSeek vision /
    summary). It runs OUTSIDE any held write lock between claim and complete.
    When handler is None (background loop not yet wired to a real post-processor),
    the rows stay queued and are picked up by the integrated consumer later.
    """
    processed = 0
    for task in pending_tasks(db, run_id=run_id, limit=limit):
        claimed = claim_stage(
            db,
            run_id=task.run_id,
            stage=task.stage,
            version=task.version,
        )
        if not claimed:
            continue
        db.commit()
        try:
            if stage_handler is not None:
                stage_handler(db, claimed)
            ok = complete_stage(
                db,
                run_id=claimed.run_id,
                stage=claimed.stage,
                version=claimed.version,
            )
            db.commit()
            if ok:
                processed += 1
        except Exception as exc:  # noqa: BLE001 - one bad stage must not poison others
            logger.warning(
                "stage %s v%s for run %s failed: %s",
                claimed.stage,
                claimed.version,
                claimed.run_id,
                exc,
            )
            fail_stage(
                db,
                run_id=claimed.run_id,
                stage=claimed.stage,
                version=claimed.version,
                error_code=type(exc).__name__,
            )
            db.commit()
    return processed


def stage_status_map(db: Session, *, run_id: int) -> dict[str, str]:
    """Latest status per stage for a run (used by polling / diagnostics)."""
    rows = db.scalars(
        select(MotionStageTask)
        .where(MotionStageTask.run_id == run_id)
        .order_by(MotionStageTask.version.desc())
    )
    out: dict[str, str] = {}
    for row in rows:
        out.setdefault(row.stage, row.status)
    return out


# --------------------------------------------------------------------------- #
# Integrated background consumer (work-package E delivery point).
#
# A-package tests / any scheduler call:
#
#     from app.services.motion.stage_tasks import process_pending_stages
#     process_pending_stages(db)                 # all pending runs
#     process_pending_stages(db, run_id=run.id)   # one run
#
# The HTTP receipt returns at evidence_ready; this consumer drives the run to a
# terminal state. Each status transition commits independently, so polling sees
# real progress and a restart resumes from the persisted rows.
# --------------------------------------------------------------------------- #

Reviewer = Callable[[list, dict], object]
Profiler = Callable[..., dict]


def _load_receipt(db: Session, run: MotionAnalysisRun) -> dict:
    job = db.get(AIJob, run.ai_job_id) if run.ai_job_id else None
    if job is None:
        return {}
    try:
        return json.loads(job.result_json or "{}") or {}
    except (ValueError, TypeError):
        return {}


def _evidence_from_receipt(receipt: dict) -> dict:
    """Translate a worker receipt into internal evidence (spec §7.3).

    A ``motion-worker-v2`` receipt is parsed by the frozen V2 contract and mapped
    through the single V2 adapter; the V1 ``recognition``/``pose``/``score`` keys
    do not exist in a V2 receipt, so reading them (and returning an empty bundle)
    is exactly the defect this replaces.

    A legacy receipt without ``schema_version`` keeps the V1 hand-written shape
    for the migration window; an unrecognised version raises instead of silently
    degrading, because a dropped schema version means dropped evidence.
    """
    version = receipt.get("schema_version")
    if version == MOTION_WORKER_RESULT_V2_SCHEMA_VERSION:
        from app.services.motion.evidence_v2 import evidence_from_worker_v2

        return evidence_from_worker_v2(receipt).to_apply_post_review()
    if version is not None and version != MOTION_WORKER_RESULT_SCHEMA_VERSION:
        raise MotionResultSchemaError(
            "不支持的回执 schema 版本", field_path="schema_version"
        )
    recognition = receipt.get("recognition")
    if not isinstance(recognition, dict):
        recognition = {}
    return {
        "recognition": recognition,
        "pose": receipt.get("pose") or {},
        "score": receipt.get("score") or {},
        "frames": receipt.get("frames") or [],
        "kinetics": receipt.get("kinetics") or {},
        "candidate_ids": set(),
        "summary": receipt.get("summary") or {},
    }


def _frames_for_review(db: Session, run: MotionAnalysisRun) -> list:
    """Build DeepSeek frame inputs from the evidence pool.

    JPEG bytes are not persisted (contract: result_json holds references only);
    the bytes live in MediaStorage under ``preview_asset_id``. When bytes are
    missing (legacy rows / failed upload), the frame is still passed with a
    text-only observation so the model can answer from the whitelisted facts;
    the reviewer must tolerate frames without image bytes.
    """
    from app.services.motion.media_storage import (
        LocalPreviewStore,
        MediaStorage,
        PreviewNotFound,
        SqlEvidenceFrameStore,
    )
    from app.core.database import Base as app_base

    table = app_base.metadata.tables.get("motion_evidence_frames")
    storage = None
    if table is not None:
        try:
            store = LocalPreviewStore(
                Path(settings.upload_dir) / "motion-previews"
            )
            storage = MediaStorage(
                store, SqlEvidenceFrameStore(db, table),
                secret=settings.secret_key or "local-dev",
            )
        except Exception:  # noqa: BLE001 - never break review on storage errors
            storage = None

    rows = db.scalars(
        select(MotionEvidenceFrame)
        .where(MotionEvidenceFrame.run_id == run.id)
        .order_by(MotionEvidenceFrame.timestamp_ms)
    )
    frames: list = []
    for row in rows:
        obs: dict = {}
        try:
            obs = json.loads(row.observation_json or "{}") or {}
        except (ValueError, TypeError):
            obs = {}
        jpeg: bytes | None = None
        if storage is not None and row.preview_asset_id:
            try:
                jpeg = storage.read_preview_bytes(row.preview_asset_id)
            except (PreviewNotFound, LookupError):
                jpeg = None
        frames.append(
            {
                "frame_id": row.frame_id,
                "timestamp_ms": row.timestamp_ms or 0,
                "jpeg": jpeg,
                "visible_regions": obs.get("visible_regions"),
                "motion_delta": obs.get("motion_delta"),
                "blur": obs.get("blur"),
                "event": obs.get("event"),
                "phase": obs.get("phase"),
            }
        )
    return frames


def _review_context(receipt: dict) -> dict:
    vq = receipt.get("video_quality") or {}
    recognition = receipt.get("recognition") or {}
    return {
        "video_duration_ms": vq.get("duration_ms"),
        "candidates": receipt.get("recognition_candidates") or [],
        "measurements": receipt.get("measurements") or {},
        "visible_regions": [],
        "missing_regions": [],
        "local_label": recognition.get("selected_type"),
        "confirmed_goal": None,
    }


def _default_reviewer(frames: list, context: dict):
    from app.services.motion import vision_review

    return vision_review.run_visual_review(frames, context)


def _default_profiler(db: Session, *, run: MotionAnalysisRun, result: dict):
    from app.services.motion import profile_store

    return profile_store.record_run_profile(db, run=run, result=result)


def _run_stage(
    db: Session,
    task: MotionStageTask,
    *,
    reviewer: Reviewer | None,
    profiler: Profiler | None,
) -> None:
    run = db.get(MotionAnalysisRun, task.run_id)
    if run is None:
        return
    receipt = _load_receipt(db, run)

    if task.stage == STAGE_VISION_REVIEW:
        coach = None
        mode = run.cloud_review_mode or "off"
        # Skip DeepSeek entirely for offline / no-consent runs.
        if mode != "off":
            call_reviewer = reviewer or _default_reviewer
            try:
                coach = call_reviewer(
                    _frames_for_review(db, run), _review_context(receipt)
                )
            except Exception as exc:  # noqa: BLE001 - review is best-effort:
                # degrade to the local-only result instead of failing the stage
                # (missing preview bytes, provider transport error, or a
                # non-JSON model response must never block the run's terminal
                # state or retry forever).
                logger.warning(
                    "vision_review degraded for run %s (mode=%s): %s",
                    run.id,
                    mode,
                    exc,
                )
                coach = None
        from app.services.motion.orchestrator import apply_post_review

        # apply_post_review writes the feedback result_json, flips run to
        # completed and commits. coach=None => local evidence only.
        apply_post_review(
            db,
            run_id=run.id,
            coach_review=coach,
            evidence=_evidence_from_receipt(receipt),
        )
        return

    if task.stage == STAGE_FEEDBACK:
        feedback = db.scalar(
            select(MotionAnalysisFeedback).where(
                MotionAnalysisFeedback.run_id == run.id
            )
        )
        final: dict = {}
        if feedback is not None:
            try:
                final = json.loads(feedback.result_json or "{}") or {}
            except (ValueError, TypeError):
                final = {}
        (profiler or _default_profiler)(db, run=run, result=final)
        return


def process_pending_stages(
    db: Session,
    *,
    run_id: int | None = None,
    limit: int = 10,
    reviewer: Reviewer | None = None,
    profiler: Profiler | None = None,
) -> int:
    """Drive persisted post-processing stages to a terminal state.

    Parameters
    ----------
    reviewer:
        ``reviewer(frames, context) -> CoachReview``. Defaults to the real
        DeepSeek ``vision_review.run_visual_review``; pass a fake in tests.
        Skipped when ``run.cloud_review_mode == "off"``.
    profiler:
        ``profiler(db, run=, result=)``; defaults to
        ``profile_store.record_run_profile`` (F-package hook).
    """

    def handler(db: Session, task: MotionStageTask) -> None:
        _run_stage(db, task, reviewer=reviewer, profiler=profiler)

    return drain_pending_stages(
        db, run_id=run_id, stage_handler=handler, limit=limit
    )
