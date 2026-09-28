"""Kinetics-400 job lifecycle: capability claim, completion validation, storage."""
import json
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import AIJob, MediaAsset
from app.services.ai_jobs import claim_next_job, create_ai_job

KINETICS_OK = {
    "method": "slowfast_kinetics400_v1",
    "top_label": "pull ups",
    "top_label_zh": "引体向上",
    "top_probability": 0.61,
    "mapped_exercise": "pullup",
    "exercise_slug": "pull_ups",
    "candidates": [
        {"label": "pull ups", "class_index": 231, "probability": 0.61},
        {"label": "squat", "class_index": 300, "probability": 0.25},
    ],
    "is_estimate": True,
}


def _make_video_asset(api, db: Session) -> MediaAsset:
    asset = MediaAsset(
        user_id=api.user_id,
        original_name="kinetics-test.mp4",
        storage_key="cloud://env/healthmate/test/kinetics-test.mp4",
        storage_backend="cloudbase",
        cloud_file_id="cloud://env/healthmate/test/kinetics-test.mp4",
        source_url="https://example.com/temp-kinetics.mp4",
        source_url_expires_at=datetime.now() + timedelta(hours=1),
        media_type="video",
        content_type="video/mp4",
        size_bytes=100,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


def _create_claimed_kinetics_job(api, db: Session):
    asset = _make_video_asset(api, db)
    job = create_ai_job(
        db,
        user_id=api.user_id,
        job_type="kinetics400",
        media_asset_id=asset.id,
        payload={},
    )
    claimed = claim_next_job(db, worker_id="worker-1", capabilities=["kinetics400"])
    assert claimed is not None and claimed.id == job.id
    lease = claimed.lease_token
    db.commit()
    return job.id, lease


def test_kinetics_job_claim_and_complete(api, migrated_engine):
    with Session(migrated_engine) as db:
        asset = _make_video_asset(api, db)
        job = create_ai_job(
            db,
            user_id=api.user_id,
            job_type="kinetics400",
            media_asset_id=asset.id,
            payload={},
        )
        # A motion/food-only worker must NOT claim a kinetics400 job.
        assert (
            claim_next_job(db, worker_id="worker-1", capabilities=["motion_pose", "food_vision"])
            is None
        )
        claimed = claim_next_job(db, worker_id="worker-1", capabilities=["kinetics400"])
        assert claimed is not None and claimed.id == job.id
        job_id, lease = job.id, claimed.lease_token
        db.commit()

    resp = api.post(
        f"/api/v1/worker/jobs/{job_id}/complete",
        headers=api.worker_headers,
        json={
            "worker_id": "worker-1",
            "lease_token": lease,
            "metrics": {"latency_ms": 4210},
            "result": KINETICS_OK,
        },
    )
    assert resp.status_code == 200
    with Session(migrated_engine) as db:
        row = db.get(AIJob, job_id)
        assert row.status == "done"
        saved = json.loads(row.result_json or "{}")
        assert saved["top_label"] == "pull ups"
        assert saved["top_probability"] == 0.61
        assert saved["candidates"][0]["label"] == "pull ups"


def test_kinetics_complete_rejects_invalid_probability(api, migrated_engine):
    with Session(migrated_engine) as db:
        job_id, lease = _create_claimed_kinetics_job(api, db)

    bad = dict(KINETICS_OK)
    bad["top_probability"] = 1.5
    resp = api.post(
        f"/api/v1/worker/jobs/{job_id}/complete",
        headers=api.worker_headers,
        json={"worker_id": "worker-1", "lease_token": lease, "result": bad},
    )
    assert resp.status_code == 422
    with Session(migrated_engine) as db:
        row = db.get(AIJob, job_id)
        assert row.status == "processing"  # lease still open, nothing stored


def test_kinetics_complete_rejects_top_label_not_in_candidates(api, migrated_engine):
    with Session(migrated_engine) as db:
        job_id, lease = _create_claimed_kinetics_job(api, db)

    bad = dict(KINETICS_OK)
    bad["top_label"] = "yoga"
    resp = api.post(
        f"/api/v1/worker/jobs/{job_id}/complete",
        headers=api.worker_headers,
        json={"worker_id": "worker-1", "lease_token": lease, "result": bad},
    )
    assert resp.status_code == 422


def test_kinetics_jobs_media_endpoint(api, migrated_engine):
    with Session(migrated_engine) as db:
        asset = _make_video_asset(api, db)
        asset_id = asset.id
    created = api.post("/api/v1/media/kinetics-jobs", json={"media_id": asset_id})
    assert created.status_code == 200
    data = created.json()
    assert data["ok"] and data["job_type"] == "kinetics400"
    job_id = data["job_id"]
    polled = api.get(f"/api/v1/media/kinetics-jobs/{job_id}")
    assert polled.status_code == 200
    assert polled.json()["status"] == "queued"
    assert polled.json()["result"] is None
