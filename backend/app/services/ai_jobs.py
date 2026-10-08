from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select, update, or_, case, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.time import utc_now, utc_iso
from app.services.storage import S3Storage, StorageError, get_storage
from app.models import (
    AIJob,
    AIWorkerNode,
    MediaAsset,
    MotionAnalysisFeedback,
    MotionAnalysisRun,
    MotionEvidenceFrame,
)


def json_loads(value: str, default):
    try:
        return json.loads(value or "")
    except (ValueError, TypeError):
        return default


def create_ai_job(
    db: Session,
    *,
    user_id: int,
    job_type: str,
    media_asset_id: int | None,
    payload: dict | None = None,
    priority: int = 100,
) -> AIJob:
    payload = dict(payload or {})
    if job_type == "food_vision":
        route = str(payload.get("route") or settings.food_route_default).lower()
        payload["route"] = route if route in {"cloud", "worker"} else "cloud"
    payload_text = json.dumps(
        payload,
        ensure_ascii=False,
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    )
    key = hashlib.sha256(
        f"{user_id}:{job_type}:{media_asset_id}:{payload_text}".encode()
    ).hexdigest()
    # Include completed/failed/waiting jobs: polling/repeated POSTs must not duplicate results.
    existing = db.scalar(
        select(AIJob)
        .where(
            AIJob.user_id == user_id,
            AIJob.job_type == job_type,
            AIJob.media_asset_id == media_asset_id,
            AIJob.payload_json == payload_text,
        )
        .order_by(AIJob.id.desc())
        .limit(1)
    )
    if existing:
        return existing
    job = AIJob(
        user_id=user_id,
        media_asset_id=media_asset_id,
        job_type=job_type,
        status="queued",
        progress=0,
        priority=priority,
        payload_json=payload_text,
        dedupe_key=key,
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(AIJob).where(AIJob.dedupe_key == key))
        if existing:
            return existing
        raise
    db.refresh(job)
    return job


def requeue_expired_jobs(db: Session) -> int:
    now = utc_now()
    purge_expired_motion_previews(db, now=now)
    condition = (AIJob.status == "processing", AIJob.lease_expires_at < now)
    clear = dict(
        worker_id="", lease_token="", lease_expires_at=None, claim_request_id=None
    )
    # R12 event-chain fix: jobs whose lease is exhausted become permanently
    # failed; the linked run must reach the same terminal state or the
    # miniprogram polls a queued/processing run forever.
    _TERMINAL_RUN = ("completed", "partial", "failed", "cancelled")
    exhausted_ids = db.execute(
        select(AIJob.id).where(*condition, AIJob.attempts >= settings.worker_max_attempts)
    ).scalars().all()
    exhausted = db.execute(
        update(AIJob)
        .where(AIJob.id.in_(exhausted_ids))
        .values(
            **clear,
            status="failed",
            finished_at=now,
            error_code="lease_exhausted",
            error_message="AI Worker 多次失联，任务已停止自动重试",
        ),
        execution_options={"synchronize_session": False},
    ).rowcount
    if exhausted_ids:
        db.execute(
            update(MotionAnalysisRun)
            .where(
                MotionAnalysisRun.ai_job_id.in_(exhausted_ids),
                MotionAnalysisRun.status.notin_(_TERMINAL_RUN),
            )
            .values(
                status="failed",
                error_code="lease_exhausted",
                finished_at=now,
            ),
            execution_options={"synchronize_session": False},
        )
    queued = db.execute(
        update(AIJob)
        .where(*condition, AIJob.attempts < settings.worker_max_attempts)
        .values(
            **clear,
            status="queued",
            progress=0,
            next_attempt_at=None,
            error_code="lease_expired",
            error_message="上一 AI Worker 租约超时，等待重新调度",
        ),
        execution_options={"synchronize_session": False},
    ).rowcount
    db.commit()
    db.expire_all()
    return exhausted + queued


