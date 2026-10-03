"""动作专属测量登记表与总入口（能力计划 §5.7）。

§5.7 的核心禁令："禁止一个通用公式给所有动作评分。" 因此这里为 8 个黄金动作
逐个登记 :class:`~healthmate_worker.processors.motion_gold.contracts.MeasurementDefinition`，
每条定义都显式写出 ``visible_regions`` / ``valid_views`` / ``per_rep`` /
``thresholds`` / ``severity_map``。

总入口 :func:`evaluate_measurements` 的纪律：

* 机位或可见部位不满足定义时，相关测量返回 ``status="unavailable"``；**绝不**
  换一个无关指标凑分（例如侧机位看不见左右膝时，不做"对称性"结论）。
* ``unavailable`` 的 ``observed_value`` 必须是 None，不按 0 分处理（§5.8）。
* 本层不产生 0–100 总分；总分/趋势属于 §5.8 的后续消费者。

本模块**不做什么**：

* 不声称任何测量是"训练过的模型输出"；这里全是规则化 v1（阈值 + 几何），
  需要真实标注数据才能验证或替换。
* 不导入 mediapipe / numpy / torch / cv2；不需要任何第三方依赖。
* 不在缺证据时给出数字。所有阈值都是命名常量。
"""

from __future__ import annotations

from typing import Iterable

from . import evidence
from .contracts import MeasurementDefinition, RepFinding

# 机位标识：front（正面/背面）、side（侧面）、unknown（无法判定）。
VIEW_FRONT = "front"
VIEW_SIDE = "side"
VIEW_UNKNOWN = "unknown"
ALL_VIEWS = frozenset({VIEW_FRONT, VIEW_SIDE, VIEW_UNKNOWN})

# 身体部位标识（与 features.LM 的点名一致，便于直接映射）。
REGION_SHOULDER = "shoulder"
REGION_ELBOW = "elbow"
REGION_WRIST = "wrist"
REGION_HIP = "hip"
REGION_KNEE = "knee"
REGION_ANKLE = "ankle"

# --- 阈值命名常量（度 / 比值 / 秒 / 次） --------------------------------------
# 全部为二维启发式"训练提示"门槛，不是临床或精确运动学判定。
SQUAT_BOTTOM_KNEE_TARGET_DEG = 100.0
SQUAT_BOTTOM_KNEE_TOLERANCE_DEG = 30.0
SQUAT_DEPTH_SD_MAX_DEG = 12.0
SQUAT_TORSO_INCLINATION_MAX_DEG = 45.0
SQUAT_KNEE_SYMMETRY_MAX_DEG = 12.0

PUSHUP_BOTTOM_ELBOW_TARGET_DEG = 95.0
PUSHUP_BOTTOM_ELBOW_TOLERANCE_DEG = 25.0
PUSHUP_BODY_LINE_MIN_DEG = 160.0
PUSHUP_RHYTHM_CV_MAX = 0.35

LUNGE_FRONT_KNEE_MIN_DEG = 80.0
LUNGE_HIP_SYMMETRY_MAX_DEG = 15.0
LUNGE_BACK_KNEE_TARGET_DEG = 110.0
LUNGE_BACK_KNEE_TOLERANCE_DEG = 30.0
LUNGE_STEP_SPAN_CV_MAX = 0.30

CURL_ELBOW_DRIFT_MAX_DEG = 18.0
CURL_TOP_ELBOW_TARGET_DEG = 55.0
CURL_TOP_ELBOW_TOLERANCE_DEG = 35.0
CURL_ELBOW_SYMMETRY_MAX_DEG = 12.0

RAISE_TOP_ABDUCTION_MIN_DEG = 78.0
RAISE_SHRUG_PROXY_MAX = 0.18
RAISE_TORSO_SWING_MAX_DEG = 12.0

PRESS_WRIST_STACK_RATIO_MAX = 0.18
PRESS_WRIST_SYNC_MAX = 0.15
PRESS_TOP_ELBOW_MIN_DEG = 155.0
PRESS_TORSO_LEAN_MAX_DEG = 20.0

