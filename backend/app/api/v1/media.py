from __future__ import annotations
import json
import re
import hashlib
from app.core.time import utc_now, utc_iso

from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from typing import Literal

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Request, Header
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from urllib.parse import urlparse, unquote
import time

from app.api.deps import bearer, current_user
from app.api.worker_deps import require_worker_token
from app.core.config import settings
from app.core.database import get_db
from app.core.media_security import UnsafeMediaURL, validate_media_source_url
from app.core.security import decode_subject
from app.models import (
    AIJob,
    MediaAsset,
    MobileMediaUploadSession,
    MotionAnalysisFeedback,
    MotionAnalysisRun,
    MotionEvidenceFrame,
    MotionUserFeedback,
    User,
)
from app.services.storage import get_storage
from app.services.storage import S3Storage, StorageError
from app.services.mobile_uploads import (
    UPLOAD_URL_TTL_SECONDS,
    MobileUploadValidationError,
    cleanup_after_expiry,
    mobile_upload_limit_bytes,
    schedule_mobile_upload_cleanup,
    verify_media_bytes,
)
from app.schemas.errors import ApiException
from app.services.ai_jobs import (
    create_ai_job,
    public_job,
    refresh_waiting_jobs,
    requeue_expired_jobs,
    json_loads,
)
from app.services.timeline import add_event
from app.services.result_summary import generate_result_summary
from app.services.motion import catalog, feedback_store
from app.services.motion.media_storage import (
    InvalidPreviewSignature,
    PreviewExpired,
    PreviewForbidden,
    PreviewNotFound,
)
from app.services.motion.orchestrator import (
    PIPELINE_VERSION,
    create_unified_run,
    get_unified_result,
    run_stage_view,
)

# V2 effective pipeline is server-selected (contract §1); clients cannot fake it.
V2_PIPELINE_VERSION = "motion-unified-v2"
ALLOWED_CLOUD_REVIEW_MODES = {"off", "skeleton", "redacted_frames"}

router = APIRouter(prefix="/media", tags=["media"])


def _require_mobile_upload_enabled() -> None:
    if not settings.mobile_upload_enabled:
        raise HTTPException(503, "Android 媒体上传尚未启用")
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
    purpose: Literal["food_analysis", "motion_analysis"] | None = None


class RefreshMediaIn(BaseModel):
    temp_url: str = Field(min_length=8, max_length=4000)
    expires_in: int = Field(default=7200, ge=300, le=86400)


class MobileUploadSessionIn(BaseModel):
    request_id: str = Field(min_length=12, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    original_name: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=3, max_length=120)
    media_type: str = Field(pattern="^(image|video)$")
    size_bytes: int = Field(ge=1)
    purpose: Literal["food_analysis", "motion_analysis"]


def _validate_mobile_upload(body: MobileUploadSessionIn, user_id: int):
    limit = mobile_upload_limit_bytes(body.purpose)
    if body.size_bytes > limit:
        raise HTTPException(413, "文件超过当前用途的服务端上传限制")
    suffix = Path(body.original_name).suffix.lower()
    expected = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
    }.get(suffix)
    if not expected or expected != body.content_type:
        raise HTTPException(415, "文件扩展名和 Content-Type 必须匹配")
    if body.media_type != ("video" if suffix in {".mp4", ".mov"} else "image"):
        raise HTTPException(400, "媒体类型与文件扩展名不一致")
    if (body.purpose == "food_analysis" and body.media_type != "image") or (
        body.purpose == "motion_analysis" and body.media_type != "video"
    ):
        raise HTTPException(400, "媒体类型与上传用途不匹配")
    safe_name = Path(body.original_name.replace("\\", "/")).name
    if safe_name in {"", ".", ".."} or any(ord(char) < 32 for char in safe_name):
        raise HTTPException(400, "文件名无效")
    return safe_name, suffix


@router.get("/mobile-upload/options")
def mobile_upload_options(user=Depends(current_user)):
    _require_mobile_upload_enabled()
    limits = {
        "food_analysis": mobile_upload_limit_bytes("food_analysis"),
        "motion_analysis": mobile_upload_limit_bytes("motion_analysis"),
    }
    return {
        "storage_backend": settings.effective_mobile_upload_backend,
        "max_upload_bytes": max(limits.values()),
        "max_upload_bytes_by_purpose": limits,
    }


