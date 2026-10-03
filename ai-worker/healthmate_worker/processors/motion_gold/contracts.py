"""Motion Coach 2.0 Gold 层数据契约（能力计划 §5.4–§5.10）。

本模块只定义**数据结构与不变量**，不含任何识别、计次或评分逻辑，也**不加载
MediaPipe / numpy / torch**，因此可以在离线环境下单独导入与测试。

明确说明本模块**不做什么**：

* 不训练、不加载、也不声称存在任何动作识别模型。``TemporalMotionOutput``
  只是一个**结果容器**：模型（未来接入）或规则基线都可以填它，字段本身
  不代表精度。
* 不把 ``unknown_score`` 或 ``action_logits`` 解释成"已确认动作"。确认动作
  必须由 :mod:`healthmate_worker.processors.motion_gold.open_set` 的显式
  门控谓词给出。
* 不允许"没有证据帧但有结论"的 finding：``RepFinding`` 的不变量由
  :mod:`healthmate_worker.processors.motion_gold.evidence` 断言，而不是靠
  调用方自觉。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# --- 版本字符串 ---------------------------------------------------------------
# 契约版本在字段语义变化时 +1；下游（backend / 小程序）只应据版本分支。
CONTRACT_VERSION = "motion-gold-contract@v1"

# --- 枚举字面量 ---------------------------------------------------------------
# finding 状态：good（达标）/ attention（值得调整）/ unavailable（本机位或
# 本段证据不足以测量，**不得**用 0 分或替代指标顶替）。
FindingStatus = Literal["good", "attention", "unavailable"]

# 能力层级（§5.1）。Gold 只有 8 个黄金动作；Silver 允许开放类别判断但不计次；
# Unknown 只保留时间线与关键帧。
CapabilityTier = Literal["gold", "silver", "unknown"]

# 开放集决策（§5.5）。identified 表示六条件全满足；likely 表示仅"软证据"
# （margin / 原型距离 / 运动模式）不足；unknown 表示视频、主体或必要部位
# 这类硬条件不成立。
OpenSetDecisionLabel = Literal["identified", "likely", "unknown"]

# 阶段标签。动态动作用 top/bottom（极值点）+ start/end（序列端点）；
# 静态动作用 hold/rest 表示"在保持位"与"已退出保持位"。
PhaseLabel = str

# 计次/计时的"种类"：动态按次，静态按时长。
ExerciseKind = Literal["dynamic", "static"]


class PhasePoint(BaseModel):
    """一个阶段边界点（§5.6）。

    语义：``frame_index`` 这一帧**开始进入** ``phase`` 标出的状态。因此序列
    的第一个点通常是 ``start``，极值点标为 ``bottom`` / ``top``。它不是
    "该阶段内的任意一帧"，而是"该阶段的起点"。

    ``value`` 为该帧用于分段的一维信号值，可为 None（例如该帧没有可用信号）。
    """

    model_config = ConfigDict(extra="forbid")

    frame_index: int = Field(ge=0, description="帧序号（从 0 开始，闭区间内的帧位置）")
    timestamp_ms: int = Field(ge=0, description="相对视频起点的毫秒时间戳")
    phase: PhaseLabel = Field(min_length=1, description="该帧起的阶段标签")
    value: float | None = Field(default=None, description="该帧的一维分段信号值")


class RepSegment(BaseModel):
    """一次完整的向心-离心周期，或一段静态保持（§5.6）。

    ``complete=True`` 只表示"观察到回到起始带并结束"，不表示动作标准。
    未完整体不参与 ``CounterResult.reps``。
    """

    model_config = ConfigDict(extra="forbid")

    rep_index: int = Field(ge=0, description="第几次（从 0 开始）")
    start_ms: int = Field(ge=0, description="起始（休息位）时刻")
    peak_ms: int = Field(ge=0, description="极值（最深处/最高点）时刻")
    end_ms: int = Field(ge=0, description="结束（回到休息位或信号中断）时刻")
    complete: bool = Field(description="是否观察到完整回到休息带")
    phases: list[PhasePoint] = Field(
        default_factory=list, description="该次动作内部的阶段边界点"
    )


class CounterResult(BaseModel):
    """计次 / 计时结果（§5.6）。

    不变量：``available=False`` 时 ``reps`` 与 ``hold_seconds`` 必须为 None，
    并且必须给出 ``reason_unavailable``——宁可说"测不了"，也不虚报次数。
    """

    model_config = ConfigDict(extra="forbid")

    available: bool
    exercise_id: str = Field(min_length=1)
    reps: int | None = Field(default=None, ge=0)
    hold_seconds: float | None = Field(default=None, ge=0.0)
    segments: list[RepSegment] = Field(default_factory=list)
    reason_unavailable: str | None = None


class MeasurementDefinition(BaseModel):
    """一个动作专属测量的定义（§5.7）。

    ``visible_regions`` / ``valid_views`` 是**前置条件**：不满足时该项测量必须
    返回 ``unavailable``，不得换用无关指标凑分。``thresholds`` 的键必须是
    :mod:`healthmate_worker.processors.motion_gold.measurements` 中登记过的
    阈值名（命名常量），不允许在评估函数里内联魔数。
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, description="测量键，例如 bottom_knee_angle_deg")
    unit: str = Field(min_length=1, description="单位：deg / ratio / seconds / count")
    visible_regions: frozenset[str] = Field(
        default_factory=frozenset, description="该测量需要的身体部位"
    )
    valid_views: frozenset[str] = Field(
        default_factory=frozenset, description="支持该测量的机位（front/side/unknown）"
    )
    per_rep: bool = Field(description="True=逐次测量，False=整段测量一次")
    thresholds: dict[str, float] = Field(
        default_factory=dict, description="阈值名 -> 数值（名称为模块级常量名）"
    )
    severity_map: dict[str, str] = Field(
        default_factory=dict, description="违反模式 -> 严重度（info/low/medium/high）"
    )