PLANK_BODY_LINE_MIN_DEG = 160.0
PLANK_DEVIATION_MAX_DEG = 12.0

HIP_BRIDGE_TOP_HIP_MIN_DEG = 165.0
HIP_BRIDGE_TOP_HOLD_MIN_SECONDS = 0.5
HIP_BRIDGE_PELVIS_TILT_MAX_DEG = 8.0
HIP_BRIDGE_RHYTHM_CV_MAX = 0.40

# --- 严重度词汇（与 backend severity 文案对齐） -------------------------------
SEVERITY_INFO = "info"
SEVERITY_LOW = "low"
SEVERITY_MEDIUM = "medium"
SEVERITY_HIGH = "high"

# --- 违反模式键（同时作为 explanation_key 的后缀，前端据此出文案） -----------
VIOLATION_DEPTH_SHALLOW = "depth_shallow"
VIOLATION_DEPTH_INCONSISTENT = "depth_inconsistent"
VIOLATION_TRUNK_LEAN = "trunk_lean"
VIOLATION_KNEE_TRACKING_ASYMMETRY = "knee_tracking_asymmetry"
VIOLATION_BODY_LINE_BREAK = "body_line_break"
VIOLATION_RHYTHM_IRREGULAR = "rhythm_irregular"
VIOLATION_FRONT_KNEE_LOW = "front_knee_excessive_flexion"
VIOLATION_HIP_UNSTABLE = "hip_unstable"
VIOLATION_BACK_LEG_SHALLOW = "back_leg_shallow"
VIOLATION_STEP_INCONSISTENT = "step_length_inconsistent"
VIOLATION_ELBOW_DRIFT = "elbow_drift"
VIOLATION_RANGE_SHORT = "range_of_motion_short"
VIOLATION_ELBOW_ASYMMETRY = "elbow_asymmetry"
VIOLATION_SHRUG = "shoulder_shrug_proxy"
VIOLATION_TORSO_SWING = "torso_swing"
VIOLATION_WRIST_NOT_STACKED = "wrist_not_stacked"
VIOLATION_WRIST_ASYNC = "wrist_async"
VIOLATION_TORSO_LEAN_BACK = "torso_lean_back"
VIOLATION_SAG = "hip_sag"
VIOLATION_PIKE = "hip_pike"
VIOLATION_PELVIS_TILT = "pelvis_tilt"
VIOLATION_HOLD_SHORT = "top_hold_short"

# --- 各定义的阈值表（键 = 上面的常量名，值 = 常量本身） ----------------------
SQUAT_MEASUREMENTS: tuple[MeasurementDefinition, ...] = (
    MeasurementDefinition(
        key="bottom_knee_angle_deg",
        unit="deg",
        visible_regions=frozenset({REGION_HIP, REGION_KNEE, REGION_ANKLE}),
        valid_views=frozenset({VIEW_FRONT, VIEW_SIDE}),
        per_rep=True,
        thresholds={
            "target_deg": SQUAT_BOTTOM_KNEE_TARGET_DEG,
            "tolerance_deg": SQUAT_BOTTOM_KNEE_TOLERANCE_DEG,
        },
        severity_map={VIOLATION_DEPTH_SHALLOW: SEVERITY_MEDIUM},
    ),
    MeasurementDefinition(
        key="torso_inclination_deg",
        unit="deg",
        visible_regions=frozenset({REGION_SHOULDER, REGION_HIP}),
        valid_views=frozenset({VIEW_SIDE}),
        per_rep=True,
        thresholds={"max_deg": SQUAT_TORSO_INCLINATION_MAX_DEG},
        severity_map={VIOLATION_TRUNK_LEAN: SEVERITY_MEDIUM},
    ),
    MeasurementDefinition(
        key="knee_symmetry_deg",
        unit="deg",
        visible_regions=frozenset({REGION_HIP, REGION_KNEE, REGION_ANKLE}),
        # 左右膝轨迹只有在正面/背面机位才同时可见；侧机位不做该结论。
        valid_views=frozenset({VIEW_FRONT}),
        per_rep=True,
        thresholds={"max_deg": SQUAT_KNEE_SYMMETRY_MAX_DEG},
        severity_map={VIOLATION_KNEE_TRACKING_ASYMMETRY: SEVERITY_MEDIUM},
    ),
    MeasurementDefinition(
        key="depth_consistency_sd_deg",
        unit="deg",
        visible_regions=frozenset({REGION_HIP, REGION_KNEE}),
        valid_views=frozenset({VIEW_FRONT, VIEW_SIDE}),
        per_rep=False,
        thresholds={"max_sd_deg": SQUAT_DEPTH_SD_MAX_DEG},
        severity_map={VIOLATION_DEPTH_INCONSISTENT: SEVERITY_LOW},
    ),
)

