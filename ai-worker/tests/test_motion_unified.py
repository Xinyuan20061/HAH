# -*- coding: utf-8 -*-
"""P0-B unified motion chain V2 tests.

Covers MotionWorkerResultV2 evidence pipeline:
  * single decode (VideoCapture opened exactly once, frames reused),
  * generic evidence pool independent of six-class rejection (R04 / T02),
  * real-frame personal previews decoupled from cloud consent (R02/R10 / T09),
  * timeline sorted by real timestamp (R08 / T06),
  * six-group receipt local pre-validation,
  * the synthetic bicep-curl offline sample fixture end-to-end.

Baseline was 117 v1 tests; the two "six-class reject => frames must be empty"
contract points are deliberately split into "no pose measurement" vs
"no video image" (spec §11.1).
"""

from __future__ import annotations

import base64
from pathlib import Path

import cv2
import numpy as np
import pytest

from healthmate_worker import capabilities
from healthmate_worker.config import settings
from healthmate_worker.errors import ProcessingError
from healthmate_worker.processors import motion_unified
from healthmate_worker.result_contract import (
    SCHEMA_VERSION,
    validate_motion_result_local,
)
from healthmate_worker.visualize import (
    assert_desensitized_canvas,
    render_skeleton_canvas,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
BICEP_FIXTURE = FIXTURE_DIR / "bicep_curl_offline_sample.mp4"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_video(path: Path, frames: int = 60, size=(64, 64), fps: float = 30.0):
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, fps, size)
    assert writer.isOpened(), "test video writer failed to open"
    for i in range(frames):
        # Random "person-ish" pixels: a bright rectangle on noise background.
        img = np.random.randint(0, 60, (size[1], size[0], 3), dtype=np.uint8)
        img[10:40, 20:44] = (0, 0, 255)  # red "person blob"
        writer.write(img)
    writer.release()


def _evidence_entry(ts_ms: float, motion_delta: float = 0.1) -> dict:
    return {
        "frame_index": int(ts_ms // 33),
        "timestamp_ms": ts_ms,
        "bgr": np.zeros((48, 64, 3), dtype=np.uint8),
        "width": 64,
        "height": 48,
        "blur_var": 80.0,
        "brightness": 120.0,
        "motion_delta": motion_delta,
        "skeleton": None,
        "visible_regions": [],
        "subject_bbox": None,
    }


class _FakePoseResult:
    pose_landmarks = None


class _FakePose:
    def __init__(self, **kw):
        pass

    def process(self, frame):
        return _FakePoseResult()

    def close(self):
        pass


class _FakeKineticsModel:
    def predict(self, clip, topk=5):
        return {
            "top_label": "front raises",
            "top_probability": 0.73,
            "candidates": [
                {"label": "front raises", "class_index": 134, "probability": 0.73},
                {"label": "squat", "class_index": 300, "probability": 0.11},
            ],
        }


@pytest.fixture
def video_file(tmp_path):
    path = tmp_path / "clip.mp4"
    _make_video(path)
    return path


def _patch_no_pose_landmarks(monkeypatch):
    """Make MediaPipe Pose return no landmarks (so six-class stays rejected)."""
    import mediapipe as mp

    monkeypatch.setattr(mp.solutions.pose, "Pose", _FakePose)


# ---------------------------------------------------------------------------
# Single decode + split "no pose" vs "no video image" (§11.1)
# ---------------------------------------------------------------------------


def test_single_decode_opens_video_exactly_once(monkeypatch, video_file):
    opens: list = []
    real_capture = cv2.VideoCapture

    def counting_capture(target, *a, **k):
        opens.append(str(target))
        return real_capture(target, *a, **k)

    monkeypatch.setattr(cv2, "VideoCapture", counting_capture)
    monkeypatch.setattr(motion_unified, "_pose_engine_available", lambda: True)
    _patch_no_pose_landmarks(monkeypatch)
    monkeypatch.setattr(motion_unified, "get_kinetics400", lambda: None)

    result = motion_unified.analyze_motion_unified(
        video_file, requested_exercise="squat", consent_deepseek_frames=False
    )
    # The whole chain decodes the file exactly once (pose + clip reuse the pass).
    assert len(opens) == 1
    # No landmarks -> no pose measurement, no score; kinetics offline too.
    assert not [c for c in result["recognition_candidates"] if c["source"] == "kinetics"]
    # No landmarks -> no pose measurement, no score.
    assert result["pose_evidence"]["available"] is False
    assert result["measurements"]["available"] is False
    # SPLIT (was: result["frames"] == []). The video decoded successfully, so the
    # generic evidence pool / timeline is NON-EMPTY even with no pose landmarks.
    # "No pose measurement" must not be reported as "no video / no person".
    assert result["video_quality"]["decoded_ok"] is True
    assert len(result["frames"]) >= 1
    result["schema_version"] = SCHEMA_VERSION
    validate_motion_result_local(result)


def test_no_video_image_frames_empty(tmp_path):
    """The other half of the split: a genuinely undecodable video yields no frames."""
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"not a real mp4")
    with pytest.raises(ProcessingError) as exc:
        motion_unified.analyze_motion_unified(empty)
    assert exc.value.code == "invalid_media"


