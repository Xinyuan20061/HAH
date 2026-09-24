from __future__ import annotations

from collections import defaultdict
import math
from statistics import mean


SUPPORTED_EXERCISES = {
    "squat",
    "pushup",
    "lunge",
    "leg_abduction",
    "arm_abduction",
    "arm_vw",
}


def _percent(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator * 100, 2) if denominator else None


def _finite_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate_annotations(rows: list[dict]) -> None:
    seen: set[str] = set()
    for index, row in enumerate(rows, 1):
        sample_id = row.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id.strip():
            raise ValueError(f"annotation line {index}: sample_id must be a non-empty string")
        if sample_id in seen:
            raise ValueError(f"annotation line {index}: duplicate sample_id {sample_id}")
        seen.add(sample_id)
        if row.get("exercise_type") not in SUPPORTED_EXERCISES:
            raise ValueError(f"annotation line {index}: unsupported exercise_type")
        if not isinstance(row.get("should_evaluate"), bool):
            raise ValueError(f"annotation line {index}: should_evaluate must be boolean")
        if row["should_evaluate"]:
            if not isinstance(row.get("reps"), int) or isinstance(row.get("reps"), bool) or row["reps"] < 0:
                raise ValueError(f"annotation line {index}: evaluable samples require non-negative integer reps")
        errors = row.get("errors", [])
        if not isinstance(errors, list) or any(not isinstance(code, str) or not code for code in errors):
            raise ValueError(f"annotation line {index}: errors must be a string list")
        events = row.get("events", [])
        if not isinstance(events, list):
            raise ValueError(f"annotation line {index}: events must be a list")
        for event in events:
            if not isinstance(event, dict) or not isinstance(event.get("type"), str) or not _finite_number(event.get("timestamp")):
                raise ValueError(f"annotation line {index}: every event needs type and finite timestamp")
        if row.get("quality_score") is not None and not (
            _finite_number(row["quality_score"]) and 0 <= row["quality_score"] <= 100
        ):
            raise ValueError(f"annotation line {index}: quality_score must be 0..100")


def _prediction_index(rows: list[dict]) -> dict[str, dict]:
    indexed: dict[str, dict] = {}
    for index, row in enumerate(rows, 1):
        sample_id = row.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id.strip():
            raise ValueError(f"prediction line {index}: sample_id must be a non-empty string")
        if sample_id in indexed:
            raise ValueError(f"prediction line {index}: duplicate sample_id {sample_id}")
        indexed[sample_id] = row
    return indexed


def _result(row: dict | None) -> dict:
    if not row or row.get("status", "completed") != "completed":
        return {}
    value = row.get("result", row.get("prediction", {}))
    return value if isinstance(value, dict) else {}


def _pose(result: dict) -> dict:
    value = result.get("pose")
    return value if isinstance(value, dict) else {}


def _match_events(truth: list[dict], predicted: list[dict], tolerance: float) -> tuple[int, int, int, list[float]]:
    true_by_type: dict[str, list[float]] = defaultdict(list)
    pred_by_type: dict[str, list[float]] = defaultdict(list)
    evaluated_types = {item["type"] for item in truth}
    for item in truth:
        true_by_type[item["type"]].append(float(item["timestamp"]))
    for item in predicted:
        event_type = item.get("event")
        timestamp = item.get("timestamp")
        if event_type in evaluated_types and _finite_number(timestamp):
            pred_by_type[event_type].append(float(timestamp))
    true_positive = false_positive = false_negative = 0
    errors: list[float] = []
    for event_type in evaluated_types:
        remaining = sorted(pred_by_type[event_type])
        for expected in sorted(true_by_type[event_type]):
            candidates = [(abs(value - expected), idx) for idx, value in enumerate(remaining)]
            if candidates:
                distance, match_index = min(candidates)
                if distance <= tolerance:
                    true_positive += 1
                    errors.append(distance)
                    remaining.pop(match_index)
                    continue
            false_negative += 1
        false_positive += len(remaining)
    return true_positive, false_positive, false_negative, errors


