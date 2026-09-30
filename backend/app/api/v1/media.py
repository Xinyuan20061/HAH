from __future__ import annotations
import json
from app.core.time import utc_now, utc_iso

from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Request, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from urllib.parse import urlparse, unquote

from app.api.deps import current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.media_security import UnsafeMediaURL, validate_media_source_url
from app.models import MediaAsset, AIJob, MotionAnalysisRun, MotionAnalysisFeedback
from app.services.storage import get_storage
from app.services.ai_jobs import (
    create_ai_job,
    public_job,
    refresh_waiting_jobs,
    requeue_expired_jobs,
    json_loads,
)
from app.services.timeline import add_event
from app.services.result_summary import generate_result_summary
from app.services.motion.orchestrator import (
    PIPELINE_VERSION,
    create_unified_run,
    get_unified_result,
    run_stage_view,
)

router = APIRouter(prefix="/media", tags=["media"])
ALLOWED = {
    "video/mp4",
    ".mp4",
    "video/quicktime",
    ".mov",
    "image/jpeg",
    ".jpg",
    ".jpeg",
    "image/png",
    ".png",
    "image/webp",
    ".webp",
}


class MotionIn(BaseModel):
    file_name: str | None = None
    media_id: int | None = None
    exercise_type: str = Field(
        default="squat",
        pattern="^(auto|squat|pushup|lunge|leg_abduction|arm_abduction|arm_vw)$",
    )


class KineticsIn(BaseModel):
    file_name: str | None = None
    media_id: int | None = None


class CloudMediaIn(BaseModel):
    file_id: str = Field(min_length=8, max_length=700)
    temp_url: str = Field(min_length=8, max_length=4000)
    media_type: str = Field(pattern="^(image|video)$")
    original_name: str = Field(default="", max_length=255)
    content_type: str = Field(default="", max_length=120)
    size_bytes: int = Field(ge=1)
    expires_in: int = Field(default=7200, ge=300, le=86400)


class RefreshMediaIn(BaseModel):
    temp_url: str = Field(min_length=8, max_length=4000)
    expires_in: int = Field(default=7200, ge=300, le=86400)


def _validate_upload_type(filename: str, content_type: str):
    suffix = Path(filename or "").suffix.lower()
    if content_type not in ALLOWED and suffix not in ALLOWED:
        raise HTTPException(400, "暂仅支持 MP4/MOV/JPG/PNG/WebP")
    return suffix


def validate_cloud_identity(file_id: str, temp_url: str, user_id: int, media_type: str):
    parsed = urlparse(file_id)
    path = unquote(parsed.path).lstrip("/")
    if parsed.scheme != "cloud" or not parsed.netloc or parsed.query or parsed.fragment:
        raise HTTPException(400, "file_id 必须是 CloudBase 稳定文件引用")
    if not path.startswith(f"healthmate/u{user_id}/{media_type}/") or any(
        x in path.split("/") for x in ["..", ".", ""]
    ):
        raise HTTPException(403, "云文件必须位于当前用户的 HealthMate 目录")
    if settings.cloudbase_env_id and not (
        parsed.netloc == settings.cloudbase_env_id
        or parsed.netloc.startswith(settings.cloudbase_env_id + ".")
    ):
        raise HTTPException(400, "云文件环境与 CLOUDBASE_ENV_ID 不匹配")
    if settings.is_production:
        source = urlparse(temp_url)
        host = (source.hostname or "").lower()
        if not any(
            host.endswith(suffix)
            for suffix in [".tcb.qcloud.la", ".myqcloud.com", ".tcb.qcloud.com"]
        ):
            raise HTTPException(400, "仅接受腾讯 CloudBase/COS 临时媒体域名")
        if not unquote(source.path).endswith("/" + path):
            raise HTTPException(400, "临时 URL 必须指向所登记 fileID 的文件路径")
    return path


