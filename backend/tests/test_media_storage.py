# -*- coding: utf-8 -*-
"""P0-B media storage signed-URL tests (offline, no real object store)."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse, parse_qs

import pytest

from app.services.motion.media_storage import (
    InMemoryEvidenceFrameStore,
    InvalidPreviewSignature,
    LocalPreviewStore,
    MediaStorage,
    PreviewNotFound,
)


def _make_storage(tmp_path: Path) -> MediaStorage:
    return MediaStorage(
        LocalPreviewStore(tmp_path / "previews"),
        InMemoryEvidenceFrameStore(),
        secret="test-secret",
    )


def _jpeg(n: int = 4096) -> bytes:
    return b"\xff\xd8\xff\xe0" + bytes((n % 251,) * n) + b"\xff\xd9"


def _qs(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


# -- upload URL roundtrip ---------------------------------------------------
def test_mint_and_validate_upload_url_roundtrip(tmp_path):
    ms = _make_storage(tmp_path)
    expiry = int(MediaStorage._now()) + 600
    url = ms.mint_upload_url(
        "preview_abc123", user_id=7, run_id=318, asset_prefix="pfx01",
        expiry_ts=expiry,
    )
    assert url.startswith("/api/v1/media/previews/preview_abc123?")
    params = _qs(url)
    assert params["asset_id"] == "preview_abc123"
    assert params["user_id"] == "7"
    assert params["run_id"] == "318"
    assert params["asset_prefix"] == "pfx01"
    assert params["exp"] == str(expiry)
    assert "sig" in params
    bound = ms.validate_upload_signature(params)
    assert bound == {"user_id": 7, "run_id": 318, "asset_id": "preview_abc123"}


def test_upload_signature_expired_rejected(tmp_path):
    ms = _make_storage(tmp_path)
    expired = int(MediaStorage._now()) - 10
    url = ms.mint_upload_url(
        "preview_x", user_id=7, run_id=318, asset_prefix="p", expiry_ts=expired,
    )
    with pytest.raises(InvalidPreviewSignature):
        ms.validate_upload_signature(_qs(url))


def test_upload_signature_tampered_rejected(tmp_path):
    ms = _make_storage(tmp_path)
    expiry = int(MediaStorage._now()) + 600
    url = ms.mint_upload_url(
        "preview_x", user_id=7, run_id=318, asset_prefix="p", expiry_ts=expiry,
    )
    params = _qs(url)
    params["user_id"] = "999"  # tamper after signing
    with pytest.raises(InvalidPreviewSignature):
        ms.validate_upload_signature(params)


def test_upload_missing_sig_rejected(tmp_path):
    ms = _make_storage(tmp_path)
    with pytest.raises(InvalidPreviewSignature):
        ms.validate_upload_signature(
            {"user_id": "7", "run_id": "318", "asset_id": "x",
             "asset_prefix": "p", "exp": "9999999999"}
        )


# -- save/read bytes roundtrip ---------------------------------------------
def test_save_then_read_preview_bytes_roundtrip(tmp_path):
    ms = _make_storage(tmp_path)
    body = _jpeg()
    ms.save_preview(
        "preview_abc123", body, user_id=7, run_id=318,
        frame_id="f_001", timestamp_ms=4800,
    )
    out = ms.read_preview_bytes("preview_abc123")
    assert out == body


def test_read_unknown_asset_not_found(tmp_path):
    ms = _make_storage(tmp_path)
    with pytest.raises(PreviewNotFound):
        ms.read_preview_bytes("preview_doesnotexist")


def test_oversized_preview_rejected(tmp_path):
    ms = _make_storage(tmp_path)
    with pytest.raises(ValueError):
        ms.save_preview("preview_big", b"x" * (100 * 1024 + 1), user_id=7, run_id=318)


# -- read URL roundtrip -----------------------------------------------------
def test_build_and_validate_read_url_roundtrip(tmp_path):
    ms = _make_storage(tmp_path)
    ms.save_preview(
        "preview_abc123", _jpeg(), user_id=7, run_id=318,
        frame_id="f_001", timestamp_ms=4800,
    )
    expiry = int(MediaStorage._now()) + 600
    url = ms.build_read_url(318, "f_001", user_id=7, expiry_ts=expiry)
    assert url.startswith("/api/v1/media/motion-analyses/318/previews/f_001?")
    params = _qs(url)
    assert params["user_id"] == "7"
    assert params["run_id"] == "318"
    assert params["frame_id"] == "f_001"
    uid = ms.validate_read_signature(params, run_id=318, frame_id="f_001")
    assert uid == 7


def test_read_url_rejects_wrong_user(tmp_path):
    ms = _make_storage(tmp_path)
    ms.save_preview("preview_abc123", _jpeg(), user_id=7, run_id=318, frame_id="f_001")
    expiry = int(MediaStorage._now()) + 600
    url = ms.build_read_url(318, "f_001", user_id=7, expiry_ts=expiry)
    params = _qs(url)
    # Another user forges a signature that does not match.
    params["user_id"] = "999"
    with pytest.raises(InvalidPreviewSignature):
        ms.validate_read_signature(params, run_id=318, frame_id="f_001")


def test_read_url_expired_rejected(tmp_path):
    ms = _make_storage(tmp_path)
    ms.save_preview("preview_abc123", _jpeg(), user_id=7, run_id=318, frame_id="f_001")
    expired = int(MediaStorage._now()) - 10
    url = ms.build_read_url(318, "f_001", user_id=7, expiry_ts=expired)
    with pytest.raises(InvalidPreviewSignature):
        ms.validate_read_signature(_qs(url), run_id=318, frame_id="f_001")


def test_build_read_url_unknown_frame_not_found(tmp_path):
    ms = _make_storage(tmp_path)
    with pytest.raises(PreviewNotFound):
        ms.build_read_url(318, "f_nope", user_id=7, expiry_ts=9999999999)
