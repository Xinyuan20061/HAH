"""平板支撑（plank）专属测量（能力计划 §5.7）。

§5.7 对平板的要求：肩髋踝线偏差、髋部下沉/抬高、有效保持时长。

测量实现（规则化 v1）：

1. ``body_line_deg``：整段（静态动作无"次数"）``body_line_deg``（肩-髋-踝
   夹角）的**最小值**；越小说明某时刻对线越差。
2. ``sagittal_deviation_deg``：整段 ``sagittal_hip_deg``（肩-髋-膝夹角）相对
   :data:`..measurements.PLANK_BODY_LINE_MIN_DEG` 的**最大绝对偏差**。偏差方向
   决定解释键：``sagittal_hip_deg`` 偏小 → 髋部下沉代理（``hip_sag``）；偏大 →
   髋部抬高代理（``hip_pike``）。两者都是二维代理，不是三维骨盆位置。
3. ``hold_seconds``：来自可审计计次器（:mod:`..counters`）的静态保持时长；
   未提供计次结果时本项为 ``unavailable``——本模块**不自己数秒**，避免出现
   两套互相矛盾的时长。

明确说明本模块**不做什么**：

* 不做"评分"，只给可解释的对线结论；不导入 numpy / mediapipe / torch。
* 不在正面机位给对线结论（正面投影读不出矢状面直线偏差）。
* 不把保持时长当成"姿势正确"的证明：时长与对线是两条独立结论。
"""

from __future__ import annotations

from .. import measurements as M
from ..contracts import RepFinding
from ._common import (
    build_windows,
    detect_view,
    make_finding,
    peak_window,
    series_for,
    spans_for,
    unavailable_finding,
    unavailable_per_span,
    window_index_at_ms,
)

EXERCISE_ID = "plank"


def measure(signal_windows, frame_ids, *, counter_result=None) -> list[RepFinding]:
    """产出平板的全部测量结论（顺序同登记表）。"""
    windows = build_windows(signal_windows, frame_ids)
    spans = spans_for(windows, counter_result)
    view = detect_view(windows)
    findings: list[RepFinding] = []
    findings.extend(_body_line(windows, spans, view))
    findings.extend(_sagittal_deviation(windows, spans, view))
    findings.extend(_hold_seconds(windows, counter_result))
    return findings


def _body_line(windows, spans, view) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "body_line_deg")
    if view not in definition.valid_views:
        return unavailable_per_span(
            exercise_id=EXERCISE_ID,
            measurement_key=definition.key,
            reason_key=f"view_unsupported:{view}",
            spans=spans,
        )
    series = series_for("body_line_deg", windows, region_gate=definition.visible_regions)
    window = peak_window(series, mode="min")
    if window is None:
        return [
            unavailable_finding(
                exercise_id=EXERCISE_ID,
                measurement_key=definition.key,
                reason_key="regions_unavailable",
            )
        ]
    value = float(window.features["body_line_deg"])
    limit = definition.thresholds["min_deg"]
    status = "good" if value >= limit else "attention"
    return [
        make_finding(
            exercise_id=EXERCISE_ID,
            measurement_key=definition.key,
            observed_value=value,
            status=status,
            explanation_key=(
                "body_line_ok" if status == "good" else M.VIOLATION_BODY_LINE_BREAK
            ),
            frames=[window],
        )
    ]


def _sagittal_deviation(windows, spans, view) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "sagittal_deviation_deg")
    if view not in definition.valid_views:
        return unavailable_per_span(
            exercise_id=EXERCISE_ID,
            measurement_key=definition.key,
            reason_key=f"view_unsupported:{view}",
            spans=spans,
        )
    series = series_for(
        "sagittal_hip_deg", windows, region_gate=definition.visible_regions
    )
    reference = definition.thresholds["min_deg"]
    deviation_limit = definition.thresholds["max_deviation_deg"]
    if not series.values:
        return [
            unavailable_finding(
                exercise_id=EXERCISE_ID,
                measurement_key=definition.key,
                reason_key="regions_unavailable",
            )
        ]
    # 找到偏离参考角最大的那一帧（正负都算），报告绝对偏差。
    position = max(
        range(len(series.values)), key=lambda i: abs(series.values[i] - reference)
    )
    window = series.windows[position]
    signed = series.values[position] - reference
    value = abs(signed)
    status = "good" if value <= deviation_limit else "attention"
    if status == "good":
        explanation = "posture_ok"
    else:
        explanation = M.VIOLATION_SAG if signed < 0 else M.VIOLATION_PIKE
    return [
        make_finding(
            exercise_id=EXERCISE_ID,
            measurement_key=definition.key,
            observed_value=value,
            status=status,
            explanation_key=explanation,
            frames=[window],
        )
    ]


def _hold_seconds(windows, counter_result) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "hold_seconds")
    hold = getattr(counter_result, "hold_seconds", None)
    available = bool(getattr(counter_result, "available", False))
    if not available or not isinstance(hold, (int, float)) or isinstance(hold, bool):
        return [
            unavailable_finding(
                exercise_id=EXERCISE_ID,
                measurement_key=definition.key,
                reason_key="no_counter_result",
            )
        ]
    frames = []
    for segment in getattr(counter_result, "segments", None) or []:
        index = window_index_at_ms(windows, getattr(segment, "start_ms", None))
        if index is not None:
            frames.append(windows[index])
    if not frames:
        # 计次结果与帧窗口对不上：不谎报"有证据"，直接不可用。
        return [
            unavailable_finding(
                exercise_id=EXERCISE_ID,
                measurement_key=definition.key,
                reason_key="hold_evidence_unavailable",
            )
        ]
    return [
        make_finding(
            exercise_id=EXERCISE_ID,
            measurement_key=definition.key,
            observed_value=float(hold),
            status="good",
            explanation_key="hold_measured",
            frames=frames,
        )
    ]
