# -*- coding: utf-8 -*-
"""E-package regression: receipt decoupling, stage queue CAS, atomic budget, purge.

Covers T12 (polling timeout -> keep processing/error, never fake-completed),
T13 (duplicate complete / duplicate stage claim -> local result preserved, atomic
reservation allows at most once, no blind retry) and T14 (V1/V2 receipts parse,
purge covers V1 + V2 + feedback snapshots + expired evidence frames).

These tests exercise E-owned services directly (stage_tasks / provider_gateway /
ai_jobs.purge / schemas.worker) and do NOT depend on the A-package decision seam.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import (
    AIJob,
    MediaAsset,
    MotionAnalysisFeedback,
    MotionAnalysisRun,
    MotionEvidenceFrame,
    MotionStageTask,
    ProviderInvocation,
    User,
)
from app.schemas.worker import (
    validate_motion_worker_result_v1,
    validate_motion_worker_result_v2,
    MotionResultSchemaError,
)
from app.services.ai_jobs import purge_expired_motion_previews
from app.services.motion.provider_gateway import (
    ProviderGateway,
    ProviderBudgetExceeded,
    build_reservation_key,
    RESERVATION_RESERVED,
    RESERVATION_SENT,
    RESERVATION_SUCCEEDED,
    RESERVATION_OUTCOME_UNKNOWN,
)
from app.services.motion.stage_tasks import (
    enqueue_stage,
    claim_stage,
    complete_stage,
    fail_stage,
    stage_status_map,
    process_pending_stages,
    STATUS_DONE,
    STATUS_QUEUED,
)


def _make_user(db: Session) -> User:
    user = User(openid="e-stage-" + __import__("uuid").uuid4().hex)
    db.add(user)
    db.commit()
    return user


def _make_run(
    db: Session,
    user_id: int,
    *,
    cloud_review_mode: str = "off",
) -> MotionAnalysisRun:
    import uuid as _uuid

    asset = MediaAsset(
        user_id=user_id,
        media_type="video",
        storage_backend="cloud_ref",
        storage_key=f"cloud://test.env/{_uuid.uuid4().hex}.mp4",
        source_url="https://example.com/v.mp4",
    )
    db.add(asset)
    db.commit()
    # Worker AIJob holding the local receipt (what /complete stores).
    receipt = {
        "pose": {"available": False, "message": "证据不足", "errors": []},
        "score": {"available": False},
        "recognition": {
            "mode": "auto",
            "requested_type": "auto",
            "selected_type": None,
            "accepted": False,
            "confidence": 0.4,
            "margin": 10.0,
            "method": "rules_v1",
            "candidates": [],
        },
        "frames": [],
        "kinetics": {"candidates": []},
    }
    job = AIJob(
        user_id=user_id,
        media_asset_id=asset.id,
        job_type="motion_unified",
        status="done",
        progress=100,
        result_json=json.dumps(receipt, ensure_ascii=False),
    )
    db.add(job)
    db.commit()
    run = MotionAnalysisRun(
        user_id=user_id,
        media_asset_id=asset.id,
        ai_job_id=job.id,
        requested_type="auto",
        pipeline_version="motion-unified-v2",
        status="evidence_ready",
        consent_version="motion-real-frames-v2",
        cloud_review_mode=cloud_review_mode,
        effective_pipeline_version="motion-unified-v2",
        result_version=1,
    )
    db.add(run)
    db.commit()
    return run


# --------------------------------------------------------------------------- #
# T12: polling timeout must keep processing / error, never fake-completed
# --------------------------------------------------------------------------- #

def test_t12_pending_stage_keeps_run_processing_not_fake_completed(migrated_engine):
    with Session(migrated_engine) as db:
        user = _make_user(db)
        run = _make_run(db, user.id)
        # Receipt was stored locally; post-processing stages enqueued but not yet
        # consumed (background worker not running / still on DeepSeek).
        enqueue_stage(db, run_id=run.id, stage="vision_review")
        enqueue_stage(db, run_id=run.id, stage="feedback_generation")
        db.commit()

        # Polling sees the run still in a processing stage, NOT completed.
        refreshed = db.get(MotionAnalysisRun, run.id)
        assert refreshed.status == "evidence_ready"
        statuses = stage_status_map(db, run_id=run.id)
        assert statuses["vision_review"] == STATUS_QUEUED
        assert statuses["feedback_generation"] == STATUS_QUEUED
        # No result snapshot written yet -> a GET must expose result=null/partial,
        # never a fabricated completed block.
        fb = db.scalar(
            select(MotionAnalysisFeedback).where(
                MotionAnalysisFeedback.run_id == run.id
            )
        )
        assert fb is None


def test_t12_failed_stage_surfaces_error_not_fake_completed(migrated_engine):
    with Session(migrated_engine) as db:
        user = _make_user(db)
        run = _make_run(db, user.id)
        task = enqueue_stage(db, run_id=run.id, stage="vision_review")
        db.commit()
        claimed = claim_stage(
            db, run_id=run.id, stage="vision_review", version=task.version
        )
        assert claimed is not None
        # Consumer crashed -> stage goes back to queued with an error, run still
        # must NOT be reported as completed.
        failed = fail_stage(
            db,
            run_id=run.id,
            stage="vision_review",
            version=task.version,
            error_code="vision_timeout",
            retry_seconds=60,
        )
        db.commit()
        assert failed is True
        row = db.get(MotionStageTask, task.id)
        assert row.status == STATUS_QUEUED
        assert row.error_code == "vision_timeout"


# --------------------------------------------------------------------------- #
# T13: duplicate complete / duplicate claim -> at most one spend, CAS guard
# --------------------------------------------------------------------------- #

def test_t13_double_claim_same_stage_loses_the_race(migrated_engine):
    with Session(migrated_engine) as db:
        user = _make_user(db)
        run = _make_run(db, user.id)
        task = enqueue_stage(db, run_id=run.id, stage="vision_review")
        db.commit()

        first = claim_stage(
            db, run_id=run.id, stage="vision_review", version=task.version
        )
        db.commit()
        assert first is not None
        # A second consumer claiming the same queued+processing row loses.
        second = claim_stage(
            db, run_id=run.id, stage="vision_review", version=task.version
        )
        assert second is None


def test_t13_old_version_cannot_complete_a_new_result(migrated_engine):
    with Session(migrated_engine) as db:
        user = _make_user(db)
        run = _make_run(db, user.id)
        v1 = enqueue_stage(db, run_id=run.id, stage="vision_review")
        db.commit()
        claim_stage(db, run_id=run.id, stage="vision_review", version=v1.version)
        db.commit()

        # Re-analysis bumps the stage to a new version.
        v2 = enqueue_stage(db, run_id=run.id, stage="vision_review")
        db.commit()
        assert v2.version == v1.version + 1

        # The stale consumer holding v1 cannot complete (CAS fails: v1 row is
        # processing but v1 != current version it may not overwrite v2).
        ok_stale = complete_stage(
            db, run_id=run.id, stage="vision_review", version=v1.version
        )
        db.commit()
        assert ok_stale is False

        # The v2 consumer can claim and complete its own row.
        claim_stage(db, run_id=run.id, stage="vision_review", version=v2.version)
        db.commit()
        ok_new = complete_stage(
            db, run_id=run.id, stage="vision_review", version=v2.version
        )
        db.commit()
        assert ok_new is True


def test_t13_atomic_reservation_allows_at_most_one_spend(migrated_engine):
    with Session(migrated_engine) as db:
        user = _make_user(db)
        run = _make_run(db, user.id)
        gw = ProviderGateway(db, user_id=user.id, run_id=run.id)
        row = gw.reserve_invocation(
            operation="deepseek_vision_review",
            evidence_hash="ev-hash-1",
            model="deepseek-vision",
            prompt_version="p-v2",
            policy_version="pol-v1",
            consent_mode="redacted_frames",
            request_fingerprint="fp-1",
        )
        db.commit()
        assert row.status == RESERVATION_RESERVED

        # The exact same seven-element fingerprint cannot be reserved twice.
        with pytest.raises(ProviderBudgetExceeded):
            gw.reserve_invocation(
                operation="deepseek_vision_review",
                evidence_hash="ev-hash-1",
                model="deepseek-vision",
                prompt_version="p-v2",
                policy_version="pol-v1",
                consent_mode="redacted_frames",
                request_fingerprint="fp-1",
            )

        # Sent -> succeeded lifecycle.
        gw.mark_sent(row)
        assert row.status == RESERVATION_SENT
        gw.mark_outcome(row, outcome=RESERVATION_SUCCEEDED, latency_ms=12.0, tokens=11)
        db.commit()

        n = db.scalar(
            select(ProviderInvocation).where(
                ProviderInvocation.reservation_key
                == build_reservation_key(
                    user_id=user.id,
                    evidence_hash="ev-hash-1",
                    operation="deepseek_vision_review",
                    model="deepseek-vision",
                    prompt_version="p-v2",
                    policy_version="pol-v1",
                    consent_mode="redacted_frames",
                )
            )
        )
        assert n.status == RESERVATION_SUCCEEDED


def test_t13_orphaned_sent_becomes_outcome_unknown_no_resend(migrated_engine):
    with Session(migrated_engine) as db:
        user = _make_user(db)
        run = _make_run(db, user.id)
        gw = ProviderGateway(db, user_id=user.id, run_id=run.id)
        row = gw.reserve_invocation(
            operation="deepseek_vision_review",
            evidence_hash="ev-hash-2",
            model="deepseek-vision",
            prompt_version="p-v2",
            policy_version="pol-v1",
            consent_mode="redacted_frames",
            request_fingerprint="fp-2",
        )
        gw.mark_sent(row)
        db.commit()
        n = gw.resolve_orphaned_reservations()
        db.commit()
        assert n == 1
        assert row.status == RESERVATION_OUTCOME_UNKNOWN


# --------------------------------------------------------------------------- #
# T14: V1 history still parses, V2 parses, purge covers all image copies
# --------------------------------------------------------------------------- #

def test_t14_v1_and_v2_receipt_schemas_both_parse():
    v1 = {
        "schema_version": "motion-worker-result-v1",
        "pose": {"available": False, "message": "证据不足", "errors": []},
        "frames": [{"event": "e0", "timestamp": 0.0}],
        "score": {"available": False},
        "recognition": {
            "mode": "auto",
            "requested_type": "auto",
            "selected_type": None,
            "accepted": False,
            "confidence": 0.5,
            "margin": 10.0,
            "method": "rules_v1",
            "candidates": [],
        },
    }
    assert validate_motion_worker_result_v1(v1) is not None

    v2 = {
        "schema_version": "motion-worker-v2",
        "video_quality": {"available": True, "decoded_ok": True, "duration_ms": 16000},
        "subject": {"available": True, "subject_id": "s_01", "visible_regions": ["elbow"]},
        "pose_evidence": {"available": True, "fps": 6.0, "frame_ids": ["p_0"]},
        "recognition_candidates": [
            {"source": "pose", "source_label": "squat", "canonical_id": "squat",
             "raw_score": 0.9, "score_type": "rule"},
            {"source": "kinetics", "source_label": "front raises", "canonical_id": None,
             "raw_score": 0.7, "score_type": "softmax"},
        ],
        "kinetics": {"candidates": [
            {"source": "kinetics", "source_label": "front raises",
             "canonical_id": None, "raw_score": 0.7, "score_type": "softmax"}
        ]},
        "frames": [
            {"frame_id": "f_01", "timestamp_ms": 4800, "preview_asset_id": "pv_1",
             "subject_id": "s_01", "phase": "抬起阶段", "finding": "前臂上转"}
        ],
        "measurements": {"available": True, "exercise_id": "squat", "reps": 5},
    }
    parsed = validate_motion_worker_result_v2(v2)
    # kinetics.candidates field must be preserved (R05).
    assert parsed["kinetics"]["candidates"][0]["source_label"] == "front raises"

    # A V2 receipt missing required groups must 422, not silently pass.
    with pytest.raises(MotionResultSchemaError):
        validate_motion_worker_result_v2({"schema_version": "motion-worker-v2"})


def test_t14_purge_strips_v1_v2_aijob_and_feedback_images_and_expires_frames(migrated_engine):
    with Session(migrated_engine) as db:
        user = _make_user(db)
        run = _make_run(db, user.id)
        # Old done AIJob still carrying base64 frames (V1 motion_pose).
        old_job = AIJob(
            user_id=user.id,
            media_asset_id=run.media_asset_id,
            job_type="motion_pose",
            status="done",
            progress=100,
            finished_at=utc_now() - timedelta(days=40),
            result_json=(
                '{"frames":[{"event":"e0","timestamp":0.0,'
                '"image_b64":"QUJD","image_mime":"image/jpeg"}]}'
            ),
        )
        # Unified V2 AIJob receipt with base64 still in frames.
        uni_job = AIJob(
            user_id=user.id,
            media_asset_id=run.media_asset_id,
            job_type="motion_unified",
            status="done",
            progress=100,
            finished_at=utc_now() - timedelta(days=40),
            result_json=(
                '{"frames":[{"frame_id":"f_01","timestamp_ms":1000,'
                '"image_b64":"QUJD","image_mime":"image/jpeg"}]}'
            ),
        )
        db.add_all([old_job, uni_job])
        db.commit()

        # Unified result snapshot with data: URLs in keyframes.
        fb = MotionAnalysisFeedback(
            run_id=run.id,
            user_id=user.id,
            schema_version="motion-unified-v2",
            result_json=(
                '{"keyframes":[{"id":"f_01","image_url":"data:image/jpeg;base64,QUJD"}]}'
            ),
            created_at=utc_now() - timedelta(days=40),
        )
        db.add(fb)

        # Expired evidence frame row.
        expired = MotionEvidenceFrame(
            run_id=run.id,
            frame_id="f_exp",
            timestamp_ms=1000,
            expires_at=utc_now() - timedelta(days=1),
        )
        live = MotionEvidenceFrame(
            run_id=run.id,
            frame_id="f_live",
            timestamp_ms=2000,
            expires_at=utc_now() + timedelta(days=7),
        )
        db.add_all([expired, live])
        db.commit()
        expired_id, live_id = expired.id, live.id

        changed = purge_expired_motion_previews(db, now=utc_now())
        db.commit()
        assert changed >= 4  # two jobs + one feedback + one deleted frame

        db.expire_all()
        # V1/V2 AIJob image bytes stripped.
        for job in (old_job, uni_job):
            reloaded = db.get(AIJob, job.id)
            assert "image_b64" not in reloaded.result_json
        # Feedback data: URL stripped.
        reloaded_fb = db.get(MotionAnalysisFeedback, fb.id)
        assert "data:image/" not in reloaded_fb.result_json
        # Expired evidence row gone, live row kept (scalar lookup avoids loading
        # the deleted instance back into the identity map).
        gone = db.scalar(
            select(MotionEvidenceFrame.id).where(MotionEvidenceFrame.id == expired_id)
        )
        kept = db.scalar(
            select(MotionEvidenceFrame.id).where(MotionEvidenceFrame.id == live_id)
        )
        assert gone is None
        assert kept == live_id


# --------------------------------------------------------------------------- #
# Async receipt decoupling: /complete returns at evidence_ready; the background
# consumer (process_pending_stages) drives the run to a terminal state.
# --------------------------------------------------------------------------- #

def test_t12_offline_consumer_drives_run_to_completed_without_deepseek(migrated_engine):
    with Session(migrated_engine) as db:
        user = _make_user(db)
        run = _make_run(db, user.id, cloud_review_mode="off")
        enqueue_stage(db, run_id=run.id, stage="vision_review")
        enqueue_stage(db, run_id=run.id, stage="feedback_generation")
        db.commit()

        seen: list[str] = []
        # cloud_review_mode == off -> reviewer must never be invoked.
        def fake_reviewer(frames, context):
            seen.append("vision")
            return None

        profiled: list[dict] = []

        def fake_profiler(db, *, run, result):
            profiled.append({"run": run.id, "has_result": bool(result)})
            return {"score_written": False, "events_written": 0}

        processed = process_pending_stages(
            db, run_id=run.id, reviewer=fake_reviewer, profiler=fake_profiler
        )
        db.commit()

        assert processed == 2
        assert seen == []  # DeepSeek skipped for offline mode
        fresh = db.get(MotionAnalysisRun, run.id)
        assert fresh.status == "completed"  # apply_post_review terminal state
        # F-package profile hook fired once with the rebuilt result.
        assert len(profiled) == 1 and profiled[0]["run"] == run.id
        # Stages all done; polling now sees a finished run, not a fake mid-state.
        assert stage_status_map(db, run_id=run.id) == {
            "vision_review": STATUS_DONE,
            "feedback_generation": STATUS_DONE,
        }


def test_t12_cloud_mode_invokes_reviewer_then_completes(migrated_engine):
    with Session(migrated_engine) as db:
        user = _make_user(db)
        run = _make_run(db, user.id, cloud_review_mode="redacted_frames")
        enqueue_stage(db, run_id=run.id, stage="vision_review")
        enqueue_stage(db, run_id=run.id, stage="feedback_generation")
        db.commit()

        calls: list[dict] = []

        def fake_reviewer(frames, context):
            calls.append({"frames": len(frames), "mode": context})
            return None  # apply_post_review accepts a None coach review

        process_pending_stages(
            db,
            run_id=run.id,
            reviewer=fake_reviewer,
            profiler=lambda db, *, run, result: {},
        )
        db.commit()

        assert len(calls) == 1  # DeepSeek review invoked exactly once
        fresh = db.get(MotionAnalysisRun, run.id)
        assert fresh.status == "completed"


def test_t13_duplicate_claim_after_enqueue_still_processes_once(migrated_engine):
    with Session(migrated_engine) as db:
        user = _make_user(db)
        run = _make_run(db, user.id, cloud_review_mode="off")
        enqueue_stage(db, run_id=run.id, stage="vision_review")
        db.commit()

        # Two concurrent drain passes: only one claims the row.
        first = process_pending_stages(
            db, run_id=run.id, reviewer=lambda f, c: None,
            profiler=lambda db, *, run, result: {},
        )
        second = process_pending_stages(
            db, run_id=run.id, reviewer=lambda f, c: None,
            profiler=lambda db, *, run, result: {},
        )
        db.commit()
        assert first == 1
        assert second == 0  # already done -> no double processing
