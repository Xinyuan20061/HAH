# -*- coding: utf-8 -*-
"""P0-A unified motion chain regression tests (V2 rebuild).

Covers: task create/poll/retry/idempotency, cross-user isolation, the rebuilt
decision gate (local reliable result stands on its own), worker-receipt
abstention, Kinetics-400 candidate participation, open-category acceptance,
category-change score invalidation, DeepSeek cache (no double billing), and
failure injection (vision failure -> local result retained, partial).

The C package (vision_review.run_visual_review / text_summary.build_summary) is
stubbed HERE via monkeypatch; no real model/network call happens.
"""

import base64
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now
from app.core.security import create_access_token
from app.main import app
from app.models import (
    AIJob,
    MediaAsset,
    MotionAnalysisFeedback,
    MotionAnalysisRun,
    MotionUserFeedback,
    ProviderInvocation,
    User,
)
from app.services.motion.decision import (
    KineticsCandidate,
    LocalEvidence,
    VisionEvidence,
    decide_motion,
    quality_from_receipt,
)


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


def _receipt(*, frames=3, pose_available=False, accepted=False, label=None, kinetics=None):
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
    receipt = {
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
    if kinetics:
        receipt["kinetics"] = kinetics
    return receipt


# --- C-package stubs (CoachReview per contract §4) ------------------------ #

def _note(frame_id, *, phase="抬起阶段", observation="前臂向上转动，哑铃靠近胸前。",
          explanation="留意上臂是否前移。", next_step="下一遍慢数两拍。", kind="general_tip"):
    return SimpleNamespace(
        frame_id=frame_id, phase=phase, observation=observation, explanation=explanation,
        next_step=next_step, advice_kind=kind,
        evidence_refs=[SimpleNamespace(frame_ids=[frame_id], start_ms=0, end_ms=1000)],
    )


def _coach(*, canonical_id=None, novel_label_zh=None, identification="identified",
           reason="画面关键帧观察。", summary="手臂屈伸轨迹可以看清。",
           next_step="下一遍放慢下放。", notes=None):
    return SimpleNamespace(
        canonical_id=canonical_id, novel_label_zh=novel_label_zh,
        identification=identification, identification_reason=reason,
        summary=summary, primary_next_step=next_step, frame_notes=notes or [],
    )


def _patch_vision(monkeypatch, coach, capture=None, counter=None):
    import app.services.motion.vision_review as vr

    def fake_run_visual_review(frames, context, catalog):
        if counter is not None:
            counter["n"] += 1
        if capture is not None:
            capture["context"] = context
        return coach

    monkeypatch.setattr(vr, "run_visual_review", fake_run_visual_review, raising=False)


def _patch_summary(monkeypatch, text="整体点评：动作轨迹清楚。", next_step="下一遍慢一点。",
                   source="visual_coach", counter=None):
    import app.services.motion.text_summary as ts

    def fake_build_summary(coach_review, metrics, evidence, catalog):
        if counter is not None:
            counter["n"] += 1
        return {"text": text, "primary_next_step": next_step, "source": source}

    monkeypatch.setattr(ts, "build_summary", fake_build_summary, raising=False)


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


def _stored_result(migrated_engine, run_id):
    """Internal result snapshot as persisted (still carries ``_meta`` diagnostics).

    The F package strips ``_meta`` from the user-facing GET (contract §3); the
    raw ``MotionAnalysisFeedback.result_json`` keeps it for diagnostics/tests.
    """
    with Session(migrated_engine) as db:
        fb = db.scalar(
            select(MotionAnalysisFeedback).where(
                MotionAnalysisFeedback.run_id == run_id
            )
        )
        return fb.result if fb else {}


def _drain_stages(migrated_engine, run_id, *, receipt=None):
    """Drive E's background stage consumer (async /complete model).

    The receipt HTTP transaction only stores local evidence and enqueues
    ``vision_review`` / ``feedback_generation`` stage rows, then returns; the
    terminal result is materialised by the background consumer. We emulate that
    consumer here by draining the queue with a handler that runs the orchestrator's
    canonical ``handle_unified_worker_result`` (which builds the vision context,
    runs C-package stubs, decides, writes feedback and flips the run to a terminal
    status). The handler is idempotent: once feedback already carries a result it
    skips, so the second enqueued stage (feedback_generation) does not re-bill.

    The C package (vision/summary) stays stubbed via monkeypatch; no real model.
    """
    from app.services.motion.stage_tasks import drain_pending_stages
    from app.services.motion.orchestrator import handle_unified_worker_result

    def handler(db, task):
        fb = db.scalar(
            select(MotionAnalysisFeedback).where(
                MotionAnalysisFeedback.run_id == run_id
            )
        )
        if fb and fb.result.get("recognition"):
            return  # already materialised by the first (vision_review) stage
        run = db.get(MotionAnalysisRun, run_id)
        job = db.get(AIJob, run.ai_job_id)
        stored = json.loads(job.result_json or "{}")
        handle_unified_worker_result(db, job, stored, None)

    with Session(migrated_engine) as db:
        drain_pending_stages(db, run_id=run_id, stage_handler=handler)


# --------------------------------------------------------------------------- #
# Decision gate unit tests (spec §3.3 / §5 / §6.4)
# --------------------------------------------------------------------------- #

def test_decision_gate_rules():
    q = quality_from_receipt(
        {"accepted": True, "selected_type": "squat"},
        {"available": True, "keypoint_valid_rate": 0.8, "reps": 3},
        [{}, {}, {}],
    )
    local = LocalEvidence(accepted=True, label_id="squat",
                          measured_exercise_id="squat", metrics_valid=True)

    # R03: no cloud review -> reliable local result is KEPT (identified), not uncertain.
    d = decide_motion(local, None, (), q)
    assert d.recognition.state == "identified"
    assert d.recognition.canonical_id == "squat"
    assert d.scoreable and d.metrics_applicable
    assert d.reason_code == "LOCAL_RELIABLE"

    # Cloud review explicitly unknown -> local result still retained.
    vis_unknown = VisionEvidence(
        canonical_id=None, novel_label_zh=None, identification="unknown",
        identification_reason="看不清", supported_by_observations=True,
    )
    d = decide_motion(local, vis_unknown, (), q)
    assert d.recognition.state == "identified" and d.recognition.canonical_id == "squat"

    # R06: vision proposes an out-of-candidate action (bicep_curl) -> accepted,
    # not rejected as "unsupported"; local disagreed -> softened to likely.
    vis_curl = VisionEvidence(
        canonical_id="bicep_curl", novel_label_zh=None, identification="identified",
        identification_reason="肘关节屈伸清楚", supported_by_observations=True,
        evidence_ids=("frame:0",),
    )
    d = decide_motion(local, vis_curl, (), q)
    assert d.recognition.canonical_id == "bicep_curl"
    assert d.recognition.state == "likely"
    # T05: the squat score/reps must NOT be inherited onto the curl result.
    assert not d.metrics_applicable and not d.scoreable
    assert d.measured_exercise_id is None

    # Kinetics-only usable candidate -> likely (never auto-identified/scored).
    k = (KineticsCandidate(source_label="front raises", canonical_id="front_raise",
                          raw_score=0.73, score_type="softmax"),)
    loc_rejected = LocalEvidence(accepted=False, label_id=None)
    d = decide_motion(loc_rejected, None, k, q)
    assert d.recognition.state == "likely" and d.recognition.canonical_id == "front_raise"
    assert d.recognition.source == "video_model"

    # Insufficient video evidence -> abstain.
    thin = quality_from_receipt({}, {}, [])
    d = decide_motion(LocalEvidence(False, None), None, (), thin)
    assert d.recognition.state == "unknown"
    assert d.reason_code == "INSUFFICIENT_VIDEO_EVIDENCE"


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
    _drain_stages(migrated_engine, run_id)
    view = api.get(f"/api/v1/media/motion-analyses/{run_id}").json()
    assert view["status"] == "abstained"
    assert view["result"]["recognition"]["state"] == "unknown"
    # F strips _meta from the user response; reason_code lives in internal diag.
    assert "_meta" not in view["result"]
    internal = _stored_result(migrated_engine, run_id)
    assert internal["_meta"]["reason_code"] == "INSUFFICIENT_VIDEO_EVIDENCE"


# --------------------------------------------------------------------------- #
# T01: local squat stands on its own with no cloud call (R03)
# --------------------------------------------------------------------------- #

def test_t01_local_squat_stands_without_cloud(migrated_engine, api, monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "")  # cloud unavailable
    import app.services.motion.vision_review as vr
    called = {"n": 0}

    def boom(*a, **k):
        called["n"] += 1
        raise AssertionError("T01 must never call the cloud vision review")

    monkeypatch.setattr(vr, "run_visual_review", boom, raising=False)

    media_id = _seed_asset(migrated_engine, api.user_id)
    r = api.post("/api/v1/media/motion-analyses",
                 json={"media_id": media_id, "requested_exercise": "auto",
                       "consent_deepseek_frames": True},
                 headers={"Idempotency-Key": "t01-key-0001"})
    run_id = r.json()["analysis_id"]
    job_id = _processing_job(migrated_engine, run_id)
    receipt = _receipt(frames=3, pose_available=True, accepted=True, label="squat")
    assert _complete(api, job_id, receipt).status_code == 200
    _drain_stages(migrated_engine, run_id, receipt=receipt)

    view = api.get(f"/api/v1/media/motion-analyses/{run_id}").json()
    # Local result retained: name + valid reps, not a REVIEW_UNAVAILABLE rejection.
    rec = view["result"]["recognition"]
    assert rec["state"] == "identified", rec
    assert rec["canonical_id"] == "squat"
    assert "深蹲" in rec["display_name"]
    # F strips _meta from the user response; reason_code lives in internal diag.
    assert "_meta" not in view["result"]
    internal = _stored_result(migrated_engine, run_id)
    assert internal["_meta"]["reason_code"] == "LOCAL_RELIABLE"
    # No cloud call happened.
    assert called["n"] == 0
    # Reps + quality score kept for the matched exercise.
    metrics = view["result"]["metrics"]
    assert any(m["id"] == "reps" and m["value"] == 3 for m in metrics)
    assert view["result"]["capabilities"]["quality_score"] == "unavailable"
    assert view["result"]["capability"]["reason_code"] == "NO_VALIDATED_SCORER"
    assert view["result"]["capabilities"]["repetitions"] == "available"


# --------------------------------------------------------------------------- #
# T03: Kinetics-400 candidate actually participates (R05)
# --------------------------------------------------------------------------- #

def test_t03_kinetics_front_raise_participates(migrated_engine, api, monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-test")
    capture: dict = {}
    coach = _coach(canonical_id="front_raise", identification="likely",
                   reason="双臂在身前抬起")
    _patch_vision(monkeypatch, coach, capture=capture)
    _patch_summary(monkeypatch)

    media_id = _seed_asset(migrated_engine, api.user_id)
    r = api.post("/api/v1/media/motion-analyses",
                 json={"media_id": media_id, "requested_exercise": "auto",
                       "consent_deepseek_frames": True},
                 headers={"Idempotency-Key": "t03-key-0001"})
    run_id = r.json()["analysis_id"]
    job_id = _processing_job(migrated_engine, run_id)
    receipt = _receipt(frames=3, pose_available=False, accepted=False)
    receipt["kinetics"] = {"candidates": [
        {"source_label": "front raises", "raw_score": 0.73, "score_type": "softmax"}
    ]}
    assert _complete(api, job_id, receipt).status_code == 200
    _drain_stages(migrated_engine, run_id, receipt=receipt)

    # The vision context actually carried the kinetics candidate + its source.
    ctx = capture["context"]
    kc = [c for c in ctx["kinetics_candidates"] if c["source_label"] == "front raises"]
    assert kc, ctx
    assert kc[0]["canonical_id"] == "front_raise"
    assert kc[0]["source"] == "kinetics"
    assert kc[0]["score_type"] == "softmax"

    view = api.get(f"/api/v1/media/motion-analyses/{run_id}").json()
    # F strips _meta from the user response; kinetics diag lives in internal snapshot.
    assert "_meta" not in view["result"]
    internal = _stored_result(migrated_engine, run_id)
    diag = internal["_meta"]["kinetics_candidates"]
    assert any(c["source_label"] == "front raises" and c["canonical_id"] == "front_raise"
               for c in diag)
    rec = view["result"]["recognition"]
    assert rec["canonical_id"] == "front_raise"
    assert rec["state"] == "likely"


# --------------------------------------------------------------------------- #
# T04: out-of-catalog category accepted via grounded vision (R06)
# --------------------------------------------------------------------------- #

def test_t04_open_category_bicep_curl_accepted(migrated_engine, api, monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-test")
    # Local candidate set is just {squat}; vision grounds a bicep curl instead.
    coach = _coach(canonical_id="bicep_curl", identification="identified",
                   reason="肘关节屈伸清楚", notes=[_note("frame:0")])
    _patch_vision(monkeypatch, coach)
    _patch_summary(monkeypatch)

    media_id = _seed_asset(migrated_engine, api.user_id)
    r = api.post("/api/v1/media/motion-analyses",
                 json={"media_id": media_id, "requested_exercise": "auto",
                       "consent_deepseek_frames": True},
                 headers={"Idempotency-Key": "t04-key-0001"})
    run_id = r.json()["analysis_id"]
    job_id = _processing_job(migrated_engine, run_id)
    receipt = _receipt(frames=3, pose_available=True, accepted=True, label="squat")
    assert _complete(api, job_id, receipt).status_code == 200
    _drain_stages(migrated_engine, run_id, receipt=receipt)

    view = api.get(f"/api/v1/media/motion-analyses/{run_id}").json()
    rec = view["result"]["recognition"]
    # Accepted as likely/identified - NOT dropped as unsupported, NOT unknown.
    assert rec["canonical_id"] == "bicep_curl", rec
    assert rec["state"] in ("identified", "likely"), rec
    assert rec["state"] != "unknown"


# --------------------------------------------------------------------------- #
# T05: category change does not inherit the old exercise's score/reps (§6.4)
# --------------------------------------------------------------------------- #

def test_t05_category_change_drops_old_score(migrated_engine, api, monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-test")
    coach = _coach(canonical_id="bicep_curl", identification="identified",
                   reason="肘关节屈伸清楚", notes=[_note("frame:0")])
    _patch_vision(monkeypatch, coach)
    _patch_summary(monkeypatch)

    media_id = _seed_asset(migrated_engine, api.user_id)
    r = api.post("/api/v1/media/motion-analyses",
                 json={"media_id": media_id, "requested_exercise": "auto",
                       "consent_deepseek_frames": True},
                 headers={"Idempotency-Key": "t05-key-0001"})
    run_id = r.json()["analysis_id"]
    job_id = _processing_job(migrated_engine, run_id)
    # Locally measured + scored as squat (reps=3, overall=78).
    receipt = _receipt(frames=3, pose_available=True, accepted=True, label="squat")
    assert _complete(api, job_id, receipt).status_code == 200
    _drain_stages(migrated_engine, run_id, receipt=receipt)

    view = api.get(f"/api/v1/media/motion-analyses/{run_id}").json()
    rec = view["result"]["recognition"]
    assert rec["canonical_id"] == "bicep_curl", rec
    # The squat reps/score must NOT hang on the curl result.
    metrics = view["result"]["metrics"]
    assert not any(m["id"] == "reps" for m in metrics), metrics
    assert not any(m["id"] == "overall" for m in metrics), metrics
    assert view["result"]["capabilities"]["quality_score"] == "unavailable"
    assert view["result"]["capabilities"]["repetitions"] == "unavailable"


# --------------------------------------------------------------------------- #
# Full chain with cloud vision: agreement + cache + no re-bill
# --------------------------------------------------------------------------- #

def test_full_chain_recognized_caches_and_polling_no_rebill(migrated_engine, api, monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-test")

    v_counter, s_counter = {"n": 0}, {"n": 0}
    coach = _coach(canonical_id="squat", identification="identified",
                   reason="下蹲幅度充分", notes=[_note("frame:0")])
    _patch_vision(monkeypatch, coach, counter=v_counter)
    _patch_summary(monkeypatch, text="下蹲节奏稳定，可再加深幅度。",
                   next_step="最低点稍作停顿。", counter=s_counter)

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
    _drain_stages(migrated_engine, run_id, receipt=receipt)

    view = api.get(f"/api/v1/media/motion-analyses/{run_id}").json()
    assert view["status"] == "completed", view
    rec = view["result"]["recognition"]
    assert rec["state"] == "identified" and rec["canonical_id"] == "squat"
    assert rec["review_status"] == "used"
    assert view["result"]["capabilities"]["quality_score"] == "unavailable"
    assert view["result"]["capability"]["reason_code"] == "NO_VALIDATED_SCORER"
    assert any(m["id"] == "reps" and m["value"] == 3 for m in view["result"]["metrics"])
    assert view["result"]["summary"]["source"] == "visual_coach"

    # Polling twice must NOT re-bill.
    api.get(f"/api/v1/media/motion-analyses/{run_id}")
    api.get(f"/api/v1/media/motion-analyses/{run_id}")
    assert v_counter["n"] == 1 and s_counter["n"] == 1, (v_counter, s_counter)

    # Second run on the SAME media with same params -> cache hit, still 1 call.
    r2 = api.post("/api/v1/media/motion-analyses",
                  json={"media_id": media_id, "requested_exercise": "auto",
                        "consent_deepseek_frames": True},
                  headers={"Idempotency-Key": "full-key-0002"})
    run2 = r2.json()["analysis_id"]
    job2 = _processing_job(migrated_engine, run2)
    assert _complete(api, job2, receipt).status_code == 200
    _drain_stages(migrated_engine, run2, receipt=receipt)
    assert v_counter["n"] == 1, v_counter
    assert s_counter["n"] == 1, s_counter

    with Session(migrated_engine) as db:
        n_vision = db.query(ProviderInvocation).filter(
            ProviderInvocation.operation == "deepseek_vision_review"
        ).count()
    assert n_vision == 1


def test_vision_failure_keeps_local_result_degrades_partial(migrated_engine, api, monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-test")
    import app.services.motion.vision_review as vr

    def boom(*a, **k):
        raise RuntimeError("deepseek_vision_timeout")

    monkeypatch.setattr(vr, "run_visual_review", boom, raising=False)

    media_id = _seed_asset(migrated_engine, api.user_id)
    r = api.post("/api/v1/media/motion-analyses",
                 json={"media_id": media_id, "requested_exercise": "auto",
                       "consent_deepseek_frames": True},
                 headers={"Idempotency-Key": "fail-key-0001"})
    run_id = r.json()["analysis_id"]
    job_id = _processing_job(migrated_engine, run_id)
    receipt = _receipt(frames=3, pose_available=True, accepted=True, label="squat")
    assert _complete(api, job_id, receipt).status_code == 200
    _drain_stages(migrated_engine, run_id, receipt=receipt)

    view = api.get(f"/api/v1/media/motion-analyses/{run_id}").json()
    assert view["status"] == "partial"
    rec = view["result"]["recognition"]
    assert rec["review_status"] == "unavailable"
    # R03: local squat is KEPT, not discarded as uncertain.
    assert rec["state"] == "identified" and rec["canonical_id"] == "squat"
    assert view["result"]["summary"]["degraded"] is True
    notice_copy = " ".join(item["text"] for item in view["result"]["notices"])
    assert "动作已完成基础分析，详细讲解暂不可用" in notice_copy
    assert "AI" not in notice_copy


def test_no_consent_never_calls_vision(migrated_engine, api, monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-test")
    import app.services.motion.vision_review as vr
    called = {"n": 0}

    def sneaky(*a, **k):
        called["n"] += 1
        raise AssertionError("must not call vision without consent")

    monkeypatch.setattr(vr, "run_visual_review", sneaky, raising=False)
    media_id = _seed_asset(migrated_engine, api.user_id)
    r = api.post("/api/v1/media/motion-analyses",
                 json={"media_id": media_id, "requested_exercise": "auto",
                       "consent_deepseek_frames": False},
                 headers={"Idempotency-Key": "noconsent-0001"})
    run_id = r.json()["analysis_id"]
    job_id = _processing_job(migrated_engine, run_id)
    receipt = _receipt(frames=3, pose_available=True, accepted=True, label="squat")
    assert _complete(api, job_id, receipt).status_code == 200
    _drain_stages(migrated_engine, run_id, receipt=receipt)
    assert called["n"] == 0
    view = api.get(f"/api/v1/media/motion-analyses/{run_id}").json()
    assert view["result"]["recognition"]["review_status"] == "skipped"
    # Local result still recognised without cloud.
    assert view["result"]["recognition"]["canonical_id"] == "squat"


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


def test_reanalyze_reuses_child_and_feedback_for_same_idempotency_key(migrated_engine, api):
    media_id = _seed_asset(migrated_engine, api.user_id)
    created = api.post(
        "/api/v1/media/motion-analyses",
        json={"media_id": media_id, "requested_exercise": "auto", "cloud_review_mode": "off"},
        headers={"Idempotency-Key": "reanalyze-parent-0001"},
    )
    assert created.status_code == 202, created.text
    parent_id = created.json()["analysis_id"]
    with Session(migrated_engine) as db:
        parent = db.get(MotionAnalysisRun, parent_id)
        parent.status = "completed"
        db.commit()

    url = f"/api/v1/media/motion-analyses/{parent_id}/reanalyze"
    headers = {"Idempotency-Key": "reanalyze-child-0001"}
    body = {"cloud_review_mode": "off", "exercise_hint": "pushup"}
    first = api.post(url, json=body, headers=headers)
    second = api.post(url, json=body, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json()["analysis_id"] == second.json()["analysis_id"]

    with Session(migrated_engine) as db:
        children = db.scalars(
            select(MotionAnalysisRun).where(MotionAnalysisRun.parent_run_id == parent_id)
        ).all()
        feedback = db.scalars(
            select(MotionUserFeedback).where(MotionUserFeedback.run_id == parent_id)
        ).all()
        assert len(children) == 1
        assert len(feedback) == 1
        assert feedback[0].corrected_label == "pushup"

    conflict = api.post(
        url,
        json={"cloud_review_mode": "off", "exercise_hint": "squat"},
        headers=headers,
    )
    assert conflict.status_code == 409
    with Session(migrated_engine) as db:
        children = db.scalars(
            select(MotionAnalysisRun).where(MotionAnalysisRun.parent_run_id == parent_id)
        ).all()
        assert len(children) == 1


def test_reanalyze_does_not_duplicate_a_preconfirmed_label(migrated_engine, api):
    media_id = _seed_asset(migrated_engine, api.user_id)
    created = api.post(
        "/api/v1/media/motion-analyses",
        json={"media_id": media_id, "requested_exercise": "auto", "cloud_review_mode": "off"},
        headers={"Idempotency-Key": "reanalyze-confirm-parent-01"},
    )
    assert created.status_code == 202, created.text
    parent_id = created.json()["analysis_id"]
    with Session(migrated_engine) as db:
        parent = db.get(MotionAnalysisRun, parent_id)
        parent.status = "completed"
        db.commit()

    confirmed = api.post(
        f"/api/v1/media/motion-analyses/{parent_id}/confirm-label",
        json={"canonical_id": "pushup"},
        headers={"Idempotency-Key": "reanalyze-confirm-label-01"},
    )
    assert confirmed.status_code == 200, confirmed.text
    child = api.post(
        f"/api/v1/media/motion-analyses/{parent_id}/reanalyze",
        json={"cloud_review_mode": "off", "exercise_hint": "pushup", "correction_confirmed": True},
        headers={"Idempotency-Key": "reanalyze-confirm-child-01"},
    )
    assert child.status_code == 200, child.text
    with Session(migrated_engine) as db:
        feedback = db.scalars(
            select(MotionUserFeedback).where(MotionUserFeedback.run_id == parent_id)
        ).all()
        assert len(feedback) == 1


def test_old_endpoints_still_registered_and_deprecated(api):
    spec = app.openapi()
    assert spec["paths"]["/api/v1/media/motion-jobs"]["post"].get("deprecated") is True
    assert spec["paths"]["/api/v1/media/kinetics-jobs"]["post"].get("deprecated") is True
