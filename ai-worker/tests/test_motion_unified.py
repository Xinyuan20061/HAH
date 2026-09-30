# -*- coding: utf-8 -*-
"""P0-B unified motion chain tests.

Covers:
  * single decode (VideoCapture opened exactly once, frames reused),
  * motion_unified_v1 capability declaration,
  * Kinetics offline degradation,
  * pose-engine-unavailable degradation,
  * both-engines-down hard failure,
  * pure-canvas skeleton keyframe desensitization (pixel-level),
  * MotionWorkerResultV1 local pre-validation (valid + invalid fixtures).
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
            "top_label": "squat",
            "top_probability": 0.72,
            "candidates": [
                {"label": "squat", "class_index": 300, "probability": 0.72},
                {"label": "tai chi", "class_index": 346, "probability": 0.11},
            ],
        }


@pytest.fixture
def video_file(tmp_path):
    path = tmp_path / "clip.mp4"
    _make_video(path)
    return path


def _patch_no_pose_landmarks(monkeypatch):
    """Make MediaPipe Pose return no landmarks (so six-class stays empty)."""
    import mediapipe as mp

    monkeypatch.setattr(mp.solutions.pose, "Pose", _FakePose)


# ---------------------------------------------------------------------------
# Single decode
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
    # Kinetics weights offline -> chain must still complete six-action path.
    monkeypatch.setattr(motion_unified, "get_kinetics400", lambda: None)

    result = motion_unified.analyze_motion_unified(
        video_file, requested_exercise="squat", consent_deepseek_frames=False
    )
    # The whole chain decodes the file exactly once (pose + clip reuse the pass).
    assert len(opens) == 1
    assert result["kinetics"]["status"] == "unavailable"
    # No landmarks -> abstain, no score, no visual content.
    assert result["recognition"]["accepted"] is False
    assert result["pose"]["available"] is False
    assert result["score"]["available"] is False
    assert result["frames"] == []
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
    assert result["kinetics"]["status"] == "unavailable"
    assert result["kinetics"]["candidates"] == []
    # Chain still produced a (rejected) six-class recognition, not a crash.
    assert result["recognition"]["mode"] == "auto"
    assert result["pose"]["available"] is False


def test_pose_unavailable_still_emits_kinetics_candidates(monkeypatch, video_file):
    monkeypatch.setattr(motion_unified, "_pose_engine_available", lambda: False)
    monkeypatch.setattr(
        motion_unified, "get_kinetics400", lambda: _FakeKineticsModel()
    )
    result = motion_unified.analyze_motion_unified(
        video_file, requested_exercise="auto", consent_deepseek_frames=False
    )
    assert result["pose"]["available"] is False
    assert result["score"]["available"] is False
    assert result["recognition"]["accepted"] is False
    assert result["recognition"]["selected_type"] is None
    # Kinetics candidates still surface (400-class layer).
    assert result["kinetics"]["status"] == "available"
    assert result["kinetics"]["top_label"] == "squat"
    assert result["kinetics"]["candidates"][0]["probability"] == 0.72
    result["schema_version"] = SCHEMA_VERSION
    validate_motion_result_local(result)


def test_both_engines_down_is_hard_failure(monkeypatch, video_file):
    monkeypatch.setattr(motion_unified, "_pose_engine_available", lambda: False)
    monkeypatch.setattr(motion_unified, "get_kinetics400", lambda: None)
    with pytest.raises(ProcessingError) as exc:
        motion_unified.analyze_motion_unified(video_file)
    assert exc.value.retryable is False
    assert "both_engines" in exc.value.code


# ---------------------------------------------------------------------------
# Pure-canvas desensitization
# ---------------------------------------------------------------------------


def _event_with_skeleton():
    skeleton = []
    for i, (x, y) in enumerate(
        [
            (0.5, 0.15),  # nose-ish (not used)
            (0.42, 0.25),  # left_shoulder
            (0.58, 0.25),  # right_shoulder
            (0.40, 0.40),  # left_elbow
            (0.60, 0.40),  # right_elbow
            (0.38, 0.55),  # left_wrist
            (0.62, 0.55),  # right_wrist
            (0.44, 0.55),  # left_hip
            (0.56, 0.55),  # right_hip
            (0.43, 0.75),  # left_knee
            (0.57, 0.75),  # right_knee
            (0.42, 0.92),  # left_ankle
            (0.58, 0.92),  # right_ankle
        ]
    ):
        skeleton.append(
            {"id": f"pt{i}", "x": x, "y": y, "visibility": 0.9}
        )
    return {"timestamp": 2.4, "event": "squat_bottom", "skeleton": skeleton}


def test_pure_canvas_keyframe_is_desensitized():
    event = _event_with_skeleton()
    preview = render_skeleton_canvas(event, max_bytes=80 * 1024)
    assert preview["image_mime"] == "image/jpeg"
    raw = base64.b64decode(preview["image_b64"])
    # JPEG magic + size budget.
    assert raw.startswith(b"\xff\xd8") and raw.endswith(b"\xff\xd9")
    assert len(raw) <= 80 * 1024

    # The "original" frame: a busy photo-like image with a person blob.
    original = np.random.randint(0, 255, (480, 320, 3), dtype=np.uint8)
    original[120:360, 110:210] = (0, 0, 255)
    check = assert_desensitized_canvas(raw, original_bgr=original)
    # Outside thin skeleton strokes, the canvas is the flat background colour.
    assert check["bg_fraction"] >= 0.88
    # And it carries no photographic content from the original frame.
    assert abs(check["correlation_with_original"]) <= 0.10


def test_desensitization_check_rejects_photo_blit():
    """Sanity: the pixel check itself must catch a real photo on the canvas."""
    original = np.random.randint(0, 255, (480, 320, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", original, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
    assert ok
    with pytest.raises(ProcessingError):
        # A photo JPEG has almost no flat background pixels -> bg_fraction fails.
        assert_desensitized_canvas(encoded.tobytes(), min_bg_fraction=0.88)


# ---------------------------------------------------------------------------
# Receipt contract: valid + invalid fixtures
# ---------------------------------------------------------------------------


def _valid_rejected_result():
    return {
        "schema_version": SCHEMA_VERSION,
        "pose": {
            "available": False,
            "message": "证据不足，未做姿态评分。",
            "exercise_type": None,
            "reps": 0,
            "keypoint_valid_rate": 0.0,
            "errors": [],
        },
        "score": {"available": False, "reason": "证据不足"},
        "recognition": {
            "mode": "auto",
            "requested_type": "auto",
            "selected_type": None,
            "accepted": False,
            "confidence": 0.0,
            "margin": 0.0,
            "method": "rule_feature_matching_v1",
            "candidates": [],
        },
        "frames": [],
    }


def test_valid_rejected_receipt_passes():
    validate_motion_result_local(_valid_rejected_result())


def test_rejected_but_scoring_is_rejected():
    result = _valid_rejected_result()
    result["score"] = {"available": True, "overall": 80}
    with pytest.raises(ProcessingError) as exc:
        validate_motion_result_local(result)
    assert exc.value.retryable is False


def test_too_many_previews_rejected():
    result = _valid_rejected_result()
    result["recognition"] = dict(result["recognition"], accepted=True, selected_type="squat")
    result["pose"] = dict(result["pose"], available=True, reps=3, keypoint_valid_rate=0.6)
    result["score"] = {"available": False, "reason": "x"}
    good_jpeg = render_skeleton_canvas(_event_with_skeleton(), max_bytes=80 * 1024)
    frames = []
    for i in range(5):  # 5 > 4 preview budget
        frames.append(
            {
                "timestamp": float(i),
                "event": f"squat_bottom",
                "image_b64": good_jpeg["image_b64"],
                "image_mime": "image/jpeg",
            }
        )
    result["frames"] = frames
    with pytest.raises(ProcessingError):
        validate_motion_result_local(result)


def test_bad_jpeg_magic_rejected():
    result = _valid_rejected_result()
    result["recognition"] = dict(result["recognition"], accepted=True, selected_type="squat")
    result["pose"] = dict(result["pose"], available=True, reps=3, keypoint_valid_rate=0.6)
    result["score"] = {"available": False, "reason": "x"}
    bad = base64.b64encode(b"not a jpeg").decode("ascii")
    result["frames"] = [
        {"timestamp": 0.0, "event": "squat_bottom", "image_b64": bad, "image_mime": "image/jpeg"}
    ]
    with pytest.raises(ProcessingError):
        validate_motion_result_local(result)


def test_schema_version_required():
    result = _valid_rejected_result()
    del result["schema_version"]
    with pytest.raises(ProcessingError):
        validate_motion_result_local(result)


# ---------------------------------------------------------------------------
# Happy path: accepted six-class result with pose score + desensitized preview
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


def test_happy_path_accepted_with_score_and_preview(monkeypatch, video_file):
    # Short-circuit the decode: feed real analyzer-grade squat samples.
    from healthmate_worker.processors import recognition as recognition_mod

    samples = {c: _squat_rows() for c in recognition_mod.SUPPORTED_EXERCISES}
    canned = {
        "fps": 30.0,
        "total_frames": 60,
        "duration": 2.0,
        "sample_sets": samples,
        "clip_frames": [],
        "sampled": len(_squat_rows()),
    }
    monkeypatch.setattr(motion_unified, "_run_single_pass_decode", lambda *a, **k: canned)
    monkeypatch.setattr(motion_unified, "get_kinetics400", lambda: None)

    result = motion_unified.analyze_motion_unified(
        video_file, requested_exercise="squat", consent_deepseek_frames=True
    )
    assert result["recognition"]["mode"] == "manual"
    assert result["recognition"]["accepted"] is True
    assert result["recognition"]["selected_type"] == "squat"
    assert result["pose"]["available"] is True
    assert result["pose"]["exercise_type"] == "squat"
    assert result["pose"]["reps"] >= 1
    assert result["score"]["available"] is True
    # consent on: up to 4 desensitized previews attached
    previews = [f for f in result["frames"] if f.get("image_b64")]
    assert 1 <= len(previews) <= 4
    for p in previews:
        raw = base64.b64decode(p["image_b64"])
        assert raw.startswith(b"\xff\xd8") and raw.endswith(b"\xff\xd9")
        assert len(raw) <= 80 * 1024
    result["schema_version"] = SCHEMA_VERSION
    validate_motion_result_local(result)
