"""阶段分段（能力计划 §5.6）：纯函数、确定性、可离线复现。

两类动作用两套机制：

* **动态动作**（深蹲/俯卧撑/弓步/弯举/侧平举/肩推）：滞回（hysteresis）+
  最短阶段时长 + 方向反转。方向反转用于标出极值点（``bottom`` / ``top``），
  极值点是"最低点膝角""底部深度"等 §5.7 测量的取样位置。
* **静态动作**（平板/臀桥保持）：进入/退出阈值 + 容错窗口。

输出是 :class:`~healthmate_worker.processors.motion_gold.contracts.PhasePoint`
列表，语义为"该帧**开始进入**该阶段"。

本模块**不做什么**：

* 不读取模型输出。§5.6 明确模型 phase 只是候选，最终计数由
  :mod:`healthmate_worker.processors.motion_gold.counters` 的可审计状态机完成；
  这里的输入是一个一维信号（可由特征、观测量或模型候选派生）。
* 不导入 numpy / mediapipe / torch；不抛数据异常（空信号返回 ``[]``，
  时间戳缺失退化为帧号）。
* 不做平滑、插值或缺失帧外推：分段只依据真实存在的样本点。
* 不对短于 :data:`DEFAULT_MIN_PHASE_FRAMES` 的抖动"补一个阶段"——抖动会被
  合并掉，而不是被计成一次动作。
"""

from __future__ import annotations

import math

from .contracts import PhasePoint

# 最短阶段时长（帧）。低于该值的阶段视为噪声/抖动，会被合并到邻居里，
# 而不是被计成一次真实动作。约 3 帧 ≈ 0.1s @30fps，远短于任何真实向心阶段。
DEFAULT_MIN_PHASE_FRAMES = 3

# 动态模式默认滞回阈值（度）。仅在调用方不传阈值时使用；它们与
# :mod:`.counters` 的计次阈值同源，避免"分段"和"计次"各用一套数字。
DEFAULT_ASCENDING_THRESHOLD = 155.0
DEFAULT_DESCENDING_THRESHOLD = 145.0

# 静态模式默认阈值（度）：信号在 [min_value, max_value] 带内视为保持。
DEFAULT_STATIC_ENTER_VALUE = 160.0
DEFAULT_STATIC_EXIT_VALUE = 150.0

# 阶段标签（避免散落的字符串字面量）。
PHASE_START = "start"
PHASE_END = "end"
PHASE_BOTTOM = "bottom"
PHASE_TOP = "top"
PHASE_HOLD = "hold"
PHASE_REST = "rest"