@router.post("/mobile-upload/sessions")
def create_mobile_upload_session(
    body: MobileUploadSessionIn,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    _require_mobile_upload_enabled()
    if settings.effective_mobile_upload_backend != "s3":
        raise HTTPException(409, "当前媒体后端未启用 COS/S3 直传")
    original_name, suffix = _validate_mobile_upload(body, user.id)
    session = db.scalar(
        select(MobileMediaUploadSession)
        .where(
            MobileMediaUploadSession.user_id == user.id,
            MobileMediaUploadSession.request_id == body.request_id,
        )
        .with_for_update()
    )
    now = utc_now()
    if session:
        if (
            session.purpose != body.purpose
            or session.media_type != body.media_type
            or session.content_type != body.content_type
            or session.size_bytes != body.size_bytes
            or session.original_name != original_name
        ):
            raise HTTPException(409, "上传编号已用于其他文件，请重新选择文件")
        if session.status == "ready" and session.media_asset_id:
            return {
                "media_id": session.media_asset_id,
                "storage_backend": "s3",
                "already_completed": True,
            }
        if session.status == "verifying":
            raise HTTPException(409, "上传会话已结束，请重新选择文件")
        retry_terminal = session.status in {"cancelled", "rejected", "expired"} or (
            session.status == "pending" and session.expires_at <= now
        )
        asset = db.get(MediaAsset, session.media_asset_id) if session.media_asset_id else None
        if retry_terminal:
            # Rotate both object keys. Any old PUT URL remains confined to the
            # old staging key and is independently deleted after its expiry.
            if session.staging_key and session.cleanup_completed_at is None:
                schedule_mobile_upload_cleanup(db, session, reason="mobile_upload_retry_rotation")
            key_id = uuid4().hex
            if asset and asset.status != "ready":
                asset.storage_key = f"u{user.id}/{body.media_type}/{key_id}{suffix}"
                asset.original_name = original_name
                asset.media_type = body.media_type
                asset.content_type = body.content_type
                asset.size_bytes = body.size_bytes
                asset.status = "uploading"
            else:
                asset = MediaAsset(
                    user_id=user.id,
                    original_name=original_name,
                    storage_key=f"u{user.id}/{body.media_type}/{key_id}{suffix}",
                    storage_backend="s3",
                    media_type=body.media_type,
                    content_type=body.content_type,
                    size_bytes=body.size_bytes,
                    status="uploading",
                )
                db.add(asset)
                db.flush()
            session.media_asset_id = asset.id
            session.staging_key = f"staging/u{user.id}/{body.purpose}/{key_id}{suffix}"
            session.status = "pending"
            session.cleanup_completed_at = None
            session.cleanup_attempts = 0
            session.last_error_code = None
            session.expires_at = now + timedelta(seconds=UPLOAD_URL_TTL_SECONDS)
            session.cleanup_after = cleanup_after_expiry(session.expires_at)
        else:
            if session.status != "pending" or not asset or not session.staging_key:
                raise HTTPException(409, "上传会话状态无效，请重新选择文件")
            session.expires_at = now + timedelta(seconds=UPLOAD_URL_TTL_SECONDS)
            session.cleanup_after = cleanup_after_expiry(session.expires_at)
    else:
        key_id = uuid4().hex
        final_key = f"u{user.id}/{body.media_type}/{key_id}{suffix}"
        staging_key = f"staging/u{user.id}/{body.purpose}/{key_id}{suffix}"
        asset = MediaAsset(
            user_id=user.id,
            original_name=original_name,
            storage_key=final_key,
            storage_backend="s3",
            media_type=body.media_type,
            content_type=body.content_type,
            size_bytes=body.size_bytes,
            status="uploading",
        )
        db.add(asset)
        db.flush()
        expires_at = now + timedelta(seconds=UPLOAD_URL_TTL_SECONDS)
        session = MobileMediaUploadSession(
            user_id=user.id,
            media_asset_id=asset.id,
            request_id=body.request_id,
            staging_key=staging_key,
            purpose=body.purpose,
            original_name=original_name,
            media_type=body.media_type,
            content_type=body.content_type,
            size_bytes=body.size_bytes,
            status="pending",
            expires_at=expires_at,
            cleanup_after=cleanup_after_expiry(expires_at),
        )
        db.add(session)
    try:
        db.flush()
        upload_url = S3Storage().presigned_upload_url(
            session.staging_key,
            body.content_type,
            body.size_bytes,
            expires_in=UPLOAD_URL_TTL_SECONDS,
        )
    except IntegrityError:
        db.rollback()
        # Concurrent retries with the same request id converge on the winner's
        # durable session instead of creating duplicate media or a 500 response.
        existing = db.scalar(
            select(MobileMediaUploadSession).where(
                MobileMediaUploadSession.user_id == user.id,
                MobileMediaUploadSession.request_id == body.request_id,
            )
        )
        if existing:
            return create_mobile_upload_session(body, user, db)
        raise HTTPException(409, "上传请求冲突，请重新选择文件") from None
    except Exception:
        db.rollback()
        raise HTTPException(503, "COS/S3 上传授权暂时不可用，请检查服务端存储配置") from None
    db.commit()
    db.refresh(asset)
    return {
        "media_id": asset.id,
        "storage_backend": "s3",
        "upload_url": upload_url,
        "method": "PUT",
        "headers": {"Content-Type": body.content_type},
        "expires_in": UPLOAD_URL_TTL_SECONDS,
        "max_size_bytes": mobile_upload_limit_bytes(body.purpose),
        "already_completed": False,
    }


@router.post("/mobile-upload/sessions/{media_id}/complete")
def complete_mobile_upload_session(
    media_id: int,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    _require_mobile_upload_enabled()
    session = db.scalar(
        select(MobileMediaUploadSession)
        .where(MobileMediaUploadSession.media_asset_id == media_id)
        .with_for_update()
    )
    if not session or session.user_id != user.id:
        raise HTTPException(404, "上传任务不存在")
    asset = db.get(MediaAsset, media_id)
    if not asset or asset.user_id != user.id or asset.storage_backend != "s3":
        raise HTTPException(404, "上传任务不存在")
    if session.status == "ready" and asset.status == "ready":
        return {"ok": True, "media_id": asset.id, "storage_backend": "s3", "size": asset.size_bytes}
    if session.status != "pending" or asset.status != "uploading":
        raise HTTPException(409, "上传任务已结束，请重新选择文件")
    # Allow a delayed completion request after PUT expiry, while the cleanup
    # grace remains open. The old URL can no longer change the staging object
    # after expiry; the committed asset is still copied to a private final key.
    if session.cleanup_after <= utc_now():
        session.status = "expired"
        session.last_error_code = "UPLOAD_URL_EXPIRED"
        asset.status = "failed"
        schedule_mobile_upload_cleanup(db, session, reason="mobile_upload_expired")
        db.commit()
        raise HTTPException(409, "上传授权已过期，请重新选择文件")
    storage = S3Storage()
    session.status = "verifying"
    copied = False
    try:
        max_size = mobile_upload_limit_bytes(session.purpose)
        verify_media_bytes(
            storage,
            key=session.staging_key,
            expected_size=session.size_bytes,
            expected_content_type=session.content_type,
            max_size=max_size,
        )
        storage.copy_object(session.staging_key, asset.storage_key, session.content_type)
        copied = True
        verify_media_bytes(
            storage,
            key=asset.storage_key,
            expected_size=session.size_bytes,
            expected_content_type=session.content_type,
            max_size=max_size,
        )
    except MobileUploadValidationError as error:
        if error.status_code >= 500 or error.error_code == "UPLOAD_OBJECT_MISSING":
            session.status = "pending"
            db.commit()
            raise HTTPException(error.status_code, error.public_message) from None
        session.status = "rejected"
        session.last_error_code = error.error_code
        asset.status = "failed"
        schedule_mobile_upload_cleanup(db, session, reason="mobile_upload_rejected")
        if copied:
            from app.services.media_reconciliation import enqueue_deletion

            task = enqueue_deletion(
                db,
                user_id=user.id,
                media_asset_id=asset.id,
                storage_backend="s3",
                storage_key=asset.storage_key,
                reason="mobile_upload_rejected_final_object",
            )
            task.next_attempt_at = utc_now()
        db.commit()
        raise HTTPException(error.status_code, error.public_message) from None
    except Exception:
        # Leave the source session retryable. CopyObject is atomic; repeating it
        # is safe, and a later complete call verifies the committed destination.
        session.status = "pending"
        db.commit()
        raise HTTPException(503, "COS/S3 文件暂时无法提交，请稍后重试") from None
    session.status = "ready"
    session.ready_at = utc_now()
    session.last_error_code = None
    asset.status = "ready"
    schedule_mobile_upload_cleanup(db, session, reason="mobile_upload_staging_cleanup")
    refresh_waiting_jobs(db, asset.id)
    add_event(
        db,
        user.id,
        "media_uploaded",
        {"media_type": asset.media_type, "storage": "s3", "size_bytes": asset.size_bytes},
        source="android.upload",
        ref_type="media_asset",
        ref_id=asset.id,
    )
    db.commit()
    return {"ok": True, "media_id": asset.id, "storage_backend": "s3", "size": asset.size_bytes}


@router.delete("/mobile-upload/sessions/{media_id}")
def abort_mobile_upload_session(
    media_id: int,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    _require_mobile_upload_enabled()
    session = db.scalar(
        select(MobileMediaUploadSession)
        .where(MobileMediaUploadSession.media_asset_id == media_id)
        .with_for_update()
    )
    if not session or session.user_id != user.id:
        raise HTTPException(404, "上传任务不存在")
    asset = db.get(MediaAsset, media_id)
    if not asset or asset.user_id != user.id or asset.storage_backend != "s3":
        raise HTTPException(404, "上传任务不存在")
    if session.status == "ready" or asset.status == "ready":
        raise HTTPException(409, "文件已完成登记，不能作为未完成上传清理")
    if session.status not in {"pending", "cancelled"}:
        raise HTTPException(409, "上传任务已结束，无法取消")
    session.status = "cancelled"
    session.cleanup_after = cleanup_after_expiry(session.expires_at)
    schedule_mobile_upload_cleanup(db, session, reason="mobile_upload_cancelled")
    db.commit()
    return {"ok": True, "deleted": False, "cleanup_pending": True, "cleanup_after": utc_iso(session.cleanup_after)}


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
    # Mini Program sessions keep their legacy `u{id}` namespace. Android uses
    # CloudBase custom auth and the stable `hm_user_{id}` UID issued by /auth.
    allowed_prefixes = (
        f"healthmate/u{user_id}/{media_type}/",
        f"healthmate/hm_user_{user_id}/{media_type}/",
    )
    if not path.startswith(allowed_prefixes) or any(
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
    if body.purpose:
        if (body.purpose == "food_analysis" and body.media_type != "image") or (
            body.purpose == "motion_analysis" and body.media_type != "video"
        ):
            raise HTTPException(400, "媒体类型与上传用途不匹配")
        if body.size_bytes > mobile_upload_limit_bytes(body.purpose):
            raise HTTPException(413, "文件超过当前用途的服务端上传限制")
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


@router.get("/{media_id}/playback")
def media_playback(media_id: int, user=Depends(current_user), db: Session = Depends(get_db)):
    """Return a user-owned, short-lived playback URL for a source video.

    A task result may outlive the mini-program's temporary local path. The API
    therefore resolves playback from the authorized media asset and never
    exposes a worker download URL as a durable asset link.
    """
    asset = db.scalar(select(MediaAsset).where(MediaAsset.id == media_id))
    if asset is None or asset.user_id != user.id:
        raise ApiException(404, "MEDIA_NOT_FOUND", "视频素材不存在")
    if asset.media_type != "video" or asset.status not in {"ready", "completed"}:
        return {
            "state": "unavailable",
            "playable_url": None,
            "expires_at": None,
            "mime_type": asset.content_type or "video/mp4",
            "duration_ms": None,
            "reason": "MEDIA_NOT_PLAYABLE",
        }

    now = utc_now()
    if asset.storage_backend == "cloudbase":
        if not asset.source_url or not asset.source_url_expires_at or asset.source_url_expires_at <= now:
            return {
                "state": "unavailable",
                "playable_url": None,
                "expires_at": asset.source_url_expires_at.isoformat() + "Z" if asset.source_url_expires_at else None,
                "mime_type": asset.content_type or "video/mp4",
                "duration_ms": None,
                "reason": "SOURCE_URL_EXPIRED",
            }
        return {
            "state": "available",
            "playable_url": asset.source_url,
            "expires_at": asset.source_url_expires_at.isoformat() + "Z",
            "mime_type": asset.content_type or "video/mp4",
            "duration_ms": None,
        }

    try:
        storage = S3Storage() if asset.storage_backend == "s3" else get_storage()
        url = storage.public_url(asset.storage_key)
    except StorageError:
        return {
            "state": "unavailable",
            "playable_url": None,
            "expires_at": None,
            "mime_type": asset.content_type or "video/mp4",
            "duration_ms": None,
            "reason": "STORAGE_UNAVAILABLE",
        }
    if url.startswith("/"):
        url = settings.public_base_url.rstrip("/") + url
    # Local URLs are served by the app and do not carry a remote expiry.
    return {
        "state": "available",
        "playable_url": url,
        "expires_at": (now + timedelta(seconds=3600)).isoformat() + "Z"
        if asset.storage_backend == "s3"
        else None,
        "mime_type": asset.content_type or "video/mp4",
        "duration_ms": None,
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
    """弃用：请使用 POST /media/motion-analyses（统一任务）。

    **弃用**：本端点保留一个完整的小程序发布周期（计划移除日期 2026-12-31）。
    移除前先确认线上旧客户端调用量为 0；调用计数见 `GET /system/metrics` 的
    `deprecated_endpoint_calls`。
    """
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
    """弃用：请使用 GET /media/motion-analyses/{analysis_id}。

    **弃用**：本端点保留一个完整的小程序发布周期（计划移除日期 2026-12-31）。
    移除前先确认线上旧客户端调用量为 0；调用计数见 `GET /system/metrics` 的
    `deprecated_endpoint_calls`。
    """
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
    """弃用：Kinetics 只作为候选证据，不单独构成结论。

    **弃用**：本端点保留一个完整的小程序发布周期（计划移除日期 2026-12-31）。
    移除前先确认线上旧客户端调用量为 0；调用计数见 `GET /system/metrics` 的
    `deprecated_endpoint_calls`。
    """
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
    """弃用：请使用 GET /media/motion-analyses/{analysis_id}。

    **弃用**：本端点保留一个完整的小程序发布周期（计划移除日期 2026-12-31）。
    移除前先确认线上旧客户端调用量为 0；调用计数见 `GET /system/metrics` 的
    `deprecated_endpoint_calls`。
    """
    requeue_expired_jobs(db)
    job = db.get(AIJob, job_id)
    if not job or job.user_id != user.id or job.job_type != "kinetics400":
        raise HTTPException(404, "任务不存在")
    return public_job(job)


@router.post("/analyze-motion", deprecated=True)
def analyze_motion_compat(
    body: MotionIn, user=Depends(current_user), db: Session = Depends(get_db)
):
    """弃用兼容入口：转交统一 motion-analyses 任务。

    **弃用**：本端点保留一个完整的小程序发布周期（计划移除日期 2026-12-31）。
    移除前先确认线上旧客户端调用量为 0；调用计数见 `GET /system/metrics` 的
    `deprecated_endpoint_calls`。
    """
    return create_motion_job(body, user, db)


# --------------------------------------------------------------------------- #
# P0-B unified motion chain (spec 5). Errors follow the unified contract:
# {code, message, request_id, retryable, details?}.
# --------------------------------------------------------------------------- #

_EXERCISE_RE = r"^(auto|[a-z][a-z0-9_]{0,40})$"


class MotionAnalysesIn(BaseModel):
    media_id: int
    requested_exercise: str = Field(default="auto", pattern=_EXERCISE_RE)
    # V2 contract §2: user choices + consent scope + expected schema. The server
    # selects the effective pipeline; clients cannot fake an algorithm version.
    exercise_hint: str | None = Field(default=None, max_length=60)
    cloud_review_mode: str = Field(default="redacted_frames", max_length=20)
    consent_version: str = Field(default="motion-real-frames-v2", max_length=60)
    response_schema: str = Field(default="motion-analysis-v2", max_length=60)
    # Legacy V1 fields kept for backward compatibility with old clients/tests.
    consent_deepseek_frames: bool | None = None
    pipeline_version: str | None = Field(default=None, max_length=60)
    analysis_revision: bool = False

    @model_validator(mode="after")
    def _check_review_mode(self):
        if self.cloud_review_mode not in ALLOWED_CLOUD_REVIEW_MODES:
            raise ValueError("cloud_review_mode 必须是 off/skeleton/redacted_frames")
        return self


class ReanalyzeIn(BaseModel):
    """POST /reanalyze: re-run a completed/partial/unknown task as a child run.

    Reuses the media asset + already-completed side-effect-free stages; always
    issues a fresh Idempotency-Key and a parent->child link. Changing the cloud
    review mode produces a genuinely new task (never reuses the old cloud mode).
    """

    cloud_review_mode: str | None = Field(default=None, max_length=20)
    # A *catalogue id* (validated server-side), never free text: it becomes the
    # child run's requested_type (spec §7.4).
    exercise_hint: str | None = Field(default=None, max_length=60)
    reason: str = Field(default="user_request", max_length=60)
    # The mobile client records its correction through /confirm-label first.
    # This prevents a second feedback row when the child run is created.
    correction_confirmed: bool = False


class ConfirmLabelIn(BaseModel):
    """POST /confirm-label: a REAL catalog id OR a human Chinese description.

    R13: a user must supply an actual category; the old ``user_confirmed``
    placeholder id is rejected. The label is a STRING (the frontend once sent a
    boolean ``true``). Source is marked user_selected and never auto-upgrades to a
    model recognition success or a reliable score.
    """

    canonical_id: str | None = Field(default=None, max_length=60)
    novel_label_zh: str | None = Field(default=None, max_length=40)
    # Legacy compat: old clients sent label_id.
    label_id: str | None = Field(default=None, max_length=60)
    correction_reason: str = Field(default="", max_length=300)


class MotionFeedbackIn(BaseModel):
    """POST /feedback: {kind, frame_id?, corrected_label?, comment?}.

    kind ∈ useful/wrong_label/wrong_frame/unhelpful_advice. frame_id, when given,
    must belong to this run. Writes motion_user_feedback (independent of the
    result snapshot) so a re-analysis never loses the correction.
    """

    kind: str | None = Field(default=None, max_length=30)
    frame_id: str | None = Field(default=None, max_length=80)
    corrected_label: str | None = Field(default=None, max_length=120)
    comment: str | None = Field(default=None, max_length=2000)
    # Legacy V1 shape kept for old clients/tests (R13 boolean -> closed kind).
    useful: bool | None = None
    label_correction: str | None = Field(default=None, max_length=120)


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


def _is_admin(user) -> bool:
    """Read-only admin gate for /admin diagnostics (contract §6).

    No role column exists on users; admins are an explicit openid allowlist in
    settings. Empty allowlist => nobody is admin => the endpoint always refuses.
    """
    openids = {s.strip() for s in (settings.motion_admin_openids or "").split(",") if s.strip()}
    return bool(getattr(user, "openid", None) and user.openid in openids)


def _resolve_consent(body: MotionAnalysesIn) -> tuple[bool, str]:
    """Map V2 cloud_review_mode -> the V1 bool consent flag used by the run/worker.

    Legacy ``consent_deepseek_frames`` (old clients) wins when explicitly sent;
    otherwise consent follows the review mode (off => no frames to the cloud).
    """
    mode = body.cloud_review_mode or "redacted_frames"
    if body.consent_deepseek_frames is not None:
        return bool(body.consent_deepseek_frames), mode
    return mode != "off", mode


def _strip_internal(result: dict | None) -> dict | None:
    """Remove internal bookkeeping (_meta/trace) from the USER-facing result.

    Raw candidates, reason codes, worker method and trace live only behind
    /admin/.../diagnostics (contract §3).
    """
    if not isinstance(result, dict):
        return result
    return {k: v for k, v in result.items() if k not in {"_meta"}}


def build_media_storage(db: Session):
    """Use the same preview store for upload, evidence and review reads."""
    from app.services.motion.media_storage import configured_media_storage

    return configured_media_storage(db)


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

    consent, cloud_mode = _resolve_consent(body)
    if idempotency_key:
        key = idempotency_key
    else:
        # Repeat clicks / retries without an explicit key still dedupe on the
        # same (user, asset, params) fingerprint. The cloud review mode is part of
        # the fingerprint: a new mode never reuses an old task's fingerprint.
        import hashlib

        material = (
            f"{user.id}:{asset.id}:{body.requested_exercise}:"
            f"{int(consent)}:{cloud_mode}:{body.consent_version}:{body.response_schema}"
        )
        key = "auto-" + hashlib.sha256(material.encode()).hexdigest()

    pipeline_version = body.pipeline_version or PIPELINE_VERSION
    run, created, conflict = create_unified_run(
        db,
        user_id=user.id,
        asset=asset,
        requested_exercise=body.requested_exercise,
        consent_deepseek_frames=consent,
        pipeline_version=pipeline_version,
        idempotency_key=key,
        analysis_revision=body.analysis_revision,
    )
    if conflict:
        return _motion_error(
            request, 409, "IDEMPOTENCY_PARAM_MISMATCH", conflict,
            details={"dedupe_key": run.dedupe_key},
        )
    if created:
        # V2 columns (contract §7): server-selected effective pipeline, request
        # fingerprint, result version, cloud review mode. Only set on a NEW run so
        # an idempotent retry never mutates the original task.
        import hashlib as _hashlib

        run.cloud_review_mode = cloud_mode
        run.effective_pipeline_version = V2_PIPELINE_VERSION
        run.result_version = 1
        run.request_fingerprint = "fp-" + _hashlib.sha256(
            f"{user.id}:{asset.id}:{body.requested_exercise}:{cloud_mode}:"
            f"{body.consent_version}:{body.response_schema}".encode()
        ).hexdigest()[:112]
        db.commit()
    return JSONResponse(
        status_code=202,
        content={
            "analysis_id": run.id,
            "status": run.status,
            "poll_after_ms": 3000,
            "created": created,
            "cloud_review_mode": run.cloud_review_mode,
            "effective_pipeline_version": run.effective_pipeline_version,
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
    # Contract §3: internal bookkeeping (_meta) is stripped from the user view;
    # it is only exposed via /admin/.../diagnostics.
    view["result"] = _strip_internal(result)
    return view


_ACTIVE_RUN_STATUSES = {"queued", "decoding", "local_inference", "processing"}


def _spawn_child_run(
    db: Session,
    *,
    user,
    run: MotionAnalysisRun,
    cloud_mode: str | None,
    exercise_hint: str | None = None,
    idempotency_key: str | None = None,
    correction_confirmed: bool = False,
) -> MotionAnalysisRun:
    """Create a child run that reuses the media asset + side-effect-free stages.

    Reuses the caller's Idempotency-Key across retries and creates a parent->child
    link; the new cloud review mode never reuses the parent's fingerprint. The
    actual stage reuse (re-running only the affected stages) is performed by the
    E-package stage queue (compare-and-set by run_id/stage/version).

    ``exercise_hint`` is the user's corrected label (spec §7.4). It must be a
    validated catalogue id, because it becomes the child's ``requested_type``;
    free text must never be written into the request model.
    """
    requested = run.requested_type
    parent_run_id = run.id
    if exercise_hint:
        requested = _validated_catalog_id(exercise_hint)
    asset = db.get(MediaAsset, run.media_asset_id)
    mode = cloud_mode or run.cloud_review_mode or "redacted_frames"
    consent = mode != "off"
    effective_key = (
        f"reanalyze:{parent_run_id}:{idempotency_key}"
        if idempotency_key
        else f"reanalyze:{parent_run_id}:{uuid4().hex}"
    )
    try:
        child, created, conflict = create_unified_run(
            db,
            user_id=user.id,
            asset=asset,
            requested_exercise=requested,
            consent_deepseek_frames=consent,
            pipeline_version=run.pipeline_version,
            idempotency_key=effective_key,
            parent_run_id=parent_run_id,
        )
    except IntegrityError:
        # Two identical retries may pass the initial lookup at once. The unique
        # dedupe key elects one creator; after the losing insert rolls back, load
        # and return the winner's child instead of exposing a transient 500.
        db.rollback()
        run = db.get(MotionAnalysisRun, parent_run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="原动作任务不存在")
        asset = db.get(MediaAsset, run.media_asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="原视频文件不存在")
        child, created, conflict = create_unified_run(
            db,
            user_id=user.id,
            asset=asset,
            requested_exercise=requested,
            consent_deepseek_frames=consent,
            pipeline_version=run.pipeline_version,
            idempotency_key=effective_key,
            parent_run_id=parent_run_id,
        )
    if conflict:
        raise HTTPException(status_code=409, detail=conflict)
    if created:
        child.cloud_review_mode = mode
        child.effective_pipeline_version = V2_PIPELINE_VERSION
        child.result_version = 1
        db.commit()
    if exercise_hint and not correction_confirmed:
        feedback_key = hashlib.sha256(
            f"reanalyze-feedback:{parent_run_id}:{effective_key}".encode()
        ).hexdigest()
        try:
            _record_feedback(
                db,
                user_id=user.id,
                run_id=parent_run_id,
                kind="wrong_label",
                corrected_label=requested,
                comment="reanalyze",
                idempotency_key=feedback_key,
            )
        except feedback_store.FeedbackIdempotencyConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return child


def _record_feedback(
    db: Session,
    *,
    user_id: int,
    run_id: int,
    kind: str,
    corrected_label: str | None = None,
    comment: str | None = None,
    idempotency_key: str | None = None,
) -> None:
    """Append to the independent feedback ledger (never rewrite the run result)."""
    run = db.get(MotionAnalysisRun, run_id)
    feedback_store.record_feedback(
        db,
        run=run,
        user_id=user_id,
        kind=kind,
        corrected_label=(corrected_label or None),
        comment=(comment or None)[:500] if comment else None,
        idempotency_key=idempotency_key,
    )
    db.commit()


def _validated_catalog_id(raw: str) -> str:
    """Validate a user-supplied exercise hint against the frozen catalogue."""
    candidate = (raw or "").strip()
    if (
        not candidate
        or candidate == "user_confirmed"
        or not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", candidate)
        or catalog.get_action(candidate) is None
    ):
        raise ApiException(
            422,
            "UNKNOWN_CATALOG_ID",
            "动作类别不在当前目录中，请重新选择或在页面内说明",
        )
    return candidate


@router.post("/motion-analyses/{analysis_id}/retry")
def retry_motion_analysis(
    analysis_id: int,
    body: ReanalyzeIn,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=120),
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    """Compat shim: failed-task retry delegates to the V2 reanalyze child-run."""
    run = _owned_run(db, request, user, analysis_id)
    if isinstance(run, JSONResponse):
        return run
    if run.status == "failed":
        child = _spawn_child_run(
            db,
            user=user,
            run=run,
            cloud_mode=body.cloud_review_mode,
            exercise_hint=body.exercise_hint,
            idempotency_key=idempotency_key,
            correction_confirmed=body.correction_confirmed,
        )
        return {"analysis_id": child.id, "parent_run_id": run.id, "status": child.status}
    return _motion_error(
        request, 409, "RETRY_NOT_ALLOWED",
        "仅终态 failed 的任务可走 /retry；如需重分析已完成任务请用 /reanalyze",
    )


@router.post("/motion-analyses/{analysis_id}/reanalyze")
def reanalyze_motion_analysis(
    analysis_id: int,
    body: ReanalyzeIn,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=120),
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    """Re-run a completed/partial/unknown (not only failed) task as a child run.

    The user may change the cloud review mode / exercise hint; a new mode creates
    a correct new task and never reuses the old task's cloud mode. Already-running
    tasks cannot be re-analysed (avoid duplicate billed work).
    """
    run = _owned_run(db, request, user, analysis_id)
    if isinstance(run, JSONResponse):
        return run
    if run.status in _ACTIVE_RUN_STATUSES:
        return _motion_error(
            request, 409, "REANALYZE_IN_PROGRESS",
            "任务仍在进行中，请等待完成后再重新分析",
        )
    child = _spawn_child_run(
        db,
        user=user,
        run=run,
        cloud_mode=body.cloud_review_mode,
        exercise_hint=body.exercise_hint,
        idempotency_key=idempotency_key,
        correction_confirmed=body.correction_confirmed,
    )
    return {
        "analysis_id": child.id,
        "parent_run_id": run.id,
        "status": child.status,
        "cloud_review_mode": child.cloud_review_mode,
    }


@router.post("/motion-analyses/{analysis_id}/confirm-label")
def confirm_motion_label(
    analysis_id: int,
    body: ConfirmLabelIn,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=120),
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    run = _owned_run(db, request, user, analysis_id)
    if isinstance(run, JSONResponse):
        return run
    canonical = (body.canonical_id or body.label_id or "").strip()
    novel = (body.novel_label_zh or "").strip()
    # R13: a real choice is required. The old ``user_confirmed`` placeholder and
    # the boolean-true payload are rejected.
    if not canonical and not novel:
        return _motion_error(
            request, 422, "NO_LABEL_SELECTED",
            "请选择真实动作类别，或填写动作描述（未选类别不得提交确认）",
        )
    if canonical:
        if canonical == "user_confirmed" or not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", canonical):
            return _motion_error(request, 422, "INVALID_LABEL_ID", "动作类别 ID 不合法")
        if catalog.get_action(canonical) is None:
            return _motion_error(request, 422, "UNKNOWN_CATALOG_ID", "动作类别不在当前目录中")
    # The correction is stored as an INDEPENDENT feedback row (spec §7.4). The
    # computed result snapshot is never rewritten: ``source=user_selected`` can
    # therefore never be mistaken for a model recognition success or a score.
    try:
        row = feedback_store.record_feedback(
            db,
            run=run,
            user_id=user.id,
            kind="wrong_label" if canonical else "unhelpful_advice",
            corrected_label=canonical or novel or None,
            comment=(body.correction_reason or "user_label_correction")[:500],
            idempotency_key=idempotency_key,
        )
    except feedback_store.FeedbackIdempotencyConflict as exc:
        return _motion_error(request, 409, "IDEMPOTENCY_CONFLICT", str(exc))
    db.commit()
    return {
        "ok": True,
        "analysis_id": run.id,
        "feedback_id": row.id,
        "canonical_id": canonical or None,
        "novel_label_zh": novel or None,
        "source": "user_selected",
        "score_fabricated": False,
    }


@router.get("/motion-analyses/{analysis_id}/trace")
def motion_analysis_trace(
    analysis_id: int,
    request: Request,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    """User-safe lightweight progress read (V2 keys; no internal debug codes)."""
    run = _owned_run(db, request, user, analysis_id)
    if isinstance(run, JSONResponse):
        return run
    result = get_unified_result(db, run) or {}
    rec = result.get("recognition") or {}
    return {
        "analysis_id": run.id,
        "status": run.status,
        "recognition_state": rec.get("state"),
        "display_name": rec.get("display_name"),
        "canonical_id": rec.get("canonical_id"),
        "review_status": rec.get("review_status"),
        "summary": result.get("summary"),
        "capabilities": result.get("capabilities"),
        "notices": result.get("notices"),
    }


@router.post("/motion-analyses/{analysis_id}/feedback")
def submit_motion_feedback(
    analysis_id: int,
    body: MotionFeedbackIn,
    request: Request,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    """Write a durable feedback row (motion_user_feedback), independent of the
    result snapshot, so a re-analysis never overwrites the correction."""
    run = _owned_run(db, request, user, analysis_id)
    if isinstance(run, JSONResponse):
        return run
    try:
        kind = feedback_store.coerce_legacy_feedback(
            kind=body.kind,
            useful=body.useful,
            label_correction=body.label_correction,
        )
        corrected_label = body.corrected_label or body.label_correction
        row = feedback_store.record_feedback(
            db,
            run=run,
            user_id=user.id,
            kind=kind,
            frame_id=body.frame_id,
            corrected_label=corrected_label,
            comment=body.comment,
        )
    except feedback_store.FeedbackValidationError as exc:
        return _motion_error(
            request, 422, exc.code, exc.message, retryable=exc.retryable
        )
    db.commit()
    return {
        "ok": True,
        "analysis_id": run.id,
        "feedback_id": row.id,
        "used_as_training_label": False,
    }


def _optional_bearer_user(credentials, db: Session):
    """Resolve the Bearer user when a token is present; None otherwise.

    Used by the preview image path, which must ALSO accept unsigned/query-signed
    requests (mini-program <image> sends no Authorization header).
    """
    if not credentials:
        return None
    sub = decode_subject(credentials.credentials)
    if not sub:
        return None
    try:
        uid = int(sub)
    except (TypeError, ValueError):
        return None
    return db.get(User, uid)


@router.get("/motion-analyses/{analysis_id}/previews/{frame_id}")
async def motion_preview(
    analysis_id: int,
    frame_id: str,
    request: Request,
    db: Session = Depends(get_db),
    credentials=Depends(bearer),
):
    """Return one of THIS run's preview frames.

    Two paths:
      * query-signed (mini-program <image>, no Authorization): a valid read
        signature bound to (run_id, frame_id) returns the raw image/jpeg bytes;
      * Bearer (authenticated API client): returns the short-lived access
        descriptor JSON, with strict _owned_run ownership.

    A missing OR invalid signature falls through to the Bearer path, so another
    user's frame never resolves.
    """
    params = dict(request.query_params)
    storage = build_media_storage(db)

    # --- signed read path: return raw JPEG bytes ----------------------------- #
    if "sig" in params:
        try:
            storage.validate_read_signature(
                params, run_id=analysis_id, frame_id=frame_id
            )
        except InvalidPreviewSignature:
            # fall through to the Bearer path (which will 401/404 as needed).
            pass
        else:
            ev = db.scalar(
                select(MotionEvidenceFrame).where(
                    MotionEvidenceFrame.run_id == analysis_id,
                    MotionEvidenceFrame.frame_id == frame_id,
                )
            )
            if ev is None or not ev.preview_asset_id:
                return _motion_error(request, 404, "FRAME_NOT_FOUND", "该帧不存在")
            try:
                data = storage.read_preview_bytes(ev.preview_asset_id, run_id=analysis_id)
            except PreviewNotFound:
                return _motion_error(request, 404, "PREVIEW_GONE", "预览已被清理")
            return Response(content=data, media_type="image/jpeg")

    # --- Bearer descriptor path --------------------------------------------- #
    user = _optional_bearer_user(credentials, db)
    if user is None:
        return _motion_error(request, 401, "NOT_AUTHORIZED", "缺少鉴权")
    run = _owned_run(db, request, user, analysis_id)
    if isinstance(run, JSONResponse):
        return run
    ev = db.scalar(
        select(MotionEvidenceFrame).where(
            MotionEvidenceFrame.run_id == run.id,
            MotionEvidenceFrame.frame_id == frame_id,
        )
    )
    if ev is None or not ev.preview_asset_id:
        return _motion_error(request, 404, "FRAME_NOT_FOUND", "该帧不属于本次分析")
    if ev.expires_at and ev.expires_at <= utc_now():
        return {
            "status": "expired",
            "analysis_id": run.id,
            "frame_id": frame_id,
            "expires_at": ev.expires_at.isoformat() + "Z",
        }
    try:
        # Production MediaStorage exposes read_preview_bytes, not get_preview.
        # Ownership was already enforced by _owned_run above, so the descriptor
        # is built from the stored evidence row (never leaks another user's).
        storage.read_preview_bytes(ev.preview_asset_id, run_id=run.id)
    except PreviewNotFound:
        return _motion_error(request, 404, "PREVIEW_GONE", "预览已被清理")
    return {
        "status": "available",
        "analysis_id": run.id,
        "frame_id": frame_id,
        "preview_asset_id": ev.preview_asset_id,
        "timestamp_ms": int(ev.timestamp_ms or 0),
        "expires_at": ev.expires_at.isoformat() + "Z" if ev.expires_at else None,
    }


@router.put("/previews/{asset_id}")
async def put_preview(
    asset_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Worker uploads a raw JPEG preview bytes under a server-minted signed URL.

    The upload signature (query) binds user_id/run_id/asset_id/expiry; an invalid
    or expired signature, or one bound to a different asset, is rejected (403).
    """
    params = dict(request.query_params)
    storage = build_media_storage(db)
    try:
        validated = storage.validate_upload_signature(params)
    except InvalidPreviewSignature:
        return _motion_error(request, 403, "INVALID_UPLOAD_SIGNATURE", "上传签名无效或已过期")
    if validated["asset_id"] != asset_id:
        return _motion_error(request, 403, "INVALID_UPLOAD_SIGNATURE", "上传签名与画面不匹配")
    body = await request.body()
    storage.save_preview(
        asset_id, body, user_id=validated["user_id"], run_id=validated["run_id"]
    )
    db.commit()
    return {"asset_id": asset_id, "size": len(body)}


@router.get("/motion-analyses/{analysis_id}/evidence")
def motion_analysis_evidence(
    analysis_id: int,
    request: Request,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    """V2 evidence read: timeline frames (no internal debug fields)."""
    run = _owned_run(db, request, user, analysis_id)
    if isinstance(run, JSONResponse):
        return run
    result = get_unified_result(db, run) or {}
    timeline = result.get("timeline") or {}
    # Mint short-lived signed read URLs the mini-program <image> can load without
    # an Authorization header (query-signature path). build_read_url is B's frozen
    # contract; the API prefix is joined here so the mini-program gets an absolute
    # path it can append to its configured base host.
    storage = build_media_storage(db)
    expiry_ts = int(time.time()) + 3600
    # <image> needs an absolute URL: join the request origin to the signed path.
    origin = str(request.base_url).rstrip("/")
    frames = []
    for frame in timeline.get("frames") or []:
        if not isinstance(frame, dict):
            continue
        frame_id = frame.get("id")
        preview_url = None
        preview_state = "unavailable"
        unavailable_reason = "missing_evidence_row"
        ev = None
        if frame_id:
            ev = db.scalar(
                select(MotionEvidenceFrame).where(
                    MotionEvidenceFrame.run_id == run.id,
                    MotionEvidenceFrame.frame_id == str(frame_id),
                )
            )
        if ev is not None and ev.preview_asset_id:
            expires_at = ev.expires_at
            if expires_at is not None and expires_at <= utc_now():
                preview_state = "expired"
                unavailable_reason = "expired"
            else:
                try:
                    # The result JSON is not an asset receipt. Verify the real
                    # object before minting a URL, otherwise a stale worker id
                    # becomes a broken image loop in the mini-program.
                    storage.read_preview_bytes(ev.preview_asset_id, run_id=run.id)
                except PreviewNotFound:
                    unavailable_reason = "object_missing"
                except Exception:  # pragma: no cover - storage provider boundary
                    unavailable_reason = "storage_unavailable"
                else:
                    preview_state = "available"
                    unavailable_reason = None
        if preview_state == "available" and frame_id:
            try:
                preview_url = origin + storage.build_read_url(
                    run.id, frame_id, user_id=run.user_id, expiry_ts=expiry_ts
                )
            except Exception:  # pragma: no cover - storage not wired / B pending
                preview_url = None
                preview_state = "unavailable"
                unavailable_reason = "signing_unavailable"
        frames.append(
            {
                "id": frame_id,
                "timestamp_ms": frame.get("timestamp_ms"),
                "phase": frame.get("phase"),
                "observation": frame.get("observation"),
                "explanation": frame.get("explanation"),
                "next_step": frame.get("next_step"),
                "advice_kind": frame.get("advice_kind"),
                "preview": {
                    "state": preview_state,
                    "url": preview_url,
                    "expires_at": ev.expires_at.isoformat() + "Z" if ev and ev.expires_at else None,
                    "sha256": frame.get("preview_sha256"),
                    "unavailable_reason": unavailable_reason,
                },
                # Legacy fields remain, but are now derived from verified bytes.
                "has_image": preview_state == "available",
                "preview_url": preview_url,
                "preview_state": preview_state,
                "unavailable_reason": unavailable_reason,
            }
        )
    return {
        "analysis_id": run.id,
        "recognition": result.get("recognition"),
        "capabilities": result.get("capabilities"),
        "metrics": result.get("metrics"),
        "frames": frames,
        "notices": result.get("notices"),
    }


# --------------------------------------------------------------------------- #
# Admin-only diagnostics (contract §6). Normal users get a 403; these fields
# (raw candidates, reason codes, versions, trace) never appear on user pages.
# --------------------------------------------------------------------------- #
admin_router = APIRouter(prefix="/admin", tags=["motion-admin"])


@admin_router.get("/motion-analyses/{analysis_id}/diagnostics")
def motion_diagnostics(
    analysis_id: int,
    request: Request,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    if not _is_admin(user):
        return _motion_error(request, 403, "ADMIN_ONLY", "仅管理员可查看动作分析诊断")
    run = db.get(MotionAnalysisRun, analysis_id)
    if run is None:
        return _motion_error(request, 404, "MOTION_ANALYSIS_NOT_FOUND", "动作分析任务不存在")
    result = get_unified_result(db, run) or {}
    meta = result.get("_meta") or {}
    return {
        "analysis_id": run.id,
        "user_id": run.user_id,
        "status": run.status,
        "error_code": run.error_code,
        "pipeline_version": run.pipeline_version,
        "effective_pipeline_version": run.effective_pipeline_version,
        "result_version": run.result_version,
        "cloud_review_mode": run.cloud_review_mode,
        "request_fingerprint": run.request_fingerprint,
        "parent_run_id": run.parent_run_id,
        "recognition": result.get("recognition"),
        "metrics": result.get("metrics"),
        "reason_code": meta.get("reason_code"),
        "candidates": meta.get("kinetics_candidates"),
        "sources": meta.get("sources"),
        "stages": meta.get("stages"),
        "model_versions": meta.get("model_versions"),
        "worker_method": meta.get("worker_method"),
    }


# --------------------------------------------------------------------------- #
# Worker preview upload-URL minting (preview byte chain). The local GPU worker
# asks the API for short-lived signed PUT URLs per frame, then uploads raw JPEG
# bytes directly under that signature. Worker-token auth mirrors /worker/jobs.
# --------------------------------------------------------------------------- #
worker_preview_router = APIRouter(
    prefix="/worker/jobs",
    tags=["motion-worker-preview"],
    dependencies=[Depends(require_worker_token)],
)


class PreviewUploadUrlsIn(BaseModel):
    frame_ids: list[str] = Field(default_factory=list, max_length=8)
    asset_prefix: str = Field(default="preview", max_length=40)


@worker_preview_router.post("/{job_id}/preview-upload-urls")
def preview_upload_urls(
    job_id: int,
    body: PreviewUploadUrlsIn,
    request: Request,
    db: Session = Depends(get_db),
):
    job = db.get(AIJob, job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    run = db.scalar(
        select(MotionAnalysisRun).where(MotionAnalysisRun.ai_job_id == job.id)
    )
    if run is None:
        raise HTTPException(404, "未找到关联动作分析任务")
    storage = build_media_storage(db)
    expiry_ts = int(time.time()) + 300
    uploads = []
    for frame_id in body.frame_ids:
        asset_id = f"{body.asset_prefix}_{frame_id}"
        upload_url = storage.mint_upload_url(
            asset_id,
            user_id=run.user_id,
            run_id=run.id,
            asset_prefix=body.asset_prefix,
            expiry_ts=expiry_ts,
        )
        uploads.append(
            {
                "frame_id": frame_id,
                "asset_id": asset_id,
                "upload_url": upload_url,
                "expires_at": expiry_ts,
            }
        )
    # ``uploads`` is the frozen field (spec §7.2). ``urls`` is a one-release
    # compatibility alias for workers built against the old response; it is
    # removed after a full mini-program/worker release cycle with usage at zero.
    return {"uploads": uploads, "urls": uploads}