def _strip_image_bytes_from_result(result: dict) -> bool:
    """Remove image bytes / data: URLs from a motion result blob (V1 or V2).

    V1 motion_pose frames carry image_b64; the unified V2 result renders
    keyframes[].image_url as a data:image/jpeg;base64 URL. Both are long-lived
    TEXT columns and must be swept after the short retention window.
    """
    stripped = False
    frames = result.get("frames")
    if isinstance(frames, list):
        for frame in frames:
            if not isinstance(frame, dict):
                continue
            had_image = False
            for key in ["image_b64", "image_mime", "preview_bytes"]:
                if key in frame:
                    frame.pop(key, None)
                    had_image = True
            url = frame.get("url")
            if isinstance(url, str) and url.startswith("data:image/"):
                frame["url"] = ""
                had_image = True
            # Legacy marker retained so historical readers/tests see the strip.
            if had_image:
                frame["preview_expired"] = True
                stripped = True
    keyframes = result.get("keyframes")
    if isinstance(keyframes, list):
        for keyframe in keyframes:
            if not isinstance(keyframe, dict):
                continue
            url = keyframe.get("image_url")
            if isinstance(url, str) and url.startswith("data:image/"):
                keyframe["image_url"] = ""
                stripped = True
    return stripped


def purge_expired_motion_previews(db: Session, *, now=None, limit: int = 100) -> int:
    """Bound preview retention without deleting structured motion evidence.

    R15: covers BOTH the legacy motion_pose chain AND the unified V2 chain --
    AIJob.result_json (raw receipt frames) and MotionAnalysisFeedback.result_json
    (the unified snapshot with data: URLs in keyframes) -- plus expired
    motion_evidence_frames rows. Expiry, media deletion and account deletion all
    flow through this sweeper.
    """
    now = now or utc_now()
    cutoff = now - timedelta(days=settings.motion_preview_retention_days)
    changed = 0

    # --- AIJob raw receipts (motion_pose + motion_unified) ---------------------
    jobs = db.scalars(
        select(AIJob)
        .where(
            AIJob.job_type.in_(["motion_pose", "motion_unified"]),
            AIJob.status == "done",
            AIJob.finished_at < cutoff,
            AIJob.result_json.contains('"image_b64"'),
        )
        .order_by(AIJob.finished_at, AIJob.id)
        .limit(max(1, min(limit, 500)))
    ).all()
    for job in jobs:
        result = json_loads(job.result_json, {})
        if not isinstance(result, dict):
            continue
        if _strip_image_bytes_from_result(result):
            job.result_json = json.dumps(result, ensure_ascii=False, default=str)
            db.add(job)
            changed += 1

    # --- unified result snapshots (MotionAnalysisFeedback.result_json) ----------
    feedbacks = db.scalars(
        select(MotionAnalysisFeedback)
        .where(
            MotionAnalysisFeedback.created_at < cutoff,
            MotionAnalysisFeedback.result_json.contains("data:image/"),
        )
        .order_by(MotionAnalysisFeedback.created_at, MotionAnalysisFeedback.id)
        .limit(max(1, min(limit, 500)))
    ).all()
    for fb in feedbacks:
        result = json_loads(fb.result_json, {})
        if not isinstance(result, dict):
            continue
        if _strip_image_bytes_from_result(result):
            fb.result_json = json.dumps(result, ensure_ascii=False, default=str)
            db.add(fb)
            changed += 1

    # --- expired generic evidence frames (reference-only rows) ------------------
    deleted = db.execute(
        delete(MotionEvidenceFrame).where(MotionEvidenceFrame.expires_at < now)
    ).rowcount
    changed += int(deleted or 0)
    return changed