def _coerce_timestamp(value, fallback: int) -> int:
    """时间戳兜底：缺失/非有限/为负时退化为帧号（毫秒近似），绝不抛给调用方。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return max(0, int(round(float(value))))
    return max(0, int(fallback))


def _finite_or_none(value) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def segment_phases(
    signal,
    *,
    ascending_threshold: float | None = None,
    descending_threshold: float | None = None,
    min_phase_frames: int = DEFAULT_MIN_PHASE_FRAMES,
    static: bool = False,
    static_enter_value: float | None = None,
    static_exit_value: float | None = None,
    timestamp_ms=None,
    bottom_label: str = PHASE_BOTTOM,
    top_label: str = PHASE_TOP,
) -> list[PhasePoint]:
    """把逐帧一维信号切成阶段边界点。

    Parameters
    ----------
    signal : 逐帧数值序列（``list[float | None]`` 或等价可迭代对象）。``None``
        表示该帧信号不可用：它不参与方向判断，也**不会**被当作 0。
    ascending_threshold : 上行/站直方向阈值。动态模式下默认
        :data:`DEFAULT_ASCENDING_THRESHOLD`；静态模式下默认
        :data:`DEFAULT_STATIC_ENTER_VALUE`。
    descending_threshold : 下行/下蹲方向阈值。动态模式下默认
        :data:`DEFAULT_DESCENDING_THRESHOLD`；静态模式下默认
        :data:`DEFAULT_STATIC_EXIT_VALUE`。
    min_phase_frames : 最短阶段时长（帧），默认 :data:`DEFAULT_MIN_PHASE_FRAMES`。
        连续两个同向极值距离小于该值时只保留更极端的一个。
    static : True 走静态保持分段（enter/exit 阈值 + 无方向反转）。
    static_enter_value / static_exit_value : 静态模式的显式阈值别名，优先级
        高于 ascending/descending_threshold。
    timestamp_ms : 与 ``signal`` 等长的毫秒时间戳序列；缺省时用帧号近似。
    bottom_label / top_label : 极值点标签，默认 ``bottom`` / ``top``。

    Returns
    -------
    list[PhasePoint]
        动态模式：``start``（第 0 帧）+ 每个极值点 + 最后一个 ``end`` 点。
        静态模式：首个 ``hold``/``rest`` 点 + 每次进出保持带的点 + 末尾
        ``end`` 点（若末帧尚未被记录）。样本不足时返回 ``[]``。

    确定性与纯度：相同输入永远给出相同输出；不读写全局状态、不依赖时间、
    不含随机性；输入不可变（不改写传入序列）。

    本函数**不**做模型推理，也不把模型 phase 候选当作最终结果；它只提供候选
    分段，计数由 :func:`counters.evaluate_counter` 的可审计状态机决定。
    """
    values = list(signal or [])
    if not values:
        return []
    if min_phase_frames < 1:
        raise ValueError("min_phase_frames 必须 >= 1")
    stamps = list(timestamp_ms) if timestamp_ms is not None else None

    def stamp(index: int) -> int:
        if stamps and index < len(stamps):
            return _coerce_timestamp(stamps[index], index)
        return index

    if static:
        enter = (
            static_enter_value
            if static_enter_value is not None
            else (ascending_threshold if ascending_threshold is not None
                  else DEFAULT_STATIC_ENTER_VALUE)
        )
        exit_value = (
            static_exit_value
            if static_exit_value is not None
            else (descending_threshold if descending_threshold is not None
                  else DEFAULT_STATIC_EXIT_VALUE)
        )
        if float(exit_value) > float(enter):
            raise ValueError("静态模式要求 static_exit_value <= static_enter_value")
        return _segment_static(values, enter, exit_value, min_phase_frames, stamp)

    ascending = (
        ascending_threshold if ascending_threshold is not None
        else DEFAULT_ASCENDING_THRESHOLD
    )
    descending = (
        descending_threshold if descending_threshold is not None
        else DEFAULT_DESCENDING_THRESHOLD
    )
    if float(ascending) <= float(descending):
        raise ValueError("动态模式要求 ascending_threshold > descending_threshold")
    return _segment_dynamic(
        values, ascending, descending, min_phase_frames, stamp, bottom_label, top_label
    )


def _segment_dynamic(
    values: list,
    ascending: float,
    descending: float,
    min_phase_frames: int,
    stamp,
    bottom_label: str,
    top_label: str,
) -> list[PhasePoint]:
    """滞回 + 方向反转的极值点检测。

    方向判定与滞回阈值解耦：方向由相邻有效样本的严格比较定义；滞回阈值
    （``ascending`` / ``descending``）用于校验参数一致性与给起始阶段贴标签，
    不参与极值点定位。因此调阈值不会悄悄换成另一个分段算法。
    """
    # 1) 逐帧方向：+1 上行，-1 下行，0 持平/不可用。
    direction: list[int] = [0] * len(values)
    previous_value: float | None = None
    for index, raw in enumerate(values):
        value = _finite_or_none(raw)
        if value is None:
            continue
        if previous_value is not None:
            if value > previous_value:
                direction[index] = 1
            elif value < previous_value:
                direction[index] = -1
        previous_value = value
    # 首个有效样本没有前驱方向：既然后面不能凭空造方向，就用滞回带上沿/
    # 下沿标出它处在哪一侧，再沿用其后第一个真实方向。
    first_valid = next(
        (i for i, v in enumerate(values) if _finite_or_none(v) is not None), None
    )
    if first_valid is not None:
        for index in range(first_valid + 1, len(direction)):
            if direction[index] != 0:
                direction[first_valid] = direction[index]
                break
        if direction[first_valid] == 0:
            first_value = _finite_or_none(values[first_valid])
            if first_value is not None:
                if first_value >= ascending:
                    direction[first_valid] = 1
                elif first_value <= descending:
                    direction[first_valid] = -1

    # 2) 方向反转点 = 极值点候选。
    candidates: list[tuple[int, str]] = []
    current_direction = 0
    for index, current in enumerate(direction):
        if current == 0:
            continue
        if current_direction == 0:
            current_direction = current
            continue
        if current != current_direction:
            previous_extreme_index = index - 1
            if _finite_or_none(values[previous_extreme_index]) is not None:
                # 由降转升 = 谷（bottom）；由升转降 = 峰（top）。
                label = bottom_label if current_direction == -1 else top_label
                candidates.append((previous_extreme_index, label))
            current_direction = current

    # 3) 最短阶段时长过滤：同向极值距离过近时只保留更极端的一个。
    kept: list[tuple[int, str]] = []
    for index, label in candidates:
        if not kept:
            kept.append((index, label))
            continue
        last_index, last_label = kept[-1]
        if index - last_index >= min_phase_frames:
            kept.append((index, label))
            continue
        if label != last_label:
            # 不同向且过近：保留先出现的那个，丢弃这次抖动。
            continue
        value = _finite_or_none(values[index])
        last_value = _finite_or_none(values[last_index])
        if value is None or last_value is None:
            continue
        # 同向且过近：保留更极端的一个（bottom 取更小，top 取更大）。
        more_extreme = (
            value < last_value if label == bottom_label else value > last_value
        )
        if more_extreme:
            kept[-1] = (index, label)

    # 4) 组装 PhasePoint：起点 + 极值点 + 终点。
    if not kept:
        start_value = _finite_or_none(values[0])
        return [
            PhasePoint(
                frame_index=0, timestamp_ms=stamp(0), phase=PHASE_START, value=start_value
            ),
            PhasePoint(
                frame_index=len(values) - 1,
                timestamp_ms=stamp(len(values) - 1),
                phase=PHASE_END,
                value=_finite_or_none(values[-1]),
            ),
        ]

    first_extreme_index = kept[0][0]
    points: list[PhasePoint] = [
        PhasePoint(
            frame_index=0,
            timestamp_ms=stamp(0),
            phase=PHASE_START,
            value=_finite_or_none(values[0]),
        )
    ]
    for index, label in kept:
        points.append(
            PhasePoint(
                frame_index=index,
                timestamp_ms=stamp(index),
                phase=label,
                value=_finite_or_none(values[index]),
            )
        )
    last_index = len(values) - 1
    if last_index > kept[-1][0] and last_index >= first_extreme_index:
        points.append(
            PhasePoint(
                frame_index=last_index,
                timestamp_ms=stamp(last_index),
                phase=PHASE_END,
                value=_finite_or_none(values[last_index]),
            )
        )
    return points


def _segment_static(
    values: list,
    enter_value: float,
    exit_value: float,
    min_phase_frames: int,
    stamp,
) -> list[PhasePoint]:
    """静态保持：进入阈值 = 确认在保持位；退出阈值 = 确认已离开。"""
    first_state = None
    for raw in values:
        value = _finite_or_none(raw)
        if value is None:
            continue
        first_state = PHASE_HOLD if value >= enter_value else PHASE_REST
        break
    if first_state is None:
        return []

    points: list[PhasePoint] = [
        PhasePoint(
            frame_index=0,
            timestamp_ms=stamp(0),
            phase=first_state,
            value=_finite_or_none(values[0]),
        )
    ]
    state = first_state
    state_started_at = 0
    for index, raw in enumerate(values):
        value = _finite_or_none(raw)
        if value is None:
            continue
        if state == PHASE_HOLD and value < exit_value:
            if index - state_started_at < min_phase_frames and len(points) > 1:
                # 抖动被合并：保持状态不变，等待真正离开保持带。
                continue
            state, state_started_at = PHASE_REST, index
            points.append(
                PhasePoint(
                    frame_index=index,
                    timestamp_ms=stamp(index),
                    phase=state,
                    value=value,
                )
            )
        elif state == PHASE_REST and value >= enter_value:
            if index - state_started_at < min_phase_frames and len(points) > 1:
                continue
            state, state_started_at = PHASE_HOLD, index
            points.append(
                PhasePoint(
                    frame_index=index,
                    timestamp_ms=stamp(index),
                    phase=state,
                    value=value,
                )
            )
    if points[-1].frame_index != len(values) - 1:
        points.append(
            PhasePoint(
                frame_index=len(values) - 1,
                timestamp_ms=stamp(len(values) - 1),
                phase=PHASE_END,
                value=_finite_or_none(values[-1]),
            )
        )
    return points


__all__ = [
    "DEFAULT_ASCENDING_THRESHOLD",
    "DEFAULT_DESCENDING_THRESHOLD",
    "DEFAULT_MIN_PHASE_FRAMES",
    "DEFAULT_STATIC_ENTER_VALUE",
    "DEFAULT_STATIC_EXIT_VALUE",
    "PHASE_BOTTOM",
    "PHASE_END",
    "PHASE_HOLD",
    "PHASE_REST",
    "PHASE_START",
    "PHASE_TOP",
    "segment_phases",
]
