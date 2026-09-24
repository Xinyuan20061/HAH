from __future__ import annotations

from collections import defaultdict


def _metrics(truth_sets: list[set[str]], predicted_sets: list[set[str]]) -> dict:
    labels = sorted(set().union(*truth_sets, *predicted_sets)) if truth_sets else []
    by_label = {}
    total_tp = total_fp = total_fn = 0
    f1_values = []
    for label in labels:
        tp = sum(label in truth and label in predicted for truth, predicted in zip(truth_sets, predicted_sets))
        fp = sum(label not in truth and label in predicted for truth, predicted in zip(truth_sets, predicted_sets))
        fn = sum(label in truth and label not in predicted for truth, predicted in zip(truth_sets, predicted_sets))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        by_label[label] = {"precision_pct": round(precision * 100, 2), "recall_pct": round(recall * 100, 2), "f1_pct": round(f1 * 100, 2), "support": tp + fn}
        total_tp += tp
        total_fp += fp
        total_fn += fn
        f1_values.append(f1)
    micro_precision = total_tp / (total_tp + total_fp) if total_tp + total_fp else 0.0
    micro_recall = total_tp / (total_tp + total_fn) if total_tp + total_fn else 0.0
    micro_f1 = 2 * micro_precision * micro_recall / (micro_precision + micro_recall) if micro_precision + micro_recall else 0.0
    return {
        "micro_f1_pct": round(micro_f1 * 100, 2),
        "macro_f1_pct": round(sum(f1_values) / len(f1_values) * 100, 2) if f1_values else None,
        "exact_match_pct": round(sum(a == b for a, b in zip(truth_sets, predicted_sets)) / len(truth_sets) * 100, 2) if truth_sets else None,
        "by_label": by_label,
    }


def evaluate_semantic_predictions(annotations: list[dict], predictions: list[dict]) -> dict:
    prediction_index = {row.get("sample_id"): row for row in predictions}
    if None in prediction_index or len(prediction_index) != len(predictions):
        raise ValueError("prediction sample_id values must be unique and non-empty")
    patterns_truth, patterns_predicted = [], []
    regions_truth, regions_predicted = [], []
    unknown_total = unknown_refused = 0
    missing = []
    for row in annotations:
        sample_id = row.get("sample_id")
        prediction = prediction_index.get(sample_id)
        if prediction is None:
            missing.append(sample_id)
            semantic = {}
            recognition = {}
        else:
            result = prediction.get("result", prediction)
            semantic = result.get("compositional_semantics", {}) if isinstance(result, dict) else {}
            recognition = result.get("recognition", {}) if isinstance(result, dict) else {}
        patterns_truth.append(set(row.get("movement_patterns", [])))
        regions_truth.append(set(row.get("observed_regions", [])))
        patterns_predicted.append({item.get("key") for item in semantic.get("movement_patterns", []) if isinstance(item, dict) and item.get("key")})
        regions_predicted.append({item.get("key") for item in semantic.get("observed_regions", []) if isinstance(item, dict) and item.get("key")})
        if row.get("is_unknown_action") is True:
            unknown_total += 1
            unknown_refused += int(recognition.get("accepted") is False)
    return {
        "schema_version": "healthmate-semantic-benchmark-v1",
        "sample_size": len(annotations),
        "missing_predictions": missing,
        "movement_patterns": _metrics(patterns_truth, patterns_predicted),
        "observed_regions": _metrics(regions_truth, regions_predicted),
        "unknown_refusal_recall_pct": round(unknown_refused / unknown_total * 100, 2) if unknown_total else None,
        "unknown_sample_size": unknown_total,
        "claims_not_measured": ["肌肉激活率", "实际训练效果", "医学损伤风险"],
    }