class RepFinding(BaseModel):
    """一次测量结论（§5.7）。

    **每个 finding 必须**要么带非空 ``evidence_frame_ids``，要么
    ``status="unavailable"``；由 :func:`evidence.assert_evidence_complete`
    强制检查。``observed_value`` 在 unavailable 时必须为 None——不可用项不按
    0 分处理。
    """

    model_config = ConfigDict(extra="forbid")

    rep_index: int | None = Field(default=None, ge=0)
    measurement_key: str = Field(min_length=1)
    observed_value: float | None = None
    status: FindingStatus
    evidence_frame_ids: list[str] = Field(default_factory=list)
    explanation_key: str = Field(min_length=1)


class TemporalMotionOutput(BaseModel):
    """时序动作模型输出容器（§5.4）。

    本契约**不声称已有训练好的模型**：字段只是接口形状。当前仓库里的规则
    基线（``processors/counters.py`` / ``motion_gold/counters.py``）不产生
    logits，它们走可审计状态机；模型接入后 ``phase_probs`` 也只作为**候选**，
    最终计数仍由状态机决定（§5.6）。
    """

    model_config = ConfigDict(extra="forbid")

    action_logits: list[float] = Field(default_factory=list)
    unknown_score: float = Field(default=1.0, ge=0.0, le=1.0)
    phase_probs: list[list[float]] = Field(default_factory=list)
    embedding: list[float] = Field(
        default_factory=list, description="仅用于相似度/漂移分析，不直接展示"
    )
    model_version: str = Field(min_length=1)


class GoldGateReport(BaseModel):
    """单个动作的能力完成线报告（§5.10）。

    ``tier="gold"`` 当且仅当 ``GOLD_THRESHOLDS`` 中每一项都达标；否则降级为
    ``silver``，并把**具体未达标项**列在 ``unmet`` 里。没有"部分 Gold"。
    """

    model_config = ConfigDict(extra="forbid")

    exercise_id: str = Field(min_length=1)
    tier: CapabilityTier = "unknown"
    passed: bool = False
    unmet: list[str] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)
    thresholds: dict[str, float] = Field(default_factory=dict)
    missing_metrics: list[str] = Field(default_factory=list)


__all__ = [
    "CONTRACT_VERSION",
    "CapabilityTier",
    "CounterResult",
    "ExerciseKind",
    "FindingStatus",
    "GoldGateReport",
    "MeasurementDefinition",
    "OpenSetDecisionLabel",
    "PhaseLabel",
    "PhasePoint",
    "RepFinding",
    "RepSegment",
    "TemporalMotionOutput",
]
