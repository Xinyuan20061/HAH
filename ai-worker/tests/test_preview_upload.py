# -*- coding: utf-8 -*-
"""Worker preview byte-upload chain tests (all providers mocked, zero network)."""
from __future__ import annotations

from pathlib import Path

from healthmate_worker.processors import motion_unified
from healthmate_worker.processors.motion_unified import make_preview_uploader

from test_motion_unified import (  # noqa: E402
    BICEP_FIXTURE,
    _patch_no_pose_landmarks,
)


class _FakeBackendAPI:
    """Records the mint-then-PUT calls the real CloudAPI would make."""

    def __init__(self, fail_put: bool = False):
        self.fail_put = fail_put
        self.mint = None
        self.puts: list[tuple[str, bytes]] = []

    def request_preview_upload_urls(self, job_id, *, frame_ids, asset_prefix):
        self.mint = {
            "job_id": job_id,
            "frame_ids": list(frame_ids),
            "asset_prefix": asset_prefix,
        }
        return {
            "uploads": [
                {
                    "frame_id": fid,
                    "asset_id": f"remote_{fid}",
                    "upload_url": f"/api/v1/internal/motion-previews/upload/{fid}",
                }
                for fid in frame_ids
            ]
        }

    def put_preview(self, upload_url: str, body: bytes) -> None:
        if self.fail_put:
            raise RuntimeError("backend down")
        self.puts.append((upload_url, body))


def _run(monkeypatch, *, fail_put=False, with_uploader=True, tmp_path=None):
    monkeypatch.setattr(motion_unified, "_pose_engine_available", lambda: True)
    _patch_no_pose_landmarks(monkeypatch)
    monkeypatch.setattr(motion_unified, "get_kinetics400", lambda: None)

    api = _FakeBackendAPI(fail_put=fail_put)
    uploader = make_preview_uploader(api, job_id=999) if with_uploader else None
    return motion_unified.analyze_motion_unified(
        BICEP_FIXTURE,
        requested_exercise="auto",
        cloud_review_mode="off",
        preview_out_dir=tmp_path / "previews",
        preview_uploader=uploader,
    ), api


def test_upload_mint_then_put_and_asset_id_alignment(monkeypatch, tmp_path):
    result, api = _run(monkeypatch, tmp_path=tmp_path)
    frames = result["frames"]
    assert len(frames) >= 4

    # 1) Backend was asked for upload URLs with the rendered frame ids.
    assert api.mint is not None
    assert api.mint["job_id"] == 999
    assert set(api.mint["frame_ids"]) == {f["frame_id"] for f in frames}
    assert api.mint["asset_prefix"]

    # 2) Every rendered JPEG was PUT exactly once, bytes match the receipt hash.
    assert len(api.puts) == len(frames)
    by_frame = {f["frame_id"]: f for f in frames}
    for url, body in api.puts:
        fid = url.rsplit("/", 1)[-1]
        import hashlib

        assert hashlib.sha256(body).hexdigest() == by_frame[fid]["preview_sha256"]

    # 3) Receipt asset ids are the remote ones returned by the backend.
    for f in frames:
        assert f["preview_asset_id"] == f"remote_{f['frame_id']}"


def test_upload_failure_degrades_without_blocking_receipt(monkeypatch, tmp_path):
    result, api = _run(monkeypatch, fail_put=True, tmp_path=tmp_path)
    frames = result["frames"]
    assert len(frames) >= 4
    # Mint still happened, but every PUT raised -> no remote ids.
    assert api.mint is not None
    assert api.puts == []
    # Receipt still valid: frames kept their local staging asset_id.
    for f in frames:
        assert f["preview_asset_id"].startswith("preview_")
        assert "preview_sha256" in f


def test_no_uploader_stays_local(monkeypatch, tmp_path):
    result, api = _run(monkeypatch, with_uploader=False, tmp_path=tmp_path)
    assert api.mint is None
    for f in result["frames"]:
        assert f["preview_asset_id"].startswith("preview_")