def heartbeat_worker(
    db: Session,
    *,
    worker_id: str,
    name: str,
    version: str,
    gpu_name: str,
    capabilities: list[str],
    metadata: dict,
) -> AIWorkerNode:
    node = db.scalar(select(AIWorkerNode).where(AIWorkerNode.worker_id == worker_id))
    if not node:
        node = AIWorkerNode(worker_id=worker_id)
        db.add(node)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            node = db.scalar(
                select(AIWorkerNode).where(AIWorkerNode.worker_id == worker_id)
            )
    node.name, node.version, node.gpu_name = name, version, gpu_name
    node.capabilities_json = json.dumps(sorted(set(capabilities)))
    node.metadata_json = json.dumps(metadata or {}, ensure_ascii=False, default=str)
    node.status, node.last_seen_at = "online", utc_now()
    db.commit()
    db.refresh(node)
    return node


def claim_next_job(
    db: Session,
    *,
    worker_id: str,
    capabilities: list[str],
    request_id: str | None = None,
) -> AIJob | None:
    requeue_expired_jobs(db)
    supported = [
        x for x in capabilities if x in {"motion_pose", "food_vision", "kinetics400"}
    ]
    # The unified motion chain (job_type "motion_unified") is only claimed by
    # workers that explicitly declare a unified capability: motion_unified_v1 (old
    # receipt contract) or motion_unified_v2 (contract "motion-worker-v2"). A worker
    # declaring neither cannot claim unified jobs; the run's effective_pipeline_version
    # selects the receipt shape server-side. During the migration window a v1 worker may
    # still claim a unified job, which is the intended backward-compatible rollout.
    if "motion_unified_v1" in capabilities or "motion_unified_v2" in capabilities:
        supported.append("motion_unified")
    if not supported:
        return None
    if request_id:
        previous = db.scalar(select(AIJob).where(AIJob.claim_request_id == request_id))
        if previous:
            return (
                previous
                if previous.status == "processing" and previous.worker_id == worker_id
                else None
            )
    # Compare-and-swap UPDATE is atomic on MySQL 5.7/8 and SQLite. No SKIP LOCKED dependency.
    # Small bounded batch lets a worker move past stale sources without spending attempts.
    for _ in range(50):
        now = utc_now()
        job = db.scalar(
            select(AIJob)
            .where(
                AIJob.status == "queued",
                AIJob.job_type.in_(supported),
                AIJob.attempts < settings.worker_max_attempts,
                or_(AIJob.next_attempt_at.is_(None), AIJob.next_attempt_at <= now),
            )
            .order_by(AIJob.priority, AIJob.created_at, AIJob.id)
            .limit(1)
        )
        if not job:
            db.rollback()
            return None
        source = job_source(db, job)
        if not source.get("url") or source.get("expired"):
            code = "media_url_expired" if source.get("expired") else "media_url_missing"
            db.execute(
                update(AIJob)
                .where(AIJob.id == job.id, AIJob.status == "queued")
                .values(
                    status="waiting_source_refresh",
                    error_code=code,
                    error_message="请在小程序刷新媒体下载地址",
                    finished_at=None,
                ),
                execution_options={"synchronize_session": False},
            )
            db.commit()
            db.expire_all()
            continue
        changed = db.execute(
            update(AIJob)
            .where(AIJob.id == job.id, AIJob.status == "queued")
            .values(
                status="processing",
                progress=1,
                worker_id=worker_id,
                lease_token=uuid4().hex,
                lease_expires_at=now + timedelta(seconds=settings.worker_lease_seconds),
                attempts=AIJob.attempts + 1,
                started_at=job.started_at or now,
                claim_request_id=request_id,
                error_code="",
                error_message="",
            ),
            execution_options={"synchronize_session": False},
        ).rowcount
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            if request_id:
                previous = db.scalar(
                    select(AIJob).where(AIJob.claim_request_id == request_id)
                )
                if previous and previous.worker_id == worker_id:
                    return previous
            raise
        db.refresh(job)
        if changed:
            return job
    return None


