"""HealthMate Motion Coach 2.0 Gold 层测试（能力计划 §5.1–§5.10）。

覆盖目标：

* :func:`features.frame_features` 对空/过短输入返回 ``available=False``，
  对合法输入返回填充好的特征字典，且**不需要 numpy / mediapipe**；
* open-set gate 在任一条件失败时给出 ``unknown``/``likely``，全通过才是
  ``identified``（逐条件参数化）；
* :func:`phases.segment_phases` 确定性且尊重最短阶段时长；
* :func:`counters.evaluate_counter` 对方波信号计数正确，证据不足时返回
  ``available=False`` 且带原因；
* 8 个黄金动作都有 ``MeasurementDefinition``；缺可见部位时
  :func:`measurements.evaluate_measurements` 返回 ``unavailable`` 而不是编数字；
* ``evidence`` 保证"每个 finding 有证据帧或 unavailable"；
* :func:`gold_gate.evaluate_gold_gate` 任一指标未达标 → ``silver``，全达标 → ``gold``；
* 全包**模块级**不导入 mediapipe / torch / cv2 / numpy（离线可测的诚实守卫）。

测试只使用标准库与被测包；合成骨架是纯几何构造，不代表真实人体数据。
"""

from __future__ import annotations

import ast
import math
import subprocess
import sys
from pathlib import Path

import pytest

from healthmate_worker.processors import motion_gold as gold
from healthmate_worker.processors.motion_gold import (
    contracts,
    evaluate_counter,
    evaluate_gold_gate,
    evaluate_measurements,
    evaluate_open_set,
    evidence,
    features,
    frame_features,
    gold_gate,
    measurements,
    open_set,
    phases,
    segment_phases,
)
from healthmate_worker.processors.motion_gold.evaluators import _common as evaluator_common

PACKAGE_DIR = Path(gold.__file__).parent
GOLD_EXERCISE_IDS = (
    "squat",
    "pushup",
    "lunge",
    "bicep_curl",
    "lateral_raise",
    "shoulder_press",
    "plank",
    "hip_bridge",
)

# MediaPipe Pose 33 点索引（与 features.LM 对齐；测试侧独立声明以免自证）。
LM = {
    "nose": 0,
    "left_eye": 2,
    "right_eye": 5,
    "left_ear": 7,
    "right_ear": 8,
    "left_shoulder": 11,
    "right_shoulder": 12,
    "left_elbow": 13,
    "right_elbow": 14,
    "left_wrist": 15,
    "right_wrist": 16,
    "left_hip": 23,
    "right_hip": 24,
    "left_knee": 25,
    "right_knee": 26,
    "left_ankle": 27,
    "right_ankle": 28,
    "left_heel": 29,
    "right_heel": 30,
    "left_foot_index": 31,
    "right_foot_index": 32,
}

# 合成骨架的几何常量（归一化单位）。
THIGH = 0.35
SHIN = 0.33
HIP_Y = 0.50
ANKLE_Y = 0.95
SIDE_DEPTH = 0.06
FRONT_SPAN = 0.12
FPS = 30.0


def squat_pose(knee_angle_deg: float, *, side_view: bool = True):
    """按膝角构造一个深蹲骨架（33 点）。

    膝角由两段腿几何决定（髋固定、踝固定），因此 ``features`` 算出的膝角应当
    单调跟随传入的 ``knee_angle_deg``——这让特征测试不依赖复刻被测公式。
    """
    points = [(0.0, 0.0, 0.0, 0.0)] * 33

    def put(name, x, y, vis=0.95):
        points[LM[name]] = (x, y, 0.0, vis)

    angle = math.radians(knee_angle_deg)
    span = math.sqrt(
        THIGH * THIGH + SHIN * SHIN - 2.0 * THIGH * SHIN * math.cos(angle)
    )
    along = (THIGH * THIGH - SHIN * SHIN + span * span) / (2.0 * span)
    height = max(0.0, THIGH * THIGH - along * along) ** 0.5
    knee_x, knee_y = height, HIP_Y + along

    lean = math.radians(max(0.0, 170.0 - knee_angle_deg) * 0.35)
    sin_lean, cos_lean = math.sin(lean), math.cos(lean)
    depth = SIDE_DEPTH if side_view else 0.0
    offset = 0.0 if side_view else FRONT_SPAN

    def torso(x_offset, y_offset):
        return (
            x_offset * cos_lean - y_offset * sin_lean,
            HIP_Y - x_offset * sin_lean - y_offset * cos_lean,
        )

    put("nose", *torso(0.10, 0.50))
    put("left_eye", *torso(0.07, 0.52))
    put("right_eye", *torso(0.13, 0.52))
    put("left_ear", *torso(0.05, 0.50))
    put("right_ear", *torso(0.15, 0.50))
    for side, x_offset, y_offset, dy in (
        ("left", 0.10, 0.25, 0.0),
        ("right", 0.10 + offset, 0.25, depth),
    ):
        for joint, drop in (("shoulder", 0.0), ("elbow", 0.13), ("wrist", 0.27)):
            x, y = torso(x_offset, y_offset - drop)
            put(f"{side}_{joint}", x, y + dy)
    for side, dx, dy in (("left", 0.06, 0.0), ("right", 0.14, depth)):
        put(f"{side}_hip", dx, HIP_Y)
        put(f"{side}_knee", knee_x + dx - 0.10, knee_y + dy)
        put(f"{side}_ankle", dx - 0.10, ANKLE_Y + dy)
        put(f"{side}_heel", dx - 0.07, ANKLE_Y + 0.04 + dy)
        put(f"{side}_foot_index", dx - 0.13, ANKLE_Y + 0.04 + dy)
    return points


