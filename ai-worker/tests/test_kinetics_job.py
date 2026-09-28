"""Tests for the standalone Kinetics-400 job processor (recognition-only)."""
from pathlib import Path

import pytest

from healthmate_worker.errors import ProcessingError
from healthmate_worker.processors.kinetics import KINETICS_LABEL_ZH, analyze_kinetics400


def fake_recognize(video_path):
    return {
        "top_label": "pull ups",
        "top_probability": 0.61,
        "mapped_exercise": "pullup",
        "exercise_slug": "pull_ups",
        "candidates": [
            {"label": "pull ups", "class_index": 231, "probability": 0.61},
            {"label": "squat", "class_index": 300, "probability": 0.25},
            {"label": "tai chi", "class_index": 346, "probability": 0.08},
        ],
    }


def test_analyze_kinetics400_shapes_result(monkeypatch):
    monkeypatch.setattr(
        "healthmate_worker.processors.kinetics.recognize_video_kinetics400",
        fake_recognize,
    )
    result = analyze_kinetics400(Path("some/video.mp4"))
    assert result["method"] == "slowfast_kinetics400_v1"
    assert result["top_label"] == "pull ups"
    assert result["top_label_zh"] == KINETICS_LABEL_ZH["pull ups"] == "引体向上"
    assert result["top_probability"] == 0.61
    assert result["mapped_exercise"] == "pullup"
    assert result["is_estimate"] is True
    assert "scope" in result
    assert len(result["candidates"]) == 3

    first = result["candidates"][0]
    assert first["label"] == "pull ups"
    assert first["label_zh"] == "引体向上"
    assert first["class_index"] == 231
    assert first["probability"] == 0.61
    assert first["exercise_slug"] == "pull_ups"
    assert first["mapped_exercise"] == "pullup"

    # A label with a zh name but no analyzer keeps slug and null mapping.
    tai = result["candidates"][2]
    assert tai["label_zh"] == "太极"
    assert tai["exercise_slug"] == "tai_chi"
    assert tai["mapped_exercise"] is None


def test_analyze_kinetics400_raises_when_model_unavailable(monkeypatch):
    monkeypatch.setattr(
        "healthmate_worker.processors.kinetics.recognize_video_kinetics400",
        lambda video_path: None,
    )
    with pytest.raises(ProcessingError) as exc:
        analyze_kinetics400(Path("some/video.mp4"))
    assert "kinetics400" in exc.value.code
    assert exc.value.retryable is False


def test_zh_map_only_contains_existing_kinetics_labels():
    """Every curated zh name must be a real Kinetics-400 label."""
    from healthmate_worker.models.kinetics_runtime import _LABELS_PATH

    labels = {
        line.strip()
        for line in _LABELS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    missing = sorted(set(KINETICS_LABEL_ZH) - labels)
    assert missing == []
