"""能力完成线门控（能力计划 §5.10）。

§5.10 的完成线（必须通过**离线独立数据**验证，不看真机）：

* Gold 8 动作 all-sample macro F1 ≥ 0.85
* unknown recall ≥ 0.85
* 动态动作计次 MAE ≤ 1 次/视频
* 静态动作时长 MAE ≤ 1.5 秒
* phase 边界中位误差 ≤ 300 ms
* 高严重度 finding 精确率 ≥ 0.85
* 不支持机位的误评分率 ≤ 2%
* 每个 finding 100% 有证据帧或明确 unavailable

本模块把上述完成线写成**可执行门控**：指标齐全且全部达标 → ``tier="gold"``；
任何一项未达标或缺失 → ``silver``（§5.10："指标未达标时，动作保留 Silver"）。
缺失的指标按**未达标**处理——不能因为没测就宣布达标。

本模块**不做什么**：

* 不产生、不估算任何指标值。``evaluate_gold_gate`` 只读调用方传入的实测
  指标；本仓库当前**没有任何**训练好的动作分类模型，也没有标注评测集，
  因此这里不可能自行算出 macro F1 / unknown recall 之类的数字。
* 不把"平均表现"当成完成线：每项指标独立判定，没有互相抵消。
* 不导入 numpy / mediapipe / torch / cv2。
"""

from __future__ import annotations

import math
from typing import Iterable

from .contracts import GoldGateReport

# --- §5.10 阈值（唯一来源；所有比较都用这里的名字） --------------------------
# 键同时是 metrics 字典的键：调用方必须用同名键传入实测值。
METRIC_MACRO_F1 = "macro_f1"
METRIC_UNKNOWN_RECALL = "unknown_recall"
METRIC_REP_MAE = "rep_mae"
METRIC_HOLD_MAE_SECONDS = "hold_mae_seconds"
METRIC_PHASE_BOUNDARY_MEDIAN_ERROR_MS = "phase_boundary_median_error_ms"
METRIC_HIGH_SEVERITY_PRECISION = "high_severity_precision"
METRIC_UNSUPPORTED_VIEW_FALSE_SCORING_RATE = "unsupported_view_false_scoring_rate"
METRIC_EVIDENCE_COVERAGE = "evidence_coverage"

GOLD_THRESHOLDS: dict[str, float] = {
    # Gold 8 动作 all-sample macro F1。
    METRIC_MACRO_F1: 0.85,
    # unknown 召回。
    METRIC_UNKNOWN_RECALL: 0.85,
    # 动态动作计次平均绝对误差（次/视频），越小越好。
    METRIC_REP_MAE: 1.0,
    # 静态动作保持时长平均绝对误差（秒），越小越好。
    METRIC_HOLD_MAE_SECONDS: 1.5,
    # phase 边界中位误差（毫秒），越小越好。
    METRIC_PHASE_BOUNDARY_MEDIAN_ERROR_MS: 300.0,
    # 高严重度 finding 精确率。
    METRIC_HIGH_SEVERITY_PRECISION: 0.85,
    # 不支持机位的误评分率（比例），越小越好。
    METRIC_UNSUPPORTED_VIEW_FALSE_SCORING_RATE: 0.02,
    # 证据覆盖率：有证据帧或 unavailable 的 finding 占比，必须 100%。
    METRIC_EVIDENCE_COVERAGE: 1.0,
}

# 判定方向：True = 越大越好（下限），False = 越小越好（上限）。
_GREATER_IS_BETTER: dict[str, bool] = {
    METRIC_MACRO_F1: True,
    METRIC_UNKNOWN_RECALL: True,
    METRIC_REP_MAE: False,
    METRIC_HOLD_MAE_SECONDS: False,
    METRIC_PHASE_BOUNDARY_MEDIAN_ERROR_MS: False,
    METRIC_HIGH_SEVERITY_PRECISION: True,
    METRIC_UNSUPPORTED_VIEW_FALSE_SCORING_RATE: False,
    METRIC_EVIDENCE_COVERAGE: True,
}

