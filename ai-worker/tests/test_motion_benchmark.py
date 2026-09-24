import pytest

from healthmate_worker.evaluation import evaluate_motion, render_markdown


def dataset():
    annotations = [
        {
            "sample_id": "squat-001",
            "exercise_type": "squat",
            "should_evaluate": True,
            "reps": 2,
            "errors": ["depth_insufficient"],
            "events": [
                {"type": "squat_bottom", "timestamp": 1.0},
                {"type": "squat_bottom", "timestamp": 3.0},
            ],
            "quality_score": 80,
        },
        {
            "sample_id": "pushup-001",
            "exercise_type": "pushup",
            "should_evaluate": True,
            "reps": 1,
            "errors": [],
            "events": [{"type": "pushup_bottom", "timestamp": 2.0}],
            "quality_score": 60,
        },
        {
            "sample_id": "squat-low-quality",
            "exercise_type": "squat",
            "should_evaluate": False,
            "errors": [],
            "events": [],
        },
    ]
    predictions = [
        {
            "sample_id": "squat-001",
            "status": "completed",
            "result": {
                "pose": {
                    "available": True,
                    "reps": 1,
                    "errors": [
                        {"code": "depth_insufficient"},
                        {"code": "trunk_lean"},
                    ],
                },
                "frames": [
                    {"event": "squat_bottom", "timestamp": 1.1},
                    {"event": "squat_bottom", "timestamp": 3.6},
                    {"event": "max_rule_risk", "timestamp": 2.0},
                ],
                "score": {"available": True, "overall": 70},
            },
        },
        {
            "sample_id": "pushup-001",
            "status": "completed",
            "result": {
                "pose": {
                    "available": True,
                    "reps": 1,
                    "errors": [{"code": "body_alignment"}],
                },
                "frames": [{"event": "pushup_bottom", "timestamp": 2.1}],
                "score": {"available": True, "overall": 50},
            },
        },
        {
            "sample_id": "squat-low-quality",
            "status": "completed",
            "result": {"pose": {"available": False, "message": "证据不足"}},
        },
    ]
    return annotations, predictions


def test_motion_benchmark_reports_honest_denominators_and_metrics():
    annotations, predictions = dataset()
    report = evaluate_motion(annotations, predictions, event_tolerance_seconds=0.35)
    assert report["sample_size"] == 3
    assert report["evaluation_decision"] == {
        "evaluable_samples": 2,
        "low_quality_samples": 1,
        "evaluable_coverage_pct": 100.0,
        "low_quality_refusal_recall_pct": 100.0,
        "decision_accuracy_pct": 100.0,
    }
    assert report["repetition_count"]["mae_accepted"] == 0.5
    assert report["repetition_count"]["exact_accuracy_all_evaluable_pct"] == 50.0
    assert report["error_detection_micro"]["precision_pct"] == 33.33
    assert report["error_detection_micro"]["recall_pct"] == 100.0
    assert report["error_detection_micro"]["f1_pct"] == 50.0
    assert report["key_event_detection"]["f1_pct"] == 66.67
    assert report["key_event_detection"]["timestamp_mae_seconds"] == 0.1
    assert report["quality_score"] == {"paired_sample_size": 2, "spearman": 1.0}


def test_missing_and_failed_predictions_reduce_coverage_not_just_sample_size():
    annotations, predictions = dataset()
    predictions = predictions[:1] + [
        {"sample_id": "pushup-001", "status": "failed", "error": "decoder failed"}
    ]
    report = evaluate_motion(annotations, predictions)
    assert report["integrity"]["missing_predictions"] == 1
    assert report["integrity"]["processing_failures"] == 1
    assert report["evaluation_decision"]["evaluable_coverage_pct"] == 50.0
    assert report["repetition_count"]["exact_accuracy_all_evaluable_pct"] == 0.0
    assert report["repetition_count"]["within_one_accuracy_all_evaluable_pct"] == 50.0


def test_motion_benchmark_validates_manifest_and_unknown_predictions():
    annotations, predictions = dataset()
    invalid = [dict(annotations[0]), dict(annotations[0])]
    with pytest.raises(ValueError, match="duplicate sample_id"):
        evaluate_motion(invalid, predictions)
    report = evaluate_motion(
        annotations,
        predictions + [{"sample_id": "not-annotated", "result": {}}],
    )
    assert report["integrity"]["unknown_prediction_ids"] == ["not-annotated"]


def test_markdown_report_discloses_scope_and_unmeasured_claims():
    report = evaluate_motion(*dataset())
    markdown = render_markdown(report)
    assert "拒绝/失败计错" in markdown
    assert "自动动作分类F1" in markdown
    assert "固定标注集" in markdown


def test_motion_benchmark_scores_auto_recognition_and_abstention():
    annotations, predictions = dataset()
    selected = ["squat", "lunge", None]
    for prediction, predicted_type in zip(predictions, selected):
        prediction.setdefault("result", {})["recognition"] = {
            "mode": "auto",
            "accepted": predicted_type is not None,
            "selected_type": predicted_type,
        }
    report = evaluate_motion(annotations, predictions)
    recognition = report["automatic_recognition"]
    assert recognition["sample_size"] == 3
    assert recognition["accepted_samples"] == 2
    assert recognition["coverage_pct"] == 66.67
    assert recognition["accuracy_all_samples_pct"] == 33.33
    assert recognition["accuracy_accepted_pct"] == 50.0
    assert recognition["confusion_matrix"]["squat"]["abstain"] == 1
    assert not any("自动动作分类F1" in item for item in report["claims_not_measured"])