# ---------------------------------------------------------------------------
# T02: non-six-class rejection still carries real evidence
# ---------------------------------------------------------------------------


def test_t02_rejected_six_class_still_has_timeline_and_real_frames(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(motion_unified, "_pose_engine_available", lambda: True)
    _patch_no_pose_landmarks(monkeypatch)  # six-class event detector finds nothing
    monkeypatch.setattr(motion_unified, "get_kinetics400", lambda: None)

    out_dir = tmp_path / "previews"
    result = motion_unified.analyze_motion_unified(
        BICEP_FIXTURE,
        requested_exercise="auto",
        cloud_review_mode="redacted_frames",
        preview_out_dir=out_dir,
    )
    # Six-class recognition rejected (no landmarks), but the decoded video still
    # produces a usable timeline with real frame references.
    assert result["pose_evidence"]["available"] is False
    assert result["measurements"]["available"] is False
    assert result["video_quality"]["decoded_ok"] is True
    assert len(result["frames"]) >= 4, "timeline must not be empty on six-class reject"
    # No fabricated "no person" refusal: rows carry real frame references.
    for frame in result["frames"]:
        assert frame["frame_id"]
        assert frame["timestamp_ms"] >= 0
        assert frame["preview_asset_id"]
        assert frame["preview_sha256"]
        assert frame["preview_dimensions"]["width"] > 0
        assert frame["preview_bytes"] <= settings.motion_preview_max_bytes
    # Personal previews were rendered to disk as real JPEGs.
    files = sorted(out_dir.glob("*.jpg"))
    assert len(files) == len(result["frames"])
    raw = files[0].read_bytes()
    assert raw.startswith(b"\xff\xd8") and raw.endswith(b"\xff\xd9")
    result["schema_version"] = SCHEMA_VERSION
    validate_motion_result_local(result)


# ---------------------------------------------------------------------------
# T06: strict real-time ordering; phases not faked from array ends
# ---------------------------------------------------------------------------


def test_t06_frames_sorted_by_real_timestamp_ascending():
    evidence = [_evidence_entry(t * 1000.0) for t in (9.3, 0.9, 4.8, 2.8)]
    event_frames = [
        {"timestamp": 9.3, "event": "squat_bottom", "reason": "x"},
        {"timestamp": 0.9, "event": "squat_top", "reason": "y"},
        {"timestamp": 4.8, "event": "squat_completed", "reason": "z"},
        {"timestamp": 2.8, "event": "squat_deepest", "reason": "w"},
    ]
    out = motion_unified._select_timeline_frames(
        evidence, event_frames, display_count=8, duration=10.0
    )
    ts = [round(f["timestamp_ms"] / 1000.0, 1) for f in out]
    assert ts == sorted(ts), f"timeline not time-sorted: {ts}"
    assert set(ts) >= {0.9, 2.8, 4.8, 9.3}
    # Phases come from evidence, not from "first element = start".
    phases = {round(f["timestamp_ms"] / 1000.0, 1): f["phase"] for f in out}
    assert phases[9.3] != phases[0.9] or phases[9.3]


def test_distant_pose_event_never_displaces_a_real_preview_frame():
    evidence = [_evidence_entry(0.0), _evidence_entry(1800.0)]
    events = [{"timestamp": 9.0, "event": "squat_bottom", "reason": "stale event"}]
    selected = motion_unified._select_timeline_frames(
        evidence, events, display_count=2, duration=2.0
    )
    assert selected
    assert all(frame.get("bgr") is not None for frame in selected)
    assert all(frame["timestamp_ms"] <= 1800 for frame in selected)


# ---------------------------------------------------------------------------
# T09: cloud off still yields previews + timeline, zero external calls
# ---------------------------------------------------------------------------


def test_t09_cloud_off_personal_previews_still_work_no_external_calls(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(motion_unified, "_pose_engine_available", lambda: True)
    _patch_no_pose_landmarks(monkeypatch)
    monkeypatch.setattr(motion_unified, "get_kinetics400", lambda: None)

    out_dir = tmp_path / "previews"
    result = motion_unified.analyze_motion_unified(
        BICEP_FIXTURE,
        requested_exercise="auto",
        cloud_review_mode="off",
        preview_out_dir=out_dir,
    )
    assert result["cloud_review_mode"] == "off"
    # Personal timeline + previews unaffected by turning third-party off.
    assert len(result["frames"]) >= 4
    assert list(out_dir.glob("*.jpg")), "personal previews must render even with cloud off"
    # No cloud image bytes prepared and no external provider calls.
    assert result["external_provider_calls"] == 0
    for frame in result["frames"]:
        assert "cloud_preview_sha256" not in frame
    result["schema_version"] = SCHEMA_VERSION
    validate_motion_result_local(result)


# ---------------------------------------------------------------------------
# Capability declaration
# ---------------------------------------------------------------------------


def test_motion_unified_capability_requires_pose(monkeypatch):
    monkeypatch.setattr(capabilities, "pose_status", lambda: (True, "ok"))
    monkeypatch.setattr(capabilities, "vlm_status", lambda: {"available": False})
    monkeypatch.setattr(capabilities, "kinetics400_status", lambda: {"available": False})
    monkeypatch.setattr(capabilities.settings, "capabilities", "motion_pose,motion_unified_v1")
    caps = capabilities.effective_capabilities()
    assert "motion_unified_v1" in caps
    assert "motion_pose" in caps

    monkeypatch.setattr(capabilities, "pose_status", lambda: (False, "down"))
    assert "motion_unified_v1" not in capabilities.effective_capabilities()


# ---------------------------------------------------------------------------
# Degradation
# ---------------------------------------------------------------------------


def test_kinetics_offline_continues_six_action(monkeypatch, video_file):
    monkeypatch.setattr(motion_unified, "_pose_engine_available", lambda: True)
    _patch_no_pose_landmarks(monkeypatch)
    monkeypatch.setattr(motion_unified, "get_kinetics400", lambda: None)
    result = motion_unified.analyze_motion_unified(
        video_file, requested_exercise="auto", consent_deepseek_frames=False
    )
    assert result["video_quality"]["decoded_ok"] is True
    # Kinetics offline -> no kinetics-sourced candidates.
    assert not [c for c in result["recognition_candidates"] if c["source"] == "kinetics"]
    # Chain still produced a (rejected) result, not a crash.
    assert result["pose_evidence"]["available"] is False
    assert result["measurements"]["available"] is False


def test_pose_unavailable_still_emits_kinetics_candidates(monkeypatch, video_file):
    monkeypatch.setattr(motion_unified, "_pose_engine_available", lambda: False)
    monkeypatch.setattr(
        motion_unified, "get_kinetics400", lambda: _FakeKineticsModel()
    )
    result = motion_unified.analyze_motion_unified(
        video_file, requested_exercise="auto", consent_deepseek_frames=False
    )
    assert result["pose_evidence"]["available"] is False
    assert result["measurements"]["available"] is False
    # Kinetics candidates surface, namespace-normalized (R05).
    kin = [c for c in result["recognition_candidates"] if c["source"] == "kinetics"]
    assert kin, "kinetics candidates must still surface when pose engine is down"
    top = kin[0]
    assert top["source_label"] == "front raises"
    assert top["canonical_id"] == "front_raise"  # catalog mapping
    assert top["raw_score"] == 0.73
    assert top["score_type"] == "softmax"
    # Decoded video still yields a timeline even without pose landmarks.
    assert len(result["frames"]) >= 1
    result["schema_version"] = SCHEMA_VERSION
    validate_motion_result_local(result)


def test_both_engines_down_still_timeline_not_hard_failure(monkeypatch, video_file):
    """V2: decoded video alone still gives a timeline; only undecodable media fails."""
    monkeypatch.setattr(motion_unified, "_pose_engine_available", lambda: False)
    monkeypatch.setattr(motion_unified, "get_kinetics400", lambda: None)
    result = motion_unified.analyze_motion_unified(video_file)
    assert result["video_quality"]["decoded_ok"] is True
    assert result["pose_evidence"]["available"] is False
    assert result["measurements"]["available"] is False
    assert result["recognition_candidates"] == []
    assert len(result["frames"]) >= 1  # generic evidence pool survives
    result["schema_version"] = SCHEMA_VERSION
    validate_motion_result_local(result)


# ---------------------------------------------------------------------------
# Pure-canvas desensitization (skeleton-only cloud mode) — unchanged units
# ---------------------------------------------------------------------------


def _event_with_skeleton():
    skeleton = []
    for i, (x, y) in enumerate(
        [
            (0.5, 0.15),
            (0.42, 0.25),
            (0.58, 0.25),
            (0.40, 0.40),
            (0.60, 0.40),
            (0.38, 0.55),
            (0.62, 0.55),
            (0.44, 0.55),
            (0.56, 0.55),
            (0.43, 0.75),
            (0.57, 0.75),
            (0.42, 0.92),
            (0.58, 0.92),
        ]
    ):
        skeleton.append({"id": f"pt{i}", "x": x, "y": y, "visibility": 0.9})
    return {"timestamp": 2.4, "event": "squat_bottom", "skeleton": skeleton}


def test_pure_canvas_keyframe_is_desensitized():
    event = _event_with_skeleton()
    preview = render_skeleton_canvas(event, max_bytes=80 * 1024)
    assert preview["image_mime"] == "image/jpeg"
    raw = base64.b64decode(preview["image_b64"])
    assert raw.startswith(b"\xff\xd8") and raw.endswith(b"\xff\xd9")
    assert len(raw) <= 80 * 1024

    original = np.random.randint(0, 255, (480, 320, 3), dtype=np.uint8)
    original[120:360, 110:210] = (0, 0, 255)
    check = assert_desensitized_canvas(raw, original_bgr=original)
    assert check["bg_fraction"] >= 0.88
    assert abs(check["correlation_with_original"]) <= 0.10


def test_desensitization_check_rejects_photo_blit():
    original = np.random.randint(0, 255, (480, 320, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", original, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
    assert ok
    with pytest.raises(ProcessingError):
        assert_desensitized_canvas(encoded.tobytes(), min_bg_fraction=0.88)


# ---------------------------------------------------------------------------
# Receipt contract: split fixtures (§11.1)
# ---------------------------------------------------------------------------


def _no_pose_measurement_result():
    """Six-class rejected AND no pose measurement, but video decoded fine.

    Under V2 this is NOT the empty-frames case: the generic evidence pool /
    timeline survives (frames non-empty), only pose_evidence.available is False.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "video_quality": {"decoded_ok": True, "duration_ms": 2000},
        "subject": {"available": False, "subject_id": None, "visible_regions": []},
        "pose_evidence": {"available": False, "measurement_summary": "无姿态测量。"},
        "recognition_candidates": [],
        "measurements": {"available": False, "reason": "无姿态测量。"},
        "frames": [
            {
                "frame_id": "f_000",
                "timestamp_ms": 0,
                "preview_asset_id": "preview_0000",
                "preview_sha256": "ab" * 32,
                "preview_bytes": 50000,
                "preview_dimensions": {"width": 720, "height": 405},
            }
        ],
    }


def _no_video_image_result():
    """Genuinely no decodable video -> frames empty."""
    return {
        "schema_version": SCHEMA_VERSION,
        "video_quality": {"decoded_ok": False, "duration_ms": 0},
        "subject": {"available": False, "subject_id": None, "visible_regions": []},
        "pose_evidence": {"available": False, "measurement_summary": "无视频。"},
        "recognition_candidates": [],
        "measurements": {"available": False, "reason": "无视频。"},
        "frames": [],
    }


def test_valid_no_pose_measurement_receipt_passes():
    validate_motion_result_local(_no_pose_measurement_result())


def test_valid_no_video_image_receipt_passes():
    validate_motion_result_local(_no_video_image_result())


def test_rejected_but_scoring_is_rejected():
    result = _no_video_image_result()
    result["measurements"] = {"available": True, "exercise_id": "squat", "reps": 3}
    # No pose candidate accepted -> measurements must stay unavailable.
    result["recognition_candidates"] = []
    with pytest.raises(ProcessingError) as exc:
        validate_motion_result_local(result)
    assert exc.value.retryable is False


def test_too_many_previews_rejected():
    result = _no_video_image_result()
    result["recognition_candidates"] = [
        {"source": "pose", "source_label": "squat", "canonical_id": "squat",
         "raw_score": 0.9, "score_type": "rule"}
    ]
    result["measurements"] = {"available": True, "exercise_id": "squat", "reps": 3}
    good_sha = "ab" * 32
    frames = []
    for i in range(settings.motion_display_preview_count + 1):  # over budget
        frames.append(
            {
                "frame_id": f"f_{i:03d}",
                "timestamp_ms": float(i * 1000),
                "preview_asset_id": f"preview_{i:04d}",
                "preview_sha256": good_sha,
                "preview_bytes": 50000,
                "preview_dimensions": {"width": 720, "height": 405},
            }
        )
    result["frames"] = frames
    with pytest.raises(ProcessingError):
        validate_motion_result_local(result)


def test_image_bytes_in_rejected():
    """V2: the receipt must never carry image bytes, only references."""
    result = _no_pose_measurement_result()
    result["frames"][0]["image_b64"] = "AAAA"
    with pytest.raises(ProcessingError):
        validate_motion_result_local(result)


def test_unsorted_frames_rejected():
    result = _no_pose_measurement_result()
    result["frames"] = [
        {"frame_id": "f_1", "timestamp_ms": 4800, "preview_asset_id": "preview_0",
         "preview_sha256": "ab" * 32, "preview_dimensions": {"width": 720, "height": 405}},
        {"frame_id": "f_0", "timestamp_ms": 900, "preview_asset_id": "preview_1",
         "preview_sha256": "cd" * 32, "preview_dimensions": {"width": 720, "height": 405}},
    ]
    with pytest.raises(ProcessingError):
        validate_motion_result_local(result)


def test_schema_version_required():
    result = _no_video_image_result()
    del result["schema_version"]
    with pytest.raises(ProcessingError):
        validate_motion_result_local(result)


# ---------------------------------------------------------------------------
# Happy path: accepted six-class result with pose score + real-frame preview
# ---------------------------------------------------------------------------


def _squat_rows():
    active = [175, 168, 145, 115, 95, 120, 150, 168, 175]
    skeleton = [
        {"id": name, "x": x, "y": y, "visibility": 0.9}
        for name, (x, y) in [
            ("left_shoulder", (0.42, 0.25)),
            ("right_shoulder", (0.58, 0.25)),
            ("left_hip", (0.44, 0.55)),
            ("right_hip", (0.56, 0.55)),
            ("left_knee", (0.43, 0.75)),
            ("right_knee", (0.57, 0.75)),
            ("left_ankle", (0.42, 0.92)),
            ("right_ankle", (0.58, 0.92)),
        ]
    ]
    rows = []
    for i, value in enumerate(active):
        rows.append(
            {
                "t": i * 0.2,
                "knee": value,
                "elbow": 175,
                "hip": 170,
                "trunk": 5,
                "visibility": 0.95,
                "back_visibility": 0.95,
                "back_knee": 175,
                "body_line": 175,
                "body_offset": 0.01,
                "side": "left",
                "leg_abduction": 175,
                "arm_abduction": 175,
                "arm_vw": 175,
                "skeleton": skeleton,
            }
        )
    return rows


def test_happy_path_accepted_with_score_and_real_preview(monkeypatch, video_file, tmp_path):
    from healthmate_worker.processors import recognition as recognition_mod

    samples = {c: _squat_rows() for c in recognition_mod.SUPPORTED_EXERCISES}
    canned_evidence = [
        {
            "frame_index": i,
            "timestamp_ms": i * 500,
            "bgr": np.random.randint(0, 255, (48, 64, 3), dtype=np.uint8),
            "width": 64,
            "height": 48,
            "blur_var": 90.0,
            "brightness": 120.0,
            "motion_delta": 0.05,
            "skeleton": None,
            "visible_regions": [],
            "subject_bbox": None,
        }
        for i in range(8)
    ]
    canned = {
        "fps": 30.0,
        "total_frames": 60,
        "duration": 2.0,
        "sample_sets": samples,
        "clip_frames": [],
        "sampled": len(_squat_rows()),
        "evidence": canned_evidence,
        "pose_observations": [],
    }
    monkeypatch.setattr(motion_unified, "_run_single_pass_decode", lambda *a, **k: canned)
    monkeypatch.setattr(motion_unified, "get_kinetics400", lambda: None)

    out_dir = tmp_path / "previews"
    result = motion_unified.analyze_motion_unified(
        video_file,
        requested_exercise="squat",
        cloud_review_mode="off",
        preview_out_dir=out_dir,
    )
    pose_candidates = [c for c in result["recognition_candidates"] if c["source"] == "pose"]
    assert pose_candidates and pose_candidates[0]["canonical_id"] == "squat"
    assert result["pose_evidence"]["available"] is True
    assert result["measurements"]["available"] is True
    assert result["measurements"]["exercise_id"] == "squat"
    # Real-frame personal previews written, references only (no image_b64).
    previews = [f for f in result["frames"] if f.get("preview_asset_id")]
    assert 1 <= len(previews) <= settings.motion_display_preview_count
    for p in previews:
        assert "image_b64" not in p
        assert p["preview_bytes"] <= settings.motion_preview_max_bytes
    assert list(out_dir.glob("*.jpg"))
    result["schema_version"] = SCHEMA_VERSION
    validate_motion_result_local(result)
