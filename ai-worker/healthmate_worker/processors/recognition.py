"""Explainable rule-based exercise recognition with an explicit abstention path.

This is deliberately a feature matcher, not a trained classifier. Match scores are
useful for ranking the six supported movements but must not be presented as
calibrated probabilities.
"""

from __future__ import annotations

import math
from statistics import median


SUPPORTED_EXERCISES = (
    "squat",
    "pushup",
    "lunge",
    "leg_abduction",
    "arm_abduction",
    "arm_vw",
)
MIN_VALID_SAMPLES = 4
MIN_MATCH_SCORE = 55.0
MIN_MARGIN = 8.0
MIN_AMPLITUDE = {
    "squat": 18.0,
    "pushup": 18.0,
    "lunge": 18.0,
    "leg_abduction": 18.0,
    "arm_abduction": 18.0,
    "arm_vw": 18.0,
}


def _finite(value) -> bool:
    return type(value) in {int, float} and math.isfinite(value)


def _scale(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    return max(0.0, min(1.0, (value - low) / (high - low)))


def _inverse(value: float, low: float, high: float) -> float:
    return 1.0 - _scale(value, low, high)


def _values(rows: list[dict], key: str) -> list[float]:
    return [float(row[key]) for row in rows if _finite(row.get(key))]


def _range(rows: list[dict], key: str) -> float:
    values = _values(rows, key)
    return max(values) - min(values) if values else 0.0


def _median(rows: list[dict], key: str, default: float = 0.0) -> float:
    values = _values(rows, key)
    return float(median(values)) if values else default


def _valid_rows(rows: list[dict]) -> list[dict]:
    return [
        row
        for row in rows
        if _finite(row.get("visibility"))
        and float(row["visibility"]) >= 0.5
        and _finite(row.get("knee"))
        and _finite(row.get("elbow"))
        and _finite(row.get("trunk"))
    ]


def _features(sample_sets: dict[str, list[dict]]) -> dict:
    fallback = next((rows for rows in sample_sets.values() if rows), [])
    squat = _valid_rows(sample_sets.get("squat", []))
    pushup = _valid_rows(sample_sets.get("pushup", []))
    lunge = _valid_rows(sample_sets.get("lunge", []))
    leg_abduction = _valid_rows(sample_sets.get("leg_abduction", fallback))
    arm_abduction = _valid_rows(sample_sets.get("arm_abduction", fallback))
    arm_vw = _valid_rows(sample_sets.get("arm_vw", fallback))
    asymmetry = [
        abs(float(row["knee"]) - float(row["back_knee"]))
        for row in lunge
        if _finite(row.get("back_knee"))
    ]
    return {
        "valid_samples": min(
            len(rows)
            for rows in [squat, pushup, lunge, leg_abduction, arm_abduction, arm_vw]
        ),
        "knee_range": _range(squat, "knee"),
        "elbow_range": _range(pushup, "elbow"),
        "pushup_knee_range": _range(pushup, "knee"),
        "trunk_median": _median(pushup, "trunk"),
        "body_line_median": _median(pushup, "body_line"),
        "lunge_knee_range": _range(lunge, "knee"),
        "knee_asymmetry_median": float(median(asymmetry)) if asymmetry else 0.0,
        "knee_asymmetry_peak": max(asymmetry) if asymmetry else 0.0,
        "leg_abduction_range": _range(leg_abduction, "leg_abduction"),
        "arm_abduction_range": _range(arm_abduction, "arm_abduction"),
        "arm_abduction_elbow_median": _median(arm_abduction, "arm_vw", 180),
        "arm_vw_range": _range(arm_vw, "arm_vw"),
        "arm_vw_position_median": _median(arm_vw, "arm_abduction", 180),
    }


def _candidate_scores(features: dict) -> dict[str, float]:
    knee_range = features["knee_range"]
    elbow_range = features["elbow_range"]
    pushup_knee_range = features["pushup_knee_range"]
    trunk = features["trunk_median"]
    body_line = features["body_line_median"]
    asymmetry = features["knee_asymmetry_median"]
    asymmetry_peak = features["knee_asymmetry_peak"]

    pushup = (
        0.42 * _scale(elbow_range, 20, 75)
        + 0.23 * _scale(trunk, 45, 82)
        + 0.20 * _scale(body_line, 145, 176)
        + 0.15 * _inverse(pushup_knee_range, 12, 55)
    )
    squat = (
        0.44 * _scale(knee_range, 25, 85)
        + 0.32 * _inverse(asymmetry, 10, 35)
        + 0.12 * _inverse(trunk, 28, 72)
        + 0.12 * _inverse(elbow_range, 15, 60)
    )
    lunge = (
        0.40 * _scale(features["lunge_knee_range"], 22, 78)
        + 0.38 * _scale(asymmetry, 12, 42)
        + 0.14 * _scale(asymmetry_peak, 25, 60)
        + 0.08 * _inverse(elbow_range, 15, 60)
    )
    leg_abduction = (
        0.58 * _scale(features["leg_abduction_range"], 15, 55)
        + 0.20 * _inverse(knee_range, 18, 65)
        + 0.12 * _inverse(elbow_range, 18, 65)
        + 0.10 * _inverse(trunk, 18, 55)
    )
    arm_abduction = (
        0.58 * _scale(features["arm_abduction_range"], 18, 85)
        + 0.20 * _scale(features["arm_abduction_elbow_median"], 135, 172)
        + 0.12 * _inverse(knee_range, 18, 65)
        + 0.10 * _inverse(features["arm_vw_range"], 20, 75)
    )
    arm_vw = (
        0.55 * _scale(features["arm_vw_range"], 18, 80)
        + 0.20 * _inverse(features["arm_vw_position_median"], 85, 165)
        + 0.15 * _inverse(knee_range, 18, 65)
        + 0.10 * _scale(features["arm_abduction_range"], 5, 45)
    )
    return {
        "squat": round(squat * 100, 1),
        "pushup": round(pushup * 100, 1),
        "lunge": round(lunge * 100, 1),
        "leg_abduction": round(leg_abduction * 100, 1),
        "arm_abduction": round(arm_abduction * 100, 1),
        "arm_vw": round(arm_vw * 100, 1),
    }


def recognize_exercise(sample_sets: dict[str, list[dict]]) -> dict:
    """Rank supported movements and abstain when evidence is weak or ambiguous."""
    features = _features(sample_sets)
    if features["valid_samples"] < MIN_VALID_SAMPLES:
        return {
            "mode": "auto",
            "requested_type": "auto",
            "selected_type": None,
            "accepted": False,
            "confidence": 0.0,
            "margin": 0.0,
            "method": "rule_feature_matching_v1",
            "candidates": [],
            "reason": "可见关键点样本不足，无法可靠识别动作；请完整入镜后重录，或手动选择动作。",
            "features": {key: round(value, 2) for key, value in features.items()},
        }

    scores = _candidate_scores(features)
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    top_type, top_score = ranked[0]
    margin = round(top_score - ranked[1][1], 1)
    movement_ranges = {
        "squat": features["knee_range"],
        "pushup": features["elbow_range"],
        "lunge": features["lunge_knee_range"],
        "leg_abduction": features["leg_abduction_range"],
        "arm_abduction": features["arm_abduction_range"],
        "arm_vw": features["arm_vw_range"],
    }
    movement_range = movement_ranges[top_type]
    accepted = (
        movement_range >= MIN_AMPLITUDE[top_type]
        and top_score >= MIN_MATCH_SCORE
        and margin >= MIN_MARGIN
    )
    confidence = round(
        max(0.0, min(1.0, (top_score / 100) * (0.55 + min(margin, 30) / 60))),
        3,
    )
    candidates = [
        {
            "exercise_type": exercise_type,
            "match_score": score,
            "rank": index,
        }
        for index, (exercise_type, score) in enumerate(ranked, 1)
    ]
    if accepted:
        reason = f"{top_type} 的动作特征匹配度最高，领先下一候选 {margin:.1f} 分。"
    elif movement_range < MIN_AMPLITUDE[top_type]:
        reason = "画面中未检测到足够的关节运动幅度；请完成至少一次完整动作后重试。"
    elif top_score < MIN_MATCH_SCORE:
        reason = "动作幅度或姿态特征不足，未达到自动识别阈值；请手动选择动作后重试。"
    else:
        reason = "前两名动作特征过于接近，系统已拒绝猜测；请手动选择动作后重试。"
    return {
        "mode": "auto",
        "requested_type": "auto",
        "selected_type": top_type if accepted else None,
        "accepted": accepted,
        "confidence": confidence if accepted else 0.0,
        "margin": margin,
        "method": "rule_feature_matching_v1",
        "candidates": candidates,
        "reason": reason,
        "features": {key: round(value, 2) for key, value in features.items()},
    }


def manual_recognition(exercise_type: str) -> dict:
    if exercise_type not in SUPPORTED_EXERCISES:
        raise ValueError("unsupported exercise type")
    return {
        "mode": "manual",
        "requested_type": exercise_type,
        "selected_type": exercise_type,
        "accepted": True,
        "confidence": 1.0,
        "margin": 100.0,
        "method": "user_selected",
        "candidates": [
            {"exercise_type": exercise_type, "match_score": 100.0, "rank": 1}
        ],
        "reason": "动作类型由用户手动选择。",
    }
