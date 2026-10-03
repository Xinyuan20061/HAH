"""臀桥（hip_bridge）专属测量（能力计划 §5.7）。

§5.7 对臀桥的要求：髋伸展幅度、顶部保持、左右骨盆差、节奏。

测量实现（规则化 v1）：

1. ``top_hip_angle_deg``：每次区间内 ``hip_angle_deg`` 的**最大值**（髋伸展顶点）；
2. ``top_hold_seconds``：每次区间内髋角处于"顶点附近"（>= 登记门槛
   :data:`..measurements.HIP_BRIDGE_TOP_HIP_MIN_DEG`）的累计时长；
3. ``pelvis_tilt_deg``：每次区间内 ``pelvis_tilt_ratio`` 的**最大值** × 100
   （无量纲比值 → 度，换算常量 :data:`RATIO_TO_DEGREE`）。它是"左右骨盆差"的
   二维代理，不是三维骨盆旋转角；仅正面/背面机位可测。
4. ``rep_period_cv``：各次周期时长的变异系数（少于 2 次无定义）。

明确说明本模块**不做什么**：

* 不声称模型判据，不判断伤病，不给医学结论；不导入 numpy / mediapipe / torch。
* 侧机位测不到左右骨盆差时返回 ``unavailable``，不拿整体髋角冒充对称性。
* 不把"顶部保持"当成"姿势正确"：保持与幅度是两条独立结论。
"""

from __future__ import annotations

from .. import measurements as M
from ..contracts import RepFinding
from ._common import (
    MS_PER_SECOND,
    aggregate_sd_consistency,
    build_windows,
    detect_view,
    is_finite,
    make_finding,
    peak_window,
    series_for,
    spans_for,
    sub_series,
    unavailable_finding,
    unavailable_per_span,
    window_index_at_ms,
)

EXERCISE_ID = "hip_bridge"

# 无量纲骨盆倾斜比值 → 度（登记阈值以度书写）。比值定义为 |Δy| / 身体尺度。
RATIO_TO_DEGREE = 100.0


def measure(signal_windows, frame_ids, *, counter_result=None) -> list[RepFinding]:
    """产出臀桥的全部测量结论（顺序同登记表）。"""
    windows = build_windows(signal_windows, frame_ids)
    spans = spans_for(windows, counter_result)
    view = detect_view(windows)
    findings: list[RepFinding] = []
    findings.extend(_top_hip_angle(windows, spans, view))
    findings.extend(_top_hold(windows, spans, view))
    findings.extend(_pelvis_tilt(windows, spans, view))
    findings.extend(_rep_period(windows, spans, counter_result))
    return findings


def _top_hip_angle(windows, spans, view) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "top_hip_angle_deg")
    if view not in definition.valid_views:
        return unavailable_per_span(
            exercise_id=EXERCISE_ID,
            measurement_key=definition.key,
            reason_key=f"view_unsupported:{view}",
            spans=spans,
        )
    series = series_for("hip_angle_deg", windows, region_gate=definition.visible_regions)
    limit = definition.thresholds["min_deg"]
    out: list[RepFinding] = []
    for span in spans:
        window = peak_window(sub_series(series, span), mode="max")
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
        value = float(window.features["hip_angle_deg"])
        status = "good" if value >= limit else "attention"
        out.append(
            make_finding(
                exercise_id=EXERCISE_ID,
                measurement_key=definition.key,
                observed_value=value,
                status=status,
                explanation_key=(
                    "hip_extension_ok" if status == "good" else M.VIOLATION_RANGE_SHORT
                ),
                rep_index=span.rep_index,
                frames=[window],
            )
        )
    return out