# 指标展示顺序（确定性输出）。
METRIC_ORDER: tuple[str, ...] = (
    METRIC_MACRO_F1,
    METRIC_UNKNOWN_RECALL,
    METRIC_REP_MAE,
    METRIC_HOLD_MAE_SECONDS,
    METRIC_PHASE_BOUNDARY_MEDIAN_ERROR_MS,
    METRIC_HIGH_SEVERITY_PRECISION,
    METRIC_UNSUPPORTED_VIEW_FALSE_SCORING_RATE,
    METRIC_EVIDENCE_COVERAGE,
)

TIER_GOLD = "gold"
TIER_SILVER = "silver"


def _finite(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def evaluate_gold_gate(
    metrics: dict, *, exercise_id: str = "aggregate"
) -> GoldGateReport:
    """按 §5.10 判定某个动作（或整体）能否进入 Gold。

    Parameters
    ----------
    metrics : ``{指标名: 实测值}``。指标名必须取 :data:`GOLD_THRESHOLDS` 的键
        （见 :data:`METRIC_ORDER`）。缺失的指标按**未达标**处理。
    exercise_id : 报告归属的动作 id；聚合报告用默认值。

    Returns
    -------
    GoldGateReport
        全部指标齐全且达标 → ``tier="gold"``、``passed=True``；否则
        ``tier="silver"``，``unmet`` 逐条列出未达标（含超限方向与缺失）的项。

    本函数**不**产生指标值，也不做统计推断：没有实测数据时结果一定是
    ``silver`` + ``missing_metrics``，绝不会"默认达标"。
    """
    provided = metrics or {}
    unmet: list[str] = []
    missing: list[str] = []
    observed: dict[str, float] = {}

    for name in METRIC_ORDER:
        threshold = GOLD_THRESHOLDS[name]
        value = provided.get(name)
        if not _finite(value):
            missing.append(name)
            unmet.append(f"{name}: 缺少实测值（按未达标处理）")
            continue
        numeric = float(value)
        observed[name] = numeric
        if _GREATER_IS_BETTER[name]:
            if numeric < threshold:
                unmet.append(f"{name}: {numeric} < 阈值 {threshold}")
        else:
            if numeric > threshold:
                unmet.append(f"{name}: {numeric} > 阈值 {threshold}")

    passed = not unmet
    return GoldGateReport(
        exercise_id=exercise_id,
        tier=TIER_GOLD if passed else TIER_SILVER,
        passed=passed,
        unmet=unmet,
        metrics=observed,
        thresholds=dict(GOLD_THRESHOLDS),
        missing_metrics=missing,
    )


def available_exercises(reports: Iterable[GoldGateReport]) -> list[str]:
    """从一组报告中挑出**达到 Gold** 的动作 id（重复只保留一次，按输入顺序）。

    未达标、指标缺失、或 tier 为 silver/unknown 的动作都不会出现在结果里。
    """
    gold: list[str] = []
    for report in reports:
        if report.tier != TIER_GOLD or not report.passed:
            continue
        if report.exercise_id not in gold:
            gold.append(report.exercise_id)
    return gold


__all__ = [
    "GOLD_THRESHOLDS",
    "METRIC_EVIDENCE_COVERAGE",
    "METRIC_HIGH_SEVERITY_PRECISION",
    "METRIC_HOLD_MAE_SECONDS",
    "METRIC_MACRO_F1",
    "METRIC_ORDER",
    "METRIC_PHASE_BOUNDARY_MEDIAN_ERROR_MS",
    "METRIC_REP_MAE",
    "METRIC_UNKNOWN_RECALL",
    "METRIC_UNSUPPORTED_VIEW_FALSE_SCORING_RATE",
    "TIER_GOLD",
    "TIER_SILVER",
    "available_exercises",
    "evaluate_gold_gate",
]
