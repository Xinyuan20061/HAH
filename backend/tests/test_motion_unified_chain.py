# -*- coding: utf-8 -*-
"""P0-B unified motion chain regression tests.

Covers: task create/poll/retry/idempotency, cross-user isolation, the
"keep the question" decision gate, worker-receipt abstention, DeepSeek cache
(no double billing across runs / across polling), failure injection (vision
timeout -> partial + degraded), and old endpoints still present + deprecated.
"""

import base64
import json
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now
from app.core.security import create_access_token
from app.main import app
from app.models import AIJob, MediaAsset, MotionAnalysisRun, ProviderInvocation, User
from app.services.motion.decision import decide_motion, LocalEvidence, quality_from_receipt
from app.services.motion.vision_review import VisionReview, FrameFinding


def _tiny_jpeg_b64():
    raw = base64.b64decode(
        "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
        "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAAB"
        "AAAAAAAAAAAAAAAAAAAACP/aAAgBAQABPwH/2Q=="
    )
    assert raw.startswith(b"\xff\xd8") and raw.endswith(b"\xff\xd9")
    return base64.b64encode(raw).decode("ascii")


JPEG = _tiny_jpeg_b64()
LEASE = "test-lease-token-1234"
WORKER = "healthmate-laptop-01"


def _receipt(*, frames=3, pose_available=False, accepted=False, label=None):
    f = []
    for i in range(frames):
        row = {"event": f"event-{i}", "timestamp": float(i)}
        if i < 4:
            row.update({"image_b64": JPEG, "image_mime": "image/jpeg"})
        f.append(row)
    if pose_available:
        pose = {
            "available": True,
            "exercise_type": label,
            "keypoint_valid_rate": 0.8,
            "reps": 3,
            "errors": [],
        }
        score = {
            "available": True,
            "completeness": 80.0,
            "stability": 78.0,
            "rhythm_control": 75.0,
            "risk_index": 12.0,
            "overall": 78.0,
            "confidence": 0.8,
        }
    else:
        pose = {"available": False, "message": "证据不足", "errors": []}
        score = {"available": False}
    candidates = [{"exercise_type": label, "match_score": 82.0}] if label else []
    return {
        "schema_version": "motion-worker-result-v1",
        "pose": pose,
        "frames": f,
        "score": score,
        "recognition": {
            "mode": "auto",
            "requested_type": "auto",
            "selected_type": label if accepted else None,
            "accepted": accepted,
            "confidence": 0.8,
            "margin": 15.0,
            "method": "rules_v1",
            "candidates": candidates,
        },
    }


def _seed_asset(migrated_engine, user_id):
    with Session(migrated_engine) as db:
        asset = MediaAsset(
            user_id=user_id,
            media_type="video",
            storage_backend="cloud_ref",
            storage_key=f"cloud://test.env/healthmate/u{user_id}/video/a.mp4",
            source_url="https://example.com/v.mp4",
        )
        db.add(asset)
        db.commit()
        return asset.id


def _complete(api, job_id, result):
    return api.post(
        f"/api/v1/worker/jobs/{job_id}/complete",
        headers=api.worker_headers,
        json={
            "worker_id": WORKER,
            "lease_token": LEASE,
            "result": result,
            "metrics": {"latency_ms": 1.0},
        },
    )


def _processing_job(migrated_engine, run_id):
    with Session(migrated_engine) as db:
        run = db.get(MotionAnalysisRun, run_id)
        job = db.get(AIJob, run.ai_job_id)
        job.status = "processing"
        job.worker_id = WORKER
        job.lease_token = LEASE
        job.lease_expires_at = utc_now() + timedelta(minutes=5)
        db.commit()
        return job.id


# --------------------------------------------------------------------------- #
# Decision gate unit tests (spec 4.5, "keep the question")
# --------------------------------------------------------------------------- #

