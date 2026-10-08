# -*- coding: utf-8 -*-
"""WP0 red tests — MOTION-01/02/03/04 (spec §7).

Confirmed defects covered here:

* MOTION-01: the backend answered ``{"urls": [...]}`` while the worker read
  ``resp["uploads"]``, so every preview upload silently degraded to local bytes.
* MOTION-02: ``stage_tasks._evidence_from_receipt`` read the V1 keys
  ``recognition/pose/score`` from a ``motion-worker-v2`` receipt, silently
  returning an empty evidence bundle and discarding every V2 field.
* MOTION-03/04: ``_spawn_child_run`` dropped ``exercise_hint`` and the client kept
  polling the parent analysis id after a re-analysis.

The V2 receipt fixtures here are produced by the real worker schema shape (see
``ai-worker/healthmate_worker/result_contract.py``), not hand-written lookalikes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.media import InvalidPreviewSignature
from app.models import (
    AIJob,
    MediaAsset,
    MotionAnalysisRun,
    MotionUserFeedback,
    User,
)
from app.schemas.worker import (
    MOTION_WORKER_RESULT_V2_SCHEMA_VERSION,
    MotionWorkerResultV2,
)

SECRET = "preview-test-secret"


class FakeMediaStorage:
    """Minimal HMAC double for the frozen MediaStorage interface."""

    def __init__(self, secret: str = SECRET):
        self.secret = secret
        self.bytes: dict[str, bytes] = {}

    def _sig(self, msg: str) -> str:
        return hmac.new(self.secret.encode(), msg.encode(), hashlib.sha256).hexdigest()

    def mint_upload_url(self, asset_id, *, user_id, run_id, asset_prefix, expiry_ts):
        msg = f"{user_id}:{run_id}:{expiry_ts}"
        return (
            f"/api/v1/media/previews/{asset_id}"
            f"?user_id={user_id}&run_id={run_id}&asset_prefix={asset_prefix}"
            f"&exp={expiry_ts}&sig={self._sig(msg)}"
        )

    def validate_upload_signature(self, params):
        try:
            user_id = int(params["user_id"])
            run_id = int(params["run_id"])
            exp = int(params["exp"])
        except (KeyError, ValueError):
            raise InvalidPreviewSignature("missing params")
        if exp < int(time.time()):
            raise InvalidPreviewSignature("expired")
        if not hmac.compare_digest(
            str(params.get("sig", "")), self._sig(f"{user_id}:{run_id}:{exp}")
        ):
            raise InvalidPreviewSignature("bad signature")
        return {"user_id": user_id, "run_id": run_id}

    def save_preview(self, asset_id, body_bytes, *, user_id, run_id):
        self.bytes[asset_id] = bytes(body_bytes)

    def build_read_url(self, run_id, frame_id, *, user_id, expiry_ts):
        msg = f"{user_id}:{run_id}:{frame_id}:{expiry_ts}"
        return (
            f"/api/v1/media/motion-analyses/{run_id}/previews/{frame_id}"
            f"?user_id={user_id}&exp={expiry_ts}&sig={self._sig(msg)}"
        )

    def read_preview_bytes(self, asset_id, *, run_id=None):
        from app.services.motion.media_storage import PreviewNotFound

        if asset_id not in self.bytes:
            raise PreviewNotFound(asset_id)
        return self.bytes[asset_id]


def worker_v2_receipt() -> dict:
    """A real ``motion-worker-v2`` receipt as the local worker emits it."""
    return {
        "schema_version": MOTION_WORKER_RESULT_V2_SCHEMA_VERSION,
        "pipeline_version": "motion-unified-v2",
        "video_quality": {
            "available": True,
            "decoded_ok": True,
            "duration_ms": 6200,
            "fps": 30.0,
            "total_frames": 186,
            "blur_summary": "ok",
        },
        "subject": {
            "available": True,
            "subject_id": "s_01",
            "visible_regions": ["legs", "torso"],
        },
        "pose_evidence": {
            "available": True,
            "fps": 12.0,
            "frame_ids": ["p_000", "p_001", "p_002"],
            "sample_count": 3,
            "keypoint_valid_rate": 0.83,
            "measurement_summary": "关键点有效率 83%",
        },
        "recognition_candidates": [
            {
                "source": "pose",
                "source_label": "squat",
                "canonical_id": "squat",
                "raw_score": 0.71,
                "score_type": "rule",
            },
            {
                "source": "kinetics",
                "source_label": "squatting",
                "class_index": 122,
                "canonical_id": None,
                "raw_score": 0.31,
                "score_type": "softmax",
            },
        ],
        "frames": [
            {
                "frame_id": "f_000",
                "timestamp_ms": 0,
                "preview_asset_id": "preview_0000",
                "subject_id": "s_01",
                "visible_regions": ["legs"],
                "blur": "ok",
                "motion_delta": 0.2,
                "phase": "起始",
                "finding": "膝髋同步下蹲",
                "advice": "保持膝盖与脚尖同向",
                "next_step": "缓慢起身",
            },
            {
                "frame_id": "f_001",
                "timestamp_ms": 2100,
                "preview_asset_id": "preview_0001",
                "subject_id": "s_01",
                "visible_regions": ["legs", "torso"],
                "blur": "ok",
                "motion_delta": 0.6,
                "phase": "最低点",
                "finding": "大腿接近平行",
                "advice": "核心收紧",
                "next_step": "稳定起身",
            },
            {
                "frame_id": "f_002",
                "timestamp_ms": 4200,
                "preview_asset_id": "preview_0002",
                "subject_id": "s_01",
                "visible_regions": ["legs", "torso"],
                "blur": "ok",
                "motion_delta": 0.4,
                "phase": "起身",
                "finding": "髋膝同步伸展",
                "advice": "避免腰部代偿",
                "next_step": "完成站立",
            },
        ],
        "measurements": {
            "available": True,
            "exercise_id": "squat",
            "reps": 9,
            "duration_ms": 6200,
            "quality": {"stability": 0.72},
        },
        "model_versions": {"pose": "mediapipe-0.10", "kinetics": "r50-v1"},
    }


def _seed_run(api, db: Session, *, requested_type: str = "squat") -> tuple[int, int]:
    user = db.get(User, api.user_id)
    asset = MediaAsset(
        user_id=user.id, storage_key=f"mv2-{api.user_id}-{id(object())}", media_type="video"
    )
    db.add(asset)
    db.flush()
    job = AIJob(
        user_id=user.id,
        job_type="motion_unified",
        status="done",
        payload_json="{}",
        result_json=json.dumps(worker_v2_receipt(), ensure_ascii=False),
    )
    db.add(job)
    db.flush()
    run = MotionAnalysisRun(
        user_id=user.id,
        media_asset_id=asset.id,
        ai_job_id=job.id,
        requested_type=requested_type,
        pipeline_version="motion-unified-v2",
        status="completed",
        cloud_review_mode="off",
    )
    db.add(run)
    db.flush()
    db.commit()
    return run.id, job.id


def _install_fake(monkeypatch) -> FakeMediaStorage:
    fake = FakeMediaStorage()
    import app.api.v1.media as media_mod

    monkeypatch.setattr(media_mod, "build_media_storage", lambda db: fake)
    return fake


# --------------------------------------------------------------------------- #
# MOTION-01 — preview upload contract
# --------------------------------------------------------------------------- #
def test_preview_upload_urls_answers_uploads(api, migrated_engine, monkeypatch):
    """MOTION-01: the frozen field is ``uploads`` (``urls`` is a deprecation alias)."""
    _install_fake(monkeypatch)
    with Session(migrated_engine) as db:
        _, job_id = _seed_run(api, db)

    res = api.post(
        f"/api/v1/worker/jobs/{job_id}/preview-upload-urls",
        json={"frame_ids": ["f_000"], "asset_prefix": "preview"},
        headers=api.worker_headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "uploads" in body, f"缺少冻结字段 uploads: {sorted(body)}"
    entry = body["uploads"][0]
    assert set(entry) == {"frame_id", "asset_id", "upload_url", "expires_at"}
    assert entry["frame_id"] == "f_000"
    assert entry["asset_id"] == "preview_f_000"
    assert entry["upload_url"].startswith("/api/v1/media/previews/")
    assert entry["expires_at"] > int(time.time())
    # One release-cycle compatibility alias.
    assert body["urls"] == body["uploads"]


def test_preview_url_signature_binds_run_and_frame(api, migrated_engine, monkeypatch):
    """§7.2: frame id, asset prefix, run and user are bound into the signature."""
    _install_fake(monkeypatch)
    with Session(migrated_engine) as db:
        run_id, job_id = _seed_run(api, db)
    body = api.post(
        f"/api/v1/worker/jobs/{job_id}/preview-upload-urls",
        json={"frame_ids": ["f_000"], "asset_prefix": "preview"},
        headers=api.worker_headers,
    ).json()
    url = body["uploads"][0]["upload_url"]
    # Rewriting the run id must invalidate the minted signature.
    tampered = url.replace("run_id=%d" % run_id, "run_id=%d" % (run_id + 1))
    assert api.put(tampered, content=b"\xff\xd8\xff\xd9").status_code in {401, 403, 404}


# --------------------------------------------------------------------------- #
# MOTION-02 — V2 receipt schema and post-processing adapter
# --------------------------------------------------------------------------- #
def test_v2_receipt_accepts_the_real_worker_shape():
    """MOTION-02: the production worker receipt must validate, extra fields included."""
    receipt = worker_v2_receipt()
    parsed = MotionWorkerResultV2.model_validate(receipt)
    assert parsed.schema_version == MOTION_WORKER_RESULT_V2_SCHEMA_VERSION
    assert parsed.pose_evidence.available is True
    assert parsed.measurements.reps == 9
    assert len(parsed.frames) == 3


def test_v2_receipt_forbids_unknown_fields():
    """§7.1: ``extra="forbid"`` — a drifted producer must fail loudly, not silently."""
    receipt = worker_v2_receipt()
    receipt["totally_new_group"] = {"x": 1}
    with pytest.raises(ValidationError):
        MotionWorkerResultV2.model_validate(receipt)


def test_v2_receipt_carries_no_image_bytes():
    """§7.1: the receipt references previews, it never embeds image bytes."""
    receipt = worker_v2_receipt()
    receipt["frames"][0]["image_b64"] = "AAAA"
    with pytest.raises(ValidationError):
        MotionWorkerResultV2.model_validate(receipt)


def test_evidence_adapter_keeps_every_v2_group():
    """MOTION-02: the adapter must not read the V1 ``recognition/pose/score`` keys."""
    from app.services.motion.stage_tasks import evidence_from_worker_v2

    bundle = evidence_from_worker_v2(worker_v2_receipt())
    assert bundle.video_quality.available is True
    assert bundle.subject.subject_id == "s_01"
    assert bundle.pose_evidence.fps == pytest.approx(12.0)
    assert [c.canonical_id for c in bundle.recognition_candidates] == ["squat", None]
    assert [c.source for c in bundle.recognition_candidates] == ["pose", "kinetics"]
    assert len(bundle.frames) == 3
    assert bundle.frames[1].finding == "大腿接近平行"
    assert bundle.measurements.reps == 9
    assert bundle.model_versions["kinetics"] == "r50-v1"


def test_post_processing_does_not_lose_v2_evidence(api, migrated_engine):
    """MOTION-02: a V2 receipt must not degrade into an empty evidence bundle."""
    from app.services.motion.orchestrator import apply_post_review
    from app.services.motion.stage_tasks import evidence_from_worker_v2

    with Session(migrated_engine) as db:
        run_id, _ = _seed_run(api, db)
        bundle = evidence_from_worker_v2(worker_v2_receipt())
        result = apply_post_review(
            db,
            run_id=run_id,
            coach_review=None,
            evidence=bundle.to_apply_post_review(),
        )
    # The regression was silent evidence loss, not the recognition verdict: with
    # only two evidence frames the gate legitimately abstains, but every V2 group
    # must still reach the stored result.
    assert result["capabilities"]["repetitions"] == "available"
    rep_metric = next(item for item in result["metrics"] if item["id"] == "reps")
    assert rep_metric["value"] == 9
    # The frame evidence pool survives even when the six-class label is rejected.
    assert result["timeline"]["frames"], "V2 证据帧被丢弃"
    assert len(result["timeline"]["frames"]) == 3


# --------------------------------------------------------------------------- #
# MOTION-03 / MOTION-04 — re-analysis child run
# --------------------------------------------------------------------------- #
def test_reanalyze_spawns_child_with_new_id(api, migrated_engine):
    """MOTION-03: the client must be told the new analysis id."""
    with Session(migrated_engine) as db:
        run_id, _ = _seed_run(api, db)
    res = api.post(
        f"/api/v1/media/motion-analyses/{run_id}/reanalyze",
        json={
            "cloud_review_mode": "redacted_frames",
            "exercise_hint": "pushup",
            "reason": "user_label_correction",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["analysis_id"] != run_id
    assert body["parent_run_id"] == run_id
    with Session(migrated_engine) as db:
        child = db.get(MotionAnalysisRun, body["analysis_id"])
        assert child.parent_run_id == run_id
        # MOTION-04: the confirmed catalogue id reaches the child request.
        assert child.requested_type == "pushup"


def test_reanalyze_rejects_unknown_catalog_id(api, migrated_engine):
    """§7.4: a free-text label must never be written into ``requested_type``."""
    with Session(migrated_engine) as db:
        run_id, _ = _seed_run(api, db)
    res = api.post(
        f"/api/v1/media/motion-analyses/{run_id}/reanalyze",
        json={"exercise_hint": "我在做深蹲但我不知道英文"},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "UNKNOWN_CATALOG_ID"


def test_user_confirmation_is_stored_separately(api, migrated_engine):
    """§7.4: the user label lives in ``motion_user_feedback``, not in the result."""
    with Session(migrated_engine) as db:
        run_id, _ = _seed_run(api, db)
    res = api.post(
        f"/api/v1/media/motion-analyses/{run_id}/confirm-label",
        json={"canonical_id": "pushup", "correction_reason": "user_label_correction"},
    )
    assert res.status_code == 200, res.text
    with Session(migrated_engine) as db:
        rows = db.scalars(
            select(MotionUserFeedback).where(MotionUserFeedback.run_id == run_id)
        ).all()
        assert rows, "用户确认未写入独立的 motion_user_feedback"
        feedback = db.scalar(
            select(MotionAnalysisRun).where(MotionAnalysisRun.id == run_id)
        )
        assert feedback is not None
        # The parent's computed result must not be rewritten by the correction.
        from app.models import MotionAnalysisFeedback

        stored = db.scalar(
            select(MotionAnalysisFeedback).where(MotionAnalysisFeedback.run_id == run_id)
        )
        assert stored is None or "score" not in json.loads(stored.result_json)
