"""黄金动作专属评估器注册表（能力计划 §5.7 / §5.2）。

一个动作一个模块、一个 ``measure(signal_windows, frame_ids, *, counter_result=None)``
实现。注册表的存在是为了让 :func:`..measurements.evaluate_measurements` 能按
动作分发，而不是让某个"通用公式"跨动作复用（§5.7 明令禁止）。

本包**不做什么**：

* 不含任何训练产物或权重文件；全部是规则化 v1 的几何/阈值判据，需要真实标注
  数据（每动作至少数十条带标注视频）才能验证或替换为学习型测量。
* 不在模块导入时加载 mediapipe / numpy / torch / cv2（见
  ``tests/test_motion_gold.py`` 的模块级导入守卫测试）。
* 不在证据不足时给分数：所有评估器都通过
  :func:`._common.make_finding` 保证"有证据帧或 unavailable"。
"""

from __future__ import annotations

from . import (
    bicep_curl,
    hip_bridge,
    lateral_raise,
    lunge,
    plank,
    pushup,
    shoulder_press,
    squat,
)

# 动作 id → measure 函数。键与 :data:`..measurements.MEASUREMENT_REGISTRY` 一致。
EVALUATORS = {
    "squat": squat.measure,
    "pushup": pushup.measure,
    "lunge": lunge.measure,
    "bicep_curl": bicep_curl.measure,
    "lateral_raise": lateral_raise.measure,
    "shoulder_press": shoulder_press.measure,
    "plank": plank.measure,
    "hip_bridge": hip_bridge.measure,
}

__all__ = [
    "EVALUATORS",
    "bicep_curl",
    "hip_bridge",
    "lateral_raise",
    "lunge",
    "plank",
    "pushup",
    "shoulder_press",
    "squat",
]
