from __future__ import annotations
import json
from app.core.time import utc_now, utc_iso

from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from urllib.parse import urlparse, unquote

from app.api.deps import current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.media_security import UnsafeMediaURL, validate_media_source_url
from app.models import MediaAsset, AIJob
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


@router.post("/motion-jobs")
def create_motion_job(
    body: MotionIn, user=Depends(current_user), db: Session = Depends(get_db)
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
        raise HTTPException(400, "动作分析仅支持视频")

    job = create_ai_job(
        db,
        user_id=user.id,
        job_type="motion_pose",
        media_asset_id=asset.id,
        payload={"exercise_type": body.exercise_type},
    )

    return {"ok": True, **public_job(job)}


@router.get("/motion-jobs/{job_id}")
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


@router.post("/kinetics-jobs")
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


@router.get("/kinetics-jobs/{job_id}")
async def get_kinetics_job(
    job_id: int, user=Depends(current_user), db: Session = Depends(get_db)
):
    requeue_expired_jobs(db)
    job = db.get(AIJob, job_id)
    if not job or job.user_id != user.id or job.job_type != "kinetics400":
        raise HTTPException(404, "任务不存在")
    return public_job(job)


@router.post("/analyze-motion")
def analyze_motion_compat(
    body: MotionIn, user=Depends(current_user), db: Session = Depends(get_db)
):
    return create_motion_job(body, user, db)