@router.post("/register-cloud")
def register_cloud_media(
    body: CloudMediaIn, user=Depends(current_user), db: Session = Depends(get_db)
):
    if not body.file_id.startswith("cloud://"):
        raise HTTPException(
            400, "file_id 必须是 wx.cloud.uploadFile 返回的 cloud:// 文件 ID"
        )
    if body.size_bytes > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"文件请控制在 {settings.max_upload_mb}MB 以内")
    try:
        temp_url = validate_media_source_url(body.temp_url)
    except UnsafeMediaURL as exc:
        raise HTTPException(400, str(exc))
    path = validate_cloud_identity(body.file_id, temp_url, user.id, body.media_type)
    suffix = _validate_upload_type(path, body.content_type)
    inferred = "video" if suffix in {".mp4", ".mov"} else "image"
    if body.media_type != inferred:
        raise HTTPException(400, "媒体类型与文件扩展名不一致")
    expected = {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix)
    if not expected or (body.content_type and body.content_type != expected):
        raise HTTPException(400, "文件扩展名或 Content-Type 不受支持")

    # Stable idempotency: the same cloud file registered twice returns the existing asset.
    asset = db.scalar(
        select(MediaAsset)
        .where(MediaAsset.storage_key == body.file_id)
        .with_for_update()
    )
    if asset and asset.user_id != user.id:
        raise HTTPException(409, "该云文件已绑定其他用户")
    if not asset:
        asset = MediaAsset(
            user_id=user.id,
            original_name=body.original_name,
            storage_key=body.file_id,
            storage_backend="cloudbase",
            cloud_file_id=body.file_id,
            media_type=body.media_type,
            content_type=expected,
            size_bytes=body.size_bytes,
            status="ready",
        )
        db.add(asset)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            asset = db.scalar(
                select(MediaAsset)
                .where(MediaAsset.storage_key == body.file_id)
                .with_for_update()
            )
            if not asset or asset.user_id != user.id:
                raise HTTPException(409, "该云文件已绑定其他用户")
    elif asset.media_type != body.media_type:
        raise HTTPException(409, "已登记的云文件不能变更媒体类型")
    asset.source_url = temp_url
    asset.source_url_expires_at = utc_now() + timedelta(seconds=body.expires_in)
    asset.original_name = body.original_name or asset.original_name
    asset.content_type = body.content_type or asset.content_type
    asset.size_bytes = body.size_bytes or asset.size_bytes
    refresh_waiting_jobs(db, asset.id)
    add_event(
        db,
        user.id,
        "media_uploaded",
        {
            "media_type": body.media_type,
            "storage": "cloudbase",
            "size_bytes": asset.size_bytes,
        },
        source="wx.cloud",
        ref_type="media_asset",
        ref_id=asset.id,
    )
    db.commit()
    db.refresh(asset)
    return {
        "ok": True,
        "media_id": asset.id,
        "file_name": asset.storage_key,
        "cloud_file_id": asset.cloud_file_id,
        "media_type": asset.media_type,
        "size": asset.size_bytes,
        "storage_backend": asset.storage_backend,
        "source_url_expires_at": utc_iso(asset.source_url_expires_at)
        if asset.source_url_expires_at
        else None,
    }


