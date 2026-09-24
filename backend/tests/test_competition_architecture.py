from app.core.time import utc_now, utc_iso
from datetime import datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models import AIJob, MediaAsset, User
from app.services.ai_jobs import (
    claim_next_job,
    create_ai_job,
    job_source,
    requeue_expired_jobs,
)
from app.services.privacy import build_export_zip, cloud_file_ids


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_database_job_queue_claims_capability_and_uses_lease():
    with _db() as db:
        user = User(openid="worker-test")
        db.add(user)
        db.commit()
        db.refresh(user)
        asset = MediaAsset(
            user_id=user.id,
            original_name="squat.mp4",
            storage_key="cloud://env/healthmate/test/squat.mp4",
            storage_backend="cloudbase",
            cloud_file_id="cloud://env/healthmate/test/squat.mp4",
            source_url="https://example.com/temp.mp4",
            source_url_expires_at=utc_now() + timedelta(hours=1),
            media_type="video",
            content_type="video/mp4",
            size_bytes=1024,
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        first = create_ai_job(
            db,
            user_id=user.id,
            job_type="motion_pose",
            media_asset_id=asset.id,
            payload={"exercise_type": "squat"},
        )
        duplicate = create_ai_job(
            db,
            user_id=user.id,
            job_type="motion_pose",
            media_asset_id=asset.id,
            payload={"exercise_type": "squat"},
        )
        assert duplicate.id == first.id
        assert (
            claim_next_job(db, worker_id="gpu-1", capabilities=["food_vision"]) is None
        )
        job = claim_next_job(db, worker_id="gpu-1", capabilities=["motion_pose"])
        assert job is not None
        assert (
            job.status == "processing" and job.worker_id == "gpu-1" and job.lease_token
        )
        assert job.attempts == 1 and job.lease_expires_at > utc_now()


def test_expired_worker_lease_is_requeued():
    with _db() as db:
        user = User(openid="lease-test")
        db.add(user)
        db.commit()
        db.refresh(user)
        job = create_ai_job(
            db, user_id=user.id, job_type="food_vision", media_asset_id=None
        )
        job.status = "processing"
        job.worker_id = "lost-worker"
        job.lease_token = "dead"
        job.lease_expires_at = utc_now() - timedelta(seconds=1)
        job.attempts = 1
        db.commit()
        assert requeue_expired_jobs(db) == 1
        db.refresh(job)
        assert job.status == "queued"
        assert (
            not job.worker_id and not job.lease_token and job.lease_expires_at is None
        )


def test_cloud_media_expiry_and_privacy_export_do_not_leak_temp_url():
    import io, json, zipfile

    with _db() as db:
        user = User(openid="privacy-cloud-test", nickname="tester")
        db.add(user)
        db.commit()
        db.refresh(user)
        asset = MediaAsset(
            user_id=user.id,
            original_name="meal.jpg",
            storage_key="cloud://env/healthmate/meal.jpg",
            storage_backend="cloudbase",
            cloud_file_id="cloud://env/healthmate/meal.jpg",
            source_url="https://example.com/signed-secret-url",
            source_url_expires_at=utc_now() - timedelta(seconds=1),
            media_type="image",
            content_type="image/jpeg",
            size_bytes=100,
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        job = AIJob(
            user_id=user.id,
            media_asset_id=asset.id,
            job_type="food_vision",
            payload_json="{}",
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        source = job_source(db, job)
        assert source["expired"] is True
        assert cloud_file_ids(db, user.id) == [asset.cloud_file_id]
        raw = build_export_zip(db, user.id)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            payload = json.loads(z.read("healthmate-export.json"))
        exported = payload["data"]["media_assets"][0]
        assert exported["cloud_file_id"] == asset.cloud_file_id
        assert exported["source_url"] == ""
        assert "signed-secret-url" not in raw.decode("latin1", errors="ignore")