def test_decision_gate_rules():
    q = quality_from_receipt(
        {"accepted": True, "selected_type": "squat"},
        {"available": True, "keypoint_valid_rate": 0.8, "reps": 3},
        [{}, {}, {}],
    )
    local = LocalEvidence(accepted=True, label_id="squat")
    # No review yet -> uncertain
    d = decide_motion(local, {"squat"}, None, q)
    assert d.state == "uncertain" and d.reason_code == "REVIEW_UNAVAILABLE_OR_UNCERTAIN"
    # Review unknown -> uncertain
    from app.services.motion.decision import ReviewEvidence
    d = decide_motion(local, {"squat"}, ReviewEvidence("unknown", ()), q)
    assert d.reason_code == "REVIEW_UNAVAILABLE_OR_UNCERTAIN"
    # Review label outside candidates -> unsupported
    d = decide_motion(local, {"squat"}, ReviewEvidence("pushup", ("frame:0",)), q)
    assert d.reason_code == "UNSUPPORTED_REVIEW"
    # Agreement on a scorer with full cycle -> recognized + scoreable
    d = decide_motion(local, {"squat"}, ReviewEvidence("squat", ("frame:0",)), q)
    assert d.state == "recognized" and d.scoreable and d.reason_code == "EVIDENCE_AGREEMENT"
    # Disagreement while pose reliable -> model disagreement
    d = decide_motion(local, {"squat", "pushup"}, ReviewEvidence("pushup", ("frame:0",)), q)
    assert d.reason_code == "MODEL_DISAGREEMENT"
    # Insufficient evidence -> abstained
    thin = quality_from_receipt({}, {}, [])
    d = decide_motion(LocalEvidence(False, None), set(), None, thin)
    assert d.state == "abstained" and d.reason_code == "INSUFFICIENT_VIDEO_EVIDENCE"


# --------------------------------------------------------------------------- #
# API-level tests
# --------------------------------------------------------------------------- #