PUSHUP_MEASUREMENTS: tuple[MeasurementDefinition, ...] = (
    MeasurementDefinition(
        key="elbow_rom_deg",
        unit="deg",
        visible_regions=frozenset({REGION_SHOULDER, REGION_ELBOW, REGION_WRIST}),
        valid_views=frozenset({VIEW_SIDE}),
        per_rep=True,
        thresholds={
            "bottom_target_deg": PUSHUP_BOTTOM_ELBOW_TARGET_DEG,
            "bottom_tolerance_deg": PUSHUP_BOTTOM_ELBOW_TOLERANCE_DEG,
        },
        severity_map={VIOLATION_DEPTH_SHALLOW: SEVERITY_MEDIUM},
    ),
    MeasurementDefinition(
        key="body_line_deg",
        unit="deg",
        visible_regions=frozenset(
            {REGION_SHOULDER, REGION_HIP, REGION_ANKLE}
        ),
        valid_views=frozenset({VIEW_SIDE}),
        per_rep=True,
        thresholds={"min_deg": PUSHUP_BODY_LINE_MIN_DEG},
        severity_map={VIOLATION_BODY_LINE_BREAK: SEVERITY_HIGH},
    ),
    MeasurementDefinition(
        key="rep_period_cv",
        unit="ratio",
        visible_regions=frozenset({REGION_ELBOW}),
        valid_views=ALL_VIEWS,
        per_rep=False,
        thresholds={"max_cv": PUSHUP_RHYTHM_CV_MAX},
        severity_map={VIOLATION_RHYTHM_IRREGULAR: SEVERITY_LOW},
    ),
)

LUNGE_MEASUREMENTS: tuple[MeasurementDefinition, ...] = (
    MeasurementDefinition(
        key="front_knee_min_deg",
        unit="deg",
        visible_regions=frozenset({REGION_HIP, REGION_KNEE, REGION_ANKLE}),
        valid_views=frozenset({VIEW_SIDE}),
        per_rep=True,
        thresholds={"min_deg": LUNGE_FRONT_KNEE_MIN_DEG},
        severity_map={VIOLATION_FRONT_KNEE_LOW: SEVERITY_MEDIUM},
    ),
    MeasurementDefinition(
        key="hip_symmetry_deg",
        unit="deg",
        visible_regions=frozenset({REGION_SHOULDER, REGION_HIP, REGION_KNEE}),
        valid_views=frozenset({VIEW_FRONT}),
        per_rep=True,
        thresholds={"max_deg": LUNGE_HIP_SYMMETRY_MAX_DEG},
        severity_map={VIOLATION_HIP_UNSTABLE: SEVERITY_LOW},
    ),
    MeasurementDefinition(
        key="back_knee_angle_deg",
        unit="deg",
        visible_regions=frozenset({REGION_HIP, REGION_KNEE, REGION_ANKLE}),
        valid_views=frozenset({VIEW_SIDE}),
        per_rep=True,
        thresholds={
            "target_deg": LUNGE_BACK_KNEE_TARGET_DEG,
            "tolerance_deg": LUNGE_BACK_KNEE_TOLERANCE_DEG,
        },
        severity_map={VIOLATION_BACK_LEG_SHALLOW: SEVERITY_MEDIUM},
    ),
    MeasurementDefinition(
        key="step_span_cv",
        unit="ratio",
        visible_regions=frozenset({REGION_ANKLE}),
        valid_views=frozenset({VIEW_SIDE}),
        per_rep=False,
        thresholds={"max_cv": LUNGE_STEP_SPAN_CV_MAX},
        severity_map={VIOLATION_STEP_INCONSISTENT: SEVERITY_LOW},
    ),
)