def squat_sequence(*, side_view=True, cycles=3):
    frames = []
    for _ in range(cycles):
        for index in range(20):
            frames.append(
                squat_pose(170.0 - 75.0 * index / 19.0, side_view=side_view)
            )
        for index in range(20):
            frames.append(
                squat_pose(95.0 + 75.0 * index / 19.0, side_view=side_view)
            )
    return frames


def frames_to_features(frames, *, include_landmarks=True):
    return [
        frame_features(
            frame,
            frame_index=index,
            timestamp_ms=round(index * 1000 / FPS),
            include_landmarks=include_landmarks,
        )
        for index, frame in enumerate(frames)
    ]


def features_to_windows(feature_rows):
    return [
        {
            "frame_id": evidence.frame_id_of(index),
            "frame_index": index,
            "timestamp_ms": round(index * 1000 / FPS),
            "features": row,
        }
        for index, row in enumerate(feature_rows)
    ]


def square_wave(high: float = 170.0, low: float = 95.0, half: int = 40, cycles: int = 2):
    return ([high] * half + [low] * half) * cycles + [high] * half


# ---------------------------------------------------------------------------
# features（§5.4）
# ---------------------------------------------------------------------------

def test_frame_features_rejects_empty_input_without_raising():
    result = frame_features([])
    assert result["available"] is False
    assert result["reason_unavailable"]
    assert result["feature_version"] == features.FEATURE_VERSION


@pytest.mark.parametrize(
    "bad_input",
    [
        None,
        [(0.0, 0.0, 0.0, 0.9)],
        [(0.0, 0.0, 0.0, 0.9)] * 5,
        12345,
    ],
)
def test_frame_features_rejects_short_or_invalid_input(bad_input):
    result = frame_features(bad_input)
    assert result["available"] is False
    assert "reason_unavailable" in result

    # 过短但非空的输入要能解释缺多少点，而不是静默返回空。
    if isinstance(bad_input, list) and bad_input:
        assert result["landmark_count"] == len(bad_input)
        assert result["required_count"] == features.MIN_LANDMARKS


def test_frame_features_populates_vector_for_valid_pose():
    result = frame_features(squat_pose(120.0))
    assert result["available"] is True
    for key in (
        "knee_angle_deg",
        "hip_angle_deg",
        "elbow_angle_deg",
        "knee_symmetry_deg",
        "torso_inclination_deg",
        "body_scale",
        "visibility_mean",
        "visible_ratio",
        "subject_box",
        "landmarks_normalized_count",
        "shoulder_width_ratio",
        "shrug_proxy_ratio",
        "pelvis_tilt_ratio",
        "velocity_main",
        "acceleration_main",
        "subject_box_motion",
    ):
        assert key in result, key
    assert 0.0 < result["knee_angle_deg"] < 180.0
    assert result["body_scale"] and result["body_scale"] > 0.0
    assert len(result["subject_box"]) == 4


def test_knee_angle_follows_synthetic_geometry():
    """膝角应随合成几何单调变化：越深的姿态角度越小。"""
    deep = frame_features(squat_pose(95.0))["knee_angle_deg"]
    mid = frame_features(squat_pose(130.0))["knee_angle_deg"]
    stand = frame_features(squat_pose(175.0))["knee_angle_deg"]
    assert deep is not None and mid is not None and stand is not None
    assert deep < mid < stand


def test_frame_features_does_not_fabricate_missing_angles():
    """只有腿可见时，手臂角度必须是 None，不能填 0。"""
    points = list(squat_pose(120.0))
    for name in ("left_elbow", "right_elbow", "left_wrist", "right_wrist"):
        points[LM[name]] = (0.0, 0.0, 0.0, 0.0)
    result = frame_features(points)
    assert result["available"] is True
    assert result["elbow_angle_deg"] is None
    assert result["elbow_symmetry_deg"] is None
    # 腿仍可测，因此膝角不为 None。
    assert result["knee_angle_deg"] is not None


def test_frame_features_sequence_computes_temporal_terms():
    frames = squat_sequence(side_view=True, cycles=1)
    rows = features.frame_features_sequence(frames, fps=FPS)
    assert len(rows) == len(frames)
    assert all(row["available"] for row in rows)
    velocities = [row["velocity_main"] for row in rows[1:]]
    assert any(value < 0 for value in velocities)  # 下蹲阶段
    assert any(value > 0 for value in velocities)  # 起立阶段
    assert any(row["subject_box_motion"] > 0 for row in rows[1:])


def test_frame_features_sequence_rejects_invalid_fps():
    rows = features.frame_features_sequence(squat_sequence(cycles=1), fps=0)
    assert rows and all(row["available"] is False for row in rows)


