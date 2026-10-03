"""侧平举（lateral_raise）专属测量（能力计划 §5.7）。

§5.7 对侧平举的要求：最高角度、耸肩代理、躯干摆动、速度控制。

测量实现（规则化 v1）：

1. ``top_abduction_deg``：每次区间内**肩外展角**最大值。外展角 =
   ``180 - shoulder_angle_deg``（手臂下垂时肩角≈180 → 外展≈0；抬平 ≈90）。
   这是图像平面的外展近似，不是三维肩关节角。
2. ``shrug_proxy_ratio``：每次区间内 ``shrug_proxy_ratio`` 的**最小值**
   （肩带被上提时肩-髋竖向距离缩短）。它是**代理**，不是斜方肌上束肌电。
3. ``torso_swing_deg``：每次区间内 ``torso_inclination_deg`` 的**极差**
   （借力摆动幅度）。

明确说明本模块**不做什么**：

* 不做速度控制评分：§5.7 的"速度控制"需要可靠的帧率与标注基线，本 v1 不
  用不稳定的二阶差分硬凑一个结论（宁缺勿假）。
* 不声称模型判据，不判断伤病，不给医学结论；不导入 numpy / mediapipe / torch。
* 侧机位测不到左右肩横向展开时，``top_abduction_deg`` 返回 ``unavailable``。
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
    sub_series,
    unavailable_finding,
    unavailable_per_span,
)

EXERCISE_ID = "lateral_raise"

# 肩角（躯干-肩-肘）与外展角的换算基准：手臂沿躯干下垂时肩角≈180°。
SHOULDER_ANGLE_STRAIGHT_DEG = 180.0


def measure(signal_windows, frame_ids, *, counter_result=None) -> list[RepFinding]:
    """产出侧平举的全部测量结论（顺序同登记表）。"""
    windows = build_windows(signal_windows, frame_ids)
    spans = spans_for(windows, counter_result)
    view = detect_view(windows)
    findings: list[RepFinding] = []
    findings.extend(_top_abduction(windows, spans, view))
    findings.extend(_shrug_proxy(windows, spans, view))
    findings.extend(_torso_swing(windows, spans, view))
    return findings


def _abduction(window) -> float | None:
    """由 ``shoulder_angle_deg`` 推出外展角；取不到返回 None（不填 0）。"""
    angle = window.features.get("shoulder_angle_deg")
    if not isinstance(angle, (int, float)) or isinstance(angle, bool):
        return None
    return SHOULDER_ANGLE_STRAIGHT_DEG - float(angle)


def _top_abduction(windows, spans, view) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "top_abduction_deg")
    if view not in definition.valid_views:
        return unavailable_per_span(
            exercise_id=EXERCISE_ID,
            measurement_key=definition.key,
            reason_key=f"view_unsupported:{view}",
            spans=spans,
        )
    series = series_for(
        "shoulder_angle_deg",
        windows,
        region_gate=definition.visible_regions,
        derive=_abduction,
    )
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
        value = _abduction(window)
        if value is None:
            out.append(
                unavailable_finding(
                    exercise_id=EXERCISE_ID,
                    measurement_key=definition.key,
                    reason_key="abduction_undefined",
                    rep_index=span.rep_index,
                )
            )
            continue
        status = "good" if value >= limit else "attention"
        out.append(
            make_finding(
                exercise_id=EXERCISE_ID,
                measurement_key=definition.key,
                observed_value=value,
                status=status,
                explanation_key=(
                    "raise_height_ok" if status == "good" else M.VIOLATION_RANGE_SHORT
                ),
                rep_index=span.rep_index,
                frames=[window],
            )
        )
    return out


def _shrug_proxy(windows, spans, view) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "shrug_proxy_ratio")
    if view not in definition.valid_views:
        return unavailable_per_span(
            exercise_id=EXERCISE_ID,
            measurement_key=definition.key,
            reason_key=f"view_unsupported:{view}",
            spans=spans,
        )
    series = series_for(
        "shrug_proxy_ratio", windows, region_gate=definition.visible_regions
    )
    drop_limit = definition.thresholds["max_ratio"]
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
        # 相对该次起点（手臂下垂）的最大缩短量 = 耸肩代理。
        baseline = span_series.values[0]
        window = peak_window(span_series, mode="min")
        assert window is not None
        value = baseline - min(span_series.values)
        status = "good" if value <= drop_limit else "attention"
        out.append(
            make_finding(
                exercise_id=EXERCISE_ID,
                measurement_key=definition.key,
                observed_value=value,
                status=status,
                explanation_key=(
                    "shoulders_down" if status == "good" else M.VIOLATION_SHRUG
                ),
                rep_index=span.rep_index,
                frames=[window],
            )
        )
    return out


def _torso_swing(windows, spans, view) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "torso_swing_deg")
    if view not in definition.valid_views:
        return unavailable_per_span(
            exercise_id=EXERCISE_ID,
            measurement_key=definition.key,
            reason_key=f"view_unsupported:{view}",
            spans=spans,
        )
    series = series_for(
        "torso_inclination_deg", windows, region_gate=definition.visible_regions
    )
    limit = definition.thresholds["max_deg"]
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
        swing = max(span_series.values) - min(span_series.values)
        status = "good" if swing <= limit else "attention"
        out.append(
            make_finding(
                exercise_id=EXERCISE_ID,
                measurement_key=definition.key,
                observed_value=swing,
                status=status,
                explanation_key=(
                    "torso_stable" if status == "good" else M.VIOLATION_TORSO_SWING
                ),
                rep_index=span.rep_index,
                frames=span_series.windows,
            )
        )
    return out