BICEP_CURL_MEASUREMENTS: tuple[MeasurementDefinition, ...] = (
    MeasurementDefinition(
        key="elbow_drift_deg",
        unit="deg",
        visible_regions=frozenset({REGION_SHOULDER, REGION_ELBOW, REGION_HIP}),
        valid_views=frozenset({VIEW_SIDE}),
        per_rep=True,
        thresholds={"max_deg": CURL_ELBOW_DRIFT_MAX_DEG},
        severity_map={VIOLATION_ELBOW_DRIFT: SEVERITY_MEDIUM},
    ),
    MeasurementDefinition(
        key="top_elbow_angle_deg",
        unit="deg",
        visible_regions=frozenset({REGION_SHOULDER, REGION_ELBOW, REGION_WRIST}),
        valid_views=frozenset({VIEW_FRONT, VIEW_SIDE}),
        per_rep=True,
        thresholds={
            "target_deg": CURL_TOP_ELBOW_TARGET_DEG,
            "tolerance_deg": CURL_TOP_ELBOW_TOLERANCE_DEG,
        },
        severity_map={VIOLATION_RANGE_SHORT: SEVERITY_MEDIUM},
    ),
    MeasurementDefinition(
        key="elbow_symmetry_deg",
        unit="deg",
        visible_regions=frozenset({REGION_SHOULDER, REGION_ELBOW, REGION_WRIST}),
        valid_views=frozenset({VIEW_FRONT}),
        per_rep=True,
        thresholds={"max_deg": CURL_ELBOW_SYMMETRY_MAX_DEG},
        severity_map={VIOLATION_ELBOW_ASYMMETRY: SEVERITY_LOW},
    ),
)

LATERAL_RAISE_MEASUREMENTS: tuple[MeasurementDefinition, ...] = (
    MeasurementDefinition(
        key="top_abduction_deg",
        unit="deg",
        visible_regions=frozenset({REGION_SHOULDER, REGION_ELBOW}),
        valid_views=frozenset({VIEW_FRONT}),
        per_rep=True,
        thresholds={"min_deg": RAISE_TOP_ABDUCTION_MIN_DEG},
        severity_map={VIOLATION_RANGE_SHORT: SEVERITY_MEDIUM},
    ),
    MeasurementDefinition(
        key="shrug_proxy_ratio",
        unit="ratio",
        visible_regions=frozenset({REGION_SHOULDER, REGION_HIP}),
        valid_views=frozenset({VIEW_FRONT}),
        per_rep=True,
        thresholds={"max_ratio": RAISE_SHRUG_PROXY_MAX},
        severity_map={VIOLATION_SHRUG: SEVERITY_MEDIUM},
    ),
    MeasurementDefinition(
        key="torso_swing_deg",
        unit="deg",
        visible_regions=frozenset({REGION_SHOULDER, REGION_HIP}),
        valid_views=frozenset({VIEW_FRONT, VIEW_SIDE}),
        per_rep=True,
        thresholds={"max_deg": RAISE_TORSO_SWING_MAX_DEG},
        severity_map={VIOLATION_TORSO_SWING: SEVERITY_LOW},
    ),
)

