"""二头弯举（bicep_curl）专属测量（能力计划 §5.7）。

§5.7 对弯举的要求：肘部漂移、活动范围、离心控制、左右对称。

测量实现（规则化 v1）：

1. ``elbow_drift_deg``：每次区间内 ``elbow_drift_ratio``（肩-肘竖向距离 /
   身体尺度，无量纲）的**极差**。登记表里的阈值按角度书写，这里用
   :data:`DEGREE_TO_SCALE_RATIO` 显式换算成无量纲门槛，避免"角度阈值直接套
   在比值上"的假精确。
2. ``top_elbow_angle_deg``：每次区间内肘角**最小值**（最大屈曲 = 动作顶点）；
3. ``elbow_symmetry_deg``：每次区间内左右肘角差的**均值**（正面机位才可测）。

明确说明本模块**不做什么**：

* 不声称模型判据，不判断伤病，不给医学结论；不导入 numpy / mediapipe / torch。
* 侧机位测不到左右对称时返回 ``unavailable``，不拿单侧数据冒充对称性。
* 不把"离心控制"包装成看起来精确的数字：本 v1 只用活动范围与漂移代理；
  真正的离心时长/加速度控制需要标注数据校准后才能给出。
"""

from __future__ import annotations

from .. import measurements as M
from ..contracts import RepFinding
from ._common import (
    aggregate_simple,
    build_windows,
    detect_view,
    make_finding,
    peak_window,
    series_for,
    spans_for,
    sub_series,
    unavailable_finding,
    unavailable_per_span,
)

EXERCISE_ID = "bicep_curl"

# 登记阈值（角度）→ 无量纲漂移门槛的换算：登记值按"角度"书写，而特征里的
# ``elbow_drift_ratio`` 是"身体尺度倍数"。这里给出显式换算常量，而不是把
# 60 这样的裸数字写进比较式。
DEGREE_TO_SCALE_RATIO = 1.0 / 100.0


def measure(signal_windows, frame_ids, *, counter_result=None) -> list[RepFinding]:
    """产出二头弯举的全部测量结论（顺序同登记表）。"""
    windows = build_windows(signal_windows, frame_ids)
    spans = spans_for(windows, counter_result)
    view = detect_view(windows)
    findings: list[RepFinding] = []
    findings.extend(_elbow_drift(windows, spans, view))
    findings.extend(_top_elbow(windows, spans, view))
    findings.extend(_elbow_symmetry(windows, spans, view))
    return findings


def _elbow_drift(windows, spans, view) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "elbow_drift_deg")
    if view not in definition.valid_views:
        # 正面机位下"肘部前后漂移"投影不可读：显式不可测。
        return unavailable_per_span(
            exercise_id=EXERCISE_ID,
            measurement_key=definition.key,
            reason_key=f"view_unsupported:{view}",
            spans=spans,
        )
    series = series_for(
        "elbow_drift_ratio", windows, region_gate=definition.visible_regions
    )
    limit = definition.thresholds["max_deg"] * DEGREE_TO_SCALE_RATIO
    out: list[RepFinding] = []
    for span in spans:
        span_series = sub_series(series, span)
        if not span_series.values:
            out.append(
                unavailable_finding(
                    exercise_id=EXERCISE_ID,
                    measurement_key=definition.key,
                    reason_key="regions_unavailable",
                    rep_index=span.rep_index,
                )
            )
            continue
        drift = max(span_series.values) - min(span_series.values)
        status = "good" if drift <= limit else "attention"
        out.append(
            make_finding(
                exercise_id=EXERCISE_ID,
                measurement_key=definition.key,
                observed_value=drift,
                status=status,
                explanation_key=(
                    "elbow_stable" if status == "good" else M.VIOLATION_ELBOW_DRIFT
                ),
                rep_index=span.rep_index,
                frames=span_series.windows,
            )
        )
    if not out:
        return [
            unavailable_finding(
                exercise_id=EXERCISE_ID,
                measurement_key=definition.key,
                reason_key="no_measurement_window",
            )
        ]
    return out


def _top_elbow(windows, spans, view) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "top_elbow_angle_deg")
    if view not in definition.valid_views:
        return unavailable_per_span(
            exercise_id=EXERCISE_ID,
            measurement_key=definition.key,
            reason_key=f"view_unsupported:{view}",
            spans=spans,
        )
    series = series_for("elbow_angle_deg", windows, region_gate=definition.visible_regions)
    target = definition.thresholds["target_deg"]
    tolerance = definition.thresholds["tolerance_deg"]
    out: list[RepFinding] = []
    for span in spans:
        window = peak_window(sub_series(series, span), mode="min")
        if window is None:
            out.append(
                unavailable_finding(
                    exercise_id=EXERCISE_ID,
                    measurement_key=definition.key,
                    reason_key="regions_unavailable",
                    rep_index=span.rep_index,
                )
            )
            continue
        value = float(window.features["elbow_angle_deg"])
        status = "good" if abs(value - target) <= tolerance else "attention"
        out.append(
            make_finding(
                exercise_id=EXERCISE_ID,
                measurement_key=definition.key,
                observed_value=value,
                status=status,
                explanation_key=(
                    "full_range" if status == "good" else M.VIOLATION_RANGE_SHORT
                ),
                rep_index=span.rep_index,
                frames=[window],
            )
        )
    return out


def _elbow_symmetry(windows, spans, view) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "elbow_symmetry_deg")
    if view not in definition.valid_views:
        return unavailable_per_span(
            exercise_id=EXERCISE_ID,
            measurement_key=definition.key,
            reason_key=f"view_unsupported:{view}",
            spans=spans,
        )
    series = series_for("elbow_symmetry_deg", windows, region_gate=definition.visible_regions)
    return aggregate_simple(
        exercise_id=EXERCISE_ID,
        definition=definition,
        series=series,
        spans=spans,
        limit=definition.thresholds["max_deg"],
        greater_is_better=False,
        good_key="symmetry_ok",
        attention_key=M.VIOLATION_ELBOW_ASYMMETRY,
    )
