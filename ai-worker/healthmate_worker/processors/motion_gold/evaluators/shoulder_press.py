"""肩推（shoulder_press）专属测量（能力计划 §5.7）。

§5.7 对肩推的要求：腕肘堆叠代理、左右同步、活动范围、躯干后仰。

测量实现（规则化 v1）：

1. ``wrist_stack_ratio``：每次区间内 ``wrist_stack_ratio``（腕相对肘的横向
   偏移 / 肩宽）的**最大值**——过大说明腕没有落在肘上方（堆叠代理）；
2. ``wrist_sync_ratio``：**左右同步代理**。单目二维下"左右腕高度差"没有稳定
   观测，因此本实现使用 ``elbow_angle_deg`` 的左右差角 / 100（把角度换算成
   与登记阈值同量纲的无量纲比值，见 :data:`DEGREE_TO_RATIO`），并在解释键中
   标注 ``proxy``。它衡量的是"两侧是否同时推起"，不声称测到腕部轨迹。
3. ``top_elbow_angle_deg``：每次区间内肘角**最大值**（推起顶点）；
4. ``torso_lean_deg``：侧机位下每次区间内躯干倾角的**最大值**（后仰借力）。

明确说明本模块**不做什么**：

* "腕肘堆叠"是图像平面代理，不等于三维肩胛平面评估；文档与解释键均标注为
  proxy，不冒充关节中心轨迹。
* 不做负重、不做关节健康判断；不导入 numpy / mediapipe / torch。
* 正面机位测不到躯干后仰时返回 ``unavailable``，不换用其它指标。
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

EXERCISE_ID = "shoulder_press"

# 角度 → 无量纲比值的换算（登记阈值以比值书写，左右差角以度观测）。
DEGREE_TO_RATIO = 1.0 / 100.0


def measure(signal_windows, frame_ids, *, counter_result=None) -> list[RepFinding]:
    """产出肩推的全部测量结论（顺序同登记表）。"""
    windows = build_windows(signal_windows, frame_ids)
    spans = spans_for(windows, counter_result)
    view = detect_view(windows)
    findings: list[RepFinding] = []
    findings.extend(_wrist_stack(windows, spans, view))
    findings.extend(_wrist_sync(windows, spans, view))
    findings.extend(_top_elbow(windows, spans, view))
    findings.extend(_torso_lean(windows, spans, view))
    return findings


def _wrist_stack(windows, spans, view) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "wrist_stack_ratio")
    if view not in definition.valid_views:
        return unavailable_per_span(
            exercise_id=EXERCISE_ID,
            measurement_key=definition.key,
            reason_key=f"view_unsupported:{view}",
            spans=spans,
        )
    series = series_for(
        "wrist_stack_ratio", windows, region_gate=definition.visible_regions
    )
    limit = definition.thresholds["max_ratio"]
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
                    "stack_ok.proxy"
                    if status == "good"
                    else f"{M.VIOLATION_WRIST_NOT_STACKED}.proxy"
                ),
                rep_index=span.rep_index,
                frames=[window],
            )
        )
    return out


def _wrist_sync(windows, spans, view) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "wrist_sync_ratio")
    if view not in definition.valid_views:
        return unavailable_per_span(
            exercise_id=EXERCISE_ID,
            measurement_key=definition.key,
            reason_key=f"view_unsupported:{view}",
            spans=spans,
        )
    series = series_for(
        "elbow_symmetry_deg",
        windows,
        region_gate=definition.visible_regions,
        derive=lambda window: _scaled_symmetry(window),
    )
    limit = definition.thresholds["max_ratio"]
    out: list[RepFinding] = []
    for span in spans:
        span_series = sub_series(series, span)
        window = peak_window(span_series, mode="max")
        if window is None:
            out.append(
                unavailable_finding(
                    exercise_id=EXERCISE_ID,
                    measurement_key=definition.key,
                    reason_key="wrist_dy_unavailable",
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
                    "sync_ok.proxy" if status == "good"
                    else f"{M.VIOLATION_WRIST_ASYNC}.proxy"
                ),
                rep_index=span.rep_index,
                frames=[window],
            )
        )
    return out


def _scaled_symmetry(window) -> float | None:
    """左右肘角差（度）→ 与登记阈值同量纲的无量纲比值；不可测返回 None。"""
    difference = window.features.get("elbow_symmetry_deg")
    if not isinstance(difference, (int, float)) or isinstance(difference, bool):
        return None
    return float(difference) * DEGREE_TO_RATIO


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
        value = float(window.features["elbow_angle_deg"])
        status = "good" if value >= limit else "attention"
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


def _torso_lean(windows, spans, view) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "torso_lean_deg")
    if view not in definition.valid_views:
        # 正面机位无法读出"后仰"：显式不可测。
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
                    "torso_upright" if status == "good" else M.VIOLATION_TORSO_LEAN_BACK
                ),
                rep_index=span.rep_index,
                frames=[window],
            )
        )
    return out
