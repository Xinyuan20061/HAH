from __future__ import annotations

import math
import re
from collections import defaultdict
from statistics import mean


def _name(value) -> str:
    return re.sub(r"[\s、，,()（）/\\_-]+", "", str(value or "").strip().lower())


def _number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator * 100, 2) if denominator else None


def _validate(annotations: list[dict], predictions: list[dict]) -> dict[str, dict]:
    seen = set()
    for index, row in enumerate(annotations, 1):
        sample_id = row.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id or sample_id in seen:
            raise ValueError(
                f"annotation line {index}: sample_id missing or duplicated"
            )
        seen.add(sample_id)
        if not isinstance(row.get("dish_name"), str) or not row["dish_name"].strip():
            raise ValueError(f"annotation line {index}: dish_name is required")
        if not _number(row.get("calories")) or row["calories"] < 0:
            raise ValueError(f"annotation line {index}: calories must be non-negative")
        if not isinstance(row.get("items"), list) or not row["items"]:
            raise ValueError(f"annotation line {index}: items must be a non-empty list")
    indexed = {}
    for index, row in enumerate(predictions, 1):
        sample_id = row.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id or sample_id in indexed:
            raise ValueError(
                f"prediction line {index}: sample_id missing or duplicated"
            )
        indexed[sample_id] = row
    return indexed


def _dish_correct(annotation: dict, prediction: dict) -> bool:
    accepted = {_name(annotation["dish_name"])}
    accepted.update(_name(value) for value in annotation.get("dish_aliases", []))
    predicted = _name(prediction.get("dish_name"))
    return bool(predicted and predicted in accepted)


def _dish_rank(annotation: dict, prediction: dict) -> int | None:
    accepted = {_name(annotation["dish_name"])}
    accepted.update(_name(value) for value in annotation.get("dish_aliases", []))
    candidates = [prediction.get("dish_name")]
    candidates.extend(prediction.get("dish_candidates") or [])
    candidates.extend(prediction.get("visible_items") or [])
    for index, candidate in enumerate(candidates[:3], 1):
        normalized = _name(candidate)
        if normalized and normalized in accepted:
            return index
    return None