def _top_hold(windows, spans, view) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "top_hold_seconds")
    if view not in definition.valid_views:
        return unavailable_per_span(
            exercise_id=EXERCISE_ID,
            measurement_key=definition.key,
            reason_key=f"view_unsupported:{view}",
            spans=spans,
        )
    series = series_for("hip_angle_deg", windows, region_gate=definition.visible_regions)
    threshold = M.HIP_BRIDGE_TOP_HIP_MIN_DEG
    min_seconds = definition.thresholds["min_seconds"]
    out: list[RepFinding] = []
    for span in spans:
        span_series = sub_series(series, span)
        in_band = [
            window
            for value, window in zip(span_series.values, span_series.windows)
            if value >= threshold
        ]
        duration = _span_duration_seconds(in_band)
        if duration is None:
            out.append(
                unavailable_finding(
                    exercise_id=EXERCISE_ID,
                    measurement_key=definition.key,
                    reason_key="top_hold_unavailable",
                    rep_index=span.rep_index,
                )
            )
            continue
        status = "good" if duration >= min_seconds else "attention"
        out.append(
            make_finding(
                exercise_id=EXERCISE_ID,
                measurement_key=definition.key,
                observed_value=duration,
                status=status,
                explanation_key=(
                    "top_hold_ok" if status == "good" else M.VIOLATION_HOLD_SHORT
                ),
                rep_index=span.rep_index,
                frames=in_band,
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


def _span_duration_seconds(windows) -> float | None:
    """一组帧窗口的时间跨度（秒）；缺时间戳或帧数不足时返回 None（不外推）。"""
    stamps = [
        window.timestamp_ms for window in windows if isinstance(window.timestamp_ms, int)
    ]
    if len(stamps) < 2:
        return None
    return max(0.0, (max(stamps) - min(stamps)) / MS_PER_SECOND)


def _pelvis_tilt(windows, spans, view) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "pelvis_tilt_deg")
    if view not in definition.valid_views:
        return unavailable_per_span(
            exercise_id=EXERCISE_ID,
            measurement_key=definition.key,
            reason_key=f"view_unsupported:{view}",
            spans=spans,
        )
    series = series_for(
        "pelvis_tilt_ratio",
        windows,
        region_gate=definition.visible_regions,
        derive=_scaled_tilt,
    )
    limit = definition.thresholds["max_deg"]
    out: list[RepFinding] = []
    for span in spans:
        span_series = sub_series(series, span)
        window = peak_window(span_series, mode="max")
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
        value = max(span_series.values)
        status = "good" if value <= limit else "attention"
        out.append(
            make_finding(
                exercise_id=EXERCISE_ID,
                measurement_key=definition.key,
                observed_value=value,
                status=status,
                explanation_key=(
                    "pelvis_level" if status == "good" else M.VIOLATION_PELVIS_TILT
                ),
                rep_index=span.rep_index,
                frames=[window],
            )
        )
    return out


def _scaled_tilt(window) -> float | None:
    """骨盆倾斜比值 → 度；不可测返回 None（不填 0 表示"完全水平"）。"""
    ratio = window.features.get("pelvis_tilt_ratio")
    if not isinstance(ratio, (int, float)) or isinstance(ratio, bool):
        return None
    return float(ratio) * RATIO_TO_DEGREE


def _rep_period(windows, spans, counter_result) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "rep_period_cv")
    durations: list[float] = []
    segments = getattr(counter_result, "segments", None) or []
    for segment in segments:
        start = getattr(segment, "start_ms", None)
        end = getattr(segment, "end_ms", None)
        if not (is_finite(start) and is_finite(end)):
            continue
        durations.append(max(0.0, (float(end) - float(start)) / MS_PER_SECOND))
    evidence = []
    for segment in segments:
        index = window_index_at_ms(windows, getattr(segment, "start_ms", None))
        if index is not None:
            evidence.append(windows[index])
    return aggregate_sd_consistency(
        exercise_id=EXERCISE_ID,
        definition=definition,
        per_span_values=durations,
        evidence=evidence,
        limit=definition.thresholds["max_cv"],
        good_key="rhythm_ok",
        attention_key=M.VIOLATION_RHYTHM_IRREGULAR,
        normalized=True,
    )