# ---------------------------------------------------------------------------
# open_set（§5.5）
# ---------------------------------------------------------------------------

def _passing_conditions() -> dict:
    return {
        "video_ok": True,
        "subject_ok": True,
        "required_regions_visible": True,
        "temporal_margin": 0.5,
        "prototype_distance": 0.1,
        "motion_pattern_match": 0.9,
    }


def test_open_set_identified_when_all_conditions_pass():
    decision = evaluate_open_set(**_passing_conditions())
    assert decision.decision == "identified"
    assert decision.failed_conditions == []
    assert decision.accepted is True


@pytest.mark.parametrize(
    "parameter,value,condition,expected",
    [
        ("video_ok", False, open_set.CONDITION_VIDEO, "unknown"),
        ("subject_ok", False, open_set.CONDITION_SUBJECT, "unknown"),
        ("required_regions_visible", False, open_set.CONDITION_REGIONS, "unknown"),
        ("temporal_margin", None, open_set.CONDITION_MARGIN, "likely"),
        ("prototype_distance", None, open_set.CONDITION_DISTANCE, "likely"),
        ("motion_pattern_match", None, open_set.CONDITION_PATTERN, "likely"),
    ],
)
def test_open_set_never_confirms_when_one_condition_fails(
    parameter, value, condition, expected
):
    """任一条件失败 → 不可能 identified；硬条件 unknown、软证据 likely。"""
    decision = evaluate_open_set(**{**_passing_conditions(), parameter: value})
    assert decision.decision == expected
    assert decision.failed_conditions == [condition]
    assert decision.accepted is False


@pytest.mark.parametrize(
    "failure",
    [
        {"temporal_margin": 0.01},
        {"prototype_distance": 0.99},
        {"motion_pattern_match": 0.05},
    ],
)
def test_open_set_likely_when_soft_threshold_missed(failure):
    decision = evaluate_open_set(**{**_passing_conditions(), **failure})
    assert decision.decision == "likely"
    assert decision.hard_failures == []
    assert len(decision.soft_failures) == 1


def test_open_set_thresholds_are_named_parameters_with_defaults():
    decision = evaluate_open_set(**_passing_conditions())
    assert decision.thresholds == {
        "margin_threshold": open_set.DEFAULT_MARGIN_THRESHOLD,
        "distance_threshold": open_set.DEFAULT_DISTANCE_THRESHOLD,
        "pattern_threshold": open_set.DEFAULT_PATTERN_THRESHOLD,
    }
    strict = evaluate_open_set(
        **{**_passing_conditions(), "temporal_margin": 0.25},
        margin_threshold=0.30,
    )
    assert strict.decision == "likely"


def test_open_set_reports_every_failed_condition_not_just_the_first():
    decision = evaluate_open_set(
        video_ok=False,
        subject_ok=False,
        required_regions_visible=False,
        temporal_margin=None,
        prototype_distance=None,
        motion_pattern_match=None,
    )
    assert decision.decision == "unknown"
    assert set(decision.failed_conditions) == set(open_set.HARD_CONDITIONS) | set(
        open_set.SOFT_CONDITIONS
    )


# ---------------------------------------------------------------------------
# phases（§5.6）
# ---------------------------------------------------------------------------

def test_segment_phases_is_deterministic():
    signal = square_wave()
    first = [point.model_dump() for point in segment_phases(signal, min_phase_frames=5)]
    second = [point.model_dump() for point in segment_phases(signal, min_phase_frames=5)]
    assert first == second
    assert len(first) >= 3


def test_segment_phases_finds_extrema_of_square_wave():
    phases_out = segment_phases(square_wave(), min_phase_frames=5)
    labels = [point.phase for point in phases_out]
    assert labels[0] == phases.PHASE_START
    assert labels[-1] == phases.PHASE_END
    assert labels.count(phases.PHASE_BOTTOM) == 2
    assert labels.count(phases.PHASE_TOP) >= 1
    for point in phases_out:
        assert point.frame_index >= 0
        assert point.timestamp_ms >= 0


def test_segment_phases_respects_min_phase_duration():
    """50 帧上升 + 2 帧下跌 + 50 帧上升：短抖动在 min_phase_frames 较大时被合并。"""
    signal = [100.0 + index for index in range(50)]
    signal += [140.0, 139.0]
    signal += [140.0 + index for index in range(50)]

    jittery = segment_phases(signal, min_phase_frames=1)
    smoothed = segment_phases(signal, min_phase_frames=5)
    assert len(smoothed) <= len(jittery)


def test_segment_phases_static_mode_uses_enter_and_exit_thresholds():
    signal = [170.0] * 40 + [120.0] * 40 + [170.0] * 40
    statics = segment_phases(signal, static=True, min_phase_frames=5)
    labels = [point.phase for point in statics]
    assert labels[0] == phases.PHASE_HOLD
    assert phases.PHASE_REST in labels
    assert set(labels) <= {phases.PHASE_HOLD, phases.PHASE_REST, phases.PHASE_END}


def test_segment_phases_handles_empty_and_unusable_signals():
    assert segment_phases([]) == []
    only_none = segment_phases([None, None, None])
    assert [point.phase for point in only_none] == [phases.PHASE_START, phases.PHASE_END]


