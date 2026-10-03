"""Motion Coach 2.0 的 Gold 层骨架（能力计划 §5.1–§5.10）。

本包把计划里"Gold 8 动作"的结构性要求落成**可离线测试**的模块：

======================  ====================================================
模块                    职责
======================  ====================================================
:mod:`.contracts`        Pydantic v2 数据契约（§5.4/§5.6/§5.7/§5.10）
:mod:`.features`         逐帧骨架特征 + 时序差分（§5.4）
:mod:`.open_set`         §5.5 六条件开放集门控（显式谓词）
:mod:`.phases`           阶段分段：滞回 + 最短时长 + 方向反转（§5.6）
:mod:`.counters`         可审计计次/计时状态机（§5.6）
:mod:`.measurements`     8 个黄金动作的测量定义与总入口（§5.7）
:mod:`.evidence`         证据帧绑定与"有证据或 unavailable"断言（§5.7/§5.10）
:mod:`.gold_gate`        §5.10 完成线门控
:mod:`.evaluators`       每个动作一个规则化 v1 测量实现
======================  ====================================================

诚实声明（重要）：

* 本包**没有**训练好的动作识别模型，也**不声称**任何精度数字。§5.10 的
  macro F1 / unknown recall / MAE 等指标必须用真实标注数据实测后，
  通过 :func:`evaluate_gold_gate` 传入；本包不会自行估算。
* :mod:`.evaluators` 是**规则化 v1**（阈值 + 几何），它们能给出可解释、可复现
  的测量结论，但阈值需要标注数据校准；替换为学习型测量时请保留
  ``RepFinding`` 契约与"有证据帧或 unavailable"的不变量。
* 全包不在模块导入时加载 mediapipe / numpy / torch / cv2，也不新增第三方依赖
  （只用标准库 + 仓库已有的 pydantic）。缺失这类库时，本包仍可导入、可测试。
"""

from __future__ import annotations

__all__ = [
    # --- 契约（§5.4 / §5.6 / §5.7 / §5.10） -------------------------------
    "CONTRACT_VERSION",
    "CounterResult",
    "GoldGateReport",
    "MeasurementDefinition",
    "PhasePoint",
    "RepFinding",
    "RepSegment",
    "TemporalMotionOutput",
    # --- 特征（§5.4） ------------------------------------------------------
    "FEATURE_VERSION",
    "frame_features",
    "frame_features_sequence",
    # --- 开放集门控（§5.5） ------------------------------------------------
    "OpenSetDecision",
    "evaluate_open_set",
    # --- 阶段分段（§5.6） --------------------------------------------------
    "segment_phases",
    # --- 计次/计时（§5.6） -------------------------------------------------
    "evaluate_counter",
    "exercise_kind",
    # --- 测量（§5.7） ------------------------------------------------------
    "GOLD_EXERCISE_IDS",
    "MEASUREMENT_REGISTRY",
    "evaluate_measurements",
    "measurement_definitions",
    # --- 证据（§5.7 / §5.10） ---------------------------------------------
    "assert_evidence_complete",
    "attach_evidence",
    "frame_id_of",
    "has_evidence",
    "representative_evidence",
    "with_evidence",
    # --- 完成线门控（§5.10） ----------------------------------------------
    "GOLD_THRESHOLDS",
    "available_exercises",
    "evaluate_gold_gate",
]

from .contracts import (
    CONTRACT_VERSION,
    CounterResult,
    GoldGateReport,
    MeasurementDefinition,
    PhasePoint,
    RepFinding,
    RepSegment,
    TemporalMotionOutput,
)
from .counters import evaluate_counter, exercise_kind
from .evidence import (
    assert_evidence_complete,
    attach_evidence,
    frame_id_of,
    has_evidence,
    representative_evidence,
    with_evidence,
)
from .features import FEATURE_VERSION, frame_features, frame_features_sequence
from .gold_gate import GOLD_THRESHOLDS, available_exercises, evaluate_gold_gate
from .measurements import (
    GOLD_EXERCISE_IDS,
    MEASUREMENT_REGISTRY,
    evaluate_measurements,
    measurement_definitions,
)
from .open_set import OpenSetDecision, evaluate_open_set
from .phases import segment_phases