SHOULDER_PRESS_MEASUREMENTS: tuple[MeasurementDefinition, ...] = (
    MeasurementDefinition(
        key="wrist_stack_ratio",
        unit="ratio",
        visible_regions=frozenset({REGION_SHOULDER, REGION_ELBOW, REGION_WRIST}),
        valid_views=frozenset({VIEW_FRONT}),
        per_rep=True,
        thresholds={"max_ratio": PRESS_WRIST_STACK_RATIO_MAX},
        severity_map={VIOLATION_WRIST_NOT_STACKED: SEVERITY_MEDIUM},
    ),
    MeasurementDefinition(
        key="wrist_sync_ratio",
        unit="ratio",
        visible_regions=frozenset({REGION_WRIST, REGION_SHOULDER}),
        valid_views=frozenset({VIEW_FRONT}),
        per_rep=True,
        thresholds={"max_ratio": PRESS_WRIST_SYNC_MAX},
        severity_map={VIOLATION_WRIST_ASYNC: SEVERITY_LOW},
    ),
    MeasurementDefinition(
        key="top_elbow_angle_deg",
        unit="deg",
        visible_regions=frozenset({REGION_SHOULDER, REGION_ELBOW, REGION_WRIST}),
        valid_views=frozenset({VIEW_FRONT, VIEW_SIDE}),
        per_rep=True,
        thresholds={"min_deg": PRESS_TOP_ELBOW_MIN_DEG},
        severity_map={VIOLATION_RANGE_SHORT: SEVERITY_MEDIUM},
    ),
    MeasurementDefinition(
        key="torso_lean_deg",
        unit="deg",
        visible_regions=frozenset({REGION_SHOULDER, REGION_HIP}),
        valid_views=frozenset({VIEW_SIDE}),
        per_rep=True,
        thresholds={"max_deg": PRESS_TORSO_LEAN_MAX_DEG},
        severity_map={VIOLATION_TORSO_LEAN_BACK: SEVERITY_MEDIUM},
    ),
)

PLANK_MEASUREMENTS: tuple[MeasurementDefinition, ...] = (
    MeasurementDefinition(
        key="body_line_deg",
        unit="deg",
        visible_regions=frozenset({REGION_SHOULDER, REGION_HIP, REGION_ANKLE}),
        valid_views=frozenset({VIEW_SIDE}),
        per_rep=False,
        thresholds={"min_deg": PLANK_BODY_LINE_MIN_DEG},
        severity_map={VIOLATION_BODY_LINE_BREAK: SEVERITY_HIGH},
    ),
    MeasurementDefinition(
        key="sagittal_deviation_deg",
        unit="deg",
        visible_regions=frozenset({REGION_SHOULDER, REGION_HIP, REGION_KNEE}),
        valid_views=frozenset({VIEW_SIDE}),
        per_rep=False,
        thresholds={
            "min_deg": PLANK_BODY_LINE_MIN_DEG,
            "max_deviation_deg": PLANK_DEVIATION_MAX_DEG,
        },
        severity_map={VIOLATION_SAG: SEVERITY_MEDIUM, VIOLATION_PIKE: SEVERITY_MEDIUM},
    ),
    MeasurementDefinition(
        key="hold_seconds",
        unit="seconds",
        visible_regions=frozenset({REGION_SHOULDER, REGION_HIP, REGION_KNEE}),
        valid_views=frozenset({VIEW_SIDE}),
        per_rep=False,
        thresholds={"min_deg": PLANK_BODY_LINE_MIN_DEG},
        severity_map={VIOLATION_BODY_LINE_BREAK: SEVERITY_HIGH},
    ),
)

HIP_BRIDGE_MEASUREMENTS: tuple[MeasurementDefinition, ...] = (
    MeasurementDefinition(
        key="top_hip_angle_deg",
        unit="deg",
        visible_regions=frozenset({REGION_SHOULDER, REGION_HIP, REGION_KNEE}),
        valid_views=frozenset({VIEW_SIDE}),
        per_rep=True,
        thresholds={"min_deg": HIP_BRIDGE_TOP_HIP_MIN_DEG},
        severity_map={VIOLATION_RANGE_SHORT: SEVERITY_MEDIUM},
    ),
    MeasurementDefinition(
        key="top_hold_seconds",
        unit="seconds",
        visible_regions=frozenset({REGION_HIP, REGION_KNEE}),
        valid_views=frozenset({VIEW_SIDE}),
        per_rep=True,
        thresholds={"min_seconds": HIP_BRIDGE_TOP_HOLD_MIN_SECONDS},
        severity_map={VIOLATION_HOLD_SHORT: SEVERITY_LOW},
    ),
    MeasurementDefinition(
        key="pelvis_tilt_deg",
        unit="deg",
        visible_regions=frozenset({REGION_SHOULDER, REGION_HIP, REGION_KNEE}),
        valid_views=frozenset({VIEW_FRONT}),
        per_rep=True,
        thresholds={"max_deg": HIP_BRIDGE_PELVIS_TILT_MAX_DEG},
        severity_map={VIOLATION_PELVIS_TILT: SEVERITY_LOW},
    ),
    MeasurementDefinition(
        key="rep_period_cv",
        unit="ratio",
        visible_regions=frozenset({REGION_HIP}),
        valid_views=ALL_VIEWS,
        per_rep=False,
        thresholds={"max_cv": HIP_BRIDGE_RHYTHM_CV_MAX},
        severity_map={VIOLATION_RHYTHM_IRREGULAR: SEVERITY_LOW},
    ),
)