def test_segment_phases_rejects_inconsistent_thresholds():
    with pytest.raises(ValueError):
        segment_phases([170.0, 100.0], ascending_threshold=100.0, descending_threshold=170.0)
    with pytest.raises(ValueError):
        segment_phases([170.0, 100.0], min_phase_frames=0)


# ---------------------------------------------------------------------------
# counters（§5.6）
# ---------------------------------------------------------------------------

def test_evaluate_counter_counts_square_wave_cycles():
    result = evaluate_counter("squat", square_wave(cycles=2))
    assert result.available is True
    assert result.reps == 2
    assert result.hold_seconds is None
    assert [segment.complete for segment in result.segments] == [True, True]
    assert [segment.rep_index for segment in result.segments] == [0, 1]
    assert result.segments[0].peak_ms < result.segments[0].end_ms


def test_evaluate_counter_handles_unknown_exercise():
    result = evaluate_counter("moon_walk", square_wave())
    assert result.available is False
    assert result.reps is None
    assert result.hold_seconds is None
    assert result.reason_unavailable
    assert "moon_walk" in result.reason_unavailable


@pytest.mark.parametrize(
    "payload",
    [
        [],
        None,
        [None] * 40,
        [170.0, 165.0, 170.0],
    ],
)
def test_evaluate_counter_reports_insufficient_input(payload):
    result = evaluate_counter("squat", payload)
    assert result.available is False
    assert result.reps is None
    assert result.hold_seconds is None
    assert result.reason_unavailable


def test_evaluate_counter_ignores_model_phase_candidates():
    """§5.6：模型 phase 只是候选，计数必须来自状态机。"""
    signal = square_wave(cycles=1)
    model_output = contracts.TemporalMotionOutput(
        action_logits=[0.9, 0.05],
        unknown_score=0.02,
        phase_probs=[[0.1, 0.9]] * 10,
        embedding=[0.1, 0.2],
        model_version="fake-rules@v0",
    )
    without = evaluate_counter("squat", signal)
    with_model = evaluate_counter("squat", signal, model_output)
    assert with_model.reps == without.reps == 1


def test_evaluate_counter_static_hold_reports_seconds():
    result = evaluate_counter("plank", [168.0] * 120)
    assert result.available is True
    assert result.reps is None
    assert result.hold_seconds is not None
    assert 3.5 <= result.hold_seconds <= 4.5
    assert result.segments and result.segments[0].complete is True


def test_evaluate_counter_static_hold_below_threshold_is_unavailable():
    result = evaluate_counter("plank", [168.0] * 30)
    assert result.available is False
    assert result.hold_seconds is None
    assert result.reason_unavailable


def test_evaluate_counter_accepts_visibility_tuples_and_rejects_low_visibility():
    good = [(index * (1000.0 / FPS), value, 0.9) for index, value in enumerate(square_wave())]
    assert evaluate_counter("squat", good).reps == 2
    hidden = [(index * (1000.0 / FPS), value, 0.1) for index, value in enumerate(square_wave())]
    result = evaluate_counter("squat", hidden)
    assert result.available is False
    assert result.reason_unavailable


def test_evaluate_counter_interrupt_does_not_join_across_gap():
    """跨 3 秒空档不能把两段拼成一次动作：打断的那一段不计数。"""
    # 第 1 次：休息 20 帧 + 最低点 20 帧 + 回到休息 20 帧（计数）；
    # 第 2 次：下沉 20 帧 + 未回休息 20 帧（被打断，不计数）。
    signal = [170.0] * 20 + [95.0] * 20 + [170.0] * 20 + [110.0] * 20 + [95.0] * 20
    stamps = [index * (1000.0 / FPS) for index in range(len(signal))]
    # 在第二次动作中途插入 3 秒空档。
    stamps = [stamp if index < 60 else stamp + 3000 for index, stamp in enumerate(stamps)]
    result = evaluate_counter("squat", list(zip(stamps, signal)))
    assert result.available is True
    assert result.reps == 1
    assert len(result.segments) == 1
    assert result.segments[0].complete is True


# ---------------------------------------------------------------------------
# measurements / evaluators（§5.2 / §5.7）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("exercise_id", GOLD_EXERCISE_IDS)
def test_every_gold_exercise_has_measurement_definitions(exercise_id):
    definitions = measurements.measurement_definitions(exercise_id)
    assert definitions, exercise_id
    for definition in definitions:
        assert isinstance(definition, contracts.MeasurementDefinition)
        assert definition.key
        assert definition.unit
        assert definition.visible_regions
        assert definition.valid_views
        assert definition.thresholds
        assert definition.severity_map
        assert isinstance(definition.per_rep, bool)
        for value in definition.thresholds.values():
            assert isinstance(value, float)


@pytest.mark.parametrize("exercise_id", GOLD_EXERCISE_IDS)
def test_evaluate_measurements_never_fabricates_without_windows(exercise_id):
    findings = evaluate_measurements(exercise_id, [], [])
    assert findings, exercise_id
    for finding in findings:
        assert finding.status == "unavailable"
        assert finding.observed_value is None
        assert finding.evidence_frame_ids == []
        assert finding.explanation_key.startswith(exercise_id)


