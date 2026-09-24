"""Tests for the DeepSeek motion-review / fallback module (no network)."""

import pytest

from healthmate_worker.errors import ProcessingError
from healthmate_worker.processors import motion_review
from healthmate_worker.processors.motion_review import (
    explain_frames_deepseek,
    review_exercise,
    select_review_rows,
    skeleton_summary,
)


def _row(t, visibility=0.9):
    return {
        "t": t,
        "candidate": "squat",
        "knee": 120.0,
        "elbow": 175.0,
        "hip": 160.0,
        "trunk": 8.0,
        "body_line": 172.0,
        "visibility": visibility,
        "skeleton": [
            {"id": "left_shoulder", "x": 0.4, "y": 0.2, "visibility": 0.9},
            {"id": "left_hip", "x": 0.45, "y": 0.5, "visibility": 0.9},
            {"id": "left_knee", "x": 0.5, "y": 0.7, "visibility": 0.9},
            {"id": "left_ankle", "x": 0.55, "y": 0.95, "visibility": 0.9},
        ],
    }


def test_select_review_rows_even_sampling():
    sample_sets = {"squat": [_row(t) for t in range(10)]}
    selected = select_review_rows(sample_sets, count=4)
    assert len(selected) == 4
    times = [row["t"] for row in selected]
    assert times == sorted(times)


def test_select_review_rows_empty():
    assert select_review_rows({}, count=4) == []
    assert select_review_rows({"squat": []}, count=4) == []


def test_skeleton_summary_contains_angles():
    summary = skeleton_summary({"squat": [_row(0.2), _row(0.4)]})
    assert "膝角" in summary and "squat" in summary


def test_review_exercise_none_when_vlm_unavailable(monkeypatch):
    def fake_request(*args, **kwargs):
        raise ProcessingError("vlm_http", "unavailable", True)

    monkeypatch.setattr(motion_review, "_request_review", fake_request)
    monkeypatch.setattr(
        motion_review, "render_stick_frames", lambda *a, **k: ["aGVsbG8="]
    )
    result, reason = review_exercise(
        "no-such-file.mp4",
        {"squat": [_row(0.2)]},
        local_pick="squat",
        local_accepted=True,
        local_reason="ok",
    )
    assert result is None
    assert "视觉模型调用失败" in reason


def test_review_exercise_returns_none_without_frames(monkeypatch):
    monkeypatch.setattr(motion_review, "render_stick_frames", lambda *a, **k: [])
    result, reason = review_exercise(
        "no-such-file.mp4",
        {"squat": [_row(0.2)]},
        local_pick="squat",
        local_accepted=True,
        local_reason="ok",
    )
    assert result is None
    assert "渲染失败" in reason


def test_explain_frames_deepseek_empty_without_key(monkeypatch):
    monkeypatch.setattr(
        motion_review,
        "provider_config",
        lambda provider=None: {"model": "", "api_key": "", "base_url": "http://x"},
    )
    explained = explain_frames_deepseek(
        [{"index": 0, "timestamp": 1.2, "stage": "最低点", "finding": "深", "advice": "慢"}],
        "squat",
    )
    assert explained == {}