def _average_ranks(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        end = index
        while end + 1 < len(ordered) and ordered[end + 1][1] == ordered[index][1]:
            end += 1
        rank = (index + end) / 2 + 1
        for position in range(index, end + 1):
            ranks[ordered[position][0]] = rank
        index = end + 1
    return ranks


def _spearman(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    x = _average_ranks(left)
    y = _average_ranks(right)
    x_mean, y_mean = mean(x), mean(y)
    numerator = sum((a - x_mean) * (b - y_mean) for a, b in zip(x, y))
    denominator = math.sqrt(
        sum((a - x_mean) ** 2 for a in x) * sum((b - y_mean) ** 2 for b in y)
    )
    return round(numerator / denominator, 4) if denominator else None


def evaluate_motion(
    annotations: list[dict], predictions: list[dict], event_tolerance_seconds: float = 0.35
) -> dict:
    if event_tolerance_seconds <= 0 or not math.isfinite(event_tolerance_seconds):
        raise ValueError("event_tolerance_seconds must be positive")
    _validate_annotations(annotations)
    predicted_by_id = _prediction_index(predictions)
    annotated_ids = {row["sample_id"] for row in annotations}
    unknown_predictions = sorted(set(predicted_by_id) - annotated_ids)

    processing_failures = missing_predictions = 0
    evaluable = low_quality = accepted_evaluable = refused_low_quality = 0
    refusal_correct = 0
    rep_absolute_errors: list[int] = []
    rep_exact_all = rep_within_one_all = 0
    error_tp = error_fp = error_fn = 0
    per_error: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    event_tp = event_fp = event_fn = 0
    event_errors: list[float] = []
    manual_scores: list[float] = []
    predicted_scores: list[float] = []
    exercise_rows: dict[str, list[tuple[dict, dict | None]]] = defaultdict(list)
    recognition_rows: list[tuple[str, str | None]] = []

    for annotation in annotations:
        prediction_row = predicted_by_id.get(annotation["sample_id"])
        exercise_rows[annotation["exercise_type"]].append((annotation, prediction_row))
        if prediction_row is None:
            missing_predictions += 1
        elif prediction_row.get("status", "completed") != "completed":
            processing_failures += 1
        result = _result(prediction_row)
        pose = _pose(result)
        recognition = result.get("recognition")
        if isinstance(recognition, dict) and recognition.get("mode") == "auto":
            selected = (
                recognition.get("selected_type")
                if recognition.get("accepted") is True
                else None
            )
            recognition_rows.append((annotation["exercise_type"], selected))
        available = pose.get("available") is True
        should_evaluate = annotation["should_evaluate"]
        evaluable += int(should_evaluate)
        low_quality += int(not should_evaluate)
        accepted_evaluable += int(should_evaluate and available)
        refused_low_quality += int(not should_evaluate and not available and bool(result))
        refusal_correct += int(available == should_evaluate and bool(result))

        if should_evaluate:
            predicted_reps = pose.get("reps")
            if available and isinstance(predicted_reps, int) and not isinstance(predicted_reps, bool):
                distance = abs(predicted_reps - annotation["reps"])
                rep_absolute_errors.append(distance)
                rep_exact_all += int(distance == 0)
                rep_within_one_all += int(distance <= 1)

            truth_errors = set(annotation.get("errors", []))
            predicted_errors = {
                item.get("code")
                for item in pose.get("errors", [])
                if isinstance(item, dict) and isinstance(item.get("code"), str)
            } if available else set()
            for code in truth_errors | predicted_errors:
                if code in truth_errors and code in predicted_errors:
                    error_tp += 1
                    per_error[code]["tp"] += 1
                elif code in predicted_errors:
                    error_fp += 1
                    per_error[code]["fp"] += 1
                else:
                    error_fn += 1
                    per_error[code]["fn"] += 1

            frames = result.get("frames") if isinstance(result.get("frames"), list) else []
            tp, fp, fn, times = _match_events(annotation.get("events", []), frames, event_tolerance_seconds)
            event_tp += tp
            event_fp += fp
            event_fn += fn
            event_errors.extend(times)

            manual_score = annotation.get("quality_score")
            score = result.get("score") if isinstance(result.get("score"), dict) else {}
            predicted_score = score.get("overall")
            if manual_score is not None and score.get("available") is True and _finite_number(predicted_score):
                manual_scores.append(float(manual_score))
                predicted_scores.append(float(predicted_score))

    def classification(tp: int, fp: int, fn: int) -> dict:
        precision = _percent(tp, tp + fp)
        recall = _percent(tp, tp + fn)
        f1 = round(2 * precision * recall / (precision + recall), 2) if precision is not None and recall is not None and precision + recall else None
        return {"precision_pct": precision, "recall_pct": recall, "f1_pct": f1, "tp": tp, "fp": fp, "fn": fn}

    per_error_metrics = {
        code: classification(counts["tp"], counts["fp"], counts["fn"])
        for code, counts in sorted(per_error.items())
    }
    recognition_by_class = {}
    recognition_f1_values = []
    for exercise in sorted(SUPPORTED_EXERCISES):
        tp = sum(truth == exercise and predicted == exercise for truth, predicted in recognition_rows)
        fp = sum(truth != exercise and predicted == exercise for truth, predicted in recognition_rows)
        fn = sum(truth == exercise and predicted != exercise for truth, predicted in recognition_rows)
        metrics = classification(tp, fp, fn)
        recognition_by_class[exercise] = metrics
        if metrics["f1_pct"] is not None:
            recognition_f1_values.append(metrics["f1_pct"])
    recognition_accepted = sum(predicted in SUPPORTED_EXERCISES for _, predicted in recognition_rows)
    recognition_correct = sum(truth == predicted for truth, predicted in recognition_rows)
    recognition_confusion = {
        truth: {
            predicted: sum(
                actual == truth and selected == (None if predicted == "abstain" else predicted)
                for actual, selected in recognition_rows
            )
            for predicted in [*sorted(SUPPORTED_EXERCISES), "abstain"]
        }
        for truth in sorted(SUPPORTED_EXERCISES)
    }
    automatic_recognition = {
        "sample_size": len(recognition_rows),
        "accepted_samples": recognition_accepted,
        "abstained_samples": len(recognition_rows) - recognition_accepted,
        "coverage_pct": _percent(recognition_accepted, len(recognition_rows)),
        "accuracy_all_samples_pct": _percent(recognition_correct, len(recognition_rows)),
        "accuracy_accepted_pct": _percent(recognition_correct, recognition_accepted),
        "macro_f1_pct": round(mean(recognition_f1_values), 2)
        if recognition_f1_values
        else None,
        "by_class": recognition_by_class,
        "confusion_matrix": recognition_confusion,
    }
    exercise_breakdown = {}
    for exercise, rows in sorted(exercise_rows.items()):
        valid = [
            (annotation, _result(prediction))
            for annotation, prediction in rows
            if annotation["should_evaluate"]
        ]
        accepted = [
            (annotation, result)
            for annotation, result in valid
            if _pose(result).get("available") is True
            and isinstance(_pose(result).get("reps"), int)
        ]
        distances = [abs((result["pose"]["reps"]) - annotation["reps"]) for annotation, result in accepted]
        exercise_breakdown[exercise] = {
            "sample_size": len(rows),
            "evaluable_samples": len(valid),
            "accepted_samples": len(accepted),
            "coverage_pct": _percent(len(accepted), len(valid)),
            "rep_mae_accepted": round(mean(distances), 4) if distances else None,
            "exact_count_accuracy_all_evaluable_pct": _percent(sum(distance == 0 for distance in distances), len(valid)),
        }

    return {
        "schema_version": "healthmate-motion-benchmark-v1",
        "sample_size": len(annotations),
        "integrity": {
            "annotation_count": len(annotations),
            "prediction_count": len(predictions),
            "missing_predictions": missing_predictions,
            "unknown_prediction_ids": unknown_predictions,
            "processing_failures": processing_failures,
        },
        "evaluation_decision": {
            "evaluable_samples": evaluable,
            "low_quality_samples": low_quality,
            "evaluable_coverage_pct": _percent(accepted_evaluable, evaluable),
            "low_quality_refusal_recall_pct": _percent(refused_low_quality, low_quality),
            "decision_accuracy_pct": _percent(refusal_correct, len(annotations)),
        },
        "repetition_count": {
            "accepted_sample_size": len(rep_absolute_errors),
            "all_evaluable_sample_size": evaluable,
            "mae_accepted": round(mean(rep_absolute_errors), 4) if rep_absolute_errors else None,
            "exact_accuracy_all_evaluable_pct": _percent(rep_exact_all, evaluable),
            "within_one_accuracy_all_evaluable_pct": _percent(rep_within_one_all, evaluable),
        },
        "error_detection_micro": classification(error_tp, error_fp, error_fn),
        "error_detection_by_code": per_error_metrics,
        "key_event_detection": {
            **classification(event_tp, event_fp, event_fn),
            "timestamp_mae_seconds": round(mean(event_errors), 4) if event_errors else None,
            "matched_event_count": len(event_errors),
            "tolerance_seconds": event_tolerance_seconds,
        },
        "quality_score": {
            "paired_sample_size": len(manual_scores),
            "spearman": _spearman(manual_scores, predicted_scores),
        },
        "automatic_recognition": automatic_recognition,
        "by_exercise": exercise_breakdown,
        "claims_not_measured": (
            ["自动动作分类F1：本次预测未包含自动识别样本"]
            if not recognition_rows
            else []
        )
        + ["损伤风险概率：当前规则偏差分不是医学风险模型"],
    }


def _display(value) -> str:
    return "暂无样本" if value is None else str(value)


def _display_percent(value) -> str:
    return "暂无样本" if value is None else f"{value}%"


def render_markdown(report: dict) -> str:
    decision = report["evaluation_decision"]
    reps = report["repetition_count"]
    errors = report["error_detection_micro"]
    events = report["key_event_detection"]
    score = report["quality_score"]
    recognition = report["automatic_recognition"]
    lines = [
        "# HealthMate 动作算法离线评测报告",
        "",
        f"- 标注样本：{report['sample_size']}",
        f"- 缺失预测：{report['integrity']['missing_predictions']}",
        f"- 处理失败：{report['integrity']['processing_failures']}",
        "",
        "## 核心结果",
        "",
        "|指标|结果|样本口径|",
        "|---|---:|---|",
        f"|可评价样本覆盖率|{_display_percent(decision['evaluable_coverage_pct'])}|应评价 {decision['evaluable_samples']} 条|",
        f"|低质量拒绝召回率|{_display_percent(decision['low_quality_refusal_recall_pct'])}|低质量 {decision['low_quality_samples']} 条|",
        f"|次数 MAE|{_display(reps['mae_accepted'])}|仅已接受评价 {reps['accepted_sample_size']} 条|",
        f"|次数完全正确率|{_display_percent(reps['exact_accuracy_all_evaluable_pct'])}|全部应评价 {reps['all_evaluable_sample_size']} 条；拒绝/失败计错|",
        f"|错误提示 Micro-F1|{_display_percent(errors['f1_pct'])}|TP={errors['tp']}，FP={errors['fp']}，FN={errors['fn']}|",
        f"|关键事件 F1|{_display_percent(events['f1_pct'])}|容差 ±{events['tolerance_seconds']} 秒|",
        f"|关键事件时间 MAE|{_display(events['timestamp_mae_seconds'])} 秒|匹配 {events['matched_event_count']} 个事件|",
        f"|评分 Spearman|{_display(score['spearman'])}|人工与算法配对 {score['paired_sample_size']} 条|",
        f"|自动识别覆盖率|{_display_percent(recognition['coverage_pct'])}|自动模式 {recognition['sample_size']} 条；拒识不计覆盖|",
        f"|自动识别全样本准确率|{_display_percent(recognition['accuracy_all_samples_pct'])}|拒识与错分均计错|",
        f"|自动识别 Macro-F1|{_display_percent(recognition['macro_f1_pct'])}|仅基于含自动识别结果的固定标注集|",
        "",
        "## 分动作次数结果",
        "",
        "|动作|全部样本|应评价|已接受|覆盖率|次数MAE|完全正确率|",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for exercise, values in report["by_exercise"].items():
        lines.append(
            f"|{exercise}|{values['sample_size']}|{values['evaluable_samples']}|{values['accepted_samples']}|"
            f"{_display_percent(values['coverage_pct'])}|{_display(values['rep_mae_accepted'])}|"
            f"{_display_percent(values['exact_count_accuracy_all_evaluable_pct'])}|"
        )
    provenance = report.get("provenance")
    if isinstance(provenance, dict):
        lines.extend(
            [
                "",
                "## 可复现信息",
                "",
                f"- 生成时间（UTC）：{provenance.get('generated_at', '')}",
                f"- Worker版本：{provenance.get('worker_version', '')}",
                f"- 标注SHA-256：`{provenance.get('annotations_sha256', '')}`",
                f"- 预测SHA-256：`{provenance.get('predictions_sha256', '')}`",
                f"- Python：{provenance.get('python', '')}",
                "",
            ]
        )
    lines.extend(["", "## 不得据此宣称", ""])
    lines.extend(f"- {claim}" for claim in report["claims_not_measured"])
    lines.extend(
        [
            "",
            "> 准确率仅对本报告对应的固定标注集有效；需同时披露样本量、机位、光照和人群覆盖。",
            "",
        ]
    )
    return "\n".join(lines)