def evaluate_food(annotations: list[dict], predictions: list[dict]) -> dict:
    indexed = _validate(annotations, predictions)
    completed = dish_correct = dish_top3 = item_tp = item_fp = item_fn = 0
    range_covered = consistent = item_sum_evaluable = 0
    calorie_errors = []
    calorie_percentage_errors = []
    confidence_pairs = []
    strata = defaultdict(list)
    macro_errors = {key: [] for key in ("protein", "carbs", "fat")}
    macro_percentage_errors = {key: [] for key in ("protein", "carbs", "fat")}

    for annotation in annotations:
        row = indexed.get(annotation["sample_id"])
        result = row.get("result", row.get("prediction", {})) if row else {}
        true_items = {_name(item) for item in annotation["items"] if _name(item)}
        if (
            not row
            or row.get("status", "completed") != "completed"
            or not isinstance(result, dict)
        ):
            item_fn += len(true_items)
            continue
        completed += 1
        correct = _dish_correct(annotation, result)
        dish_correct += int(correct)
        dish_top3 += int((_dish_rank(annotation, result) or 99) <= 3)
        confidence = result.get("confidence")
        if _number(confidence) and 0 <= confidence <= 1:
            confidence_pairs.append((float(confidence), int(correct)))

        predicted_items = {
            _name(item.get("name"))
            for item in result.get("items", [])
            if isinstance(item, dict) and _name(item.get("name"))
        }
        item_tp += len(true_items & predicted_items)
        item_fp += len(predicted_items - true_items)
        item_fn += len(true_items - predicted_items)

        predicted_calories = result.get("calories")
        current_error = None
        if _number(predicted_calories):
            error = abs(float(predicted_calories) - float(annotation["calories"]))
            current_error = error
            calorie_errors.append(error)
            if annotation["calories"] > 0:
                calorie_percentage_errors.append(error / annotation["calories"] * 100)
            low, high = (
                result.get("calorie_range_low"),
                result.get("calorie_range_high"),
            )
            range_covered += int(
                _number(low)
                and _number(high)
                and float(low) <= annotation["calories"] <= float(high)
            )
            item_values = [
                item.get("calories")
                for item in result.get("items", [])
                if isinstance(item, dict)
            ]
            if item_values and all(_number(value) for value in item_values):
                item_sum_evaluable += 1
                tolerance = max(20.0, abs(float(predicted_calories)) * 0.1)
                consistent += int(
                    abs(sum(item_values) - float(predicted_calories)) <= tolerance
                )
        for nutrient in macro_errors:
            truth, predicted = annotation.get(nutrient), result.get(nutrient)
            if _number(truth) and _number(predicted):
                error = abs(float(predicted) - float(truth))
                macro_errors[nutrient].append(error)
                if float(truth) > 0:
                    macro_percentage_errors[nutrient].append(error / float(truth) * 100)
        for key, value in (annotation.get("strata") or {}).items():
            strata[f"{key}={value}"].append((correct, current_error))

    item_precision = item_tp / (item_tp + item_fp) if item_tp + item_fp else None
    item_recall = item_tp / (item_tp + item_fn) if item_tp + item_fn else None
    item_f1 = (
        2 * item_precision * item_recall / (item_precision + item_recall)
        if item_precision is not None
        and item_recall is not None
        and item_precision + item_recall
        else None
    )
    return {
        "sample_size": len(annotations),
        "completed_samples": completed,
        "coverage_pct": _rate(completed, len(annotations)),
        "dish_accuracy_all_samples_pct": _rate(dish_correct, len(annotations)),
        "dish_accuracy_completed_pct": _rate(dish_correct, completed),
        "dish_top3_accuracy_all_samples_pct": _rate(dish_top3, len(annotations)),
        "dish_top3_accuracy_completed_pct": _rate(dish_top3, completed),
        "item_detection": {
            "precision_pct": round(item_precision * 100, 2)
            if item_precision is not None
            else None,
            "recall_pct": round(item_recall * 100, 2)
            if item_recall is not None
            else None,
            "f1_pct": round(item_f1 * 100, 2) if item_f1 is not None else None,
            "tp": item_tp,
            "fp": item_fp,
            "fn": item_fn,
        },
        "calorie_mae_kcal": round(mean(calorie_errors), 2) if calorie_errors else None,
        "calorie_mape_pct": round(mean(calorie_percentage_errors), 2)
        if calorie_percentage_errors
        else None,
        "calorie_range_coverage_pct": _rate(range_covered, len(calorie_errors)),
        "macronutrients": {
            nutrient: {
                "sample_size": len(macro_errors[nutrient]),
                "mae_g": round(mean(macro_errors[nutrient]), 2) if macro_errors[nutrient] else None,
                "mape_pct": round(mean(macro_percentage_errors[nutrient]), 2)
                if macro_percentage_errors[nutrient]
                else None,
            }
            for nutrient in macro_errors
        },
        "item_sum_consistency_pct": _rate(consistent, item_sum_evaluable),
        "confidence_brier": round(
            mean(
                (confidence - outcome) ** 2 for confidence, outcome in confidence_pairs
            ),
            4,
        )
        if confidence_pairs
        else None,
        "strata": {
            key: {
                "sample_size": len(values),
                "dish_accuracy_pct": _rate(
                    sum(correct for correct, _ in values), len(values)
                ),
                "calorie_mae_kcal": round(
                    mean(error for _, error in values if error is not None), 2
                )
                if any(error is not None for _, error in values)
                else None,
            }
            for key, values in sorted(strata.items())
        },
        "notes": [
            "未完成样本计入全样本菜名准确率分母，防止以失败换取高分。",
            "热量区间覆盖率必须与区间宽度一起解读；本报告不将营养估算视为医学测量。",
            "指标只对输入的冻结标注集有效，示例数据不得作为比赛结论。",
        ],
    }