@router.put("/{media_id}/refresh-source")
def refresh_cloud_source(
    media_id: int,
    body: RefreshMediaIn,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    asset = db.scalar(
        select(MediaAsset).where(MediaAsset.id == media_id).with_for_update()
    )
    if not asset or asset.user_id != user.id:
        raise HTTPException(404, "媒体资产不存在")
    if asset.storage_backend != "cloudbase":
        raise HTTPException(409, "该素材不是 CloudBase 文件")
    try:
        asset.source_url = validate_media_source_url(body.temp_url)
    except UnsafeMediaURL as exc:
        raise HTTPException(400, str(exc))
    validate_cloud_identity(
        asset.cloud_file_id, asset.source_url, user.id, asset.media_type
    )
    asset.source_url_expires_at = utc_now() + timedelta(seconds=body.expires_in)
    resumed = refresh_waiting_jobs(db, asset.id)
    db.commit()
    return {
        "ok": True,
        "media_id": asset.id,
        "resumed_jobs": resumed,
        "source_url_expires_at": utc_iso(asset.source_url_expires_at),
    }


@router.post("/upload")
async def upload_media(
    file: UploadFile = File(...),
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    if settings.is_production:
        raise HTTPException(
            409,
            "生产环境请使用 wx.cloud.uploadFile + /media/register-cloud，禁止写容器临时磁盘",
        )
    suffix = _validate_upload_type(file.filename or "", file.content_type or "")
    data = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"文件请控制在 {settings.max_upload_mb}MB 以内")
    content_type = file.content_type or ""
    media_type = (
        "video"
        if content_type.startswith("video") or suffix in {".mp4", ".mov"}
        else "image"
    )
    key = f"u{user.id}/{media_type}/{uuid4().hex}{suffix or '.bin'}"
    storage = get_storage()
    storage.put_bytes(key, data, content_type)
    public_url = storage.public_url(key)
    if public_url.startswith("/"):
        public_url = settings.public_base_url.rstrip("/") + public_url
    asset = MediaAsset(
        user_id=user.id,
        original_name=file.filename or "",
        storage_key=key,
        storage_backend=settings.storage_backend.lower(),
        cloud_file_id="",
        source_url=public_url,
        media_type=media_type,
        content_type=content_type,
        size_bytes=len(data),
    )
    db.add(asset)
    db.flush()
    add_event(
        db,
        user.id,
        "media_uploaded",
        {
            "media_type": media_type,
            "storage": asset.storage_backend,
            "size_bytes": len(data),
        },
        source="upload",
        ref_type="media_asset",
        ref_id=asset.id,
    )
    db.commit()
    db.refresh(asset)
    return {
        "ok": True,
        "media_id": asset.id,
        "file_name": key,
        "media_type": media_type,
        "size": len(data),
        "url": public_url,
        "storage_backend": asset.storage_backend,
    }


@router.post("/motion-jobs", deprecated=True)
def create_motion_job(
    body: MotionIn, user=Depends(current_user), db: Session = Depends(get_db)
):
    """Deprecated: use POST /media/motion-analyses (one unified task)."""
    asset = None
    if body.media_id:
        asset = db.get(MediaAsset, body.media_id)
    elif body.file_name:
        asset = (
            db.query(MediaAsset)
            .filter(MediaAsset.storage_key == body.file_name)
            .first()
        )
    if not asset or asset.user_id != user.id:
        raise HTTPException(404, "视频资产不存在")
    if asset.media_type != "video":
        raise HTTPException(400, "动作分析仅支持视频")

    job = create_ai_job(
        db,
        user_id=user.id,
        job_type="motion_pose",
        media_asset_id=asset.id,
        payload={"exercise_type": body.exercise_type},
    )

    return {"ok": True, **public_job(job)}


@router.get("/motion-jobs/{job_id}", deprecated=True)
async def get_motion_job(
    job_id: int, user=Depends(current_user), db: Session = Depends(get_db)
):
    requeue_expired_jobs(db)
    job = db.get(AIJob, job_id)
    if not job or job.user_id != user.id or job.job_type != "motion_pose":
        raise HTTPException(404, "任务不存在")
    result = json_loads(job.result_json, None) if job.result_json else None
    if (
        isinstance(result, dict)
        and job.status in {"done", "completed", "succeeded"}
        and not result.get("summary")
    ):
        summary = await generate_result_summary(db, user, result)
        if summary:
            result["summary"] = summary
            job.result_json = json.dumps(result, ensure_ascii=False)
            db.commit()
    return public_job(job)


@router.post("/kinetics-jobs", deprecated=True)
def create_kinetics_job(
    body: KineticsIn, user=Depends(current_user), db: Session = Depends(get_db)
):
    asset = None
    if body.media_id:
        asset = db.get(MediaAsset, body.media_id)
    elif body.file_name:
        asset = (
            db.query(MediaAsset)
            .filter(MediaAsset.storage_key == body.file_name)
            .first()
        )
    if not asset or asset.user_id != user.id:
        raise HTTPException(404, "视频资产不存在")
    if asset.media_type != "video":
        raise HTTPException(400, "400 类识别仅支持视频")

    job = create_ai_job(
        db,
        user_id=user.id,
        job_type="kinetics400",
        media_asset_id=asset.id,
        payload={},
    )

    return {"ok": True, **public_job(job)}


@router.get("/kinetics-jobs/{job_id}", deprecated=True)
async def get_kinetics_job(
    job_id: int, user=Depends(current_user), db: Session = Depends(get_db)
):
    requeue_expired_jobs(db)
    job = db.get(AIJob, job_id)
    if not job or job.user_id != user.id or job.job_type != "kinetics400":
        raise HTTPException(404, "任务不存在")
    return public_job(job)


@router.post("/analyze-motion", deprecated=True)
def analyze_motion_compat(
    body: MotionIn, user=Depends(current_user), db: Session = Depends(get_db)
):
    return create_motion_job(body, user, db)


# --------------------------------------------------------------------------- #
# P0-B unified motion chain (spec 5). Errors follow the unified contract:
# {code, message, request_id, retryable, details?}.
# --------------------------------------------------------------------------- #

_EXERCISE_RE = r"^(auto|squat|pushup|lunge|leg_abduction|arm_abduction|arm_vw)$"


class MotionAnalysesIn(BaseModel):
    media_id: int
    requested_exercise: str = Field(pattern=_EXERCISE_RE)
    consent_deepseek_frames: bool = False
    pipeline_version: str = Field(default=PIPELINE_VERSION, max_length=60)
    analysis_revision: bool = False


class MotionRetryIn(BaseModel):
    reason: str = Field(default="after_fix", max_length=40)
    pipeline_version: str | None = Field(default=None, max_length=60)
    analysis_revision: bool = False


class ConfirmLabelIn(BaseModel):
    label_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,40}$")
    correction_reason: str = Field(default="", max_length=300)