def extend_lease(
    db: Session,
    job: AIJob,
    *,
    worker_id: str,
    lease_token: str,
    progress: int | None = None,
    commit: bool = True,
):
    now = utc_now()
    values = {
        "lease_expires_at": now + timedelta(seconds=settings.worker_lease_seconds)
    }
    if progress is not None:
        value = min(99, max(0, int(progress)))
        values["progress"] = case((AIJob.progress < value, value), else_=AIJob.progress)
    changed = db.execute(
        update(AIJob)
        .where(
            AIJob.id == job.id,
            AIJob.status == "processing",
            AIJob.worker_id == worker_id,
            AIJob.lease_token == lease_token,
            AIJob.lease_expires_at > now,
        )
        .values(**values),
        execution_options={"synchronize_session": False},
    ).rowcount
    if not changed:
        db.rollback()
        raise ValueError("任务租约已失效或过期")
    # complete/fail keep the UPDATE row lock until their final commit.
    if commit:
        db.commit()
    db.refresh(job)
    return job


def job_source(db: Session, job: AIJob) -> dict:
    asset = db.get(MediaAsset, job.media_asset_id) if job.media_asset_id else None
    if not asset:
        return {}
    url = (asset.source_url or "").strip()
    if asset.storage_backend == "s3":
        try:
            url = S3Storage().public_url(asset.storage_key)
        except StorageError:
            url = ""
    if not url and asset.storage_backend == "local" and not settings.is_production:
        url = f"{settings.public_base_url.rstrip('/')}/uploads/{asset.storage_key.lstrip('/')}"
    return {
        "media_id": asset.id,
        "url": url,
        "cloud_file_id": asset.cloud_file_id or "",
        "content_type": asset.content_type,
        "media_type": asset.media_type,
        "original_name": asset.original_name,
        "size_bytes": asset.size_bytes,
        "source_url_expires_at": utc_iso(asset.source_url_expires_at),
        "expired": bool(
            asset.source_url_expires_at and asset.source_url_expires_at <= utc_now()
        ),
    }


def refresh_waiting_jobs(db: Session, media_id: int) -> int:
    # URL renewal is not an inference retry: preserve consumed attempts and original job ID.
    return db.execute(
        update(AIJob)
        .where(
            AIJob.media_asset_id == media_id, AIJob.status == "waiting_source_refresh"
        )
        .values(
            status="queued",
            progress=0,
            error_code="",
            error_message="",
            finished_at=None,
            next_attempt_at=None,
            claim_request_id=None,
        ),
        execution_options={"synchronize_session": False},
    ).rowcount


def public_job(job: AIJob) -> dict:
    result = json_loads(job.result_json, None) if job.result_json else None
    if isinstance(result, dict) and isinstance(result.get("frames"), list):
        for frame in result["frames"]:
            if not isinstance(frame, dict):
                continue
            encoded = frame.pop("image_b64", None)
            if encoded:
                frame["url"] = f"data:image/jpeg;base64,{encoded}"

    error = None
    if job.error_message:
        if job.status == "queued":
            error = "任务暂时中断，正在重新安排。"
        elif job.status == "waiting_source_refresh":
            error = "媒体访问地址需要刷新，刷新后会继续处理。"
        elif job.status == "failed":
            if job.job_type == "food_analysis":
                error = "餐食识别没有完成，可以手动填写饮食记录。"
            elif job.job_type in {"motion_unified", "motion_pose", "kinetics400"}:
                error = "视频分析没有完成，请重新选择视频后再试。"
            else:
                error = "任务没有完成，请稍后重试。"
        elif job.status == "cancelled":
            error = "任务已取消。"

    return {
        "job_id": job.id,
        "job_type": job.job_type,
        "media_id": job.media_asset_id,
        "status": job.status,
        "progress": job.progress,
        "attempts": job.attempts,
        "error_code": job.error_code or None,
        # Worker diagnostics stay in the database for operators; never return
        # arbitrary worker text (which may contain endpoints or stack details)
        # to user-facing job pages.
        "error": error,
        "result": result,
        "created_at": utc_iso(job.created_at),
        "started_at": utc_iso(job.started_at),
        "finished_at": utc_iso(job.finished_at),
    }
