"""弓步蹲（lunge）专属测量（能力计划 §5.7）。

§5.7 对弓步蹲的要求：前膝控制、左右稳定、后腿深度、步距一致性。

测量实现（规则化 v1）：

1. ``front_knee_min_deg``：每次区间内膝角**最小值**（前膝屈曲深度）；低于
   :data:`..measurements.LUNGE_FRONT_KNEE_MIN_DEG` 提示前膝屈曲过大；
2. ``hip_symmetry_deg``：每次区间内左右髋角差的**均值**（正面机位才可测）；
3. ``back_knee_angle_deg``：单目视频**无法区分前后腿**，因此本实现用同一条
   膝角序列的区间均值作为代理，并在文档与 ``explanation_key`` 中明确标注为
   ``back_leg_proxy``——它不声称真的测到了后腿；
4. ``step_span_cv``：各次动作中左右踝横向跨度（肩宽归一化）的变异系数。

明确说明本模块**不做什么**：

* 不区分前后腿却假装能区分；不用"后腿"结论误导用户（代理已在解释键中标注）。
* 不声称模型判据，不给医学结论，不导入 numpy / mediapipe / torch。
"""

from __future__ import annotations

from .. import measurements as M
from ..contracts import RepFinding
from ._common import (
    aggregate_sd_consistency,
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

EXERCISE_ID = "lunge"


def measure(signal_windows, frame_ids, *, counter_result=None) -> list[RepFinding]:
    """产出弓步蹲的全部测量结论（顺序同登记表）。"""
    windows = build_windows(signal_windows, frame_ids)
    spans = spans_for(windows, counter_result)
    view = detect_view(windows)
    findings: list[RepFinding] = []
    findings.extend(_front_knee(windows, spans))
    findings.extend(_hip_symmetry(windows, spans, view))
    findings.extend(_back_knee_proxy(windows, spans, view))
    findings.extend(_step_span(windows, spans, view))
    return findings


def _front_knee(windows, spans) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "front_knee_min_deg")
    series = series_for("knee_angle_deg", windows, region_gate=definition.visible_regions)
    limit = definition.thresholds["min_deg"]
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
        value = float(window.features["knee_angle_deg"])
        status = "good" if value >= limit else "attention"
        out.append(
            make_finding(
                exercise_id=EXERCISE_ID,
                measurement_key=definition.key,
                observed_value=value,
                status=status,
                explanation_key=(
                    "front_knee_ok" if status == "good" else M.VIOLATION_FRONT_KNEE_LOW
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


def _hip_symmetry(windows, spans, view) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "hip_symmetry_deg")
    if view not in definition.valid_views:
        return unavailable_per_span(
            exercise_id=EXERCISE_ID,
            measurement_key=definition.key,
            reason_key=f"view_unsupported:{view}",
            spans=spans,
        )
    series = series_for("hip_symmetry_deg", windows, region_gate=definition.visible_regions)
    return aggregate_simple(
        exercise_id=EXERCISE_ID,
        definition=definition,
        series=series,
        spans=spans,
        limit=definition.thresholds["max_deg"],
        greater_is_better=False,
        good_key="hip_stable",
        attention_key=M.VIOLATION_HIP_UNSTABLE,
    )


def _back_knee_proxy(windows, spans, view) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "back_knee_angle_deg")
    if view not in definition.valid_views:
        return unavailable_per_span(
            exercise_id=EXERCISE_ID,
            measurement_key=definition.key,
            reason_key=f"view_unsupported:{view}",
            spans=spans,
        )
    series = series_for("knee_angle_deg", windows, region_gate=definition.visible_regions)
    target = definition.thresholds["target_deg"]
    tolerance = definition.thresholds["tolerance_deg"]
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
        value = sum(span_series.values) / len(span_series.values)
        status = "good" if abs(value - target) <= tolerance else "attention"
        out.append(
            make_finding(
                exercise_id=EXERCISE_ID,
                measurement_key=definition.key,
                observed_value=value,
                status=status,
                explanation_key=(
                    "back_leg_ok"
                    if status == "good"
                    else f"{M.VIOLATION_BACK_LEG_SHALLOW}.back_leg_proxy"
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


def _step_span(windows, spans, view) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "step_span_cv")
    if view not in definition.valid_views:
        return unavailable_per_span(
            exercise_id=EXERCISE_ID,
            measurement_key=definition.key,
            reason_key=f"view_unsupported:{view}",
            spans=spans,
        )
    series = series_for("ankle_span_ratio", windows, region_gate=definition.visible_regions)
    per_rep: list[float] = []
    evidence = []
    for span in spans:
        span_series = sub_series(series, span)
        if not span_series.values:
            continue
        per_rep.append(sum(span_series.values) / len(span_series.values))
        evidence.append(span_series.windows[0])
    return aggregate_sd_consistency(
        exercise_id=EXERCISE_ID,
        definition=definition,
        per_span_values=per_rep,
        evidence=evidence,
        limit=definition.thresholds["max_cv"],
        good_key="step_consistent",
        attention_key=M.VIOLATION_STEP_INCONSISTENT,
        normalized=True,
    )
