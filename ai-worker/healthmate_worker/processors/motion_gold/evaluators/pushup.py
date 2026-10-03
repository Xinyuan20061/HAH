"""俯卧撑（pushup）专属测量（能力计划 §5.7）。

§5.7 对俯卧撑的要求：肘角范围、肩髋踝直线偏差、底部深度、重复节奏。

测量实现（规则化 v1）：

1. ``elbow_rom_deg``：每次动作区间内肘角**最小值**（底部深度）；
2. ``body_line_deg``：每次区间内肩-髋-踝夹角**最小值**（最差对线时刻）；
3. ``rep_period_cv``：各次周期时长的变异系数（少于 2 次无定义）。

明确说明本模块**不做什么**：

* 不声称这是训练好的模型；它是阈值 + 几何规则，需真实标注数据验证。
* 肩/髋/踝任一不可见时 ``body_line_deg`` 返回 ``unavailable``，不拿肘角替代。
* 不判断伤病，不给医学结论；不导入 numpy / mediapipe / torch。
"""

from __future__ import annotations

from .. import measurements as M
from ..contracts import RepFinding
from ._common import (
    MS_PER_SECOND,
    build_windows,
    detect_view,
    is_finite,
    make_finding,
    peak_window,
    series_for,
    spans_for,
    stdev_of,
    sub_series,
    unavailable_finding,
    unavailable_per_span,
    window_index_at_ms,
)

EXERCISE_ID = "pushup"


def measure(signal_windows, frame_ids, *, counter_result=None) -> list[RepFinding]:
    """产出俯卧撑的全部测量结论（顺序同登记表）。

    本函数不推断"标准俯卧撑"，也不在机位/部位不满足时给数字。
    """
    windows = build_windows(signal_windows, frame_ids)
    spans = spans_for(windows, counter_result)
    view = detect_view(windows)
    findings: list[RepFinding] = []
    findings.extend(_elbow_rom(windows, spans, view))
    findings.extend(_body_line(windows, spans, view))
    findings.extend(_rep_period(windows, spans, counter_result))
    return findings


def _elbow_rom(windows, spans, view) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "elbow_rom_deg")
    if view not in definition.valid_views:
        return unavailable_per_span(
            exercise_id=EXERCISE_ID,
            measurement_key=definition.key,
            reason_key=f"view_unsupported:{view}",
            spans=spans,
        )
    series = series_for("elbow_angle_deg", windows, region_gate=definition.visible_regions)
    target = definition.thresholds["bottom_target_deg"]
    tolerance = definition.thresholds["bottom_tolerance_deg"]
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
                    "depth_ok" if status == "good" else M.VIOLATION_DEPTH_SHALLOW
                ),
                rep_index=span.rep_index,
                frames=[window],
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


def _body_line(windows, spans, view) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "body_line_deg")
    if view not in definition.valid_views:
        # 正面机位下"直线偏差"投影不可读：显式不可测。
        return unavailable_per_span(
            exercise_id=EXERCISE_ID,
            measurement_key=definition.key,
            reason_key=f"view_unsupported:{view}",
            spans=spans,
        )
    series = series_for("body_line_deg", windows, region_gate=definition.visible_regions)
    limit = definition.thresholds["min_deg"]
    out: list[RepFinding] = []
    for span in spans:
        span_series = sub_series(series, span)
        window = peak_window(span_series, mode="min")
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
        value = float(window.features["body_line_deg"])
        status = "good" if value >= limit else "attention"
        out.append(
            make_finding(
                exercise_id=EXERCISE_ID,
                measurement_key=definition.key,
                observed_value=value,
                status=status,
                explanation_key=(
                    "body_line_ok" if status == "good" else M.VIOLATION_BODY_LINE_BREAK
                ),
                rep_index=span.rep_index,
                frames=[window],
            )
        )
    return out


def _rep_period(windows, spans, counter_result) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "rep_period_cv")
    durations: list[float] = []
    segments = getattr(counter_result, "segments", None) or []
    for segment in segments:
        start = getattr(segment, "start_ms", None)
        end = getattr(segment, "end_ms", None)
        if not (is_finite(start) and is_finite(end)):
            continue
        duration = (float(end) - float(start)) / MS_PER_SECOND
        if duration >= 0.0:
            durations.append(duration)
    if len(durations) < 2:
        return [
            unavailable_finding(
                exercise_id=EXERCISE_ID,
                measurement_key=definition.key,
                reason_key="single_rep_no_rhythm",
            )
        ]
    average = sum(durations) / len(durations)
    if average <= 0.0:
        return [
            unavailable_finding(
                exercise_id=EXERCISE_ID,
                measurement_key=definition.key,
                reason_key="degenerate_rep_period",
            )
        ]
    deviation = stdev_of(durations)
    coefficient = (deviation or 0.0) / average
    limit = definition.thresholds["max_cv"]
    status = "good" if coefficient <= limit else "attention"
    evidence_windows = _windows_at_segment_starts(windows, segments)
    return [
        make_finding(
            exercise_id=EXERCISE_ID,
            measurement_key=definition.key,
            observed_value=coefficient,
            status=status,
            explanation_key=(
                "rhythm_ok" if status == "good" else M.VIOLATION_RHYTHM_IRREGULAR
            ),
            frames=evidence_windows,
        )
    ]


def _windows_at_segment_starts(windows, segments):
    """取每次动作起始时刻最近的帧窗口作为节律证据（不外推、不造帧）。"""
    out = []
    for segment in segments:
        index = window_index_at_ms(windows, getattr(segment, "start_ms", None))
        if index is not None:
            out.append(windows[index])
    return out


__all__ = ["EXERCISE_ID", "measure"]
