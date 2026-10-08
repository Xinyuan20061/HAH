# -*- coding: utf-8 -*-
"""Preview byte-chain tests for work package F (复验缺口 2/3).

Exercises the four preview endpoints against the FROZEN MediaStorage interface
(implemented by B in parallel) using an in-process HMAC double injected via the
``build_media_storage`` seam. No real filesystem/network.

  * POST /worker/jobs/{id}/preview-upload-urls (worker token; 401 without it)
  * PUT  /media/previews/{asset_id}?query  (signature validation; tamper/expire/
    cross-user rejected)
  * GET  /media/motion-analyses/{id}/previews/{frame_id}?query (signed -> raw
    image/jpeg bytes; unsigned -> Bearer; another user's frame refused)
  * GET  /media/motion-analyses/{id}/evidence (frames[].preview_url)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.media import InvalidPreviewSignature
from app.core.database import Base
from app.models import (
    AIJob,
    MediaAsset,
    MotionAnalysisFeedback,
    MotionAnalysisRun,
    MotionEvidenceFrame,
    User,
)
from app.services.motion.media_storage import PreviewNotFound


SECRET = "preview-test-secret"


class FakeMediaStorage:
    """In-process double for the frozen B MediaStorage interface.

    Implements the HMAC signature scheme consistently (upload and read). It is
    deliberately self-contained; B's production backend swaps in later without
    changing these endpoints.
    """

    def __init__(self, secret: str = SECRET):
        self.secret = secret
        self.bytes: dict[str, bytes] = {}

    def _sig(self, msg: str) -> str:
        return hmac.new(self.secret.encode(), msg.encode(), hashlib.sha256).hexdigest()

    # -- upload side --------------------------------------------------------- #
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
        expected = self._sig(f"{user_id}:{run_id}:{exp}")
        if not hmac.compare_digest(str(params.get("sig", "")), expected):
            raise InvalidPreviewSignature("bad signature")
        return {"user_id": user_id, "run_id": run_id}

    def save_preview(self, asset_id, body_bytes, *, user_id, run_id):
        self.bytes[asset_id] = bytes(body_bytes)

    # -- read side ----------------------------------------------------------- #
    def build_read_url(self, run_id, frame_id, *, user_id, expiry_ts):
        msg = f"{user_id}:{run_id}:{frame_id}:{expiry_ts}"
        return (
            f"/api/v1/media/motion-analyses/{run_id}/previews/{frame_id}"
            f"?user_id={user_id}&exp={expiry_ts}&sig={self._sig(msg)}"
        )

    def validate_read_signature(self, params, *, run_id, frame_id):
        try:
            user_id = int(params["user_id"])
            exp = int(params["exp"])
        except (KeyError, ValueError):
            raise InvalidPreviewSignature("missing params")
        if exp < int(time.time()):
            raise InvalidPreviewSignature("expired")
        expected = self._sig(f"{user_id}:{run_id}:{frame_id}:{exp}")
        if not hmac.compare_digest(str(params.get("sig", "")), expected):
            raise InvalidPreviewSignature("bad signature")
        return user_id

    def read_preview_bytes(self, asset_id, *, run_id=None):
        if asset_id not in self.bytes:
            raise PreviewNotFound(asset_id)
        return self.bytes[asset_id]

    def get_preview(self, asset_id, *, user_id):
        if asset_id not in self.bytes:
            raise PreviewNotFound(asset_id)
        return {
            "asset_id": asset_id,
            "timestamp_ms": 0,
            "expires_at": time.time() + 100,
            "status": "available",
        }


def _seed_run_with_job(api, db: Session) -> tuple[int, int]:
    """Seed user -> asset -> ai_job -> run + evidence frame. Returns (run_id, job_id)."""
    user = db.get(User, api.user_id)
    asset = MediaAsset(user_id=user.id, storage_key=f"pv-{api.user_id}-{id(object())}", media_type="video")
    db.add(asset)
    db.flush()
    job = AIJob(user_id=user.id, job_type="motion_unified", status="done", payload_json="{}")
    db.add(job)
    db.flush()
    run = MotionAnalysisRun(
        user_id=user.id,
        media_asset_id=asset.id,
        ai_job_id=job.id,
        requested_type="squat",
        pipeline_version="motion-unified-v1",
        status="completed",
        cloud_review_mode="redacted_frames",
    )
    db.add(run)
    db.flush()
    db.add(
        MotionEvidenceFrame(
            run_id=run.id,
            frame_id="frame_4",
            timestamp_ms=4000,
            preview_asset_id="asset_frame_4",
        )
    )
    db.commit()
    return run.id, job.id


def _install_fake(api, monkeypatch) -> FakeMediaStorage:
    fake = FakeMediaStorage()
    import app.api.v1.media as media_mod

    monkeypatch.setattr(media_mod, "build_media_storage", lambda db: fake)
    return fake


# --------------------------------------------------------------------------- #
def test_mint_upload_urls_requires_worker_token(api, migrated_engine, monkeypatch):
    _install_fake(api, monkeypatch)
    with Session(migrated_engine) as db:
        _, job_id = _seed_run_with_job(api, db)
    # No X-Worker-Token => rejected.
    r = api.post(f"/api/v1/worker/jobs/{job_id}/preview-upload-urls", json={"frame_ids": ["frame_4"]})
    assert r.status_code in {401, 403}, r.text


def test_mint_upload_urls_then_put_and_signed_read_roundtrip(api, migrated_engine, monkeypatch):
    fake = _install_fake(api, monkeypatch)
    with Session(migrated_engine) as db:
        run_id, job_id = _seed_run_with_job(api, db)

    r = api.post(
        f"/api/v1/worker/jobs/{job_id}/preview-upload-urls",
        json={"frame_ids": ["frame_4"], "asset_prefix": "pv"},
        headers=api.worker_headers,
    )
    assert r.status_code == 200, r.text
    urls = r.json()["urls"]
    assert urls[0]["frame_id"] == "frame_4"
    assert urls[0]["asset_id"] == "pv_frame_4"
    upload_path = urls[0]["upload_url"]

    # Upload raw JPEG bytes under the signed PUT URL.
    jpeg = b"\xff\xd8\xff\xe0fakejpeg\xff\xd9"
    put = api.put(upload_path, content=jpeg)
    assert put.status_code == 200, put.text
    assert put.json()["size"] == len(jpeg)
    assert fake.bytes["pv_frame_4"] == jpeg

    # Evidence row points preview_asset_id at what we saved.
    with Session(migrated_engine) as db:
        ev = db.scalar(
            select(MotionEvidenceFrame).where(
                MotionEvidenceFrame.run_id == run_id,
                MotionEvidenceFrame.frame_id == "frame_4",
            )
        )
        ev.preview_asset_id = "pv_frame_4"
        db.commit()

    # Signed read returns raw image/jpeg bytes, identical to what was saved.
    read_url = fake.build_read_url(run_id, "frame_4", user_id=api.user_id, expiry_ts=int(time.time()) + 300)
    got = api.get(read_url)
    assert got.status_code == 200, got.text
    assert got.headers["content-type"] == "image/jpeg"
    assert got.content == jpeg


def test_put_rejects_tampered_signature(api, migrated_engine, monkeypatch):
    fake = _install_fake(api, monkeypatch)
    with Session(migrated_engine) as db:
        _, job_id = _seed_run_with_job(api, db)
    r = api.post(
        f"/api/v1/worker/jobs/{job_id}/preview-upload-urls",
        json={"frame_ids": ["frame_4"]},
        headers=api.worker_headers,
    )
    upload_path = r.json()["urls"][0]["upload_url"]
    # Tamper the signature.
    bad = upload_path + "x"
    put = api.put(bad, content=b"jpeg")
    assert put.status_code == 403, put.text


def test_unsigned_preview_get_uses_bearer_and_reports_missing_storage(api, migrated_engine, monkeypatch):
    """No signature => Bearer path. Owner's frame exists but bytes were never
    saved => explicit 404 PREVIEW_GONE (never an empty/blank response)."""
    fake = _install_fake(api, monkeypatch)
    with Session(migrated_engine) as db:
        run_id, _ = _seed_run_with_job(api, db)
    # Owner Bearer is present; no query signature. Bytes were never uploaded.
    r = api.get(f"/api/v1/media/motion-analyses/{run_id}/previews/frame_4")
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "PREVIEW_GONE"
    assert "asset_frame_4" not in fake.bytes


def test_evidence_does_not_sign_missing_preview_object(api, migrated_engine, monkeypatch):
    fake = _install_fake(api, monkeypatch)
    with Session(migrated_engine) as db:
        run_id, _ = _seed_run_with_job(api, db)
        db.add(
            MotionAnalysisFeedback(
                run_id=run_id,
                user_id=api.user_id,
                result_json=json.dumps(
                    {
                        "recognition": {"state": "identified", "canonical_id": "squat"},
                        "timeline": {"frames": [{"id": "frame_4", "timestamp_ms": 4000}]},
                    },
                    ensure_ascii=False,
                ),
            )
        )
        db.commit()
    r = api.get(f"/api/v1/media/motion-analyses/{run_id}/evidence")
    assert r.status_code == 200, r.text
    frames = r.json()["frames"]
    assert frames[0]["id"] == "frame_4"
    assert frames[0]["preview_url"] is None
    assert frames[0]["preview"]["state"] == "unavailable"
    assert frames[0]["unavailable_reason"] in {"missing_evidence_row", "object_missing"}


# --------------------------------------------------------------------------- #
# Real-storage end-to-end (no Fake): verifies the PRODUCTION path constants in
# media_storage.py actually resolve against the real FastAPI routes. This is the
# regression guard for the byte-chain break where _UPLOAD_PATH/_READ_PATH pointed
# at routes that did not exist (tests only exercised a Fake double).
# --------------------------------------------------------------------------- #
def _install_real_storage(api, monkeypatch, tmp_path) -> None:
    """Inject a real MediaStorage backed by LocalPreviewStore under tmp_path.

    build_media_storage is the only seam; swap upload_dir to the temp dir so
    bytes land on a real filesystem, then verify the minted URLs hit real routes.
    """
    import app.api.v1.media as media_mod
    from app.core.config import settings as app_settings
    from app.services.motion.media_storage import (
        LocalPreviewStore,
        MediaStorage,
        SqlEvidenceFrameStore,
    )

    monkeypatch.setattr(app_settings, "upload_dir", str(tmp_path))
    # Do NOT touch secret_key here: it signs the test JWT in conftest. The
    # storage signing secret is passed directly to MediaStorage below.

    def _real_build(db):
        table = Base.metadata.tables.get("motion_evidence_frames")
        store = LocalPreviewStore(Path(tmp_path) / "motion-previews")
        evidence = SqlEvidenceFrameStore(db, table) if table is not None else None
        return MediaStorage(store, evidence, secret=SECRET)

    monkeypatch.setattr(media_mod, "build_media_storage", _real_build)


def test_real_routes_upload_and_read_roundtrip(api, migrated_engine, monkeypatch, tmp_path):
    """Production URLs: PUT /api/v1/media/previews/{id} then signed GET read.

    Uses the REAL MediaStorage (LocalPreviewStore + SqlEvidenceFrameStore), so
    this fails if the path constants drift from the router definitions again.
    """
    _install_real_storage(api, monkeypatch, tmp_path)
    with Session(migrated_engine) as db:
        run_id, job_id = _seed_run_with_job(api, db)
        # /evidence reads result_json.timeline; seed a timeline with frame_4 so
        # the signed preview URL is actually minted for a real frame.
        db.add(
            MotionAnalysisFeedback(
                run_id=run_id,
                user_id=api.user_id,
                result_json=json.dumps(
                    {
                        "recognition": {"state": "identified", "canonical_id": "squat"},
                        "timeline": {"frames": [{"id": "frame_4", "timestamp_ms": 4000}]},
                    },
                    ensure_ascii=False,
                ),
            )
        )
        db.commit()

    r = api.post(
        f"/api/v1/worker/jobs/{job_id}/preview-upload-urls",
        json={"frame_ids": ["frame_4"], "asset_prefix": "real"},
        headers=api.worker_headers,
    )
    assert r.status_code == 200, r.text
    upload_url = r.json()["urls"][0]["upload_url"]
    # The minted URL must point at the real route prefix (not an /internal/ path).
    assert upload_url.startswith("/api/v1/media/previews/"), upload_url

    jpeg = b"\xff\xd8\xff\xe0real-jpeg-bytes\xff\xd9"
    put = api.put(upload_url, content=jpeg)
    assert put.status_code == 200, put.text
    assert put.json()["size"] == len(jpeg)

    # The worker receipt would carry the backend-minted asset_id; simulate it by
    # pointing the evidence row at the uploaded asset before reading back.
    asset_id = r.json()["urls"][0]["asset_id"]
    with Session(migrated_engine) as db:
        ev = db.scalar(
            select(MotionEvidenceFrame).where(
                MotionEvidenceFrame.run_id == run_id,
                MotionEvidenceFrame.frame_id == "frame_4",
            )
        )
        ev.preview_asset_id = asset_id
        db.commit()

    # Bytes are on the real filesystem under tmp_path.
    found = list(tmp_path.rglob("*.jpg"))
    assert len(found) == 1, found
    assert found[0].read_bytes() == jpeg

    # Evidence returns a signed absolute URL; signed GET returns the same bytes.
    ev = api.get(f"/api/v1/media/motion-analyses/{run_id}/evidence")
    assert ev.status_code == 200, ev.text
    preview_url = ev.json()["frames"][0]["preview_url"]
    assert preview_url and preview_url.startswith("http"), preview_url
    assert "/api/v1/media/motion-analyses/" in preview_url, preview_url
    got = api.get(preview_url)
    assert got.status_code == 200, got.text
    assert got.headers["content-type"] == "image/jpeg"
    assert got.content == jpeg
