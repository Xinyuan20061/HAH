"""Tests for the standalone Kinetics-400 job processor (recognition-only)."""
from pathlib import Path

import pytest

from healthmate_worker.errors import ProcessingError
from healthmate_worker.processors.kinetics import KINETICS_LABEL_ZH, analyze_kinetics400


def fake_recognize(video_path):
    # "front raises" IS onboarded in the shared catalog (-> front_raise); "squat"
    # maps to squat; "tai chi" has no catalog entry and keeps canonical_id=None.
    return {
        "top_label": "front raises",
        "top_probability": 0.61,
        "mapped_exercise": "front_raise",
        "exercise_slug": "front_raises",
        "candidates": [
            {"label": "front raises", "class_index": 134, "probability": 0.61},
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
    assert result["top_label"] == "front raises"
    assert result["top_label_zh"] == KINETICS_LABEL_ZH["front raises"] == "前平举"
    assert result["top_probability"] == 0.61
    assert result["mapped_exercise"] == "front_raise"
    assert result["is_estimate"] is True
    assert "scope" in result
    assert len(result["candidates"]) == 3

    first = result["candidates"][0]
    assert first["label"] == "front raises"
    assert first["label_zh"] == "前平举"
    assert first["class_index"] == 134
    assert first["probability"] == 0.61
    assert first["exercise_slug"] == "front_raises"
    # V2: canonical id comes from the shared catalog, not a scattered table.
    assert first["mapped_exercise"] == "front_raise"
    assert first["canonical_id"] == "front_raise"

    # A label with a zh name but no catalog mapping keeps slug and null mapping.
    tai = result["candidates"][2]
    assert tai["label_zh"] == "太极"
    assert tai["exercise_slug"] == "tai_chi"
    assert tai["mapped_exercise"] is None
    assert tai["canonical_id"] is None


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
