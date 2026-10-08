import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session
from uuid import uuid4

from app.models import MediaAsset, MediaDeletionTask, MobileMediaUploadSession
import app.api.v1.media as media_api


class FakeS3Storage:
    metadata = {"ContentLength": 12, "ContentType": "image/jpeg"}
    deleted = []
    copied = []
    copy_failures = 0

    def presigned_upload_url(self, key, content_type, size_bytes, expires_in=300):
        self.key = key
        self.content_type = content_type
        self.size_bytes = size_bytes
        self.expires_in = expires_in
        return "https://cos.example.test/presigned-put"

    def object_metadata(self, key):
        return self.metadata

    def object_prefix(self, key, byte_count=32):
        return b"\xff\xd8\xff\xe0" + b"x" * max(0, byte_count - 4)

    def copy_object(self, source_key, destination_key, content_type):
        self.copied.append((source_key, destination_key, content_type))
        if type(self).copy_failures:
            type(self).copy_failures -= 1
            raise RuntimeError("simulated transient copy error")

    def delete(self, key):
        self.deleted.append(key)


def payload(request_id="android-upload-0001", **overrides):
    return {
        "request_id": request_id,
        "original_name": "meal.jpg",
        "content_type": "image/jpeg",
        "media_type": "image",
        "size_bytes": 12,
        "purpose": "food_analysis",
        **overrides,
    }


def test_android_upload_routes_are_disabled_until_profile_opts_in(api, monkeypatch):
    monkeypatch.setattr(media_api.settings, "mobile_upload_enabled", False)

    options = api.get("/api/v1/media/mobile-upload/options")
    session = api.post(
        "/api/v1/media/mobile-upload/sessions", json=payload()
    )
    complete = api.post("/api/v1/media/mobile-upload/sessions/1/complete")
    cancel = api.delete("/api/v1/media/mobile-upload/sessions/1")

    assert options.status_code == 503
    assert session.status_code == 503
    assert complete.status_code == 503
    assert cancel.status_code == 503