@pytest.mark.parametrize("exercise_id", GOLD_EXERCISE_IDS)
def test_evaluate_measurements_unavailable_when_regions_missing(exercise_id):
    """只有鼻子的"特征"：所有需要身体部位的测量都必须 unavailable。"""
    empty_features = {
        "available": True,
        "feature_version": features.FEATURE_VERSION,
        "frame_index": 0,
        "timestamp_ms": 0,
        "nose_x": 0.5,
    }
    windows = [
        {
            "frame_id": evidence.frame_id_of(index),
            "frame_index": index,
            "timestamp_ms": index * 33,
            "features": dict(empty_features, frame_index=index),
        }
        for index in range(10)
    ]
    findings = evaluate_measurements(exercise_id, windows)
    assert findings
    for finding in findings:
        assert finding.status == "unavailable"
        assert finding.observed_value is None
        assert finding.evidence_frame_ids == []


def test_evaluate_measurements_squat_side_view_measures_depth_with_evidence():
    features_rows = frames_to_features(squat_sequence(side_view=True, cycles=3))
    windows = features_to_windows(features_rows)
    signal = [row["knee_angle_deg"] for row in features_rows]
    counter = evaluate_counter("squat", signal)
    assert counter.reps == 3

    findings = evaluate_measurements("squat", windows, counter_result=counter)
    by_key = {}
    for finding in findings:
        by_key.setdefault(finding.measurement_key, []).append(finding)

    depth = by_key["bottom_knee_angle_deg"]
    assert len(depth) == 3
    for finding in depth:
        assert finding.status in {"good", "attention"}
        assert finding.observed_value is not None
        assert finding.evidence_frame_ids
        assert finding.rep_index is not None

    # 侧机位看不到左右膝横向差异：必须 unavailable，不能编一个对称性数字。
    for finding in by_key["knee_symmetry_deg"]:
        assert finding.status == "unavailable"
        assert finding.observed_value is None

    # 侧机位可以评躯干倾角。
    torso = by_key["torso_inclination_deg"]
    assert all(finding.status in {"good", "attention"} for finding in torso)
    assert all(finding.evidence_frame_ids for finding in torso)


def test_evaluate_measurements_front_view_measures_symmetry_but_not_torso():
    features_rows = frames_to_features(squat_sequence(side_view=False, cycles=2))
    windows = features_to_windows(features_rows)
    findings = evaluate_measurements("squat", windows)
    by_key = {}
    for finding in findings:
        by_key.setdefault(finding.measurement_key, []).append(finding)
    assert any(
        finding.status in {"good", "attention"}
        for finding in by_key["knee_symmetry_deg"]
    )
    assert all(
        finding.status == "unavailable" for finding in by_key["torso_inclination_deg"]
    )


def test_evaluate_measurements_unknown_exercise_is_unavailable():
    findings = evaluate_measurements("moon_walk", [], [])
    assert findings
    assert findings[0].status == "unavailable"
    assert findings[0].observed_value is None
    assert "moon_walk" in findings[0].explanation_key


def _generic_feature_row(index: int, *, view: str) -> dict:
    """构造一帧"所有测量都接近达标"的特征，用于跑通每个评估器的判定分支。

    ``view="front"`` 让机位启发式判为 front，``view="side"`` 判为 side；两者
    合起来覆盖 8 个动作的 ``valid_views``（front / side）。
    """
    row = {
        "available": True,
        "feature_version": features.FEATURE_VERSION,
        "frame_index": index,
        "timestamp_ms": index * 33,
        # 关节角（度）。
        "knee_angle_deg": 105.0,
        "left_knee_angle_deg": 110.0,
        "right_knee_angle_deg": 100.0,
        "hip_angle_deg": 172.0,
        "left_hip_angle_deg": 174.0,
        "right_hip_angle_deg": 170.0,
        "elbow_angle_deg": 168.0,
        "left_elbow_angle_deg": 170.0,
        "right_elbow_angle_deg": 166.0,
        "shoulder_angle_deg": 95.0,
        "left_shoulder_angle_deg": 95.0,
        "right_shoulder_angle_deg": 95.0,
        "ankle_angle_deg": 95.0,
        "sagittal_hip_deg": 172.0,
        "leg_line_deg": 175.0,
        "body_line_deg": 172.0,
        "body_offset": 0.02,
        "knee_over_ankle_ratio": 0.05,
        # 对称性（度）。
        "knee_symmetry_deg": 4.0,
        "hip_symmetry_deg": 4.0,
        "elbow_symmetry_deg": 4.0,
        "shoulder_symmetry_deg": 4.0,
        "wrist_visibility": 0.9,
        # 可见度统计。
        "visibility_mean": 0.9,
        "visibility_min": 0.8,
        "visible_ratio": 1.0,
        # 躯干与尺度。
        "body_scale": 0.3,
        "torso_inclination_deg": 10.0,
        "torso_dx": 0.0,
        "torso_dy": 0.3,
        # 无量纲衍生比值。
        "shoulder_width_ratio": 1.35,
        "shoulder_dy": 0.0,
        "shrug_proxy_ratio": 0.70,
        "elbow_drift_ratio": 0.20,
        "wrist_stack_ratio": 0.05,
        "ankle_span_ratio": 0.40,
        "knee_span_ratio": 0.30,
        "pelvis_tilt_ratio": 0.02,
        # 时序占位。
        "velocity_main": 0.0,
        "acceleration_main": 0.0,
        "subject_box_motion": 0.0,
        "torso_angular_velocity_deg_s": 0.0,
        "subject_box": [0.4, 0.2, 0.6, 0.9],
        "landmarks_normalized_count": 20,
    }
    if view == "side":
        row.update(
            {
                "shoulder_width_ratio": 0.75,
                "shoulder_dy": 0.10,
                "elbow_drift_ratio": 0.20,
                "body_line_deg": 172.0,
            }
        )
    return row


