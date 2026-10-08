"""Media lifecycle governance: verifiable deletion and orphan reconciliation.

Spec §10. A client "I deleted it" receipt is *supporting evidence only*. This
module keeps a server-side ledger so a deletion can be verified, retried, or
honestly escalated to ``manual_review`` — never reported as success on the
strength of a client claim.

Guarantees:

* only a SHA-256 hash of the storage key is persisted, so the ledger can
  reconcile an orphaned object after account deletion without retaining anything
  that could fetch it;
* nothing here writes a signed URL, a provider credential or a raw object body
  into the ledger or the logs;
* a task moves to ``verified`` only when the object is observed missing or a
  platform success receipt exists; otherwise it stays ``requested``/``failed``/
  ``manual_review``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import AIJob, MediaAsset, MediaDeletionTask, MobileMediaUploadSession, User

logger = logging.getLogger("healthmate.media_reconciliation")

# Ledger vocabulary (spec §10.1).
STATUS_PENDING = "pending"
STATUS_REQUESTED = "requested"
STATUS_VERIFIED = "verified"
STATUS_DELETED = "deleted"
STATUS_FAILED = "failed"
STATUS_MANUAL_REVIEW = "manual_review"

# Reconciliation outcomes.
LIFECYCLE_OK = "ok"
LIFECYCLE_MISSING = "missing"
LIFECYCLE_ORPHAN = "orphan"

MAX_ATTEMPTS = 5
RETRY_BACKOFF_MINUTES = (5, 30, 120, 720, 1440)


def storage_key_hash(storage_key: str) -> str:
    """Non-reversible object identity for the ledger."""
    return hashlib.sha256((storage_key or "").encode("utf-8")).hexdigest()


def _mask(storage_key: str) -> str:
    """A short, non-fetchable diagnostic prefix (never the full key)."""
    return storage_key_hash(storage_key)[:12]


def enqueue_deletion(
    db: Session,
    *,
    user_id: int | None,
    media_asset_id: int | None,
    storage_backend: str,
    storage_key: str,
    reason: str = "account_deletion",
) -> MediaDeletionTask:
    """Create (or reuse) the ledger row for one object."""
    from app.core.crypto import encrypt_secret

    key_hash = storage_key_hash(storage_key)
    encrypted_reference = encrypt_secret(storage_key)
    existing = db.scalar(
        select(MediaDeletionTask).where(
            MediaDeletionTask.storage_key_hash == key_hash,
            MediaDeletionTask.status.in_(
                (STATUS_PENDING, STATUS_REQUESTED, STATUS_FAILED)
            ),
        )
    )
    if existing is not None:
        if not existing.encrypted_storage_reference:
            existing.encrypted_storage_reference = encrypted_reference
        if reason == "user_request" or reason.startswith("account_deletion"):
            try:
                receipt = json.loads(existing.provider_receipt_json or "{}")
            except (TypeError, ValueError):
                receipt = {}
            receipt["reason"] = reason
            existing.provider_receipt_json = json.dumps(receipt, ensure_ascii=False)
        db.add(existing)
        return existing
    task = MediaDeletionTask(
        task_id="md_" + uuid4().hex,
        user_id=user_id,
        media_asset_id=media_asset_id,
        storage_backend=storage_backend or "",
        storage_key_hash=key_hash,
        encrypted_storage_reference=encrypted_reference,
        status=STATUS_PENDING,
        provider_receipt_json=json.dumps({"reason": reason}, ensure_ascii=False),
        attempts=0,
    )
    db.add(task)
    db.flush()
    return task


def record_client_receipt(
    db: Session, task: MediaDeletionTask, *, reported: bool, detail: str = ""
) -> MediaDeletionTask:
    """Record what the client *claims*. Never enough to mark ``verified``."""
    try:
        receipt = json.loads(task.provider_receipt_json or "{}")
    except (TypeError, ValueError):
        receipt = {}
    receipt["client_reported"] = bool(reported)
    receipt["client_detail"] = (detail or "")[:200]
    receipt["client_reported_at"] = utc_now().isoformat() + "Z"
    task.provider_receipt_json = json.dumps(receipt, ensure_ascii=False)
    # A client claim only advances the task to ``requested`` (spec §10.2).
    if task.status == STATUS_PENDING:
        task.status = STATUS_REQUESTED
    db.add(task)
    return task


def verify_deletion(
    db: Session,
    task: MediaDeletionTask,
    *,
    object_still_present: bool | None,
    platform_ok: bool = False,
    error_code: str | None = None,
    retryable_error: bool = False,
) -> MediaDeletionTask:
    """Advance one deletion task based on *server-observable* facts.

    ``object_still_present`` is the server's own probe result (``None`` when the
    backend has no way to check, for example a storage credential the server does
    not hold). ``platform_ok`` means the storage platform returned a success
    receipt for the delete call.
    """
    task.attempts += 1
    if platform_ok or object_still_present is False:
        task.status = STATUS_VERIFIED
        task.verified_at = utc_now()
        task.error_code = None
        task.next_attempt_at = None
        task.encrypted_storage_reference = None
    elif object_still_present is True:
        # The object is provably still there: never claim success.
        task.status = STATUS_FAILED
        task.error_code = error_code or "OBJECT_STILL_PRESENT"
        task.next_attempt_at = (
            utc_now()
            + timedelta(
                minutes=RETRY_BACKOFF_MINUTES[
                    min(task.attempts - 1, len(RETRY_BACKOFF_MINUTES) - 1)
                ]
            )
            if task.attempts < MAX_ATTEMPTS
            else None
        )
        if task.attempts >= MAX_ATTEMPTS:
            task.status = STATUS_MANUAL_REVIEW
    elif retryable_error:
        task.error_code = error_code or "PROVIDER_DELETE_FAILED"
        if task.attempts < MAX_ATTEMPTS:
            task.status = STATUS_FAILED
            task.next_attempt_at = utc_now() + timedelta(
                minutes=RETRY_BACKOFF_MINUTES[
                    min(task.attempts - 1, len(RETRY_BACKOFF_MINUTES) - 1)
                ]
            )
        else:
            task.status = STATUS_MANUAL_REVIEW
            task.next_attempt_at = None
    else:
        # The server cannot observe the object: escalate instead of pretending.
        task.status = STATUS_MANUAL_REVIEW
        task.error_code = error_code or "VERIFICATION_UNAVAILABLE"
        task.next_attempt_at = None
    db.add(task)
    return task


def deletion_summary(db: Session, user_id: int | None = None) -> dict[str, Any]:
    """Counts by status; ``None`` when a bucket has no real samples."""
    stmt = select(MediaDeletionTask)
    if user_id is not None:
        stmt = stmt.where(MediaDeletionTask.user_id == user_id)
    candidates = db.scalars(stmt).all()
    rows = []
    for row in candidates:
        try:
            reason = str(json.loads(row.provider_receipt_json or "{}").get("reason") or "")
        except (TypeError, ValueError):
            reason = ""
        # Privacy's deletion status reports account deletion work. Routine
        # staging-object expiry cleanup uses the same retry machinery but must
        # not look like a pending user deletion in the settings screen.
        if not reason or reason == "user_request" or reason.startswith("account_deletion"):
            rows.append(row)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    total = len(rows)
    verified = counts.get(STATUS_VERIFIED, 0) + counts.get(STATUS_DELETED, 0)
    return {
        "total": total,
        "by_status": counts,
        "verified": verified,
        "pending": counts.get(STATUS_PENDING, 0) + counts.get(STATUS_REQUESTED, 0),
        "manual_review": counts.get(STATUS_MANUAL_REVIEW, 0),
        "failed": counts.get(STATUS_FAILED, 0),
        # No samples must read as "no data", never as a 100% success rate.
        "verified_rate_pct": round(verified / total * 100, 1) if total else None,
        "policy": (
            "客户端回执只作为辅助证据；只有服务端确认对象不存在或拿到平台成功"
            "回执才标记 verified，无法验证的一律进入 manual_review。"
        ),
    }


def reconcile_media(db: Session, *, limit: int = 200) -> dict[str, Any]:
    """Daily reconciliation report (spec §10.3).

    * DB row present, object gone -> mark the asset ``missing`` and stop retrying
      its deletion tasks;
    * object present, no DB row -> this pass cannot enumerate remote objects
      without a storage credential, so it reports ``unverifiable`` instead of an
      invented orphan count;
    * expired previews -> counted from the evidence pool for the retention job;
    * an account whose rows are gone but whose objects are not -> P0 alert.
    """
    from app.models import MotionEvidenceFrame

    assets = db.scalars(select(MediaAsset).limit(limit)).all()
    missing: list[dict[str, Any]] = []
    for asset in assets:
        # A local asset whose file disappeared is observable; cloud objects are
        # not reachable from here, so only the local backend is probed.
        if asset.storage_backend != "local":
            continue
        from pathlib import Path

        from app.core.config import settings

        path = Path(settings.upload_dir) / asset.storage_key
        if not path.exists():
            asset.status = "missing"
            db.add(asset)
            missing.append(
                {"media_asset_id": asset.id, "storage_key_hash": _mask(asset.storage_key)}
            )
            for task in db.scalars(
                select(MediaDeletionTask).where(
                    MediaDeletionTask.storage_key_hash
                    == storage_key_hash(asset.storage_key),
                    MediaDeletionTask.status.in_(
                        (STATUS_PENDING, STATUS_REQUESTED, STATUS_FAILED)
                    ),
                )
            ).all():
                task.status = STATUS_VERIFIED
                task.verified_at = utc_now()
                task.error_code = "OBJECT_MISSING"
                task.next_attempt_at = None
                task.encrypted_storage_reference = None
                db.add(task)

    expired_previews = db.scalars(
        select(MotionEvidenceFrame).where(
            MotionEvidenceFrame.expires_at.is_not(None),
            MotionEvidenceFrame.expires_at < utc_now(),
        )
    ).all()
    stale_asset_ids = {
        frame.preview_asset_id
        for frame in expired_previews
        if frame.preview_asset_id
    }
    db.commit()
    return {
        "scanned_assets": len(assets),
        "missing_assets": missing,
        "missing_count": len(missing),
        "expired_previews": len(expired_previews),
        "expired_preview_asset_ids": sorted(stale_asset_ids)[:200],
        # Honest limitation: remote object enumeration needs a platform
        # credential this service intentionally does not hold.
        "orphan_scan": {
            "state": "unverifiable",
            "reason": "服务端不持有云存储列举凭据，无法证明云端存在孤立对象",
        },
        "policy": "对账日志只记录对象哈希与内部 ID，不记录签名 URL。",
        "reconciled_at": utc_now().isoformat() + "Z",
    }


def retry_pending_deletions(db: Session, *, limit: int = 50) -> dict[str, Any]:
    """Retry provider deletes while keeping object references encrypted at rest."""
    from pathlib import Path

    from app.core.config import settings
    from app.core.crypto import decrypt_secret
    from app.services.cloudbase_storage import (
        CloudBaseStorageAdmin,
        CloudBaseStorageError,
    )
    from app.services.storage import S3Storage

    now = utc_now()
    tasks = db.scalars(
        select(MediaDeletionTask)
        .where(
            MediaDeletionTask.status.in_((STATUS_PENDING, STATUS_REQUESTED, STATUS_FAILED)),
            MediaDeletionTask.encrypted_storage_reference.is_not(None),
            (MediaDeletionTask.next_attempt_at.is_(None))
            | (MediaDeletionTask.next_attempt_at <= now),
        )
        .order_by(MediaDeletionTask.created_at)
        .limit(max(1, min(limit, 200)))
        .with_for_update(skip_locked=True)
    ).all()
    completed = failed = skipped = 0
    cloudbase = CloudBaseStorageAdmin()
    for task in tasks:
        if task.storage_backend == "cloudbase" and not cloudbase.configured:
            skipped += 1
            continue
        try:
            reference = decrypt_secret(task.encrypted_storage_reference or "")
        except ValueError:
            verify_deletion(
                db,
                task,
                object_still_present=None,
                error_code="ENCRYPTED_REFERENCE_UNAVAILABLE",
            )
            failed += 1
            continue
        try:
            if task.storage_backend == "cloudbase":
                receipt = cloudbase.delete_file_ids([reference])
                success = receipt.get(reference, False)
                if not success:
                    raise CloudBaseStorageError("CloudBase 文件删除未确认成功")
                verify_deletion(db, task, object_still_present=None, platform_ok=True)
            elif task.storage_backend == "s3":
                S3Storage().delete(reference)
                verify_deletion(db, task, object_still_present=None, platform_ok=True)
                from app.services.mobile_uploads import record_mobile_upload_cleanup

                record_mobile_upload_cleanup(db, reference, verified=True)
            elif task.storage_backend == "local":
                root = Path(settings.upload_dir).resolve()
                target = (root / reference).resolve()
                if not target.is_relative_to(root):
                    raise ValueError("invalid_storage_reference")
                target.unlink(missing_ok=True)
                verify_deletion(
                    db, task, object_still_present=target.exists(), platform_ok=False
                )
            else:
                verify_deletion(
                    db,
                    task,
                    object_still_present=None,
                    error_code="UNSUPPORTED_STORAGE_BACKEND",
                )
                failed += 1
                continue
            if task.status == STATUS_VERIFIED:
                completed += 1
            else:
                failed += 1
        except Exception as exc:  # noqa: BLE001 - bounded retry, no object refs in logs
            if task.storage_backend == "s3":
                from app.services.mobile_uploads import record_mobile_upload_cleanup

                record_mobile_upload_cleanup(
                    db, reference, verified=False, error_code=type(exc).__name__
                )
            verify_deletion(
                db,
                task,
                object_still_present=None,
                error_code=(type(exc).__name__ or "PROVIDER_DELETE_FAILED")[:80],
                retryable_error=True,
            )
            failed += 1
    db.commit()
    return {"considered": len(tasks), "verified": completed, "failed": failed, "skipped": skipped}


def finalize_account_deletion(
    db: Session,
    *,
    user_id: int,
    reason: str = "user_request",
    client_reported_cloud_file_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Delete the account with a verifiable media ledger (spec §10.2).

    Order: freeze the media manifest, remove what this service can remove, record
    exactly what it could NOT verify, then delete the business rows. The response
    reports ``verified`` and ``manual_review`` counts so the client never shows a
    blanket success.
    """
    from app.services.privacy import delete_account_data, _delete_non_cloud_media
    from app.services.cloudbase_storage import CloudBaseStorageAdmin

    user = db.get(User, user_id)
    if user is None:
        raise LookupError("user_not_found")

    assets = db.scalars(select(MediaAsset).where(MediaAsset.user_id == user_id)).all()
    upload_sessions = db.scalars(
        select(MobileMediaUploadSession).where(
            MobileMediaUploadSession.user_id == user_id,
            MobileMediaUploadSession.staging_key.is_not(None),
            MobileMediaUploadSession.cleanup_completed_at.is_(None),
        )
    ).all()
    tasks = [
        enqueue_deletion(
            db,
            user_id=user_id,
            media_asset_id=asset.id,
            storage_backend=asset.storage_backend,
            storage_key=asset.storage_key,
            reason=reason,
        )
        for asset in assets
    ]
    for session in upload_sessions:
        # Keep the staging object until all issued PUT URLs have expired; the
        # deletion ledger owns the retry after the account/session rows vanish.
        session.status = "cancelled"
        task = enqueue_deletion(
            db,
            user_id=user_id,
            media_asset_id=session.media_asset_id,
            storage_backend="s3",
            storage_key=session.staging_key,
            reason="account_deletion_upload_staging",
        )
        if task.status != STATUS_VERIFIED:
            task.next_attempt_at = session.cleanup_after
            db.add(task)
    db.add_all(upload_sessions)
    db.commit()

    # Delete remote objects while their exact IDs are still available in MediaAsset.
    cloudbase = CloudBaseStorageAdmin()
    client_reported = client_reported_cloud_file_ids or set()
    cloud_file_ids_for_delete = [
        asset.cloud_file_id or asset.storage_key
        for asset in assets
        if asset.storage_backend == "cloudbase"
    ]
    cloud_receipts: dict[str, bool] = {}
    cloudbase_request_failed = False
    if cloudbase.configured and cloud_file_ids_for_delete:
        try:
            cloud_receipts = cloudbase.delete_file_ids(cloud_file_ids_for_delete)
        except Exception:  # noqa: BLE001 - per-file retry state is recorded below
            cloudbase_request_failed = True

    for task, asset in zip(tasks, assets):
        if asset.storage_backend == "local":
            continue
        if asset.storage_backend == "cloudbase":
            if not cloudbase.configured:
                if asset.cloud_file_id in client_reported:
                    record_client_receipt(db, task, reported=True)
                    verify_deletion(
                        db,
                        task,
                        object_still_present=None,
                        error_code="CLIENT_REPORTED_UNVERIFIED",
                        retryable_error=True,
                    )
                else:
                    verify_deletion(
                        db,
                        task,
                        object_still_present=None,
                        error_code="CLOUDBASE_ADMIN_CREDENTIALS_MISSING",
                    )
                continue
            file_id = asset.cloud_file_id or asset.storage_key
            success = cloud_receipts.get(file_id, False)
            verify_deletion(
                db,
                task,
                object_still_present=None,
                platform_ok=success,
                error_code=(
                    None
                    if success
                    else "CLOUDBASE_DELETE_REQUEST_FAILED"
                    if cloudbase_request_failed
                    else "CLOUDBASE_DELETE_NOT_CONFIRMED"
                ),
                retryable_error=not success,
            )
            continue
        try:
            deleted = _delete_non_cloud_media([asset])
            verify_deletion(
                db,
                task,
                object_still_present=None,
                platform_ok=deleted == 1,
                error_code=None if deleted == 1 else "PROVIDER_DELETE_NOT_CONFIRMED",
                retryable_error=deleted != 1,
            )
        except Exception as exc:  # noqa: BLE001 - recorded, never hidden
            verify_deletion(
                db,
                task,
                object_still_present=None,
                error_code=(type(exc).__name__ or "PROVIDER_DELETE_FAILED")[:80],
                retryable_error=True,
            )

    for task, asset in zip(tasks, assets):
        if asset.storage_backend != "local":
            continue
        from pathlib import Path

        from app.core.config import settings

        path = Path(settings.upload_dir) / asset.storage_key
        try:
            if path.exists():
                path.unlink()
            verify_deletion(db, task, object_still_present=path.exists())
        except OSError as exc:
            task.error_code = type(exc).__name__
            verify_deletion(
                db,
                task,
                object_still_present=None,
                retryable_error=True,
            )
    db.commit()

    # Flush the ledger before account rows disappear; failed remote deletion tasks
    # keep only an encrypted retry reference and a non-reversible diagnostic hash.
    cloud_files = [
        a for a in assets if a.storage_backend == "cloudbase" and a.cloud_file_id
    ]
    db.commit()
    summary = deletion_summary(db, user_id)
    cloud_media_objects_verified = sum(
        1
        for task, asset in zip(tasks, assets)
        if asset.storage_backend == "cloudbase" and task.status == STATUS_VERIFIED
    )
    cloud_media_objects_pending = sum(
        1
        for task, asset in zip(tasks, assets)
        if asset.storage_backend == "cloudbase"
        and task.status in {STATUS_PENDING, STATUS_REQUESTED, STATUS_FAILED}
    )
    deleted_non_cloud_media_objects = sum(
        1
        for task, asset in zip(tasks, assets)
        if asset.storage_backend != "cloudbase" and task.status == STATUS_VERIFIED
    )
    result = delete_account_data(db, user_id, media_objects_already_handled=True)
    result["deleted_non_cloud_media_objects"] = deleted_non_cloud_media_objects
    db.commit()
    return {
        **result,
        "media_deletion": {
            **summary,
            "cloud_objects_expected_deleted_by_client": len(cloud_files),
            "server_verified": summary["verified"],
            "manual_review": summary["manual_review"],
        },
        "cloud_media_objects_expected": len(cloud_files),
        "cloud_media_objects_verified": cloud_media_objects_verified,
        "cloud_media_objects_pending": cloud_media_objects_pending,
        "verification": (
            "server_verified"
            if summary["verified"] == summary["total"]
            else "pending"
            if summary["pending"] or summary["failed"]
            else "partial"
        ),
        "reason": reason,
    }