class MotionFeedbackIn(BaseModel):
    useful: bool
    label_correction: str | None = Field(default=None, max_length=60)
    frame_id: str | None = Field(default=None, max_length=60)
    comment: str | None = Field(default=None, max_length=600)


def _motion_error(request: Request, status: int, code: str, message: str, retryable: bool = False, details: dict | None = None):
    payload = {
        "code": code,
        "message": message,
        "request_id": getattr(request.state, "request_id", None),
        "retryable": retryable,
    }
    if details is not None:
        payload["details"] = details
    return JSONResponse(status_code=status, content=payload)


def _owned_run(db: Session, request: Request, user, run_id: int) -> MotionAnalysisRun | JSONResponse:
    run = db.get(MotionAnalysisRun, run_id)
    if not run or run.user_id != user.id:
        return _motion_error(request, 404, "MOTION_ANALYSIS_NOT_FOUND", "动作分析任务不存在")
    return run


@router.post("/motion-analyses", status_code=202)
def create_motion_analysis(
    body: MotionAnalysesIn,
    request: Request,
    user=Depends(current_user),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, min_length=8, max_length=120),
):
    asset = db.get(MediaAsset, body.media_id)
    if not asset or asset.user_id != user.id:
        return _motion_error(request, 404, "MEDIA_NOT_FOUND", "视频资产不存在")
    if asset.media_type != "video":
        return _motion_error(request, 400, "MEDIA_NOT_VIDEO", "动作分析仅支持视频")

    if idempotency_key:
        key = idempotency_key
    else:
        # Repeat clicks / retries without an explicit key still dedupe on the
        # same (user, asset, params) fingerprint.
        import hashlib

        material = f"{user.id}:{asset.id}:{body.requested_exercise}:{int(body.consent_deepseek_frames)}:{body.pipeline_version}"
        key = "auto-" + hashlib.sha256(material.encode()).hexdigest()

    run, created, conflict = create_unified_run(
        db,
        user_id=user.id,
        asset=asset,
        requested_exercise=body.requested_exercise,
        consent_deepseek_frames=body.consent_deepseek_frames,
        pipeline_version=body.pipeline_version,
        idempotency_key=key,
        analysis_revision=body.analysis_revision,
    )
    if conflict:
        return _motion_error(
            request, 409, "IDEMPOTENCY_PARAM_MISMATCH", conflict,
            details={"dedupe_key": run.dedupe_key},
        )
    return JSONResponse(
        status_code=202,
        content={
            "analysis_id": run.id,
            "status": run.status,
            "poll_after_ms": 3000,
            "created": created,
        },
    )


