"""计次与计时（能力计划 §5.6）：可审计状态机，不采信模型 phase。

§5.6 的关键约束：**模型 phase 只提供候选，最终计数由可审计状态机完成**。

* 动态动作：滞回阈值 + 最短阶段时长 + 方向反转（rest → excursion → rest）。
  只有观察到一个**完整周期**才 +1；没回到休息带的算 ``complete=False``，不计数。
* 静态动作：姿态进入/退出阈值 + 容错窗口（允许短暂掉帧，但窗口外的空档
  不计入保持时长）。静态动作**不**统计"次数"。

本模块**不做什么**：

* 不读取 :class:`~healthmate_worker.processors.motion_gold.contracts.TemporalMotionOutput`
  的 logits 或 phase_probs 来决定次数；``phase_output`` 只被记录为候选来源，
  计数完全来自信号本身（因此可逐帧审计、可离线复现）。
* 不做插值、不做跨场景切换/长时间遮挡的连接：超过
  :data:`DEFAULT_INTERRUPT_GAP_SECONDS` 的空档会打断周期，且**不会**把两段
  拼成一次动作。
* 证据不足时返回 ``available=False`` + ``reason_unavailable``，绝不用"大概"的
  数字充数。所有阈值都是模块级命名常量，函数内不出现魔数。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

from .contracts import CounterResult, ExerciseKind, PhasePoint, RepSegment

# 计次器版本：阈值/状态机变化时 +1（下游趋势对比必须同版本）。
COUNTER_VERSION = "motion-gold-counter@v1"

# --- 观测门槛（不是"动作标准"，只是"能不能算"） ------------------------------
# 关键点平均可见度低于该值时，本帧信号不参与状态机。
MIN_VISIBILITY = 0.5
# 单个（动态）周期的最短时长，短于此视为抖动。
DEFAULT_MIN_REP_SECONDS = 0.5
# 相邻有效样本间隔超过该值视为中断（换机位/长时间遮挡），不跨段连接。
DEFAULT_INTERRUPT_GAP_SECONDS = 0.75
# 静态保持允许的最大掉帧间隔：窗口内的空档仍计入保持时长。
DEFAULT_HOLD_GAP_SECONDS = 0.5
# 静态保持的最短时长与最少有效帧数（低于此按"证据不足"返回）。
PLANK_MIN_HOLD_SECONDS = 3.0
HIP_BRIDGE_MIN_HOLD_SECONDS = 2.0
MIN_HOLD_FRAMES = 4
# 动态计次最少需要的有效帧数（少于 4 帧不可能构成一个完整周期）。
MIN_CYCLE_FRAMES = 4

# --- 状态机状态名（审计用，不是阶段标签） -------------------------------------
STATE_SEEK_REST = "seek_rest"
STATE_IN_REST = "in_rest"
STATE_IN_EXCURSION = "in_excursion"


@dataclass(frozen=True)
class DynamicCounterConfig:
    """一个动态动作的计次配置。

    ``rest_high=True``：休息位在高值侧（如深蹲站立膝角大），向心方向是**下降**，
    回到 >= ``hi`` 才算完成一次。
    ``rest_high=False``：休息位在低值侧（如肩推底部），向心方向是**上升**，
    回到 <= ``lo`` 才算完成一次。
    """

    signal: str
    hi: float
    lo: float
    rest_high: bool = True
    min_rep_seconds: float = DEFAULT_MIN_REP_SECONDS
    interrupt_gap_seconds: float = DEFAULT_INTERRUPT_GAP_SECONDS


@dataclass(frozen=True)
class StaticCounterConfig:
    """一个静态动作的计时配置：``min_value`` 是"在保持位"的姿态阈值。"""

    signal: str
    min_value: float
    min_hold_seconds: float
    hold_gap_seconds: float = DEFAULT_HOLD_GAP_SECONDS
    min_hold_frames: int = MIN_HOLD_FRAMES


# --- 8 个黄金动作的计次阈值（§5.2 / §5.7） ------------------------------------
# 全部是二维角度启发式门槛，用于一般训练反馈，不是临床运动学结论。
SQUAT_REST_KNEE_DEG = 155.0
SQUAT_BOTTOM_KNEE_DEG = 140.0
PUSHUP_REST_ELBOW_DEG = 152.0
PUSHUP_BOTTOM_ELBOW_DEG = 138.0
LUNGE_REST_KNEE_DEG = 152.0
LUNGE_BOTTOM_KNEE_DEG = 138.0
CURL_REST_ELBOW_DEG = 140.0
CURL_BOTTOM_ELBOW_DEG = 100.0
RAISE_REST_ARM_DEG = 150.0
RAISE_TOP_ARM_DEG = 120.0
PRESS_BOTTOM_ELBOW_DEG = 100.0
PRESS_TOP_ELBOW_DEG = 140.0
PLANK_MIN_SAGITTAL_DEG = 160.0
HIP_BRIDGE_TOP_HIP_DEG = 165.0

DYNAMIC_COUNTERS: dict[str, DynamicCounterConfig] = {
    "squat": DynamicCounterConfig(
        signal="knee_angle_deg",
        hi=SQUAT_REST_KNEE_DEG,
        lo=SQUAT_BOTTOM_KNEE_DEG,
        rest_high=True,
    ),
    "pushup": DynamicCounterConfig(
        signal="elbow_angle_deg",
        hi=PUSHUP_REST_ELBOW_DEG,
        lo=PUSHUP_BOTTOM_ELBOW_DEG,
        rest_high=True,
    ),
    "lunge": DynamicCounterConfig(
        signal="knee_angle_deg",
        hi=LUNGE_REST_KNEE_DEG,
        lo=LUNGE_BOTTOM_KNEE_DEG,
        rest_high=True,
    ),
    "bicep_curl": DynamicCounterConfig(
        signal="elbow_angle_deg",
        hi=CURL_REST_ELBOW_DEG,
        lo=CURL_BOTTOM_ELBOW_DEG,
        rest_high=True,
    ),
    "lateral_raise": DynamicCounterConfig(
        signal="shoulder_angle_deg",
        hi=RAISE_REST_ARM_DEG,
        lo=RAISE_TOP_ARM_DEG,
        rest_high=True,
    ),
    "shoulder_press": DynamicCounterConfig(
        signal="elbow_angle_deg",
        hi=PRESS_TOP_ELBOW_DEG,
        lo=PRESS_BOTTOM_ELBOW_DEG,
        rest_high=False,
    ),
}

STATIC_COUNTERS: dict[str, StaticCounterConfig] = {
    "plank": StaticCounterConfig(
        signal="sagittal_hip_deg",
        min_value=PLANK_MIN_SAGITTAL_DEG,
        min_hold_seconds=PLANK_MIN_HOLD_SECONDS,
    ),
    "hip_bridge": StaticCounterConfig(
        signal="hip_angle_deg",
        min_value=HIP_BRIDGE_TOP_HIP_DEG,
        min_hold_seconds=HIP_BRIDGE_MIN_HOLD_SECONDS,
    ),
}

GOLD_EXERCISE_KINDS: dict[str, ExerciseKind] = {
    **{key: "dynamic" for key in DYNAMIC_COUNTERS},
    **{key: "static" for key in STATIC_COUNTERS},
}

# 默认帧率：仅当样本里没有时间戳时用来把帧号换成秒。命名常量，不内联。
DEFAULT_FPS = 30.0
# 时间戳判定上界：全部为整数且最大值不超过该值的序列按"帧号"解释，
# 否则按毫秒解释。10000 帧 @30fps ≈ 5.5 分钟，已远超单条分析的时长上限。
FRAME_INDEX_MAX = 10000.0


@dataclass
class _Sample:
    index: int
    t: float
    value: float


@dataclass
class _Cycle:
    start: _Sample
    peak: _Sample
    end: _Sample
    complete: bool = True


@dataclass
class _Audit:
    """状态机审计轨迹：可直接用于解释"为什么计了 N 次"。"""

    version: str = COUNTER_VERSION
    states: list[dict] = field(default_factory=list)
    interrupted: bool = False
    incomplete_cycles: int = 0


def exercise_kind(exercise_id: str) -> ExerciseKind | None:
    """返回该动作的计次种类；未登记的动作返回 None（而不是猜一个）。"""
    return GOLD_EXERCISE_KINDS.get(exercise_id)


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _mean(values: Iterable[float]) -> float | None:
    items = [float(v) for v in values if _finite(v)]
    if not items:
        return None
    return sum(items) / len(items)


def _unavailable(
    exercise_id: str, reason: str, *, phases: list[PhasePoint] | None = None
) -> CounterResult:
    """统一的"测不了"结果：reps/hold_seconds 必须为 None（不按 0 处理）。"""
    del phases  # 只用于强调：不可用时不给任何分段结论
    return CounterResult(
        available=False,
        exercise_id=exercise_id,
        reps=None,
        hold_seconds=None,
        segments=[],
        reason_unavailable=reason,
    )


def _resolve_series(
    features_or_signal, signal_key: str
) -> tuple[list, list | None, list | None]:
    """把多种输入形态归一成 ``(values, timestamps_ms, visibility)``。

    支持：

    * 逐帧特征字典序列 ``[{"available": True, "knee_angle_deg": ..., ...}, ...]``；
    * 三元组序列 ``[(t_ms, value, visibility), ...]``；
    * 纯数值/None 序列 ``[value, ...]``（时间戳退化为帧号）；
    * 单键映射 ``{"knee_angle_deg": [...], "t_ms": [...]}`` / ``{"signal": [...]}``。

    本函数**不**猜字段语义：``value`` 缺失即为 None，不会被当成 0。
    """
    if features_or_signal is None:
        return [], None, None
    if isinstance(features_or_signal, dict):
        values = None
        for candidate in (signal_key, "signal", "values", "value"):
            if candidate in features_or_signal:
                values = features_or_signal[candidate]
                break
        if values is None:
            return [], None, None
        stamps = features_or_signal.get("t_ms") or features_or_signal.get("timestamp_ms")
        visibility = features_or_signal.get("visibility")
        return list(values), (list(stamps) if stamps is not None else None), (
            list(visibility) if visibility is not None else None
        )

    values: list = []
    stamps: list = []
    visibility: list = []
    for index, item in enumerate(features_or_signal):
        if isinstance(item, dict):
            values.append(item.get(signal_key))
            stamp = item.get("t_ms", item.get("timestamp_ms"))
            if stamp is None:
                stamp = index
            stamps.append(stamp)
            visibility.append(item.get("visibility"))
        elif isinstance(item, (list, tuple)):
            if len(item) >= 2:
                stamps.append(item[0])
                values.append(item[1])
                visibility.append(item[2] if len(item) > 2 else None)
            elif len(item) == 1:
                stamps.append(index)
                values.append(item[0])
                visibility.append(None)
            else:
                stamps.append(index)
                values.append(None)
                visibility.append(None)
        else:
            stamps.append(index)
            values.append(item)
            visibility.append(None)
    return values, stamps, visibility


def _to_seconds(stamps: list, values: list, fps: float) -> list[float]:
    """时间戳（毫秒 / 秒 / 帧号）→ 秒。

    判定规则（避免把帧号当毫秒）：

    * 全部为整数且最大值 <= :data:`FRAME_INDEX_MAX` → 视为**帧号**；
    * 其余情况视为**毫秒**（worker 回执与小程序统一使用毫秒时间戳）。

    缺失/非法时间戳退化为帧号，绝不用 0 让时间轴塌缩。
    """
    numeric = [s for s in stamps if _finite(s)]
    all_integral = bool(numeric) and all(float(s).is_integer() for s in numeric)
    max_stamp = max((float(s) for s in numeric), default=0.0)
    treat_as_frames = all_integral and max_stamp <= FRAME_INDEX_MAX
    out: list[float] = []
    for index, stamp in enumerate(stamps):
        if _finite(stamp):
            value = float(stamp)
            if treat_as_frames:
                out.append(value / fps)
            else:
                out.append(value / 1000.0)
        else:
            out.append(index / fps)
    return out


def _build_samples(
    values: list,
    stamps: list | None,
    visibility: list | None,
    *,
    fps: float = DEFAULT_FPS,
) -> tuple[list[_Sample], int, int]:
    """过滤不可用样本，返回 ``(samples, total_frames, rejected_frames)``。

    不可用 = 非有限值，或可见度低于 :data:`MIN_VISIBILITY`。被过滤的帧不会
    被插值填补。
    """
    total = len(values)
    resolved_stamps = stamps if stamps is not None else list(range(total))
    resolved_visibility = visibility if visibility is not None else [None] * total
    if len(resolved_stamps) < total:
        resolved_stamps = list(resolved_stamps) + list(
            range(len(resolved_stamps), total)
        )
    if len(resolved_visibility) < total:
        resolved_visibility = list(resolved_visibility) + [None] * (
            total - len(resolved_visibility)
        )
    seconds = _to_seconds(resolved_stamps, values, fps)

    samples: list[_Sample] = []
    rejected = 0
    for index, value in enumerate(values):
        if not _finite(value):
            rejected += 1
            continue
        visibility_value = resolved_visibility[index]
        if _finite(visibility_value) and float(visibility_value) < MIN_VISIBILITY:
            rejected += 1
            continue
        samples.append(_Sample(index=index, t=seconds[index], value=float(value)))
    samples.sort(key=lambda s: (s.t, s.index))
    return samples, total, rejected


def _count_cycles(
    samples: list[_Sample], config: DynamicCounterConfig
) -> tuple[list[_Cycle], _Audit]:
    """滞回周期状态机（可审计）。

    状态：``seek_rest`` → ``in_rest`` → ``in_excursion`` → （回到休息带）``in_rest``。
    只有回到休息带且周期时长 >= ``min_rep_seconds`` 才产出 ``complete=True``。
    """
    audit = _Audit()
    cycles: list[_Cycle] = []
    state = STATE_SEEK_REST
    rest_sample: _Sample | None = None
    extreme_sample: _Sample | None = None
    previous: _Sample | None = None

    for sample in samples:
        if previous is not None and (
            sample.t - previous.t > config.interrupt_gap_seconds
            or sample.t < previous.t
        ):
            # 机位切换/长时间遮挡：不跨段连接，未闭合的周期记为不完整。
            if state == STATE_IN_EXCURSION:
                audit.incomplete_cycles += 1
            audit.interrupted = True
            audit.states.append(
                {"frame_index": sample.index, "t": round(sample.t, 3), "state": "interrupt"}
            )
            state, rest_sample, extreme_sample = STATE_SEEK_REST, None, None
        previous = sample

        if state == STATE_SEEK_REST:
            if _in_rest(sample.value, config):
                rest_sample, state = sample, STATE_IN_REST
                audit.states.append(
                    {"frame_index": sample.index, "t": round(sample.t, 3), "state": state}
                )
        elif state == STATE_IN_REST:
            if _in_excursion(sample.value, config):
                extreme_sample, state = sample, STATE_IN_EXCURSION
                audit.states.append(
                    {"frame_index": sample.index, "t": round(sample.t, 3), "state": state}
                )
        elif state == STATE_IN_EXCURSION:
            if extreme_sample is None or _more_extreme(sample.value, extreme_sample.value, config):
                extreme_sample = sample
            if _in_rest(sample.value, config):
                if (
                    rest_sample is not None
                    and extreme_sample is not None
                    and sample.t - rest_sample.t >= config.min_rep_seconds
                ):
                    cycles.append(
                        _Cycle(start=rest_sample, peak=extreme_sample, end=sample)
                    )
                rest_sample, extreme_sample, state = sample, None, STATE_IN_REST
                audit.states.append(
                    {"frame_index": sample.index, "t": round(sample.t, 3), "state": state}
                )

    if state == STATE_IN_EXCURSION:
        audit.incomplete_cycles += 1
        if rest_sample is not None and extreme_sample is not None:
            # 未回到休息带：作为 complete=False 段保留（不计数，但可解释）。
            cycles.append(
                _Cycle(start=rest_sample, peak=extreme_sample, end=extreme_sample,
                       complete=False)
            )
    return cycles, audit


def _in_rest(value: float, config: DynamicCounterConfig) -> bool:
    return value >= config.hi if config.rest_high else value <= config.lo


def _in_excursion(value: float, config: DynamicCounterConfig) -> bool:
    return value < config.lo if config.rest_high else value > config.hi


def _more_extreme(value: float, current: float, config: DynamicCounterConfig) -> bool:
    return value < current if config.rest_high else value > current


def _cycle_phases(cycle: _Cycle, rest_label: str, extreme_label: str) -> list[PhasePoint]:
    """把一次周期转成阶段点（start / bottom|top / end）。"""
    return [
        PhasePoint(
            frame_index=cycle.start.index,
            timestamp_ms=max(0, int(round(cycle.start.t * 1000.0))),
            phase=rest_label,
            value=cycle.start.value,
        ),
        PhasePoint(
            frame_index=cycle.peak.index,
            timestamp_ms=max(0, int(round(cycle.peak.t * 1000.0))),
            phase=extreme_label,
            value=cycle.peak.value,
        ),
        PhasePoint(
            frame_index=cycle.end.index,
            timestamp_ms=max(0, int(round(cycle.end.t * 1000.0))),
            phase=rest_label,
            value=cycle.end.value,
        ),
    ]


def _segments_from_cycles(
    cycles: list[_Cycle], config: DynamicCounterConfig
) -> list[RepSegment]:
    """产出 ``RepSegment``（只给 complete 周期编号，未完成段保留但 rep_index 不推进）。"""
    segments: list[RepSegment] = []
    rest_label = "rest_high" if config.rest_high else "rest_low"
    extreme_label = "bottom" if config.rest_high else "top"
    rep_index = 0
    for cycle in cycles:
        segments.append(
            RepSegment(
                rep_index=rep_index,
                start_ms=max(0, int(round(cycle.start.t * 1000.0))),
                peak_ms=max(0, int(round(cycle.peak.t * 1000.0))),
                end_ms=max(0, int(round(cycle.end.t * 1000.0))),
                complete=cycle.complete,
                phases=_cycle_phases(cycle, rest_label, extreme_label),
            )
        )
        if cycle.complete:
            rep_index += 1
    return segments


def _count_static(
    samples: list[_Sample], config: StaticCounterConfig
) -> tuple[float, list[RepSegment]]:
    """静态保持时长：只累加"在保持位"且间隔不超过容错窗口的时间段。

    空档（掉帧/遮挡）超过 :data:`DEFAULT_HOLD_GAP_SECONDS` 时不计数——宁可
    少算，也不把两段保持拼成一段更长的记录。
    """
    segments: list[RepSegment] = []
    total = 0.0
    run_start: _Sample | None = None
    run_last: _Sample | None = None
    run_frames = 0

    def flush() -> None:
        nonlocal total, run_start, run_last, run_frames
        if run_start is None or run_last is None:
            return
        duration = max(0.0, run_last.t - run_start.t)
        if run_frames >= config.min_hold_frames and duration > 0.0:
            total += duration
            segments.append(
                RepSegment(
                    rep_index=len(segments),
                    start_ms=max(0, int(round(run_start.t * 1000.0))),
                    peak_ms=max(0, int(round(run_last.t * 1000.0))),
                    end_ms=max(0, int(round(run_last.t * 1000.0))),
                    complete=True,
                    phases=[
                        PhasePoint(
                            frame_index=run_start.index,
                            timestamp_ms=max(0, int(round(run_start.t * 1000.0))),
                            phase="hold",
                            value=run_start.value,
                        ),
                        PhasePoint(
                            frame_index=run_last.index,
                            timestamp_ms=max(0, int(round(run_last.t * 1000.0))),
                            phase="end",
                            value=run_last.value,
                        ),
                    ],
                )
            )
        run_start = run_last = None
        run_frames = 0

    for sample in samples:
        if sample.value < config.min_value:
            flush()
            continue
        if run_start is None:
            run_start = run_last = sample
            run_frames = 1
            continue
        assert run_last is not None
        if sample.t - run_last.t > config.hold_gap_seconds:
            # 容错窗口外的空档：结束当前段，从这一帧重新开始。
            flush()
            run_start = run_last = sample
            run_frames = 1
            continue
        run_last = sample
        run_frames += 1
    flush()
    return total, segments


def evaluate_counter(
    exercise_id: str,
    features_or_signal,
    phase_output=None,
    *,
    fps: float = DEFAULT_FPS,
    dynamic_config: DynamicCounterConfig | None = None,
    static_config: StaticCounterConfig | None = None,
) -> CounterResult:
    """对一次分析做计次（动态）或计时（静态）。

    Parameters
    ----------
    exercise_id : 黄金动作 id（§5.2 的 8 个之一）。
    features_or_signal : 逐帧特征字典序列、``(t_ms, value, visibility)`` 序列、
        纯数值序列，或 ``{"<signal>": [...]}`` 映射。信号的字段名由该动作的
        配置决定。
    phase_output : 模型阶段输出（``TemporalMotionOutput`` 或兼容 dict）。
        **只作为候选记录，不参与计数**；传 None 完全等价。
    fps : 无时间戳时把帧号换算成秒的帧率，默认 :data:`DEFAULT_FPS`。
    dynamic_config / static_config : 覆盖内置阈值（默认取
        :data:`DYNAMIC_COUNTERS` / :data:`STATIC_COUNTERS`）。

    Returns
    -------
    CounterResult
        动态：``reps`` 为**完整**周期数，``segments`` 含每次的 start/peak/end。
        静态：``hold_seconds`` 为有效保持时长（秒），``reps`` 保持 None。
        证据不足（动作未登记 / 信号缺失 / 有效帧太少 / 保持过短）时
        ``available=False`` 且给出 ``reason_unavailable``。

    本函数不训练也不调用任何模型，不"根据 phase 候选倒推次数"，也不会在
    信号缺失时返回 0 次冒充测量结果。
    """
    del phase_output  # 明确：模型 phase 仅是候选，不参与计数（§5.6）
    if not isinstance(exercise_id, str) or not exercise_id:
        return CounterResult(
            available=False,
            exercise_id=exercise_id or "unknown",
            reps=None,
            hold_seconds=None,
            segments=[],
            reason_unavailable="未提供 exercise_id，无法选择计次器。",
        )

    kind = exercise_kind(exercise_id)
    if kind is None:
        return _unavailable(
            exercise_id,
            f"{exercise_id} 未登记 Gold 计次器（仅 Silver：识别/时间线，不计数）。",
        )

    if kind == "dynamic":
        config = dynamic_config or DYNAMIC_COUNTERS[exercise_id]
        values, stamps, visibility = _resolve_series(features_or_signal, config.signal)
        if not values:
            return _unavailable(
                exercise_id,
                f"未提供 {config.signal} 时序信号（需要逐帧特征或数值序列）。",
            )
        samples, total, rejected = _build_samples(values, stamps, visibility, fps=fps)
        if len(samples) < MIN_CYCLE_FRAMES:
            return _unavailable(
                exercise_id,
                f"有效 {config.signal} 样本仅 {len(samples)}/{total} 帧"
                f"（低于 {MIN_CYCLE_FRAMES} 帧或可见度不足），不足以计次。",
            )
        cycles, audit = _count_cycles(samples, config)
        segments = _segments_from_cycles(cycles, config)
        reps = sum(1 for segment in segments if segment.complete)
        if reps == 0:
            return CounterResult(
                available=False,
                exercise_id=exercise_id,
                reps=None,
                hold_seconds=None,
                segments=segments,
                reason_unavailable=(
                    "未检测到完整动作周期（可能只录到半次、或"
                    f"{config.signal} 变化未越过滞回阈值；"
                    f"未闭合段 {audit.incomplete_cycles} 个"
                    f"{'，且检测到信号中断' if audit.interrupted else ''}）。"
                ),
            )
        return CounterResult(
            available=True,
            exercise_id=exercise_id,
            reps=reps,
            hold_seconds=None,
            segments=segments,
            reason_unavailable=None,
        )

    config = static_config or STATIC_COUNTERS[exercise_id]
    values, stamps, visibility = _resolve_series(features_or_signal, config.signal)
    if not values:
        return _unavailable(
            exercise_id,
            f"未提供 {config.signal} 时序信号（静态动作需要姿态时序）。",
        )
    samples, total, rejected = _build_samples(values, stamps, visibility, fps=fps)
    if len(samples) < config.min_hold_frames:
        return _unavailable(
            exercise_id,
            f"有效姿态样本仅 {len(samples)}/{total} 帧（低于 {config.min_hold_frames} 帧），"
            "无法计量保持时长。",
        )
    hold_seconds, segments = _count_static(samples, config)
    if hold_seconds < config.min_hold_seconds:
        return CounterResult(
            available=False,
            exercise_id=exercise_id,
            reps=None,
            hold_seconds=None,
            segments=segments,
            reason_unavailable=(
                f"有效保持时长 {hold_seconds:.1f}s 低于 {config.min_hold_seconds:.1f}s 门槛"
                f"（{config.signal} 未持续处于保持位）。"
            ),
        )
    return CounterResult(
        available=True,
        exercise_id=exercise_id,
        reps=None,
        hold_seconds=round(hold_seconds, 2),
        segments=segments,
        reason_unavailable=None,
    )


__all__ = [
    "COUNTER_VERSION",
    "CURL_BOTTOM_ELBOW_DEG",
    "CURL_REST_ELBOW_DEG",
    "DEFAULT_FPS",
    "DEFAULT_HOLD_GAP_SECONDS",
    "DEFAULT_INTERRUPT_GAP_SECONDS",
    "DEFAULT_MIN_REP_SECONDS",
    "DYNAMIC_COUNTERS",
    "DynamicCounterConfig",
    "GOLD_EXERCISE_KINDS",
    "HIP_BRIDGE_MIN_HOLD_SECONDS",
    "HIP_BRIDGE_TOP_HIP_DEG",
    "LUNGE_BOTTOM_KNEE_DEG",
    "LUNGE_REST_KNEE_DEG",
    "MIN_CYCLE_FRAMES",
    "MIN_HOLD_FRAMES",
    "MIN_VISIBILITY",
    "PLANK_MIN_HOLD_SECONDS",
    "PLANK_MIN_SAGITTAL_DEG",
    "PRESS_BOTTOM_ELBOW_DEG",
    "PRESS_TOP_ELBOW_DEG",
    "PUSHUP_BOTTOM_ELBOW_DEG",
    "PUSHUP_REST_ELBOW_DEG",
    "RAISE_REST_ARM_DEG",
    "RAISE_TOP_ARM_DEG",
    "SQUAT_BOTTOM_KNEE_DEG",
    "SQUAT_REST_KNEE_DEG",
    "STATIC_COUNTERS",
    "StaticCounterConfig",
    "evaluate_counter",
    "exercise_kind",
]
