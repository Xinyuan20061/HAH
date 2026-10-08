# -*- coding: utf-8 -*-
"""R12 event-chain regression: a permanently-failed AI job must advance the
linked MotionAnalysisRun to a terminal state.

Incident: job 67 uploaded a V2 receipt to the pre-upgrade cloud backend, got a
permanent 422, the worker reported /fail, the job became ``failed`` — but the
run stayed ``queued`` forever, so the miniprogram polled "正在处理…" indefinitely
and the idempotency key kept reusing the dead run.
"""

import json
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import AIJob, MediaAsset, MotionAnalysisRun
from app.services.ai_jobs import requeue_expired_jobs

LEASE = "lease-fail-link-run"
WORKER = "healthmate-laptop-01"

_TERMINAL_RUN = {"completed", "partial", "failed", "cancelled"}


def _seed(db: Session, user_id: int, job_status: str = "processing",
          attempts: int = 0, lease_expires: bool = True, run_status: str = "queued"):
    asset = MediaAsset(
        user_id=user_id,
        original_name="squat.mp4",
        storage_key=f"cloud://env/healthmate/test/{user_id}.mp4",
        storage_backend="cloudbase",
        cloud_file_id=f"cloud://env/healthmate/test/{user_id}.mp4",
        source_url="https://example.com/temp.mp4",
        source_url_expires_at=utc_now() + timedelta(hours=1),
        media_type="video",
        content_type="video/mp4",
        size_bytes=1024,
    )
    db.add(asset)
    db.flush()
    job = AIJob(
        user_id=user_id,
        media_asset_id=asset.id,
        job_type="motion_unified",
        status=job_status,
        progress=10,
        priority=100,
        payload_json=json.dumps(
            {"analysis_id": None, "consent_deepseek_frames": True}
        ),
        worker_id=WORKER,
        lease_token=LEASE,
        lease_expires_at=utc_now() + (timedelta(minutes=5) if lease_expires else timedelta(seconds=-5)),
        attempts=attempts,
    )
    db.add(job)
    db.flush()
    run = MotionAnalysisRun(
        user_id=user_id,
        media_asset_id=asset.id,
        ai_job_id=job.id,
        dedupe_key=f"test-fail-link-{job.id}",
        requested_type="auto",
        pipeline_version="motion-unified-v1",
        status=run_status,
        consent_version="motion-real-frames-v2",
        model_versions_json=json.dumps({"consent_deepseek_frames": True}),
    )
    db.add(run)
    db.commit()
    return job.id, run.id, asset.id


def _fail_payload(error_code="MOTION_RESULT_SCHEMA_INVALID", retryable=False):
    return {
        "worker_id": WORKER,
        "lease_token": LEASE,
        "error_code": error_code,
        "error_message": "Cloud API HTTP 422 (/worker/jobs/1/complete) field_path=frames[0].x",
        "retryable": retryable,
    }


def test_fail_job_advances_run_to_failed(api, db):
    job_id, run_id, _ = _seed(db, api.user_id, attempts=1)
    resp = api.post(
        f"/api/v1/worker/jobs/{job_id}/fail",
        headers=api.worker_headers,
        json=_fail_payload(retryable=False),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "failed"
    assert resp.json()["retrying"] is False

    db.rollback()
    job = db.get(AIJob, job_id)
    run = db.get(MotionAnalysisRun, run_id)
    assert job.status == "failed"
    assert run.status == "failed"
    assert run.error_code == "MOTION_RESULT_SCHEMA_INVALID"
    assert run.finished_at is not None


def test_fail_retryable_keeps_run_active(api, db):
    job_id, run_id, _ = _seed(db, api.user_id, attempts=1)
    resp = api.post(
        f"/api/v1/worker/jobs/{job_id}/fail",
        headers=api.worker_headers,
        json=_fail_payload(retryable=True),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "queued"
    assert resp.json()["retrying"] is True

    db.rollback()
    db.expire_all()
    job = db.get(AIJob, job_id)
    run = db.get(MotionAnalysisRun, run_id)
    assert job.status == "queued"
    # A retryable failure is not terminal: the run stays active for re-claim.
    assert run.status == "queued"
    assert run.finished_at is None


def test_fail_does_not_touch_terminal_run(api, db):
    job_id, run_id, _ = _seed(db, api.user_id, attempts=1, run_status="completed")
    resp = api.post(
        f"/api/v1/worker/jobs/{job_id}/fail",
        headers=api.worker_headers,
        json=_fail_payload(retryable=False),
    )
    assert resp.status_code == 200, resp.text
    db.expire_all()
    run = db.get(MotionAnalysisRun, run_id)
    assert run.status == "completed"


def test_requeue_exhausted_job_advances_run_to_failed(db, api):
    job_id, run_id, _ = _seed(
        db, api.user_id, job_status="processing",
        attempts=99, lease_expires=False, run_status="queued",
    )
    requeue_expired_jobs(db)
    db.expire_all()
    job = db.get(AIJob, job_id)
    run = db.get(MotionAnalysisRun, run_id)
    assert job.status == "failed"
    assert job.error_code == "lease_exhausted"
    assert run.status == "failed"
    assert run.error_code == "lease_exhausted"
    assert run.finished_at is not None


def test_requeue_retryable_keeps_run_active(db, api):
    job_id, run_id, _ = _seed(
        db, api.user_id, job_status="processing",
        attempts=1, lease_expires=False, run_status="queued",
    )
    requeue_expired_jobs(db)
    db.expire_all()
    job = db.get(AIJob, job_id)
    run = db.get(MotionAnalysisRun, run_id)
    assert job.status == "queued"
    assert run.status == "queued"