@router.get("/motion-analyses/{analysis_id}")
def read_motion_analysis(
    analysis_id: int,
    request: Request,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    run = _owned_run(db, request, user, analysis_id)
    if isinstance(run, JSONResponse):
        return run
    requeue_expired_jobs(db)
    view = run_stage_view(db, run)
    result = get_unified_result(db, run)
    view["result"] = result
    return view


@router.post("/motion-analyses/{analysis_id}/retry")
def retry_motion_analysis(
    analysis_id: int,
    body: MotionRetryIn,
    request: Request,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    run = _owned_run(db, request, user, analysis_id)
    if isinstance(run, JSONResponse):
        return run
    if run.status != "failed":
        return _motion_error(
            request, 409, "RETRY_NOT_ALLOWED",
            "仅终态 failed 的任务可以重试，旧失败任务保留原因",
        )
    new_pipeline = body.pipeline_version or run.pipeline_version
    if new_pipeline == run.pipeline_version and not body.analysis_revision:
        return _motion_error(
            request, 409, "RETRY_NEEDS_REVISION",
            "重试必须提供新的 pipeline_version 或显式 analysis_revision",
        )
    asset = db.get(MediaAsset, run.media_asset_id)
    import uuid as _uuid

    child, created, conflict = create_unified_run(
        db,
        user_id=user.id,
        asset=asset,
        requested_exercise=run.requested_type,
        consent_deepseek_frames=bool(run.model_versions.get("consent_deepseek_frames")),
        pipeline_version=new_pipeline,
        idempotency_key=f"retry:{run.id}:{_uuid.uuid4().hex}",
        parent_run_id=run.id,
    )
    return {"analysis_id": child.id, "parent_run_id": run.id, "status": child.status}


@router.post("/motion-analyses/{analysis_id}/confirm-label")
def confirm_motion_label(
    analysis_id: int,
    body: ConfirmLabelIn,
    request: Request,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    run = _owned_run(db, request, user, analysis_id)
    if isinstance(run, JSONResponse):
        return run
    feedback = db.scalar(
        select(MotionAnalysisFeedback).where(MotionAnalysisFeedback.run_id == run.id)
    )
    result = feedback.result if feedback else {}
    # Record the correction; never fabricate a score out of it.
    result["user_confirmation"] = {
        "label_id": body.label_id,
        "correction_reason": body.correction_reason,
        "recorded_at": utc_now().isoformat() + "Z",
    }
    if feedback is None:
        feedback = MotionAnalysisFeedback(
            run_id=run.id, user_id=user.id, result_json=json.dumps(result, ensure_ascii=False)
        )
        db.add(feedback)
    else:
        feedback.result_json = json.dumps(result, ensure_ascii=False)
    db.commit()
    return {"ok": True, "analysis_id": run.id, "label_id": body.label_id, "score_fabricated": False}


@router.get("/motion-analyses/{analysis_id}/trace")
def motion_analysis_trace(
    analysis_id: int,
    request: Request,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    run = _owned_run(db, request, user, analysis_id)
    if isinstance(run, JSONResponse):
        return run
    result = get_unified_result(db, run) or {}
    rec = result.get("recognition") or {}
    meta = result.get("_meta") or {}
    return {
        "analysis_id": run.id,
        "status": run.status,
        "recognition_state": rec.get("state"),
        "label_id": rec.get("label_id"),
        "reason_code": rec.get("reason_code"),
        "review_status": rec.get("review_status"),
        "summary": result.get("summary"),
        "sources": rec.get("sources"),
        "stage_log": meta.get("stages"),
        "limitations": result.get("limitations"),
    }


@router.post("/motion-analyses/{analysis_id}/feedback")
def submit_motion_feedback(
    analysis_id: int,
    body: MotionFeedbackIn,
    request: Request,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    run = _owned_run(db, request, user, analysis_id)
    if isinstance(run, JSONResponse):
        return run
    feedback = db.scalar(
        select(MotionAnalysisFeedback).where(MotionAnalysisFeedback.run_id == run.id)
    )
    result = feedback.result if feedback else {}
    # One feedback record per user per run; this is a quality signal, NOT a
    # training label and never rewrites measured facts.
    result["user_feedback"] = {
        "useful": body.useful,
        "label_correction": body.label_correction,
        "frame_id": body.frame_id,
        "comment": body.comment,
        "recorded_at": utc_now().isoformat() + "Z",
    }
    if feedback is None:
        feedback = MotionAnalysisFeedback(
            run_id=run.id, user_id=user.id, result_json=json.dumps(result, ensure_ascii=False)
        )
        db.add(feedback)
    else:
        feedback.result_json = json.dumps(result, ensure_ascii=False)
    db.commit()
    return {"ok": True, "analysis_id": run.id, "used_as_training_label": False}


@router.get("/motion-analyses/{analysis_id}/evidence")
def motion_analysis_evidence(
    analysis_id: int,
    request: Request,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    run = _owned_run(db, request, user, analysis_id)
    if isinstance(run, JSONResponse):
        return run
    result = get_unified_result(db, run) or {}
    keyframes = []
    for frame in result.get("keyframes") or []:
        keyframes.append(
            {
                "id": frame.get("id"),
                "t_ms": frame.get("t_ms"),
                "phase": frame.get("phase"),
                "finding": frame.get("finding"),
                "advice": frame.get("advice"),
                "evidence_type": frame.get("evidence_type"),
                "has_image": bool(frame.get("image_url")),
            }
        )
    meta = result.get("_meta") or {}
    return {
        "analysis_id": run.id,
        "recognition": result.get("recognition"),
        "score": result.get("score"),
        "keyframes": keyframes,
        "model_versions": meta.get("model_versions"),
        "worker_method": meta.get("worker_method"),
    }