@pytest.mark.parametrize("exercise_id", GOLD_EXERCISE_IDS)
@pytest.mark.parametrize("view", ["side", "front"])
def test_every_evaluator_honours_evidence_invariant_on_valid_windows(
    exercise_id, view
):
    """有可用窗口时：每个 finding 要么有证据帧，要么 unavailable（§5.10 末项）。"""
    windows = [
        {
            "frame_id": evidence.frame_id_of(index),
            "frame_index": index,
            "timestamp_ms": index * 33,
            "features": _generic_feature_row(index, view=view),
        }
        for index in range(12)
    ]
    counter = counters_for(exercise_id, windows)
    findings = evaluate_measurements(
        exercise_id, windows, counter_result=counter
    )
    assert findings, exercise_id
    evidence.assert_evidence_complete(findings)
    for finding in findings:
        assert finding.status in {"good", "attention", "unavailable"}
        if finding.status == "unavailable":
            assert finding.observed_value is None
            assert finding.evidence_frame_ids == []
        else:
            assert finding.observed_value is not None
            assert math.isfinite(finding.observed_value)
            assert finding.evidence_frame_ids
        assert finding.measurement_key in {
            definition.key
            for definition in measurements.measurement_definitions(exercise_id)
        }


def counters_for(exercise_id: str, windows: list[dict]):
    """按动作种类造一个可用的计次结果（静态给秒数，动态给两次动作）。"""
    kind = gold.exercise_kind(exercise_id)
    if kind == "static":
        return evaluate_counter(exercise_id, [170.0] * 120)
    last = windows[-1]
    return contracts.CounterResult(
        available=True,
        exercise_id=exercise_id,
        reps=2,
        hold_seconds=None,
        segments=[
            contracts.RepSegment(
                rep_index=0,
                start_ms=0,
                peak_ms=99,
                end_ms=198,
                complete=True,
                phases=[],
            ),
            contracts.RepSegment(
                rep_index=1,
                start_ms=198,
                peak_ms=297,
                end_ms=int(last["timestamp_ms"]),
                complete=True,
                phases=[],
            ),
        ],
        reason_unavailable=None,
    )


def test_single_rep_has_no_consistency_measurement():
    """一次动作时"深度一致性"没有定义，必须是 unavailable（不是标准差 0）。"""
    features_rows = frames_to_features(squat_sequence(side_view=True, cycles=1))
    findings = evaluate_measurements("squat", features_to_windows(features_rows))
    consistency = [
        finding for finding in findings
        if finding.measurement_key == "depth_consistency_sd_deg"
    ]
    assert len(consistency) == 1
    assert consistency[0].status == "unavailable"
    assert consistency[0].observed_value is None
    assert "single_rep_no_consistency" in consistency[0].explanation_key


def test_detect_view_is_deterministic_and_named():
    features_rows = frames_to_features(squat_sequence(side_view=True, cycles=1))
    windows = evaluator_common.build_windows(features_to_windows(features_rows))
    first = evaluator_common.detect_view(windows)
    second = evaluator_common.detect_view(windows)
    assert first == second
    assert first in {"front", "side", "unknown"}


# ---------------------------------------------------------------------------
# evidence（§5.7 / §5.10）
# ---------------------------------------------------------------------------

def test_frame_id_round_trip():
    identifier = evidence.frame_id_of(7)
    assert identifier == "f0007"
    assert evidence.frame_index_from_id(identifier) == 7
    assert evidence.frame_index_from_id("bogus") is None
    assert evidence.frame_ids_from([3, 3, 1]) == ["f0003", "f0001"]


def test_evidence_assertion_rejects_finding_without_frames():
    bad = contracts.RepFinding(
        rep_index=0,
        measurement_key="bottom_knee_angle_deg",
        observed_value=100.0,
        status="attention",
        evidence_frame_ids=[],
        explanation_key="squat.bottom_knee_angle_deg.depth_shallow",
    )
    with pytest.raises(evidence.EvidenceGapError):
        evidence.assert_evidence_complete([bad])


def test_evidence_assertion_accepts_unavailable_without_frames():
    finding = contracts.RepFinding(
        rep_index=None,
        measurement_key="knee_symmetry_deg",
        observed_value=None,
        status="unavailable",
        evidence_frame_ids=[],
        explanation_key="squat.knee_symmetry_deg.view_unsupported:side",
    )
    evidence.assert_evidence_complete([finding])
    assert evidence.has_evidence(finding) is True


