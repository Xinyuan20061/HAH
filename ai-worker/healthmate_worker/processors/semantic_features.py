"""Compositional pose semantics that do not require an exact exercise label.

These are observable movement descriptors, not claims about muscle activation or
training outcomes.  They remain useful when the closed-set recognizer abstains.
"""

from __future__ import annotations

import math
from statistics import median


MIN_SAMPLES = 4


def _finite(value) -> bool:
    return type(value) in {int, float} and math.isfinite(value)


def _values(rows: list[dict], key: str) -> list[float]:
    return [float(row[key]) for row in rows if _finite(row.get(key))]


def _range(rows: list[dict], key: str) -> float:
    values = _values(rows, key)
    return max(values) - min(values) if values else 0.0


def _score(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    return round(max(0.0, min(1.0, (value - low) / (high - low))) * 100, 1)


def infer_compositional_semantics(sample_sets: dict[str, list[dict]]) -> dict:
    """Describe visible motion before assigning a named exercise.

    All labels are deliberately coarse and compositional.  A future trained
    skeleton-text model can replace this implementation without changing the API.
    """
    streams = [rows for rows in sample_sets.values() if isinstance(rows, list)]
    rows = max(streams, key=len, default=[])
    rows = [
        row
        for row in rows
        if _finite(row.get("visibility"))
        and float(row["visibility"]) >= 0.5
        and _finite(row.get("knee"))
        and _finite(row.get("elbow"))
        and _finite(row.get("trunk"))
    ]
    if len(rows) < MIN_SAMPLES:
        return {
            "available": False,
            "method": "pose_compositional_rules_v1",
            "confidence": 0.0,
            "movement_patterns": [],
            "observed_regions": [],
            "reason": "可见骨骼样本不足，无法生成动作模式描述。",
            "scope": "可观察动作语义，不代表动作名称、肌肉激活或训练效果",
        }

    knee_range = _range(rows, "knee")
    elbow_range = _range(rows, "elbow")
    trunk_values = _values(rows, "trunk")
    trunk_median = float(median(trunk_values)) if trunk_values else 0.0
    body_line_values = _values(rows, "body_line")
    body_line_median = float(median(body_line_values)) if body_line_values else 0.0
    asymmetries = [
        abs(float(row["knee"]) - float(row["back_knee"]))
        for row in rows
        if _finite(row.get("back_knee"))
    ]
    asymmetry = float(median(asymmetries)) if asymmetries else 0.0

    if trunk_median >= 55:
        orientation_key, orientation_name = "horizontal", "躯干接近水平"
    elif trunk_median <= 30:
        orientation_key, orientation_name = "upright", "躯干接近直立"
    else:
        orientation_key, orientation_name = "inclined", "躯干明显倾斜"
    laterality_key = "unilateral" if asymmetry >= 12 else "bilateral"
    laterality_name = "单侧/不对称下肢模式" if laterality_key == "unilateral" else "双侧近似对称模式"

    patterns = []

    def add_pattern(key: str, name: str, score: float, evidence: str):
        patterns.append(
            {"key": key, "name": name, "score": round(score, 1), "evidence": evidence}
        )

    if knee_range >= 18:
        add_pattern(
            "knee_dominant",
            "膝主导屈伸",
            _score(knee_range, 18, 90),
            f"膝角活动范围约 {knee_range:.1f}°",
        )
        add_pattern(
            "unilateral_lower_body" if laterality_key == "unilateral" else "bilateral_lower_body",
            "单侧下肢模式" if laterality_key == "unilateral" else "双侧下肢模式",
            max(_score(knee_range, 18, 90), _score(asymmetry, 12, 45)) if laterality_key == "unilateral" else _score(knee_range, 18, 90),
            f"左右膝角中位差约 {asymmetry:.1f}°",
        )
    if elbow_range >= 18:
        add_pattern(
            "elbow_flexion_extension",
            "肘关节屈伸",
            _score(elbow_range, 18, 90),
            f"肘角活动范围约 {elbow_range:.1f}°",
        )
        if orientation_key == "horizontal":
            add_pattern(
                "horizontal_upper_body",
                "水平体位上肢动作",
                round((_score(elbow_range, 18, 90) + _score(trunk_median, 55, 82)) / 2, 1),
                f"躯干倾角中位数约 {trunk_median:.1f}°",
            )
    if not patterns:
        add_pattern(
            "static_or_low_amplitude",
            "静态或低幅度动作",
            _score(18 - max(knee_range, elbow_range), 0, 18),
            f"最大关节活动范围约 {max(knee_range, elbow_range):.1f}°",
        )

    regions = []
    if knee_range >= 18:
        regions.append({"key": "lower_body", "name": "下肢", "basis": "检测到明显膝关节屈伸"})
    if elbow_range >= 18:
        regions.append({"key": "upper_body", "name": "上肢", "basis": "检测到明显肘关节屈伸"})
    if orientation_key == "horizontal" and body_line_median >= 145:
        regions.append({"key": "trunk_stability", "name": "躯干稳定", "basis": "水平体位下身体线条较连续"})

    strongest_range = max(knee_range, elbow_range)
    confidence = min(1.0, len(rows) / 12) * min(1.0, strongest_range / 55)
    return {
        "available": True,
        "method": "pose_compositional_rules_v1",
        "confidence": round(confidence, 3),
        "orientation": {"key": orientation_key, "name": orientation_name},
        "laterality": {"key": laterality_key, "name": laterality_name},
        "movement_patterns": sorted(patterns, key=lambda item: (-item["score"], item["key"])),
        "observed_regions": regions,
        "evidence": {
            "valid_samples": len(rows),
            "knee_range_degrees": round(knee_range, 2),
            "elbow_range_degrees": round(elbow_range, 2),
            "trunk_median_degrees": round(trunk_median, 2),
            "knee_asymmetry_median_degrees": round(asymmetry, 2),
        },
        "scope": "可观察动作语义，不代表动作名称、肌肉激活或训练效果",
    }
