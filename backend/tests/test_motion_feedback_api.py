# -*- coding: utf-8 -*-
"""T11 backend-side tests for work package F (contract §6 / spec §11.1).

Covers:
  * confirm-label: a REAL string canonical_id (or novel Chinese description) is
    required; the old ``user_confirmed`` placeholder and empty selection are
    rejected (R13: label is a string, never a boolean).
  * feedback: closed kind vocabulary, frame must belong to THIS run, durable
    motion_user_feedback row independent of the result snapshot.
  * previews / admin diagnostics / capabilities smoke checks.

Uses the real request/response layer with seeded rows; storage and external
models are never touched (no network, no on-disk previews).
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    MediaAsset,
    MotionAnalysisFeedback,
    MotionAnalysisRun,
    MotionUserFeedback,
    User,
)


def _seed_run(api, db: Session, *, result: dict | None = None) -> int:
    user = db.get(User, api.user_id)
    asset = MediaAsset(
        user_id=user.id,
        storage_key=f"t11-{api.user_id}-{id(object())}",
        media_type="video",
    )
    db.add(asset)
    db.flush()
    run = MotionAnalysisRun(
        user_id=user.id,
        media_asset_id=asset.id,
        requested_type="squat",
        pipeline_version="motion-unified-v1",
        status="completed",
        cloud_review_mode="redacted_frames",
    )
    db.add(run)
    db.flush()
    if result is not None:
        db.add(
            MotionAnalysisFeedback(
                run_id=run.id,
                user_id=user.id,
                result_json=json.dumps(result, ensure_ascii=False),
            )
        )
    db.commit()
    return run.id


_V2_RESULT = {
    "analysis_id": 0,
    "status": "completed",
    "pipeline_version": "motion-unified-v1",
    "result_version": 1,
    "recognition": {
        "state": "identified",
        "canonical_id": "squat",
        "display_name": "深蹲",
        "reason": "motion_matches",
        "source": "vision",
    },
    "capabilities": {
        "recognition": True,
        "timeline": True,
        "coaching": True,
        "repetitions": False,
        "quality_score": False,
    },
    "summary": {"text": "下蹲节奏稳定。", "source": "rule", "degraded": False},
    "metrics": [],
    "timeline": {
        "duration_ms": 5000,
        "frames": [
            {"id": "frame_1", "timestamp_ms": 1000, "phase": "descend", "observation": "开始下蹲"},
            {"id": "frame_4", "timestamp_ms": 4000, "phase": "stand", "observation": "起身"},
        ],
    },
    "notices": [],
    "_meta": {"reason_code": "INTERNAL", "kinetics_candidates": [{"label": "x"}]},
}


# --------------------------------------------------------------------------- #
# confirm-label (R13)
# --------------------------------------------------------------------------- #
def test_confirm_label_accepts_real_string_canonical_id(api, migrated_engine):
    with Session(migrated_engine) as db:
        run_id = _seed_run(api, db, result=dict(_V2_RESULT))
    r = api.post(f"/api/v1/media/motion-analyses/{run_id}/confirm-label", json={"canonical_id": "squat"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["score_fabricated"] is False
    assert body["canonical_id"] == "squat"


def test_confirm_label_rejects_placeholder_user_confirmed(api, migrated_engine):
    with Session(migrated_engine) as db:
        run_id = _seed_run(api, db, result=dict(_V2_RESULT))
    r = api.post(
        f"/api/v1/media/motion-analyses/{run_id}/confirm-label",
        json={"canonical_id": "user_confirmed"},
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] in {"INVALID_LABEL_ID", "NO_LABEL_SELECTED"}


def test_confirm_label_rejects_empty_selection(api, migrated_engine):
    with Session(migrated_engine) as db:
        run_id = _seed_run(api, db, result=dict(_V2_RESULT))
    r = api.post(f"/api/v1/media/motion-analyses/{run_id}/confirm-label", json={})
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "NO_LABEL_SELECTED"


def test_confirm_label_rejects_unknown_catalog_id(api, migrated_engine):
    with Session(migrated_engine) as db:
        run_id = _seed_run(api, db, result=dict(_V2_RESULT))
    r = api.post(
        f"/api/v1/media/motion-analyses/{run_id}/confirm-label",
        json={"canonical_id": "not_a_real_action_xyz"},
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "UNKNOWN_CATALOG_ID"


def test_confirm_label_accepts_novel_chinese_description(api, migrated_engine):
    with Session(migrated_engine) as db:
        run_id = _seed_run(api, db, result=dict(_V2_RESULT))
    r = api.post(
        f"/api/v1/media/motion-analyses/{run_id}/confirm-label",
        json={"novel_label_zh": "保加利亚分腿蹲"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["novel_label_zh"] == "保加利亚分腿蹲"


# --------------------------------------------------------------------------- #
# feedback: kind enum + frame membership + durable row
# --------------------------------------------------------------------------- #
def test_feedback_writes_durable_row_associating_named_frame(api, migrated_engine):
    with Session(migrated_engine) as db:
        run_id = _seed_run(api, db, result=dict(_V2_RESULT))
    r = api.post(
        f"/api/v1/media/motion-analyses/{run_id}/feedback",
        json={"kind": "wrong_label", "frame_id": "frame_4", "corrected_label": "squat", "comment": "标错了"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["used_as_training_label"] is False
    with Session(migrated_engine) as db:
        rows = db.scalars(
            select(MotionUserFeedback).where(MotionUserFeedback.run_id == run_id)
        ).all()
        assert len(rows) == 1
        assert rows[0].kind == "wrong_label"
        assert rows[0].frame_id == "frame_4"
        assert rows[0].corrected_label == "squat"


def test_feedback_rejects_invalid_kind(api, migrated_engine):
    with Session(migrated_engine) as db:
        run_id = _seed_run(api, db, result=dict(_V2_RESULT))
    r = api.post(
        f"/api/v1/media/motion-analyses/{run_id}/feedback",
        json={"kind": "bogus_kind"},
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "INVALID_KIND"


def test_feedback_rejects_frame_not_belonging_to_run(api, migrated_engine):
    with Session(migrated_engine) as db:
        run_id = _seed_run(api, db, result=dict(_V2_RESULT))
    r = api.post(
        f"/api/v1/media/motion-analyses/{run_id}/feedback",
        json={"kind": "wrong_frame", "frame_id": "frame_999"},
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "FRAME_NOT_IN_RUN"


def test_feedback_legacy_boolean_shape_still_accepted(api, migrated_engine):
    with Session(migrated_engine) as db:
        run_id = _seed_run(api, db, result=dict(_V2_RESULT))
    # R13 legacy: old frontend sent useful=false + label_correction (a string).
    r = api.post(
        f"/api/v1/media/motion-analyses/{run_id}/feedback",
        json={"useful": False, "label_correction": "squat", "comment": "关键帧不准"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["used_as_training_label"] is False
    with Session(migrated_engine) as db:
        rows = db.scalars(
            select(MotionUserFeedback).where(MotionUserFeedback.run_id == run_id)
        ).all()
        assert rows[0].kind == "wrong_label"


# --------------------------------------------------------------------------- #
# user view strips internal bookkeeping; diagnostics gated; capabilities shape
# --------------------------------------------------------------------------- #
def test_user_result_view_strips_meta(api, migrated_engine):
    with Session(migrated_engine) as db:
        run_id = _seed_run(api, db, result=dict(_V2_RESULT))
    r = api.get(f"/api/v1/media/motion-analyses/{run_id}")
    assert r.status_code == 200, r.text
    result = r.json().get("result") or {}
    assert "_meta" not in result
    assert result["recognition"]["canonical_id"] == "squat"


def test_admin_diagnostics_rejects_normal_user(api, migrated_engine):
    with Session(migrated_engine) as db:
        run_id = _seed_run(api, db, result=dict(_V2_RESULT))
    r = api.get(f"/api/v1/admin/motion-analyses/{run_id}/diagnostics")
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "ADMIN_ONLY"


def test_capabilities_endpoint_reads_catalog(api):
    r = api.get("/api/v1/fitness/motion-capabilities")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["catalog_version"]
    assert isinstance(body["actions"], list)
    assert body["actions"] and "capabilities" in body["actions"][0]