def test_create_and_poll_unified_run(migrated_engine, api):
    media_id = _seed_asset(migrated_engine, api.user_id)
    resp = api.post(
        "/api/v1/media/motion-analyses",
        json={"media_id": media_id, "requested_exercise": "auto",
              "consent_deepseek_frames": True, "pipeline_version": "motion-unified-v1"},
        headers={"Idempotency-Key": "key-create-123456"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    run_id = body["analysis_id"]

    view = api.get(f"/api/v1/media/motion-analyses/{run_id}")
    assert view.status_code == 200
    assert view.json()["result"] is None  # worker has not run yet


def test_idempotency_same_key_same_task_different_params_409(migrated_engine, api):
    media_id = _seed_asset(migrated_engine, api.user_id)
    headers = {"Idempotency-Key": "idem-key-abcdef"}
    r1 = api.post("/api/v1/media/motion-analyses",
                  json={"media_id": media_id, "requested_exercise": "auto",
                        "consent_deepseek_frames": True}, headers=headers)
    assert r1.status_code == 202
    r2 = api.post("/api/v1/media/motion-analyses",
                  json={"media_id": media_id, "requested_exercise": "auto",
                        "consent_deepseek_frames": False}, headers=headers)
    assert r2.status_code == 409
    assert r2.json()["code"] == "IDEMPOTENCY_PARAM_MISMATCH"
    # Same params -> same task
    r3 = api.post("/api/v1/media/motion-analyses",
                  json={"media_id": media_id, "requested_exercise": "auto",
                        "consent_deepseek_frames": True}, headers=headers)
    assert r3.status_code == 202
    assert r3.json()["analysis_id"] == r1.json()["analysis_id"]


def test_cross_user_isolation_404(migrated_engine, api):
    media_id = _seed_asset(migrated_engine, api.user_id)
    r = api.post("/api/v1/media/motion-analyses",
                 json={"media_id": media_id, "requested_exercise": "auto",
                       "consent_deepseek_frames": False},
                 headers={"Idempotency-Key": "iso-key-0001"})
    run_id = r.json()["analysis_id"]
    # A second user must not see it.
    with Session(migrated_engine) as db:
        other = User(openid="other-" + "x" * 20)
        db.add(other)
        db.commit()
        other_id = other.id
    api.headers["Authorization"] = "Bearer " + create_access_token(str(other_id))
    try:
        resp = api.get(f"/api/v1/media/motion-analyses/{run_id}")
        assert resp.status_code == 404
        assert resp.json()["code"] == "MOTION_ANALYSIS_NOT_FOUND"
    finally:
        api.headers["Authorization"] = "Bearer " + _original_token(api)


def _original_token(client):
    return client.headers["Authorization"].removeprefix("Bearer ")


def test_worker_receipt_abstained_run(migrated_engine, api, monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "")
    media_id = _seed_asset(migrated_engine, api.user_id)
    r = api.post("/api/v1/media/motion-analyses",
                 json={"media_id": media_id, "requested_exercise": "auto",
                       "consent_deepseek_frames": False},
                 headers={"Idempotency-Key": "abst-key-0001"})
    run_id = r.json()["analysis_id"]
    job_id = _processing_job(migrated_engine, run_id)
    # Worker abstains: only 2 event frames, no pose, no recognition.
    resp = _complete(api, job_id, _receipt(frames=2, pose_available=False, accepted=False))
    assert resp.status_code == 200, resp.text
    view = api.get(f"/api/v1/media/motion-analyses/{run_id}").json()
    assert view["status"] == "abstained"
    assert view["result"]["recognition"]["reason_code"] == "INSUFFICIENT_VIDEO_EVIDENCE"


def test_full_chain_recognized_caches_and_polling_no_rebill(migrated_engine, api, monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-test")
    import app.services.motion.orchestrator as orch

    calls = {"vision": 0, "summary": 0}

    def fake_vision(*, facts, frames, timeout=40.0):
        calls["vision"] += 1
        review = VisionReview(
            label_id="squat",
            evidence_ids=["frame:0"],
            findings=[FrameFinding(frame_id="frame:0", observation="下蹲到一半", advice="蹲得再深一点")],
            reason="candidate agreement",
        )
        return review, {"provider_request_id": "ds-req-1", "tokens": 12, "model": "deepseek-flash"}

    def fake_summary(*, facts, timeout=40.0):
        calls["summary"] += 1
        return "整体下蹲节奏稳定，可以再加深一点下蹲幅度。", {"provider_request_id": "ds-req-2", "tokens": 30}

    monkeypatch.setattr(orch, "call_vision_review", fake_vision)
    monkeypatch.setattr(orch, "call_text_summary", fake_summary)

    media_id = _seed_asset(migrated_engine, api.user_id)
    r = api.post("/api/v1/media/motion-analyses",
                 json={"media_id": media_id, "requested_exercise": "auto",
                       "consent_deepseek_frames": True},
                 headers={"Idempotency-Key": "full-key-0001"})
    run_id = r.json()["analysis_id"]
    job_id = _processing_job(migrated_engine, run_id)
    receipt = _receipt(frames=3, pose_available=True, accepted=True, label="squat")
    resp = _complete(api, job_id, receipt)
    assert resp.status_code == 200, resp.text

    view = api.get(f"/api/v1/media/motion-analyses/{run_id}").json()
    assert view["status"] == "completed", view
    rec = view["result"]["recognition"]
    assert rec["state"] == "recognized" and rec["label_id"] == "squat"
    assert rec["review_status"] == "used"
    assert rec["calibrated_confidence"] is None
    assert view["result"]["score"]["available"] is True
    assert view["result"]["summary"]["degraded"] is False

    # Polling twice must NOT re-bill the model.
    api.get(f"/api/v1/media/motion-analyses/{run_id}")
    api.get(f"/api/v1/media/motion-analyses/{run_id}")
    assert calls["vision"] == 1 and calls["summary"] == 1

    # Second run on the SAME media with same params -> cache hit, still 1 call.
    r2 = api.post("/api/v1/media/motion-analyses",
                  json={"media_id": media_id, "requested_exercise": "auto",
                        "consent_deepseek_frames": True},
                  headers={"Idempotency-Key": "full-key-0002"})
    run2 = r2.json()["analysis_id"]
    job2 = _processing_job(migrated_engine, run2)
    assert _complete(api, job2, receipt).status_code == 200
    assert calls["vision"] == 1, calls
    assert calls["summary"] == 1, calls

    # provider_invocations ledger wrote exactly one vision row.
    with Session(migrated_engine) as db:
        n_vision = db.query(ProviderInvocation).filter(
            ProviderInvocation.operation == "deepseek_vision_review"
        ).count()
    assert n_vision == 1


def test_vision_failure_degrades_to_partial(migrated_engine, api, monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-test")
    import app.services.motion.orchestrator as orch

    def boom(*, facts, frames, timeout=40.0):
        raise RuntimeError("deepseek_vision_timeout")

    monkeypatch.setattr(orch, "call_vision_review", boom)

    media_id = _seed_asset(migrated_engine, api.user_id)
    r = api.post("/api/v1/media/motion-analyses",
                 json={"media_id": media_id, "requested_exercise": "auto",
                       "consent_deepseek_frames": True},
                 headers={"Idempotency-Key": "fail-key-0001"})
    run_id = r.json()["analysis_id"]
    job_id = _processing_job(migrated_engine, run_id)
    receipt = _receipt(frames=3, pose_available=True, accepted=True, label="squat")
    assert _complete(api, job_id, receipt).status_code == 200

    view = api.get(f"/api/v1/media/motion-analyses/{run_id}").json()
    assert view["status"] == "partial"
    rec = view["result"]["recognition"]
    assert rec["review_status"] == "unavailable"
    assert rec["state"] == "uncertain"
    assert view["result"]["summary"]["degraded"] is True


def test_no_consent_never_calls_vision(migrated_engine, api, monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-test")
    import app.services.motion.orchestrator as orch
    called = {"n": 0}

    def sneaky(*, facts, frames, timeout=40.0):
        called["n"] += 1
        raise AssertionError("must not call vision without consent")

    monkeypatch.setattr(orch, "call_vision_review", sneaky)
    media_id = _seed_asset(migrated_engine, api.user_id)
    r = api.post("/api/v1/media/motion-analyses",
                 json={"media_id": media_id, "requested_exercise": "auto",
                       "consent_deepseek_frames": False},
                 headers={"Idempotency-Key": "noconsent-0001"})
    run_id = r.json()["analysis_id"]
    job_id = _processing_job(migrated_engine, run_id)
    receipt = _receipt(frames=3, pose_available=True, accepted=True, label="squat")
    assert _complete(api, job_id, receipt).status_code == 200
    assert called["n"] == 0
    view = api.get(f"/api/v1/media/motion-analyses/{run_id}").json()
    assert view["result"]["recognition"]["review_status"] == "skipped"


def test_confirm_label_and_feedback_do_not_fabricate_score(migrated_engine, api):
    media_id = _seed_asset(migrated_engine, api.user_id)
    r = api.post("/api/v1/media/motion-analyses",
                 json={"media_id": media_id, "requested_exercise": "auto",
                       "consent_deepseek_frames": False},
                 headers={"Idempotency-Key": "cfm-key-0001"})
    run_id = r.json()["analysis_id"]
    job_id = _processing_job(migrated_engine, run_id)
    assert _complete(api, job_id, _receipt(frames=2)).status_code == 200

    c = api.post(f"/api/v1/media/motion-analyses/{run_id}/confirm-label",
                 json={"label_id": "squat", "correction_reason": "我确认是深蹲"})
    assert c.status_code == 200
    assert c.json()["score_fabricated"] is False

    f = api.post(f"/api/v1/media/motion-analyses/{run_id}/feedback",
                 json={"useful": False, "label_correction": "squat",
                       "comment": "关键帧不准"})
    assert f.status_code == 200
    assert f.json()["used_as_training_label"] is False

    ev = api.get(f"/api/v1/media/motion-analyses/{run_id}/evidence")
    assert ev.status_code == 200


def test_old_endpoints_still_registered_and_deprecated(api):
    spec = app.openapi()
    assert spec["paths"]["/api/v1/media/motion-jobs"]["post"].get("deprecated") is True
    assert spec["paths"]["/api/v1/media/kinetics-jobs"]["post"].get("deprecated") is True
