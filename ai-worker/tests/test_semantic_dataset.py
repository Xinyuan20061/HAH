import copy

import pytest

from healthmate_worker.datasets.manifest import (
    assign_subject_splits,
    manifest_sha256,
    summarize_manifest,
    validate_manifest,
    verify_no_leakage,
)
from healthmate_worker.evaluation.semantic_benchmark import evaluate_semantic_predictions


def sample(sample_id="s1", subject_id="person-1", family="squat_family", instance_id="i1"):
    return {
        "sample_id": sample_id,
        "subject_id": subject_id,
        "instance_id": instance_id,
        "source_dataset": "self-collected-v1",
        "video_path": f"private-videos/{sample_id}.mp4",
        "action_text": "徒手深蹲",
        "action_family": family,
        "movement_patterns": ["knee_dominant", "bilateral_lower_body"],
        "observed_regions": ["lower_body"],
        "target_body_parts": ["quadriceps", "glutes"],
        "view": "side",
        "consent_status": "consented",
        "license_record": {
            "dataset_key": "self-collected-v1",
            "terms_reviewed_on": "2026-09-21",
            "allowed_use": "competition research",
        },
        "annotation_status": "double_reviewed",
    }


def test_manifest_validation_rejects_duplicate_and_escaping_paths():
    row = sample()
    assert validate_manifest([row])[0]["schema_version"] == "healthmate-semantic-manifest-v1"
    with pytest.raises(ValueError, match="unique"):
        validate_manifest([row, copy.deepcopy(row)])
    escaped = copy.deepcopy(row)
    escaped["video_path"] = "../outside.mp4"
    with pytest.raises(ValueError, match="escapes"):
        validate_manifest([escaped])


def test_subject_split_is_deterministic_and_prevents_subject_or_instance_leakage():
    rows = []
    for index in range(30):
        subject = f"person-{index // 2}"
        rows.append(sample(f"s{index}", subject, instance_id=f"instance-{index // 2}"))
    first = assign_subject_splits(rows, seed="fixed")
    second = assign_subject_splits(list(reversed(rows)), seed="fixed")
    assert {row["sample_id"]: row["split"] for row in first} == {
        row["sample_id"]: row["split"] for row in second
    }
    verify_no_leakage(first)
    summary = summarize_manifest(first)
    assert summary["sample_count"] == 30
    assert summary["subject_count"] == 15
    assert len(manifest_sha256(first)) == 64


def test_unseen_action_family_and_all_its_subject_rows_are_forced_to_test():
    rows = [
        sample("u1", "person-u", "unseen_pull", "ui1"),
        sample("u2", "person-u", "seen_push", "ui2"),
        sample("s1", "person-s", "seen_push", "si1"),
    ]
    assigned = assign_subject_splits(rows, unseen_action_families={"unseen_pull"})
    assert {row["split"] for row in assigned if row["subject_id"] == "person-u"} == {"test"}
    verify_no_leakage(assigned, unseen_action_families={"unseen_pull"})


def test_semantic_benchmark_reports_multilabel_and_unknown_refusal():
    annotations = [
        {
            "sample_id": "a",
            "movement_patterns": ["knee_dominant", "bilateral_lower_body"],
            "observed_regions": ["lower_body"],
            "is_unknown_action": False,
        },
        {
            "sample_id": "b",
            "movement_patterns": ["horizontal_upper_body"],
            "observed_regions": ["upper_body"],
            "is_unknown_action": True,
        },
    ]
    predictions = [
        {
            "sample_id": "a",
            "result": {
                "recognition": {"accepted": True},
                "compositional_semantics": {
                    "movement_patterns": [
                        {"key": "knee_dominant"},
                        {"key": "bilateral_lower_body"},
                    ],
                    "observed_regions": [{"key": "lower_body"}],
                },
            },
        },
        {
            "sample_id": "b",
            "result": {
                "recognition": {"accepted": False},
                "compositional_semantics": {
                    "movement_patterns": [{"key": "horizontal_upper_body"}],
                    "observed_regions": [{"key": "upper_body"}],
                },
            },
        },
    ]
    report = evaluate_semantic_predictions(annotations, predictions)
    assert report["movement_patterns"]["micro_f1_pct"] == 100.0
    assert report["observed_regions"]["exact_match_pct"] == 100.0
    assert report["unknown_refusal_recall_pct"] == 100.0
    assert "肌肉激活率" in report["claims_not_measured"]
