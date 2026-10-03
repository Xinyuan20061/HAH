"""评估器公共脚手架（能力计划 §5.7）。

这里只放**与动作无关**的机械操作：把逐帧窗口变成角度序列、按次切片、取极值、
按阈值给出结论、绑定证据帧。动作专属的判据一律放在各自的评估器模块里，避免
"一个通用公式给所有动作评分"（§5.7 明令禁止）。

本模块**不做什么**：

* 不定义任何动作专属阈值（阈值属于 :mod:`.measurements` 的登记表）。
* 不在测不到量时给数字：``observed_value=None`` + ``status="unavailable"``。
* 不导入 numpy / mediapipe / torch / cv2；只依赖标准库与本包。
* 不把"测不到"偷偷变成"good"：所有门控失败都产出 ``unavailable``。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Iterable

from .. import evidence
from ..contracts import RepFinding
from ..features import frame_features, joint_angle

# 每个 finding 最多绑定多少个证据帧（避免回执膨胀；不代表证据质量下降）。
MAX_EVIDENCE_FRAMES = 3
# 时间单位换算。
MS_PER_SECOND = 1000.0
# 归一化分母的最小可用值：低于它时"变异系数"没有意义，记为 unavailable。
NORMALIZATION_EPSILON = 1e-6

# --- 机位判定（启发式） -------------------------------------------------------
# 正面/背面机位：肩宽 >= 髋宽（透视下正面才会这样），且左右肩横向张开度
# （norm_shoulder_dx = 肩宽 / 身体尺度）足够大。
VIEW_FRONT_SHOULDER_HIP_RATIO_MIN = 1.00
VIEW_FRONT_SHOULDER_DX_MIN = 0.50
# 侧面机位：一个肩明显高于另一个肩（近侧肩遮挡远侧肩）。两个判据取"或"：
# 绝对归一化 y 差，或相对身体尺度的肩高差（小画幅/远景时更稳）。
VIEW_SIDE_SHOULDER_DY_MIN = 0.15
VIEW_SIDE_SHOULDER_DY_SCALE_MIN = 0.15
# 机位投票的最少有效帧数与有效帧占比：样本太少时返回 unknown（不猜机位）。
VIEW_MIN_VOTES = 3
VIEW_MIN_VOTE_FRACTION = 0.10


@dataclass(frozen=True)
class FrameWindow:
    """一帧的测量输入。

    ``features`` 优先（由 :func:`features.frame_features` 产出）；缺失时用
    ``landmarks`` 现算。两者都缺则该帧不可测量，不会被当成 0。
    """

    frame_id: str
    frame_index: int
    timestamp_ms: int | None
    features: dict
    landmarks: object | None = None


@dataclass(frozen=True)
class RepSpan:
    """一次动作（或整段）的帧区间 ``[start_index, end_index]``（含端点）。"""

    rep_index: int | None
    start_index: int
    end_index: int


@dataclass(frozen=True)
class Series:
    """一维测量序列：值 + 对应帧窗口 + 是否随时间递增的标记。"""

    key: str
    values: list[float]
    windows: list[FrameWindow]

    def __len__(self) -> int:  # pragma: no cover - 便捷方法
        return len(self.values)

    def mean(self) -> float | None:
        return mean_of(self.values)

    def stdev(self) -> float | None:
        return stdev_of(self.values)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def mean_of(values: Iterable[float]) -> float | None:
    items = [float(v) for v in values if _finite(v)]
    if not items:
        return None
    return sum(items) / len(items)


def stdev_of(values: Iterable[float]) -> float | None:
    items = [float(v) for v in values if _finite(v)]
    if len(items) < 2:
        return None
    average = sum(items) / len(items)
    variance = sum((item - average) ** 2 for item in items) / len(items)
    return math.sqrt(variance)


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def is_finite(value) -> bool:
    """公开的有限数判断（评估器模块共用，避免各自重复实现）。"""
    return _finite(value)


def resolve_features(window: dict, cache: dict) -> dict:
    """取该窗口的特征字典：优先现成 ``features``，否则用 landmarks 现算。

    现算结果按窗口 id 缓存，避免同一帧被多个测量重复计算。返回空字典表示
    "该帧不可测量"（不会返回伪造的全 0 特征）。
    """
    if not isinstance(window, dict):
        return {}
    existing = window.get("features")
    if isinstance(existing, dict) and existing.get("available"):
        return existing
    key = id(window)
    if key in cache:
        return cache[key]
    landmarks = window.get("landmarks")
    computed = frame_features(landmarks) if landmarks is not None else {}
    if not computed.get("available"):
        computed = {}
    cache[key] = computed
    return computed


def build_windows(
    signal_windows, frame_ids: Iterable[str] | None = None
) -> list[FrameWindow]:
    """把调用方给的窗口序列归一成 :class:`FrameWindow` 列表。

    缺 ``frame_id`` 时用 ``frame_ids`` 补齐，仍缺则用
    :func:`evidence.frame_id_of` 从帧号派生（id 只是引用，不代表帧内容）。
    """
    windows: list[FrameWindow] = []
    ids = list(frame_ids) if frame_ids is not None else []
    cache: dict = {}
    for index, raw in enumerate(signal_windows or []):
        if not isinstance(raw, dict):
            continue
        frame_index = raw.get("frame_index")
        if not isinstance(frame_index, int):
            frame_index = index
        identifier = raw.get("frame_id")
        if not isinstance(identifier, str) or not identifier:
            identifier = ids[index] if index < len(ids) else evidence.frame_id_of(frame_index)
        timestamp = raw.get("timestamp_ms", raw.get("t_ms"))
        windows.append(
            FrameWindow(
                frame_id=identifier,
                frame_index=frame_index,
                timestamp_ms=int(timestamp) if _finite(timestamp) else None,
                features=resolve_features(raw, cache),
                landmarks=raw.get("landmarks"),
            )
        )
    return windows


def has_regions(features: dict, regions: frozenset[str]) -> bool:
    """该帧特征是否覆盖定义要求的全部部位。

    部位 → 特征键的映射由 :data:`REGION_FEATURE_KEYS` 给出；缺任意一个键或
    其值为 None，即判定"该部位不可测"。
    """
    if not features.get("available"):
        return False
    for region in regions:
        keys = REGION_FEATURE_KEYS.get(region, ())
        if not keys:
            continue
        if not any(_finite(features.get(key)) for key in keys):
            return False
    return True


# 部位 → 可用特征键（任一非 None 即认为该部位可测）。
# 这些键都是 :func:`features.frame_features` 的既有产物：关节角非 None 本身
# 就证明构成该角的三个关键点都可见，不需要再单独查 landmark 表。
REGION_FEATURE_KEYS: dict[str, tuple[str, ...]] = {
    "shoulder": ("shoulder_angle_deg", "left_shoulder_angle_deg"),
    "elbow": ("elbow_angle_deg", "left_elbow_angle_deg"),
    "wrist": ("wrist_visibility", "elbow_angle_deg"),
    "hip": ("hip_angle_deg", "left_hip_angle_deg"),
    "knee": ("knee_angle_deg", "left_knee_angle_deg"),
    "ankle": ("ankle_angle_deg",),
}

# 用于机位判定的几何量由评估器直接读取特征键，不在这里硬编码。


def series_for(
    key: str,
    windows: list[FrameWindow],
    *,
    region_gate: frozenset[str],
    derive: Callable[[FrameWindow], float | None] | None = None,
) -> Series:
    """构造一个测量序列。

    ``region_gate`` 不含任何部位时跳过部位门控（用于整段聚合类测量）。
    ``derive`` 提供自适应取值函数（例如"取左右膝中较小者"）；缺省时直接读
    ``features[key]``。取不到值的帧被跳过，**不插值、不填 0**。
    """
    values: list[float] = []
    kept: list[FrameWindow] = []
    for window in windows:
        if region_gate and not has_regions(window.features, region_gate):
            continue
        value = derive(window) if derive is not None else window.features.get(key)
        if not _finite(value):
            continue
        values.append(float(value))
        kept.append(window)
    return Series(key=key, values=values, windows=kept)


def spans_for(
    windows: list[FrameWindow], counter_result=None
) -> list[RepSpan]:
    """按计次结果切片；没有计次结果时退化为"整段"（``rep_index=None``）。

    时间戳用于定位区间：``start_ms``/``end_ms`` 落在帧窗口时间上。找不到
    对应帧时**不外推**，直接退化整段（宁可不分次，也不编造每帧归属）。
    """
    if not windows:
        return []
    spans: list[RepSpan] = []
    segments = getattr(counter_result, "segments", None) or []
    if segments:
        for segment in segments:
            start_index = _index_at_ms(windows, getattr(segment, "start_ms", None))
            end_index = _index_at_ms(windows, getattr(segment, "end_ms", None))
            if start_index is None or end_index is None:
                continue
            if end_index < start_index:
                start_index, end_index = end_index, start_index
            spans.append(
                RepSpan(
                    rep_index=getattr(segment, "rep_index", None),
                    start_index=start_index,
                    end_index=end_index,
                )
            )
    if not spans:
        spans = [RepSpan(rep_index=None, start_index=0, end_index=len(windows) - 1)]
    return spans


def window_index_at_ms(windows: list[FrameWindow], stamp: object) -> int | None:
    """找时间戳最接近 ``stamp`` 的窗口下标；无可用时间戳返回 None（不外推）。"""
    return _index_at_ms(windows, stamp)


def _index_at_ms(windows: list[FrameWindow], stamp: object) -> int | None:
    if not _finite(stamp):
        return None
    target = float(stamp)
    best_index: int | None = None
    best_distance: float | None = None
    for index, window in enumerate(windows):
        if window.timestamp_ms is None:
            continue
        distance = abs(float(window.timestamp_ms) - target)
        if best_distance is None or distance < best_distance:
            best_index, best_distance = index, distance
    return best_index


def peak_window(series: Series, *, mode: str) -> FrameWindow | None:
    """取序列的极值帧（``mode`` = ``"min"`` 或 ``"max"``）；空序列返回 None。"""
    if not series.values:
        return None
    if mode == "min":
        position = min(range(len(series.values)), key=lambda i: series.values[i])
    elif mode == "max":
        position = max(range(len(series.values)), key=lambda i: series.values[i])
    else:  # pragma: no cover - 编程错误
        raise ValueError("mode 必须是 'min' 或 'max'")
    return series.windows[position]


def sub_series(series: Series, span: RepSpan) -> Series:
    """按帧区间切出子序列（区间按 ``frame_index`` 匹配，含端点）。"""
    values: list[float] = []
    kept: list[FrameWindow] = []
    for value, window in zip(series.values, series.windows):
        if span.start_index <= window.frame_index <= span.end_index:
            values.append(value)
            kept.append(window)
    return Series(key=series.key, values=values, windows=kept)


def evidence_ids(windows: Iterable[FrameWindow], *, limit: int = MAX_EVIDENCE_FRAMES) -> list[str]:
    """按顺序取代表证据帧 id（去重，最多 ``limit`` 个）。"""
    if limit < 0:
        raise ValueError("limit 不能为负")
    out: list[str] = []
    for window in windows:
        if window.frame_id not in out:
            out.append(window.frame_id)
        if len(out) >= limit:
            break
    return out


def make_finding(
    *,
    exercise_id: str,
    measurement_key: str,
    observed_value: float | None,
    status: str,
    explanation_key: str,
    rep_index: int | None = None,
    frames: Iterable[FrameWindow] | None = None,
) -> RepFinding:
    """构造并校验一个 finding。

    * ``status="unavailable"``：``observed_value`` 强制为 None，且不绑定证据
      （不可用项不该有"代表帧"式的结论）。
    * 其它状态：``observed_value`` 必须可测，并绑定证据帧；没有证据帧时抛
      :class:`evidence.EvidenceGapError`，而不是悄悄降级。
    """
    identifier = f"{exercise_id}.{measurement_key}.{explanation_key}"
    if status == "unavailable":
        return RepFinding(
            rep_index=rep_index,
            measurement_key=measurement_key,
            observed_value=None,
            status="unavailable",
            evidence_frame_ids=[],
            explanation_key=identifier,
        )
    if not _finite(observed_value):
        # 有结论却没有数值：只能是不可用，不允许伪造 0。
        return RepFinding(
            rep_index=rep_index,
            measurement_key=measurement_key,
            observed_value=None,
            status="unavailable",
            evidence_frame_ids=[],
            explanation_key=f"{identifier}.no_value",
        )
    evidence_frames = evidence_ids(frames or [])
    if not evidence_frames:
        raise evidence.EvidenceGapError(
            f"{identifier} 缺少证据帧，不能用 {status} 出结论"
        )
    return RepFinding(
        rep_index=rep_index,
        measurement_key=measurement_key,
        observed_value=round(float(observed_value), 3),
        status=status,
        evidence_frame_ids=evidence_frames,
        explanation_key=identifier,
    )


def unavailable_finding(
    *, exercise_id: str, measurement_key: str, reason_key: str,
    rep_index: int | None = None,
) -> RepFinding:
    """"本机位/本段测不了"的标准 finding（无值、无证据、可解释）。"""
    return make_finding(
        exercise_id=exercise_id,
        measurement_key=measurement_key,
        observed_value=None,
        status="unavailable",
        explanation_key=reason_key,
        rep_index=rep_index,
    )


def higher_is_better(
    *, value: float, good_min: float, attention_min: float
) -> str:
    """越大越好型判据：>= good_min → good；>= attention_min → attention；否则 attention。"""
    if value >= good_min:
        return "good"
    del attention_min  # 保留参数语义：低于 attention 线仍是 attention（不出 bad）
    return "attention"


def lower_is_better(
    *, value: float, good_max: float, attention_max: float
) -> str:
    """越小越好型判据：<= good_max → good；<= attention_max → attention；否则 attention。

    本包**不产生 ``bad`` 状态**（§5.7 只定义 good/attention/unavailable）；
    严重度体现在 ``severity_map`` 与 ``explanation_key`` 上，避免用状态名暗示
    临床结论。
    """
    if value <= good_max:
        return "good"
    del attention_max
    return "attention"


def inside_range(
    *, value: float, target: float, tolerance: float
) -> str:
    """目标区间型判据：落在 ``target ± tolerance`` 内 → good，否则 attention。"""
    return "good" if abs(value - target) <= tolerance else "attention"


def detect_view(windows: list[FrameWindow]) -> str:
    """启发式机位判定：``front`` / ``side`` / ``unknown``。

    判据（全部命名常量，输入为 :func:`features.frame_features` 的衍生比值）：

    * ``front``：``shoulder_width_ratio``（肩宽/髋宽）>=
      :data:`VIEW_FRONT_SHOULDER_HIP_RATIO_MIN`，且肩宽相对身体尺度
      （``shoulder_width_ratio / body_scale``）>=
      :data:`VIEW_FRONT_SHOULDER_DX_MIN`；
    * ``side``：``shoulder_dy``（左右肩 y 差，归一化坐标）>=
      :data:`VIEW_SIDE_SHOULDER_DY_MIN`，或 ``shoulder_dy / body_scale`` >=
      :data:`VIEW_SIDE_SHOULDER_DY_SCALE_MIN`；
    * 两者都不满足，或有效帧少于 :data:`VIEW_MIN_VOTES` /
      :data:`VIEW_MIN_VOTE_FRACTION` → ``unknown``。

    按帧投票取多数；平票时取 ``front``（确定性的平局规则，不是概率）。

    这是**机位启发式**，不是相机标定；它只决定"某项测量能不能做"
    （``valid_views`` 不含 ``unknown`` 的测量会在 unknown 下返回 unavailable），
    不参与打分，也不改变任何阈值。
    """
    front_votes = 0
    side_votes = 0
    eligible = 0
    for window in windows:
        features = window.features
        if not features.get("available"):
            continue
        body_scale = features.get("body_scale")
        ratio = features.get("shoulder_width_ratio")
        shoulder_dy = features.get("shoulder_dy")
        counted = False
        relative_dy = None
        if _finite(ratio) and _finite(body_scale) and float(body_scale) > 1e-6:
            counted = True
            spread = float(ratio) / float(body_scale)
            if (
                float(ratio) >= VIEW_FRONT_SHOULDER_HIP_RATIO_MIN
                and spread >= VIEW_FRONT_SHOULDER_DX_MIN
            ):
                front_votes += 1
            if _finite(shoulder_dy):
                relative_dy = float(shoulder_dy) / float(body_scale)
        if _finite(shoulder_dy):
            counted = True
            absolute_side = float(shoulder_dy) >= VIEW_SIDE_SHOULDER_DY_MIN
            relative_side = (
                relative_dy is not None
                and relative_dy >= VIEW_SIDE_SHOULDER_DY_SCALE_MIN
            )
            if absolute_side or relative_side:
                side_votes += 1
        if counted:
            eligible += 1
    if eligible < VIEW_MIN_VOTES:
        return "unknown"
    if eligible < VIEW_MIN_VOTE_FRACTION * max(1, len(windows)):
        return "unknown"
    if front_votes == 0 and side_votes == 0:
        return "unknown"
    return "front" if front_votes >= side_votes else "side"


def landmarks_angle(window: FrameWindow, names: tuple[str, str, str]) -> float | None:
    """直接在 landmarks 上算三点夹角（特征缺该键时的回退路径）。"""
    normalized = window.features.get("landmarks_normalized")
    if not isinstance(normalized, dict):
        return None
    points = [normalized.get(name) for name in names]
    if any(point is None for point in points):
        return None
    return joint_angle(*points)


def unavailable_per_span(
    *,
    exercise_id: str,
    measurement_key: str,
    reason_key: str,
    spans: list[RepSpan],
) -> list[RepFinding]:
    """某测量对每个已切片的区间各出一条 unavailable（保证按次对齐、不丢次）。"""
    if not spans:
        return [
            unavailable_finding(
                exercise_id=exercise_id,
                measurement_key=measurement_key,
                reason_key=reason_key,
            )
        ]
    return [
        unavailable_finding(
            exercise_id=exercise_id,
            measurement_key=measurement_key,
            reason_key=reason_key,
            rep_index=span.rep_index,
        )
        for span in spans
    ]


def aggregate_simple(
    *,
    exercise_id: str,
    definition,
    series: Series,
    spans: list[RepSpan],
    limit: float,
    greater_is_better: bool,
    good_key: str,
    attention_key: str,
    reducer: Callable[[list[float]], float | None] = mean_of,
) -> list[RepFinding]:
    """"逐次取一维统计量再与阈值比较"这类测量的通用实现。

    只做机械操作（切片 → 归约 → 比较 → 绑证据），阈值与解释键由调用方给出，
    因此**不是**"一个通用公式给所有动作评分"（§5.7）。归约值为 None 时该次
    动作记为 unavailable。
    """
    out: list[RepFinding] = []
    for span in spans:
        span_series = sub_series(series, span)
        value = reducer(span_series.values)
        if value is None:
            out.append(
                unavailable_finding(
                    exercise_id=exercise_id,
                    measurement_key=definition.key,
                    reason_key="regions_unavailable",
                    rep_index=span.rep_index,
                )
            )
            continue
        if greater_is_better:
            status = "good" if value >= limit else "attention"
        else:
            status = "good" if value <= limit else "attention"
        out.append(
            make_finding(
                exercise_id=exercise_id,
                measurement_key=definition.key,
                observed_value=value,
                status=status,
                explanation_key=good_key if status == "good" else attention_key,
                rep_index=span.rep_index,
                frames=span_series.windows,
            )
        )
    if not out:
        return [
            unavailable_finding(
                exercise_id=exercise_id,
                measurement_key=definition.key,
                reason_key="no_measurement_window",
            )
        ]
    return out


def aggregate_sd_consistency(
    *,
    exercise_id: str,
    definition,
    per_span_values: list[float],
    evidence: list[FrameWindow],
    limit: float,
    good_key: str,
    attention_key: str,
    normalized: bool = False,
) -> list[RepFinding]:
    """"多次动作之间是否一致"型测量：少于 2 次时显式 unavailable（无定义）。

    ``normalized=True`` 时输出变异系数（标准差 / 均值）而不是原始标准差；
    均值趋零时无法归一化，直接记为 unavailable，而不是返回一个巨大的比值。
    """
    if len(per_span_values) < 2:
        return [
            unavailable_finding(
                exercise_id=exercise_id,
                measurement_key=definition.key,
                reason_key="single_rep_no_consistency",
            )
        ]
    deviation = stdev_of(per_span_values)
    if deviation is None:
        return [
            unavailable_finding(
                exercise_id=exercise_id,
                measurement_key=definition.key,
                reason_key="degenerate_values",
            )
        ]
    value = deviation
    if normalized:
        average = mean_of(per_span_values)
        if average is None or abs(average) < NORMALIZATION_EPSILON:
            return [
                unavailable_finding(
                    exercise_id=exercise_id,
                    measurement_key=definition.key,
                    reason_key="degenerate_values",
                )
            ]
        value = deviation / abs(average)
    status = "good" if value <= limit else "attention"
    return [
        make_finding(
            exercise_id=exercise_id,
            measurement_key=definition.key,
            observed_value=value,
            status=status,
            explanation_key=good_key if status == "good" else attention_key,
            frames=evidence,
        )
    ]



__all__ = [
    "MAX_EVIDENCE_FRAMES",
    "MS_PER_SECOND",
    "NORMALIZATION_EPSILON",
    "REGION_FEATURE_KEYS",
    "VIEW_FRONT_SHOULDER_DX_MIN",
    "VIEW_FRONT_SHOULDER_HIP_RATIO_MIN",
    "VIEW_MIN_VOTES",
    "VIEW_MIN_VOTE_FRACTION",
    "VIEW_SIDE_SHOULDER_DY_MIN",
    "VIEW_SIDE_SHOULDER_DY_SCALE_MIN",
    "FrameWindow",
    "RepSpan",
    "Series",
    "aggregate_sd_consistency",
    "aggregate_simple",
    "build_windows",
    "clamp",
    "detect_view",
    "evidence_ids",
    "has_regions",
    "higher_is_better",
    "inside_range",
    "is_finite",
    "landmarks_angle",
    "lower_is_better",
    "make_finding",
    "mean_of",
    "peak_window",
    "resolve_features",
    "series_for",
    "spans_for",
    "stdev_of",
    "sub_series",
    "unavailable_finding",
    "unavailable_per_span",
    "window_index_at_ms",
]
