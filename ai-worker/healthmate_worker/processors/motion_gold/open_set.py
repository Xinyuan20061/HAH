"""Open-set gate（能力计划 §5.5）：显式、可测试的六条件谓词。

§5.5 要求最终动作识别**同时**满足：

    视频质量可用 AND 主体可用 AND 必要部位可见
    AND temporal margin 达标 AND 距离已知动作原型不过远
    AND 与动作专属运动模式一致

否则输出 ``unknown`` / ``likely``，保留时间线但**不进入动作评分**。

设计取舍（诚实优先）：

* 这是**纯谓词**，不做推断、不读配置、不发网络请求、不加载任何模型。所有
  阈值都是带默认值的命名参数。
* 硬条件（视频 / 主体 / 必要部位）任一失败 → ``unknown``：这三项是"能不能
  测"的前提，不是"像不像"的分数。
* 软条件（margin / 原型距离 / 运动模式）任一失败 → ``likely``：有候选动作
  但证据不足以确认，因此**不得**进入 Gold 计次与评分流程。
* 本模块**不**声称这六个条件等价于一个训练好的开放集分类器；这里只是把
  计划里的合取条件写成可审计的代码。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# --- 默认阈值（全部命名，可被参数覆盖） ---------------------------------------
# 分类 margin：top1 概率与 top2 概率的最小差值。低于该值说明模型自己都没拉开
# 候选，属于"有候选但不确认"。
DEFAULT_MARGIN_THRESHOLD = 0.20
# 原型距离：到已知动作原型的最大可接受距离。超过即"目录外"嫌疑。
DEFAULT_DISTANCE_THRESHOLD = 0.60
# 运动模式匹配：与动作专属运动模式的一致度下限（0–1）。
DEFAULT_PATTERN_THRESHOLD = 0.60
# 必要部位可见比例的观测门槛（用于调用方计算 required_regions_visible 时参考）。
DEFAULT_REGION_VISIBILITY_RATIO = 0.60

# 六条件的稳定标识（出现在 failed_conditions 里，供后端/前端做文案映射）。
CONDITION_VIDEO = "video_quality"
CONDITION_SUBJECT = "subject_available"
CONDITION_REGIONS = "required_regions_visible"
CONDITION_MARGIN = "temporal_margin"
CONDITION_DISTANCE = "prototype_distance"
CONDITION_PATTERN = "motion_pattern_match"

# 硬条件：不满足 → unknown（连"像什么"都不该说）。
HARD_CONDITIONS = (CONDITION_VIDEO, CONDITION_SUBJECT, CONDITION_REGIONS)
# 软条件：不满足 → likely（有候选但证据不足）。
SOFT_CONDITIONS = (CONDITION_MARGIN, CONDITION_DISTANCE, CONDITION_PATTERN)

DECISION_IDENTIFIED = "identified"
DECISION_LIKELY = "likely"
DECISION_UNKNOWN = "unknown"


class OpenSetDecision(BaseModel):
    """门控结论 + 失败条件清单（便于前端给"为什么没识别"的诚实解释）。"""

    model_config = ConfigDict(extra="forbid")

    decision: str = Field(description="identified / likely / unknown")
    failed_conditions: list[str] = Field(default_factory=list)
    hard_failures: list[str] = Field(default_factory=list)
    soft_failures: list[str] = Field(default_factory=list)
    thresholds: dict[str, float] = Field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        """只有 identified 才允许进入 Gold 计次/评分。"""
        return self.decision == DECISION_IDENTIFIED


def evaluate_open_set(
    *,
    video_ok: bool,
    subject_ok: bool,
    required_regions_visible: bool,
    temporal_margin: float | None,
    prototype_distance: float | None,
    motion_pattern_match: float | None,
    margin_threshold: float = DEFAULT_MARGIN_THRESHOLD,
    distance_threshold: float = DEFAULT_DISTANCE_THRESHOLD,
    pattern_threshold: float = DEFAULT_PATTERN_THRESHOLD,
) -> OpenSetDecision:
    """把 §5.5 的合取条件写成显式谓词。

    Parameters
    ----------
    video_ok : 视频质量闸门是否通过（模糊/过暗/时长异常等由上游判定）。
    subject_ok : 主体跟踪是否可用（恰好一个主要人物）。
    required_regions_visible : 动作专属必要部位是否可见。
    temporal_margin : top1 与 top2 的概率差；None 表示模型没给 margin（按失败处理，
        **不按 0 通过**）。
    prototype_distance : 到已知动作原型的距离；None 表示无法计算（按失败处理）。
    motion_pattern_match : 与动作专属运动模式的一致度（0–1）；None 按失败处理。
    margin_threshold : margin 下限，默认 :data:`DEFAULT_MARGIN_THRESHOLD`。
    distance_threshold : 距离上限，默认 :data:`DEFAULT_DISTANCE_THRESHOLD`。
    pattern_threshold : 一致度下限，默认 :data:`DEFAULT_PATTERN_THRESHOLD`。

    Returns
    -------
    OpenSetDecision
        全部通过 → ``identified``；仅软条件失败 → ``likely``；任一硬条件失败 →
        ``unknown``。``failed_conditions`` 永远列出全部未满足项（不短路、不隐藏）。

    本函数**不**做任何"近似通过"的宽容：margin 缺失、距离缺失、模式一致度缺失
    都算失败，宁可返回 ``likely`` 也不虚报 ``identified``。
    """
    failed: dict[str, str] = {}

    if not video_ok:
        failed[CONDITION_VIDEO] = "视频质量闸门未通过"
    if not subject_ok:
        failed[CONDITION_SUBJECT] = "未确认唯一主体"
    if not required_regions_visible:
        failed[CONDITION_REGIONS] = "动作必要部位不可见"

    if temporal_margin is None:
        failed[CONDITION_MARGIN] = "未提供 temporal margin"
    elif float(temporal_margin) < float(margin_threshold):
        failed[CONDITION_MARGIN] = (
            f"temporal margin {float(temporal_margin):.3f} < {float(margin_threshold):.3f}"
        )

    if prototype_distance is None:
        failed[CONDITION_DISTANCE] = "未提供原型距离"
    elif float(prototype_distance) > float(distance_threshold):
        failed[CONDITION_DISTANCE] = (
            f"原型距离 {float(prototype_distance):.3f} > {float(distance_threshold):.3f}"
        )

    if motion_pattern_match is None:
        failed[CONDITION_PATTERN] = "未提供运动模式一致度"
    elif float(motion_pattern_match) < float(pattern_threshold):
        failed[CONDITION_PATTERN] = (
            f"运动模式一致度 {float(motion_pattern_match):.3f} "
            f"< {float(pattern_threshold):.3f}"
        )

    hard = [name for name in HARD_CONDITIONS if name in failed]
    soft = [name for name in SOFT_CONDITIONS if name in failed]

    if hard:
        decision = DECISION_UNKNOWN
    elif soft:
        decision = DECISION_LIKELY
    else:
        decision = DECISION_IDENTIFIED

    return OpenSetDecision(
        decision=decision,
        failed_conditions=[name for name in (*HARD_CONDITIONS, *SOFT_CONDITIONS)
                           if name in failed],
        hard_failures=hard,
        soft_failures=soft,
        thresholds={
            "margin_threshold": float(margin_threshold),
            "distance_threshold": float(distance_threshold),
            "pattern_threshold": float(pattern_threshold),
        },
    )


__all__ = [
    "CONDITION_DISTANCE",
    "CONDITION_MARGIN",
    "CONDITION_PATTERN",
    "CONDITION_REGIONS",
    "CONDITION_SUBJECT",
    "CONDITION_VIDEO",
    "DECISION_IDENTIFIED",
    "DECISION_LIKELY",
    "DECISION_UNKNOWN",
    "DEFAULT_DISTANCE_THRESHOLD",
    "DEFAULT_MARGIN_THRESHOLD",
    "DEFAULT_PATTERN_THRESHOLD",
    "DEFAULT_REGION_VISIBILITY_RATIO",
    "HARD_CONDITIONS",
    "SOFT_CONDITIONS",
    "OpenSetDecision",
    "evaluate_open_set",
]