# 登记表：8 个黄金动作 → 测量定义（顺序即报告顺序，确定输出）。
MEASUREMENT_REGISTRY: dict[str, tuple[MeasurementDefinition, ...]] = {
    "squat": SQUAT_MEASUREMENTS,
    "pushup": PUSHUP_MEASUREMENTS,
    "lunge": LUNGE_MEASUREMENTS,
    "bicep_curl": BICEP_CURL_MEASUREMENTS,
    "lateral_raise": LATERAL_RAISE_MEASUREMENTS,
    "shoulder_press": SHOULDER_PRESS_MEASUREMENTS,
    "plank": PLANK_MEASUREMENTS,
    "hip_bridge": HIP_BRIDGE_MEASUREMENTS,
}

# 8 个黄金动作 id（§5.2 顺序，用于"能力完成线"清点）。
GOLD_EXERCISE_IDS: tuple[str, ...] = (
    "squat",
    "pushup",
    "lunge",
    "bicep_curl",
    "lateral_raise",
    "shoulder_press",
    "plank",
    "hip_bridge",
)


def measurement_definitions(exercise_id: str) -> tuple[MeasurementDefinition, ...]:
    """取该动作的测量定义；未登记动作返回空元组（不猜、不套用通用公式）。"""
    return MEASUREMENT_REGISTRY.get(exercise_id, ())


def measurement_definition(exercise_id: str, key: str) -> MeasurementDefinition | None:
    for definition in measurement_definitions(exercise_id):
        if definition.key == key:
            return definition
    return None


def _unavailable_findings(
    exercise_id: str, reason_key: str
) -> list[RepFinding]:
    """整动作不可测量：为每条登记测量各出一条 unavailable（无 observed_value）。"""
    return [
        RepFinding(
            rep_index=None,
            measurement_key=definition.key,
            observed_value=None,
            status="unavailable",
            evidence_frame_ids=[],
            explanation_key=f"{exercise_id}.{definition.key}.{reason_key}",
        )
        for definition in measurement_definitions(exercise_id)
    ]


def evaluate_measurements(
    exercise_id: str,
    signal_windows=None,
    frame_ids: Iterable[str] | None = None,
    *,
    counter_result=None,
) -> list[RepFinding]:
    """对一次分析产出全部 :class:`RepFinding`（§5.7）。

    Parameters
    ----------
    exercise_id : 黄金动作 id。
    signal_windows : 逐帧窗口序列（``[{"frame_id", "timestamp_ms", "features"|"landmarks", "rep_index"}, ...]``）。
        ``features`` 用 :func:`features.frame_features` 的输出；缺失 ``features``
        时由评估器用 ``landmarks`` 现算。两类都缺则该帧不参与测量。
    frame_ids : 可选的帧 id 列表（与窗口同序），仅在窗口缺 ``frame_id`` 时使用。
    counter_result : :class:`~healthmate_worker.processors.motion_gold.contracts.CounterResult`。
        动态动作按 ``segments`` 逐次取样；缺省时降级为"整段"测量，并明确标注
        ``explanation_key``，不伪造次数。

    Returns
    -------
    list[RepFinding]
        已通过 :func:`evidence.assert_evidence_complete` 的结论列表。凡证据或
        机位不支持者一律 ``status="unavailable"``。

    本函数不做模型推理，也不给 0–100 总分；它不"发明"任何测量值：算不出来就
    是 unavailable。
    """
    if exercise_id not in MEASUREMENT_REGISTRY:
        return [
            RepFinding(
                rep_index=None,
                measurement_key=exercise_id or "unknown",
                observed_value=None,
                status="unavailable",
                evidence_frame_ids=[],
                explanation_key=f"{exercise_id}.not_gold_registered",
            )
        ]

    # 惰性导入：评估器模块反过来引用本模块的阈值常量，运行时导入避免循环。
    from .evaluators import EVALUATORS

    measure = EVALUATORS.get(exercise_id)
    if measure is None:
        findings = _unavailable_findings(exercise_id, "no_evaluator_registered")
    else:
        findings = measure(
            list(signal_windows or []),
            list(frame_ids) if frame_ids is not None else None,
            counter_result=counter_result,
        )
        if not findings:
            findings = _unavailable_findings(exercise_id, "no_measurement_window")
    evidence.assert_evidence_complete(findings)
    return findings