def test_with_evidence_never_attaches_frames_to_unavailable():
    finding = contracts.RepFinding(
        rep_index=None,
        measurement_key="knee_symmetry_deg",
        observed_value=None,
        status="unavailable",
        evidence_frame_ids=[],
        explanation_key="squat.knee_symmetry_deg.view_unsupported:side",
    )
    assert evidence.with_evidence(finding, [1, 2, 3]).evidence_frame_ids == []
    measurable = finding.model_copy(update={"status": "good", "observed_value": 1.0})
    assert evidence.with_evidence(measurable, [1, 2, 3]).evidence_frame_ids == [
        "f0001",
        "f0002",
        "f0003",
    ]


@pytest.mark.parametrize("exercise_id", GOLD_EXERCISE_IDS)
def test_every_evaluator_finding_has_evidence_or_is_unavailable(exercise_id):
    """walk-through 守卫：真实数据路径下也满足 §5.10 末项。"""
    for side_view in (True, False):
        features_rows = frames_to_features(squat_sequence(side_view=side_view, cycles=2))
        windows = features_to_windows(features_rows)
        signal = [row["knee_angle_deg"] for row in features_rows]
        counter = evaluate_counter(exercise_id, signal)
        findings = evaluate_measurements(
            exercise_id, windows, counter_result=counter
        )
        evidence.assert_evidence_complete(findings)


def test_representative_evidence_prioritises_attention():
    attention = contracts.RepFinding(
        rep_index=0,
        measurement_key="a",
        observed_value=1.0,
        status="attention",
        evidence_frame_ids=["f0002"],
        explanation_key="x.a.y",
    )
    good = contracts.RepFinding(
        rep_index=1,
        measurement_key="b",
        observed_value=1.0,
        status="good",
        evidence_frame_ids=["f0001"],
        explanation_key="x.b.y",
    )
    assert evidence.representative_evidence([good, attention], limit=1) == ["f0002"]
    assert evidence.representative_evidence([good, attention], limit=5) == [
        "f0002",
        "f0001",
    ]


# ---------------------------------------------------------------------------
# gold_gate（§5.10）
# ---------------------------------------------------------------------------

def _passing_metrics() -> dict:
    return {
        gold_gate.METRIC_MACRO_F1: 0.90,
        gold_gate.METRIC_UNKNOWN_RECALL: 0.90,
        gold_gate.METRIC_REP_MAE: 0.5,
        gold_gate.METRIC_HOLD_MAE_SECONDS: 0.5,
        gold_gate.METRIC_PHASE_BOUNDARY_MEDIAN_ERROR_MS: 150.0,
        gold_gate.METRIC_HIGH_SEVERITY_PRECISION: 0.90,
        gold_gate.METRIC_UNSUPPORTED_VIEW_FALSE_SCORING_RATE: 0.0,
        gold_gate.METRIC_EVIDENCE_COVERAGE: 1.0,
    }


def test_gold_thresholds_match_plan_section_5_10():
    assert gold_gate.GOLD_THRESHOLDS == {
        "macro_f1": 0.85,
        "unknown_recall": 0.85,
        "rep_mae": 1.0,
        "hold_mae_seconds": 1.5,
        "phase_boundary_median_error_ms": 300.0,
        "high_severity_precision": 0.85,
        "unsupported_view_false_scoring_rate": 0.02,
        "evidence_coverage": 1.0,
    }


def test_gold_gate_passes_only_when_all_metrics_pass():
    report = evaluate_gold_gate(_passing_metrics(), exercise_id="squat")
    assert report.tier == "gold"
    assert report.passed is True
    assert report.unmet == []
    assert report.missing_metrics == []
    assert gold_gate.available_exercises([report]) == ["squat"]


@pytest.mark.parametrize(
    "metric,value",
    [
        (gold_gate.METRIC_MACRO_F1, 0.849),
        (gold_gate.METRIC_UNKNOWN_RECALL, 0.849),
        (gold_gate.METRIC_REP_MAE, 1.01),
        (gold_gate.METRIC_HOLD_MAE_SECONDS, 1.51),
        (gold_gate.METRIC_PHASE_BOUNDARY_MEDIAN_ERROR_MS, 301.0),
        (gold_gate.METRIC_HIGH_SEVERITY_PRECISION, 0.849),
        (gold_gate.METRIC_UNSUPPORTED_VIEW_FALSE_SCORING_RATE, 0.021),
        (gold_gate.METRIC_EVIDENCE_COVERAGE, 0.99),
    ],
)
def test_gold_gate_demotes_to_silver_when_one_metric_misses(metric, value):
    metrics = {**_passing_metrics(), metric: value}
    report = evaluate_gold_gate(metrics, exercise_id="pushup")
    assert report.tier == "silver"
    assert report.passed is False
    assert len(report.unmet) == 1
    assert metric in report.unmet[0]
    assert gold_gate.available_exercises([report]) == []


@pytest.mark.parametrize("missing", list(gold_gate.METRIC_ORDER))
def test_gold_gate_treats_missing_metric_as_unmet(missing):
    metrics = _passing_metrics()
    del metrics[missing]
    report = evaluate_gold_gate(metrics)
    assert report.tier == "silver"
    assert missing in report.missing_metrics
    assert any(missing in item for item in report.unmet)


