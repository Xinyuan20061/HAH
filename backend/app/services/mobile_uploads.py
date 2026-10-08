"""Validation and cleanup for short-lived Android media upload sessions."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now
from app.models import MediaAsset, MediaDeletionTask, MobileMediaUploadSession

UPLOAD_URL_TTL_SECONDS = 300
UPLOAD_CLEANUP_GRACE_SECONDS = 120
UPLOAD_VERIFICATION_PREFIX_BYTES = 32
MOTION_VIDEO_MAX_BYTES = 120 * 1024 * 1024


class MobileUploadValidationError(ValueError):
    def __init__(self, status_code: int, error_code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.public_message = message


def mobile_upload_limit_bytes(purpose: str) -> int:
    if purpose == "food_analysis":
        return settings.food_image_max_bytes
    if purpose == "motion_analysis":
        return min(MOTION_VIDEO_MAX_BYTES, settings.max_upload_mb * 1024 * 1024)
    raise ValueError("unsupported_mobile_upload_purpose")


def cleanup_after_expiry(expires_at):
    # A still-valid URL is a bearer capability. Deletion before its expiry would
    # let the holder recreate the staging object after a successful cleanup.
    return expires_at + timedelta(seconds=UPLOAD_CLEANUP_GRACE_SECONDS)


def schedule_mobile_upload_cleanup(
    db: Session,
    session: MobileMediaUploadSession,
    *,
    reason: str,
    not_before=None,
) -> MediaDeletionTask | None:
    if not session.staging_key:
        return None
    from app.services.media_reconciliation import enqueue_deletion

    task = enqueue_deletion(
        db,
        user_id=session.user_id,
        media_asset_id=session.media_asset_id,
        storage_backend="s3",
        storage_key=session.staging_key,
        reason=reason,
    )
    due_at = not_before or session.cleanup_after
    if task.status != "verified":
        task.next_attempt_at = due_at
        db.add(task)
    return task


def record_mobile_upload_cleanup(
    db: Session,
    storage_key: str,
    *,
    verified: bool,
    error_code: str | None = None,
) -> None:
    session = db.scalar(
        select(MobileMediaUploadSession).where(
            MobileMediaUploadSession.staging_key == storage_key
        )
    )
    if not session:
        return
    session.cleanup_attempts += 1
    session.last_error_code = (
        None if verified else (error_code or "STAGING_DELETE_FAILED")[:80]
    )
    if verified:
        session.cleanup_completed_at = utc_now()
        session.staging_key = None
        # A terminal upload that never became a media asset has no user-visible
        # value after its only outstanding PUT capability is gone.
        if session.status != "ready" and session.media_asset_id:
            asset = db.get(MediaAsset, session.media_asset_id)
            if asset and asset.status != "ready":
                db.delete(asset)
            session.media_asset_id = None
    db.add(session)


def reconcile_expired_mobile_uploads(
    db: Session, *, limit: int = 100
) -> dict[str, int]:
    """Schedule deletions only after every signed PUT URL has expired."""
    now = utc_now()
    sessions = db.scalars(
        select(MobileMediaUploadSession)
        .where(
            MobileMediaUploadSession.staging_key.is_not(None),
            MobileMediaUploadSession.cleanup_completed_at.is_(None),
            MobileMediaUploadSession.cleanup_after <= now,
            MobileMediaUploadSession.status.in_(
                ("pending", "cancelled", "rejected", "expired", "ready")
            ),
        )
        .order_by(MobileMediaUploadSession.cleanup_after)
        .limit(max(1, min(limit, 500)))
        .with_for_update(skip_locked=True)
    ).all()
    scheduled = 0
    for session in sessions:
        if session.status == "pending":
            session.status = "expired"
            session.last_error_code = "UPLOAD_URL_EXPIRED"
            if session.media_asset_id:
                asset = db.get(MediaAsset, session.media_asset_id)
                if asset and asset.status != "ready":
                    asset.status = "failed"
                    db.add(asset)
        schedule_mobile_upload_cleanup(db, session, reason="mobile_upload_expired")
        scheduled += 1
    db.commit()
    return {"considered": len(sessions), "scheduled": scheduled}


def verify_media_bytes(
    storage,
    *,
    key: str,
    expected_size: int,
    expected_content_type: str,
    max_size: int,
) -> dict:
    try:
        metadata = storage.object_metadata(key)
    except Exception as error:  # provider SDK response is intentionally not exposed
        response = getattr(error, "response", {}) or {}
        provider_code = str((response.get("Error") or {}).get("Code") or "")
        if provider_code in {"404", "NoSuchKey", "NotFound"}:
            raise MobileUploadValidationError(
                409, "UPLOAD_OBJECT_MISSING", "COS/S3 尚未收到完整文件，请检查上传结果"
            ) from None
        raise MobileUploadValidationError(
            503, "UPLOAD_OBJECT_UNAVAILABLE", "暂时无法核验 COS/S3 文件，请稍后重试"
        ) from None

    actual_size = int(metadata.get("ContentLength") or 0)
    actual_type = (
        str(metadata.get("ContentType") or "").split(";", 1)[0].strip().lower()
    )
    if actual_size != expected_size or actual_size > max_size:
        raise MobileUploadValidationError(
            413, "UPLOAD_SIZE_MISMATCH", "上传文件大小与用途限制不符"
        )
    if actual_type != expected_content_type.lower():
        raise MobileUploadValidationError(
            415, "UPLOAD_TYPE_MISMATCH", "上传文件类型与授权信息不符"
        )

    try:
        prefix = storage.object_prefix(key, UPLOAD_VERIFICATION_PREFIX_BYTES)
    except Exception:
        raise MobileUploadValidationError(
            503, "UPLOAD_CONTENT_UNAVAILABLE", "暂时无法核验文件内容，请稍后重试"
        ) from None
    if not _content_signature_matches(expected_content_type.lower(), prefix):
        raise MobileUploadValidationError(
            415, "UPLOAD_CONTENT_MISMATCH", "文件内容与声明的图片或视频格式不符"
        )
    return metadata


def _content_signature_matches(content_type: str, prefix: bytes) -> bool:
    if content_type == "image/jpeg":
        return prefix.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return prefix.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/webp":
        return len(prefix) >= 12 and prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP"
    if content_type in {"video/mp4", "video/quicktime"}:
        return len(prefix) >= 8 and prefix[4:8] == b"ftyp"
    return False