__all__ = [
    "ALL_VIEWS",
    "BICEP_CURL_MEASUREMENTS",
    "CURL_ELBOW_DRIFT_MAX_DEG",
    "CURL_ELBOW_SYMMETRY_MAX_DEG",
    "CURL_TOP_ELBOW_TARGET_DEG",
    "CURL_TOP_ELBOW_TOLERANCE_DEG",
    "GOLD_EXERCISE_IDS",
    "HIP_BRIDGE_MEASUREMENTS",
    "HIP_BRIDGE_PELVIS_TILT_MAX_DEG",
    "HIP_BRIDGE_RHYTHM_CV_MAX",
    "HIP_BRIDGE_TOP_HIP_MIN_DEG",
    "HIP_BRIDGE_TOP_HOLD_MIN_SECONDS",
    "LATERAL_RAISE_MEASUREMENTS",
    "LUNGE_BACK_KNEE_TARGET_DEG",
    "LUNGE_BACK_KNEE_TOLERANCE_DEG",
    "LUNGE_FRONT_KNEE_MIN_DEG",
    "LUNGE_HIP_SYMMETRY_MAX_DEG",
    "LUNGE_MEASUREMENTS",
    "LUNGE_STEP_SPAN_CV_MAX",
    "MEASUREMENT_REGISTRY",
    "PLANK_BODY_LINE_MIN_DEG",
    "PLANK_DEVIATION_MAX_DEG",
    "PLANK_MEASUREMENTS",
    "PRESS_TOP_ELBOW_MIN_DEG",
    "PRESS_TORSO_LEAN_MAX_DEG",
    "PRESS_WRIST_STACK_RATIO_MAX",
    "PRESS_WRIST_SYNC_MAX",
    "PUSHUP_BODY_LINE_MIN_DEG",
    "PUSHUP_BOTTOM_ELBOW_TARGET_DEG",
    "PUSHUP_BOTTOM_ELBOW_TOLERANCE_DEG",
    "PUSHUP_MEASUREMENTS",
    "PUSHUP_RHYTHM_CV_MAX",
    "RAISE_SHRUG_PROXY_MAX",
    "RAISE_TOP_ABDUCTION_MIN_DEG",
    "RAISE_TORSO_SWING_MAX_DEG",
    "REGION_ANKLE",
    "REGION_ELBOW",
    "REGION_HIP",
    "REGION_KNEE",
    "REGION_SHOULDER",
    "REGION_WRIST",
    "SEVERITY_HIGH",
    "SEVERITY_INFO",
    "SEVERITY_LOW",
    "SEVERITY_MEDIUM",
    "SHOULDER_PRESS_MEASUREMENTS",
    "SQUAT_BOTTOM_KNEE_TARGET_DEG",
    "SQUAT_BOTTOM_KNEE_TOLERANCE_DEG",
    "SQUAT_DEPTH_SD_MAX_DEG",
    "SQUAT_KNEE_SYMMETRY_MAX_DEG",
    "SQUAT_MEASUREMENTS",
    "SQUAT_TORSO_INCLINATION_MAX_DEG",
    "VIEW_FRONT",
    "VIEW_SIDE",
    "VIEW_UNKNOWN",
    "evaluate_measurements",
    "measurement_definition",
    "measurement_definitions",
]