def test_gold_gate_without_any_metrics_is_silver_and_lists_all_missing():
    report = evaluate_gold_gate({})
    assert report.tier == "silver"
    assert report.passed is False
    assert set(report.missing_metrics) == set(gold_gate.METRIC_ORDER)
    assert len(report.unmet) == len(gold_gate.METRIC_ORDER)


def test_available_exercises_keeps_input_order_and_deduplicates():
    gold_report = evaluate_gold_gate(_passing_metrics(), exercise_id="squat")
    silver_report = evaluate_gold_gate({}, exercise_id="plank")
    assert gold_gate.available_exercises(
        [gold_report, silver_report, gold_report]
    ) == ["squat"]


# ---------------------------------------------------------------------------
# 诚实守卫：模块级不得导入重型/可选依赖
# ---------------------------------------------------------------------------

FORBIDDEN_MODULES = ("mediapipe", "torch", "cv2", "numpy")


def _package_sources() -> list[Path]:
    return sorted(PACKAGE_DIR.rglob("*.py"))


def _module_level_imports(path: Path) -> list[tuple[int, str]]:
    """收集模块作用域的导入（含函数体内的延迟导入会被排除）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []

    def visit(node, *, top_level: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue  # 跳过嵌套作用域：延迟导入是允许的
            if isinstance(child, ast.Import):
                for alias in child.names:
                    found.append((child.lineno, alias.name.split(".")[0]))
            elif isinstance(child, ast.ImportFrom):
                if child.level == 0 and child.module:
                    found.append((child.lineno, child.module.split(".")[0]))
            visit(child, top_level=top_level)

    visit(tree, top_level=True)
    return found


def test_package_sources_are_discovered():
    sources = _package_sources()
    assert len(sources) >= 12
    assert (PACKAGE_DIR / "contracts.py") in sources
    assert (PACKAGE_DIR / "evaluators" / "hip_bridge.py") in sources


def test_no_module_scope_heavy_imports_in_package_source():
    """walk 源码 AST：模块级不得出现 mediapipe / torch / cv2 / numpy。"""
    offenders: list[str] = []
    for path in _package_sources():
        for lineno, module in _module_level_imports(path):
            if module in FORBIDDEN_MODULES:
                offenders.append(f"{path.name}:{lineno} imports {module}")
    assert offenders == [], offenders


def test_package_import_does_not_pull_heavy_modules_at_runtime():
    """子进程里 import 本包后，sys.modules 不得出现重型依赖。

    这一条用当前解释器执行（不捕获外部 stdout），失败信息里会带上命中的模块名。
    """
    code = (
        "import sys, json;"
        "import healthmate_worker.processors.motion_gold as g;"
        "print(json.dumps(sorted(m for m in sys.modules "
        "if m.split('.')[0] in {'mediapipe','torch','cv2','numpy'})))"
    )
    # cwd = ai-worker（pythonpath 的根），保证子进程能 import healthmate_worker。
    worker_root = PACKAGE_DIR.parents[2]
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(worker_root),
    )
    loaded = completed.stdout.strip()
    assert loaded == "[]", f"导入本包后加载了重型模块: {loaded}"


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------

def test_public_api_is_complete_and_importable():
    expected = {
        "CONTRACT_VERSION",
        "CounterResult",
        "FEATURE_VERSION",
        "GOLD_EXERCISE_IDS",
        "GOLD_THRESHOLDS",
        "GoldGateReport",
        "MEASUREMENT_REGISTRY",
        "MeasurementDefinition",
        "OpenSetDecision",
        "PhasePoint",
        "RepFinding",
        "RepSegment",
        "TemporalMotionOutput",
        "assert_evidence_complete",
        "attach_evidence",
        "available_exercises",
        "evaluate_counter",
        "evaluate_gold_gate",
        "evaluate_measurements",
        "evaluate_open_set",
        "exercise_kind",
        "frame_features",
        "frame_features_sequence",
        "frame_id_of",
        "has_evidence",
        "measurement_definitions",
        "representative_evidence",
        "segment_phases",
        "with_evidence",
    }
    assert set(gold.__all__) == expected
    for name in gold.__all__:
        assert hasattr(gold, name), name
    assert sorted(gold.MEASUREMENT_REGISTRY) == sorted(GOLD_EXERCISE_IDS)
    assert gold.GOLD_EXERCISE_IDS == GOLD_EXERCISE_IDS


def test_contracts_forbid_extra_fields():
    with pytest.raises(Exception):
        contracts.PhasePoint(
            frame_index=0, timestamp_ms=0, phase="start", value=1.0, surprise=1
        )
    with pytest.raises(Exception):
        contracts.RepFinding(
            rep_index=0,
            measurement_key="k",
            observed_value=1.0,
            status="bad",
            evidence_frame_ids=[],
            explanation_key="x",
        )


def test_counter_result_invariant_is_documented_by_model():
    result = contracts.CounterResult(
        available=False,
        exercise_id="squat",
        reps=None,
        hold_seconds=None,
        segments=[],
        reason_unavailable="证据不足",
    )
    assert result.reason_unavailable
    with pytest.raises(Exception):
        contracts.CounterResult(
            available=True,
            exercise_id="squat",
            reps=-1,
            hold_seconds=None,
            segments=[],
        )
