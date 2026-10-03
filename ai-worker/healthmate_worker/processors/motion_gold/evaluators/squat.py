"""深蹲（squat）专属测量（能力计划 §5.7）。

§5.7 对深蹲的要求：最低点膝/髋角、躯干倾角、左右膝轨迹、深度一致性。
本模块逐项实现，**不做**通用公式打分。

测量实现（规则化 v1，全部确定性）：

1. ``bottom_knee_angle_deg``：每次动作区间内膝角**最小值**（最低点）；
2. ``torso_inclination_deg``：每次动作区间内躯干倾角的**均值**；
3. ``knee_symmetry_deg``：每次区间内左右膝角差的**均值**（侧机位不可测）；
4. ``depth_consistency_sd_deg``：各次最低点膝角的标准差（少于 2 次无定义）。

明确说明本模块**不做什么**：

* 不声称这是训练好的模型判据；它是规则化 v1（阈值 + 几何），阈值来自
  :mod:`..measurements` 的登记表，需要真实标注数据才能验证或替换。
* 不在膝/髋/踝不可见、或机位不适配某项测量时给数字——一律
  ``status="unavailable"``，绝不换用无关指标凑分。
* 不判断伤病、不给医学结论；输出只是训练提示。
* 不导入 numpy / mediapipe / torch。
"""

from __future__ import annotations

from .. import measurements as M
from ..contracts import RepFinding
from ._common import (
    aggregate_simple,
    aggregate_sd_consistency,
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

EXERCISE_ID = "squat"


def measure(signal_windows, frame_ids, *, counter_result=None) -> list[RepFinding]:
    """产出深蹲的全部测量结论。

    Parameters
    ----------
    signal_windows : 逐帧窗口序列（``features`` 或 ``landmarks``）。
    frame_ids : 可选帧 id 列表，窗口缺 ``frame_id`` 时补齐。
    counter_result : 计次结果。提供时按次测量；缺省时退化为整段，并在
        ``explanation_key`` 里由调用方可见地体现（``no_measurement_window``）。

    Returns
    -------
    list[RepFinding]
        顺序与 :data:`..measurements.SQUAT_MEASUREMENTS` 一致。缺证据或机位不适配
        的项为 ``unavailable``（``observed_value=None``）。

    本函数不推断"标准深蹲"，只用登记阈值做区间判定，也不给 0–100 总分。
    """
    windows = build_windows(signal_windows, frame_ids)
    spans = spans_for(windows, counter_result)
    view = detect_view(windows)
    findings: list[RepFinding] = []
    findings.extend(_bottom_knee_angle(windows, spans))
    findings.extend(_torso_inclination(windows, spans, view))
    findings.extend(_knee_symmetry(windows, spans, view))
    findings.extend(_depth_consistency(windows, spans))
    return findings


def _bottom_knee_angle(windows, spans) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "bottom_knee_angle_deg")
    series = series_for("knee_angle_deg", windows, region_gate=definition.visible_regions)
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
        value = float(window.features["knee_angle_deg"])
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


def _torso_inclination(windows, spans, view) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "torso_inclination_deg")
    if view not in definition.valid_views:
        # 正面机位无法区分"躯干前倾"与"画幅内投影"，本项显式不可测。
        return unavailable_per_span(
            exercise_id=EXERCISE_ID,
            measurement_key=definition.key,
            reason_key=f"view_unsupported:{view}",
            spans=spans,
        )
    series = series_for(
        "torso_inclination_deg", windows, region_gate=definition.visible_regions
    )
    return aggregate_simple(
        exercise_id=EXERCISE_ID,
        definition=definition,
        series=series,
        spans=spans,
        limit=definition.thresholds["max_deg"],
        greater_is_better=False,
        good_key="trunk_ok",
        attention_key=M.VIOLATION_TRUNK_LEAN,
    )


def _knee_symmetry(windows, spans, view) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "knee_symmetry_deg")
    if view not in definition.valid_views:
        # 侧机位看不到左右膝横向差异：不可测，不用其它指标替代。
        return unavailable_per_span(
            exercise_id=EXERCISE_ID,
            measurement_key=definition.key,
            reason_key=f"view_unsupported:{view}",
            spans=spans,
        )
    series = series_for(
        "knee_symmetry_deg", windows, region_gate=definition.visible_regions
    )
    return aggregate_simple(
        exercise_id=EXERCISE_ID,
        definition=definition,
        series=series,
        spans=spans,
        limit=definition.thresholds["max_deg"],
        greater_is_better=False,
        good_key="symmetry_ok",
        attention_key=M.VIOLATION_KNEE_TRACKING_ASYMMETRY,
    )


def _depth_consistency(windows, spans) -> list[RepFinding]:
    definition = M.measurement_definition(EXERCISE_ID, "depth_consistency_sd_deg")
    series = series_for("knee_angle_deg", windows, region_gate=definition.visible_regions)
    bottoms: list[float] = []
    evidence_windows = []
    for span in spans:
        window = peak_window(sub_series(series, span), mode="min")
        if window is None:
            continue
        bottoms.append(float(window.features["knee_angle_deg"]))
        evidence_windows.append(window)
    return aggregate_sd_consistency(
        exercise_id=EXERCISE_ID,
        definition=definition,
        per_span_values=bottoms,
        evidence=evidence_windows,
        limit=definition.thresholds["max_sd_deg"],
        good_key="depth_consistent",
        attention_key=M.VIOLATION_DEPTH_INCONSISTENT,
    )