def test_mobile_s3_upload_session_is_user_scoped_and_verified(
    api, monkeypatch, migrated_engine
):
    monkeypatch.setattr(media_api.settings, "storage_backend", "cloud_ref")
    monkeypatch.setattr(media_api.settings, "mobile_upload_backend", "s3")
    monkeypatch.setattr(media_api, "S3Storage", FakeS3Storage)
    FakeS3Storage.metadata = {"ContentLength": 12, "ContentType": "image/jpeg"}
    FakeS3Storage.deleted.clear()
    FakeS3Storage.copied.clear()
    FakeS3Storage.copy_failures = 0

    options = api.get("/api/v1/media/mobile-upload/options")
    assert options.status_code == 200
    assert options.json()["storage_backend"] == "s3"

    created = api.post("/api/v1/media/mobile-upload/sessions", json=payload())
    assert created.status_code == 200, created.text
    session = created.json()
    assert session["storage_backend"] == "s3"
    assert session["upload_url"] == "https://cos.example.test/presigned-put"
    assert session["headers"] == {"Content-Type": "image/jpeg"}
    assert session["expires_in"] == 300

    with Session(migrated_engine) as db:
        asset = db.scalar(
            select(MediaAsset).where(MediaAsset.id == session["media_id"])
        )
        upload_session = db.scalar(
            select(MobileMediaUploadSession).where(
                MobileMediaUploadSession.media_asset_id == session["media_id"]
            )
        )
        assert asset.user_id == api.user_id
        assert asset.storage_key.startswith(f"u{api.user_id}/image/")
        assert asset.storage_key != f"u{api.user_id}/image/android-upload-0001.jpg"
        assert asset.status == "uploading"
        assert upload_session.status == "pending"
        assert upload_session.staging_key.startswith(
            f"staging/u{api.user_id}/food_analysis/"
        )
        assert upload_session.staging_key != asset.storage_key

    completed = api.post(
        f"/api/v1/media/mobile-upload/sessions/{session['media_id']}/complete"
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["ok"] is True
    assert FakeS3Storage.copied == [
        (upload_session.staging_key, asset.storage_key, "image/jpeg")
    ]
    repeated = api.post("/api/v1/media/mobile-upload/sessions", json=payload())
    assert repeated.status_code == 200
    assert repeated.json()["already_completed"] is True


def test_mobile_s3_upload_refuses_incorrect_size_and_defers_stage_cleanup(
    api, monkeypatch, migrated_engine
):
    monkeypatch.setattr(media_api.settings, "storage_backend", "cloud_ref")
    monkeypatch.setattr(media_api.settings, "mobile_upload_backend", "s3")
    monkeypatch.setattr(media_api, "S3Storage", FakeS3Storage)
    FakeS3Storage.metadata = {"ContentLength": 13, "ContentType": "image/jpeg"}
    FakeS3Storage.deleted.clear()

    created = api.post(
        "/api/v1/media/mobile-upload/sessions",
        json=payload(request_id="android-upload-0002"),
    )
    assert created.status_code == 200
    response = api.post(
        f"/api/v1/media/mobile-upload/sessions/{created.json()['media_id']}/complete"
    )
    assert response.status_code == 413
    error = response.json()
    message = error.get("detail") or (error.get("error") or {}).get("message", "")
    assert "大小" in message
    assert FakeS3Storage.deleted == []
    with Session(migrated_engine) as db:
        upload_session = db.scalar(
            select(MobileMediaUploadSession).where(
                MobileMediaUploadSession.media_asset_id == created.json()["media_id"]
            )
        )
        task = db.scalar(
            select(MediaDeletionTask)
            .where(MediaDeletionTask.storage_key_hash.is_not(None))
            .order_by(MediaDeletionTask.id.desc())
        )
        assert upload_session.status == "rejected"
        assert task.next_attempt_at == upload_session.cleanup_after


def test_mobile_upload_requires_matching_extension_and_content_type(api, monkeypatch):
    monkeypatch.setattr(media_api.settings, "storage_backend", "cloud_ref")
    monkeypatch.setattr(media_api.settings, "mobile_upload_backend", "s3")
    response = api.post(
        "/api/v1/media/mobile-upload/sessions",
        json=payload(request_id="android-upload-0003", content_type="image/png"),
    )
    assert response.status_code == 415


def test_mobile_upload_abort_deletes_only_pending_user_asset(
    api, monkeypatch, migrated_engine
):
    monkeypatch.setattr(media_api.settings, "storage_backend", "cloud_ref")
    monkeypatch.setattr(media_api.settings, "mobile_upload_backend", "s3")
    monkeypatch.setattr(media_api, "S3Storage", FakeS3Storage)
    FakeS3Storage.metadata = {"ContentLength": 12, "ContentType": "image/jpeg"}
    FakeS3Storage.deleted.clear()
    created = api.post(
        "/api/v1/media/mobile-upload/sessions",
        json=payload(request_id="android-upload-0004"),
    )
    media_id = created.json()["media_id"]
    response = api.delete(f"/api/v1/media/mobile-upload/sessions/{media_id}")
    assert response.status_code == 200
    assert response.json()["deleted"] is False
    assert response.json()["cleanup_pending"] is True
    with Session(migrated_engine) as db:
        asset = db.get(MediaAsset, media_id)
        session = db.scalar(
            select(MobileMediaUploadSession).where(
                MobileMediaUploadSession.media_asset_id == media_id
            )
        )
        assert asset.status == "uploading"
        assert session.status == "cancelled"
    assert FakeS3Storage.deleted == []


def test_mobile_upload_rejects_spoofed_image_content_before_copy(
    api, monkeypatch, migrated_engine
):
    monkeypatch.setattr(media_api.settings, "storage_backend", "cloud_ref")
    monkeypatch.setattr(media_api.settings, "mobile_upload_backend", "s3")
    monkeypatch.setattr(media_api, "S3Storage", FakeS3Storage)
    FakeS3Storage.metadata = {"ContentLength": 12, "ContentType": "image/jpeg"}
    FakeS3Storage.copied.clear()
    monkeypatch.setattr(
        FakeS3Storage, "object_prefix", lambda self, key, byte_count=32: b"not-jpeg"
    )
    created = api.post(
        "/api/v1/media/mobile-upload/sessions",
        json=payload(request_id="android-upload-0005"),
    )
    response = api.post(
        f"/api/v1/media/mobile-upload/sessions/{created.json()['media_id']}/complete"
    )
    assert response.status_code == 415
    assert FakeS3Storage.copied == []
    with Session(migrated_engine) as db:
        session = db.scalar(
            select(MobileMediaUploadSession).where(
                MobileMediaUploadSession.media_asset_id == created.json()["media_id"]
            )
        )
        assert session.status == "rejected"


def test_mobile_upload_retries_rotate_to_new_staging_key(
    api, monkeypatch, migrated_engine
):
    monkeypatch.setattr(media_api.settings, "storage_backend", "cloud_ref")
    monkeypatch.setattr(media_api.settings, "mobile_upload_backend", "s3")
    monkeypatch.setattr(media_api, "S3Storage", FakeS3Storage)
    FakeS3Storage.metadata = {"ContentLength": 12, "ContentType": "image/jpeg"}
    created = api.post(
        "/api/v1/media/mobile-upload/sessions",
        json=payload(request_id="android-upload-0006"),
    )
    media_id = created.json()["media_id"]
    with Session(migrated_engine) as db:
        session = db.scalar(
            select(MobileMediaUploadSession).where(
                MobileMediaUploadSession.media_asset_id == media_id
            )
        )
        previous_staging_key = session.staging_key
        session.status = "cancelled"
        db.commit()
    retried = api.post(
        "/api/v1/media/mobile-upload/sessions",
        json=payload(request_id="android-upload-0006"),
    )
    assert retried.status_code == 200
    assert retried.json()["media_id"] == media_id
    with Session(migrated_engine) as db:
        session = db.scalar(
            select(MobileMediaUploadSession).where(
                MobileMediaUploadSession.media_asset_id == media_id
            )
        )
        assert session.status == "pending"
        assert session.staging_key != previous_staging_key


def test_mobile_upload_purpose_limits_and_cross_user_access(
    api, monkeypatch, migrated_engine
):
    from app.core.config import settings
    from app.core.security import create_access_token
    from app.models import User

    monkeypatch.setattr(media_api.settings, "storage_backend", "cloud_ref")
    monkeypatch.setattr(media_api.settings, "mobile_upload_backend", "s3")
    too_large = api.post(
        "/api/v1/media/mobile-upload/sessions",
        json=payload(
            request_id="android-upload-0010",
            size_bytes=settings.food_image_max_bytes + 1,
        ),
    )
    assert too_large.status_code == 413
    wrong_purpose = api.post(
        "/api/v1/media/mobile-upload/sessions",
        json=payload(
            request_id="android-upload-0011",
            original_name="exercise.mp4",
            content_type="video/mp4",
            media_type="video",
        ),
    )
    assert wrong_purpose.status_code == 400

    monkeypatch.setattr(media_api, "S3Storage", FakeS3Storage)
    created = api.post(
        "/api/v1/media/mobile-upload/sessions",
        json=payload(request_id="android-upload-0012"),
    )
    with Session(migrated_engine) as db:
        other = User(openid="upload-other-" + str(uuid4()))
        db.add(other)
        db.commit()
        other_id = other.id
    other_headers = {"Authorization": f"Bearer {create_access_token(str(other_id))}"}
    media_id = created.json()["media_id"]
    complete = api.post(
        f"/api/v1/media/mobile-upload/sessions/{media_id}/complete",
        headers=other_headers,
    )
    cancel = api.delete(
        f"/api/v1/media/mobile-upload/sessions/{media_id}",
        headers=other_headers,
    )
    assert complete.status_code == 404
    assert cancel.status_code == 404


def test_mobile_upload_copy_failure_is_retryable_and_commits_one_asset(
    api, monkeypatch, migrated_engine
):
    monkeypatch.setattr(media_api.settings, "storage_backend", "cloud_ref")
    monkeypatch.setattr(media_api.settings, "mobile_upload_backend", "s3")
    monkeypatch.setattr(media_api, "S3Storage", FakeS3Storage)
    FakeS3Storage.metadata = {"ContentLength": 12, "ContentType": "image/jpeg"}
    FakeS3Storage.copied.clear()
    FakeS3Storage.copy_failures = 1
    created = api.post(
        "/api/v1/media/mobile-upload/sessions",
        json=payload(request_id="android-upload-0007"),
    )
    media_id = created.json()["media_id"]
    first = api.post(f"/api/v1/media/mobile-upload/sessions/{media_id}/complete")
    assert first.status_code == 503
    with Session(migrated_engine) as db:
        session = db.scalar(
            select(MobileMediaUploadSession).where(
                MobileMediaUploadSession.media_asset_id == media_id
            )
        )
        assert session.status == "pending"
    second = api.post(f"/api/v1/media/mobile-upload/sessions/{media_id}/complete")
    assert second.status_code == 200, second.text
    repeated = api.post(f"/api/v1/media/mobile-upload/sessions/{media_id}/complete")
    assert repeated.status_code == 200
    assert len(FakeS3Storage.copied) == 2
    with Session(migrated_engine) as db:
        assert (
            db.scalar(select(MediaAsset).where(MediaAsset.id == media_id)).status
            == "ready"
        )


def test_expired_upload_cleanup_waits_for_session_deadline_and_retries(
    api, monkeypatch, migrated_engine
):
    from app.services.mobile_uploads import reconcile_expired_mobile_uploads
    from app.services.media_reconciliation import retry_pending_deletions
    from app.services import storage as storage_service
    from app.core.time import utc_now
    from datetime import timedelta

    monkeypatch.setattr(media_api.settings, "storage_backend", "cloud_ref")
    monkeypatch.setattr(media_api.settings, "mobile_upload_backend", "s3")
    monkeypatch.setattr(media_api, "S3Storage", FakeS3Storage)
    monkeypatch.setattr(storage_service, "S3Storage", FakeS3Storage)
    FakeS3Storage.deleted.clear()
    created = api.post(
        "/api/v1/media/mobile-upload/sessions",
        json=payload(request_id="android-upload-0008"),
    )
    media_id = created.json()["media_id"]
    with Session(migrated_engine) as db:
        session = db.scalar(
            select(MobileMediaUploadSession).where(
                MobileMediaUploadSession.media_asset_id == media_id
            )
        )
        staging_key = session.staging_key
        session_id = session.id
        session.cleanup_after = utc_now() - timedelta(seconds=1)
        db.commit()
    with Session(migrated_engine) as db:
        assert reconcile_expired_mobile_uploads(db)["scheduled"] == 1
        session = db.scalar(
            select(MobileMediaUploadSession).where(
                MobileMediaUploadSession.id == session_id
            )
        )
        assert session.status == "expired"
        assert FakeS3Storage.deleted == []
        assert retry_pending_deletions(db)["verified"] == 1
    assert FakeS3Storage.deleted == [staging_key]
    with Session(migrated_engine) as db:
        session = db.scalar(
            select(MobileMediaUploadSession).where(
                MobileMediaUploadSession.media_asset_id.is_(None)
            )
        )
        assert session.cleanup_completed_at is not None


def test_routine_staging_cleanup_is_hidden_from_account_deletion_status(
    api, monkeypatch
):
    monkeypatch.setattr(media_api.settings, "storage_backend", "cloud_ref")
    monkeypatch.setattr(media_api.settings, "mobile_upload_backend", "s3")
    monkeypatch.setattr(media_api, "S3Storage", FakeS3Storage)
    FakeS3Storage.metadata = {"ContentLength": 12, "ContentType": "image/jpeg"}
    FakeS3Storage.copied.clear()

    created = api.post(
        "/api/v1/media/mobile-upload/sessions",
        json=payload(request_id="android-upload-0010"),
    )
    assert created.status_code == 200, created.text
    completed = api.post(
        f"/api/v1/media/mobile-upload/sessions/{created.json()['media_id']}/complete"
    )
    assert completed.status_code == 200, completed.text

    status = api.get("/api/v1/privacy/deletion-status")
    assert status.status_code == 200, status.text
    assert status.json()["total"] == 0
    assert status.json()["verified_rate_pct"] is None


def test_account_deletion_defers_active_upload_staging_cleanup(
    api, monkeypatch, migrated_engine
):
    from app.services import privacy as privacy_service

    monkeypatch.setattr(media_api.settings, "storage_backend", "cloud_ref")
    monkeypatch.setattr(media_api.settings, "mobile_upload_backend", "s3")
    monkeypatch.setattr(media_api, "S3Storage", FakeS3Storage)
    monkeypatch.setattr(privacy_service, "S3Storage", FakeS3Storage)
    FakeS3Storage.deleted.clear()
    created = api.post(
        "/api/v1/media/mobile-upload/sessions",
        json=payload(request_id="android-upload-0009"),
    )
    with Session(migrated_engine) as db:
        session = db.scalar(
            select(MobileMediaUploadSession).where(
                MobileMediaUploadSession.media_asset_id == created.json()["media_id"]
            )
        )
        staging_key = session.staging_key
        cleanup_after = session.cleanup_after
    aborted = api.delete(
        f"/api/v1/media/mobile-upload/sessions/{created.json()['media_id']}"
    )
    assert aborted.status_code == 200, aborted.text
    assert api.get("/api/v1/privacy/deletion-status").json()["total"] == 0
    response = api.request(
        "DELETE", "/api/v1/privacy/account", json={"confirmation": "DELETE MY DATA"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["verification"] == "pending"
    with Session(migrated_engine) as db:
        task = db.scalar(
            select(MediaDeletionTask).where(
                MediaDeletionTask.storage_key_hash
                == hashlib.sha256(staging_key.encode()).hexdigest()
            )
        )
        assert task is not None
        assert task.next_attempt_at == cleanup_after
        assert task.status == "pending"
        assert json.loads(task.provider_receipt_json)["reason"].startswith(
            "account_deletion"
        )
    assert staging_key not in FakeS3Storage.deleted
