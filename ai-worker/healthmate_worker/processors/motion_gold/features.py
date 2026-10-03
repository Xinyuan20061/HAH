"""骨架时序表示（能力计划 §5.4）：逐帧特征向量。

§5.4 要求的每帧特征：
归一化 2D/可选 z landmarks、关键关节角、一阶速度与二阶变化、左右对称性、
躯干方向与身体尺度、landmark visibility、主体框运动量。

分工：

* :func:`frame_features` 处理**单帧**：归一化坐标、关节角、对称性、躯干方向、
  身体尺度、visibility 统计、主体框。瞬时量不需要历史。
* :func:`frame_features_sequence` 在逐帧结果之上补**时序量**：一阶速度、二阶
  变化与主体框运动量（这些量在只有一帧时没有定义，单帧调用时以 0.0 占位）。

输入约定（关键，避免依赖 numpy）：``landmarks`` 是"每帧一个序列"的
``(x, y, z, visibility)`` 列表/元组，索引采用 MediaPipe Pose 的 33 点顺序
（见 :data:`LM`）。实现只用纯 Python 的 ``math``。

本模块**不做什么**：

* 不导入 mediapipe / numpy / torch / cv2；缺失或过短的输入返回
  ``{"available": False, ...}`` 而不是抛异常。
* 不做模型推理，也不声称这些手工特征是"训练过的表示"；它们只是可解释、
  可离线复现的基线特征。
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

# 特征向量版本：键集合/语义变化时 +1。
FEATURE_VERSION = "motion-gold-features@v1"

# --- MediaPipe Pose 33 点索引（只用到其中一部分） -----------------------------
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

# 计算关节角/身体尺度所必需的最小点数（左膝=25 -> 至少 26 个点）。
MIN_LANDMARKS = max(LM.values()) + 1

# 逐点 visibility 低于该值时视为不可信，但仍会出现在可见度统计里。
VISIBILITY_MIN = 0.5


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _unavailable(reason: str, **extra) -> dict:
    """统一构造"本帧不可用"标记，绝不抛异常给调用方。"""
    payload = {
        "available": False,
        "feature_version": FEATURE_VERSION,
        "reason_unavailable": reason,
    }
    payload.update(extra)
    return payload


def _point(landmarks: Sequence, name: str):
    """取一个可信点 ``(x, y, z)``；不可信/越界返回 None。"""
    index = LM[name]
    if index >= len(landmarks):
        return None
    item = landmarks[index]
    if not isinstance(item, (list, tuple)) or len(item) < 2:
        return None
    x, y = item[0], item[1]
    if not (_finite(x) and _finite(y)):
        return None
    visibility = item[3] if len(item) > 3 and _finite(item[3]) else 1.0
    if float(visibility) < VISIBILITY_MIN:
        return None
    z = item[2] if len(item) > 2 and _finite(item[2]) else 0.0
    return (float(x), float(y), float(z))


def joint_angle(a, b, c, *, max_value: float = 180.0) -> float | None:
    """三点夹角（度）。共线退化或任一点不可信时返回 None，不猜值。"""
    if a is None or b is None or c is None:
        return None
    ba = (a[0] - b[0], a[1] - b[1])
    bc = (c[0] - b[0], c[1] - b[1])
    denominator = math.hypot(*ba) * math.hypot(*bc)
    if denominator < 1e-8:
        return None
    cosine = max(-1.0, min(1.0, (ba[0] * bc[0] + ba[1] * bc[1]) / denominator))
    return min(max_value, math.degrees(math.acos(cosine)))


def _angle(landmarks: Sequence, names: tuple[str, str, str]) -> float | None:
    return joint_angle(
        _point(landmarks, names[0]),
        _point(landmarks, names[1]),
        _point(landmarks, names[2]),
    )


def _distance(a, b) -> float | None:
    if a is None or b is None:
        return None
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _midpoint(a, b):
    if a is None or b is None:
        return None
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0, (a[2] + b[2]) / 2.0)


def _mean(values: Iterable[float]) -> float | None:
    items = [float(v) for v in values if _finite(v)]
    if not items:
        return None
    return sum(items) / len(items)


def _symmetry(left: float | None, right: float | None) -> float | None:
    """左右差（同一角度的绝对差，度）。任一侧不可测时返回 None。

    返回 None 表示"该机位测不了对称性"，不是"对称性为 0"。
    """
    if left is None or right is None:
        return None
    return abs(float(left) - float(right))


def body_scale(landmarks: Sequence) -> float | None:
    """身体尺度代理：肩中点到髋中点的归一化距离。

    用作所有像素/归一化位移的无量纲化分母。两点任一不可信时返回 None。
    """
    shoulder = _midpoint(
        _point(landmarks, "left_shoulder"), _point(landmarks, "right_shoulder")
    )
    hip = _midpoint(_point(landmarks, "left_hip"), _point(landmarks, "right_hip"))
    return _distance(shoulder, hip)


def torso_inclination(landmarks: Sequence) -> float | None:
    """躯干相对图像竖直方向的夹角（度）。

    0° = 肩中点正上方的髋（直立）；数值越大表示躯干越偏离竖直。
    这是**图像坐标下的近似**，受机位与透视影响，不是临床躯干角。
    """
    shoulder = _midpoint(
        _point(landmarks, "left_shoulder"), _point(landmarks, "right_shoulder")
    )
    hip = _midpoint(_point(landmarks, "left_hip"), _point(landmarks, "right_hip"))
    if shoulder is None or hip is None:
        return None
    dx = shoulder[0] - hip[0]
    dy = shoulder[1] - hip[1]
    if math.hypot(dx, dy) < 1e-8:
        return None
    # 图像 y 轴向下，所以"竖直向上"是 (0, -1)。
    return math.degrees(math.atan2(abs(dx), -dy))


def torso_direction(landmarks: Sequence) -> tuple[float | None, float | None]:
    """躯干方向的两个分量 ``(dx, dy)``：肩中点减髋中点。

    ``dy > 0`` 表示肩在髋上方（正常站立/俯卧撑头朝上机位）；``dy < 0`` 表示
    画面里人是倒置的。用这两个分量而不是布尔标志，避免下游把一个被压扁的
    值当成有效测量。
    """
    shoulder = _midpoint(
        _point(landmarks, "left_shoulder"), _point(landmarks, "right_shoulder")
    )
    hip = _midpoint(_point(landmarks, "left_hip"), _point(landmarks, "right_hip"))
    if shoulder is None or hip is None:
        return None, None
    return float(shoulder[0] - hip[0]), float(shoulder[1] - hip[1])


def torso_vertical_offset(landmarks: Sequence) -> float | None:
    """肩中点相对髋中点的 y 偏移（正数=肩在髋上方，图像 y 向下）。

    供需要区分"头朝上/头朝下"的机位判断使用，避免把倒置画面读成立姿。
    """
    shoulder = _midpoint(
        _point(landmarks, "left_shoulder"), _point(landmarks, "right_shoulder")
    )
    hip = _midpoint(_point(landmarks, "left_hip"), _point(landmarks, "right_hip"))
    if shoulder is None or hip is None:
        return None
    return float(hip[1] - shoulder[1])


def visibility_summary(landmarks: Sequence) -> dict:
    """visibility 统计：均值/最小值/可信点比例。

    注意：可信点比例用的是 :data:`VISIBILITY_MIN`，它是**观测门槛**，
    不是"测量合格线"。
    """
    values = []
    for index in range(min(len(landmarks), MIN_LANDMARKS)):
        item = landmarks[index]
        if isinstance(item, (list, tuple)) and len(item) > 3 and _finite(item[3]):
            values.append(float(item[3]))
    if not values:
        return {"visibility_mean": None, "visibility_min": None, "visible_ratio": None}
    return {
        "visibility_mean": sum(values) / len(values),
        "visibility_min": min(values),
        "visible_ratio": sum(1 for v in values if v >= VISIBILITY_MIN) / len(values),
    }


def key_joint_angles(landmarks: Sequence) -> dict:
    """§5.4 的"关键关节角"。测不到的键为 None（不填 0）。"""
    left_knee = _angle(landmarks, ("left_hip", "left_knee", "left_ankle"))
    right_knee = _angle(landmarks, ("right_hip", "right_knee", "right_ankle"))
    left_hip = _angle(landmarks, ("left_shoulder", "left_hip", "left_knee"))
    right_hip = _angle(landmarks, ("right_shoulder", "right_hip", "right_knee"))
    left_elbow = _angle(landmarks, ("left_shoulder", "left_elbow", "left_wrist"))
    right_elbow = _angle(landmarks, ("right_shoulder", "right_elbow", "right_wrist"))
    left_shoulder = _angle(landmarks, ("left_elbow", "left_shoulder", "left_hip"))
    right_shoulder = _angle(landmarks, ("right_elbow", "right_shoulder", "right_hip"))
    left_ankle = _angle(landmarks, ("left_knee", "left_ankle", "left_foot_index"))
    right_ankle = _angle(landmarks, ("right_knee", "right_ankle", "right_foot_index"))

    left_side = _point(landmarks, "left_shoulder")
    right_side = _point(landmarks, "right_shoulder")
    left_hip_pt = _point(landmarks, "left_hip")
    right_hip_pt = _point(landmarks, "right_hip")
    knee_mid = _midpoint(_point(landmarks, "left_knee"), _point(landmarks, "right_knee"))
    ankle_mid = _midpoint(
        _point(landmarks, "left_ankle"), _point(landmarks, "right_ankle")
    )
    shoulder_mid = _midpoint(left_side, right_side)
    hip_mid = _midpoint(left_hip_pt, right_hip_pt)

    # 肩-髋-膝（矢状面近似）：用于俯卧撑/平板的"髋部塌陷/抬高"代理。
    left_sagittal = joint_angle(left_side, left_hip_pt, _point(landmarks, "left_knee"))
    right_sagittal = joint_angle(
        right_side, right_hip_pt, _point(landmarks, "right_knee")
    )
    # 髋-膝-踝（矢状面直腿度）：用于平板/俯卧撑的身体直线代理。
    left_leg_line = joint_angle(
        left_hip_pt, _point(landmarks, "left_knee"), _point(landmarks, "left_ankle")
    )
    right_leg_line = joint_angle(
        right_hip_pt, _point(landmarks, "right_knee"), _point(landmarks, "right_ankle")
    )
    # 肩-髋-踝：俯卧撑"肩髋踝直线偏差"最直接的代理。
    left_body_line = joint_angle(left_side, left_hip_pt, _point(landmarks, "left_ankle"))
    right_body_line = joint_angle(
        right_side, right_hip_pt, _point(landmarks, "right_ankle")
    )

    return {
        "knee_angle_deg": _mean([left_knee, right_knee]),
        "left_knee_angle_deg": left_knee,
        "right_knee_angle_deg": right_knee,
        "hip_angle_deg": _mean([left_hip, right_hip]),
        "left_hip_angle_deg": left_hip,
        "right_hip_angle_deg": right_hip,
        "elbow_angle_deg": _mean([left_elbow, right_elbow]),
        "left_elbow_angle_deg": left_elbow,
        "right_elbow_angle_deg": right_elbow,
        "shoulder_angle_deg": _mean([left_shoulder, right_shoulder]),
        "left_shoulder_angle_deg": left_shoulder,
        "right_shoulder_angle_deg": right_shoulder,
        "ankle_angle_deg": _mean([left_ankle, right_ankle]),
        "sagittal_hip_deg": _mean([left_sagittal, right_sagittal]),
        "leg_line_deg": _mean([left_leg_line, right_leg_line]),
        "body_line_deg": _mean([left_body_line, right_body_line]),
        # "矢状面直线度"的补充：肩-髋-踝在图像上的横向偏移（归一化）。
        "body_offset": _offset(shoulder_mid, hip_mid, ankle_mid),
        "knee_over_ankle_ratio": _ratio(knee_mid, ankle_mid, shoulder_mid),
    }


def _offset(shoulder, hip, ankle) -> float | None:
    """肩、髋、踝三点横向偏移（归一化）：共线时为 0。"""
    if shoulder is None or hip is None or ankle is None:
        return None
    total = abs(shoulder[0] - hip[0]) + abs(hip[0] - ankle[0])
    return float(total)


def _ratio(near, far, reference) -> float | None:
    """膝关节相对踝关节的横向偏移，用肩-踝横向跨度归一化。"""
    if near is None or far is None or reference is None:
        return None
    span = abs(reference[0] - far[0])
    if span < 1e-6:
        return None
    return float((near[0] - far[0]) / span)


def _landmark_visibility(landmarks: Sequence, name: str) -> float | None:
    """单点 visibility 原值（不做门槛过滤）；越界返回 None。"""
    index = LM[name]
    if index >= len(landmarks):
        return None
    item = landmarks[index]
    if not isinstance(item, (list, tuple)) or len(item) < 4:
        return None
    return float(item[3]) if _finite(item[3]) else None


def _mean_pair(left: float | None, right: float | None) -> float | None:
    values = [v for v in (left, right) if _finite(v)]
    return sum(values) / len(values) if values else None


def derived_ratios(landmarks: Sequence) -> dict:
    """供测量层使用的**无量纲**几何比值（全部可解释、无需训练）。

    * ``shoulder_width_ratio``：肩宽 / 髋宽（机位启发式用）。
    * ``shoulder_dy``：左右肩归一化 y 之差（侧机位近侧肩遮挡代理）。
    * ``shrug_proxy_ratio``：肩中点相对髋中点的竖直距离，用身体尺度归一化
      （侧平举"耸肩代理"：数值变小表示肩带被上提）。
    * ``elbow_drift_ratio``：弯举时肩-肘的**竖直**距离 / 身体尺度（肘部前移
      漂移代理）。
    * ``wrist_stack_ratio``：腕相对肘的**横向**偏移 / 肩宽（肩推"腕肘堆叠"代理）。
    * ``ankle_span_ratio``：左右踝归一化横向距离 / 肩宽（弓步步距代理）。
    * ``knee_span_ratio``：左右膝归一化横向距离 / 肩宽。
    * ``pelvis_tilt_ratio``：左右髋归一化 y 之差（骨盆左右差代理）。

    任一分量所需关键点不可见时返回 None——**不填 0**，否则会把"看不见"读成
    "完全对称/完全在正确位置"。
    """
    left_shoulder = _point(landmarks, "left_shoulder")
    right_shoulder = _point(landmarks, "right_shoulder")
    left_hip = _point(landmarks, "left_hip")
    right_hip = _point(landmarks, "right_hip")
    left_elbow = _point(landmarks, "left_elbow")
    right_elbow = _point(landmarks, "right_elbow")
    left_ankle = _point(landmarks, "left_ankle")
    right_ankle = _point(landmarks, "right_ankle")
    left_knee = _point(landmarks, "left_knee")
    right_knee = _point(landmarks, "right_knee")
    scale = body_scale(landmarks)

    shoulder_width = _distance(left_shoulder, right_shoulder)
    hip_width = _distance(left_hip, right_hip)
    shoulder_mid = _midpoint(left_shoulder, right_shoulder)
    hip_mid = _midpoint(left_hip, right_hip)

    shoulder_width_ratio = None
    if shoulder_width is not None and hip_width is not None and hip_width > 1e-6:
        shoulder_width_ratio = shoulder_width / hip_width

    shoulder_dy = None
    if left_shoulder is not None and right_shoulder is not None:
        shoulder_dy = abs(left_shoulder[1] - right_shoulder[1])

    shrug_proxy_ratio = None
    if shoulder_mid is not None and hip_mid is not None and scale and scale > 1e-6:
        shrug_proxy_ratio = abs(hip_mid[1] - shoulder_mid[1]) / scale

    elbow_drift_ratio = None
    if scale and scale > 1e-6:
        drifts = [
            abs(elbow[1] - shoulder[1]) / scale
            for shoulder, elbow in (
                (left_shoulder, left_elbow),
                (right_shoulder, right_elbow),
            )
            if shoulder is not None and elbow is not None
        ]
        if drifts:
            elbow_drift_ratio = sum(drifts) / len(drifts)

    wrist_stack_ratio = None
    if shoulder_width is not None and shoulder_width > 1e-6:
        offsets = [
            abs(wrist[0] - elbow[0]) / shoulder_width
            for elbow, wrist in ((left_elbow, _point(landmarks, "left_wrist")),
                                 (right_elbow, _point(landmarks, "right_wrist")))
            if elbow is not None and wrist is not None
        ]
        if offsets:
            wrist_stack_ratio = sum(offsets) / len(offsets)

    ankle_span_ratio = None
    if left_ankle is not None and right_ankle is not None and shoulder_width and shoulder_width > 1e-6:
        ankle_span_ratio = abs(left_ankle[0] - right_ankle[0]) / shoulder_width

    knee_span_ratio = None
    if left_knee is not None and right_knee is not None and shoulder_width and shoulder_width > 1e-6:
        knee_span_ratio = abs(left_knee[0] - right_knee[0]) / shoulder_width

    pelvis_tilt_ratio = None
    if left_hip is not None and right_hip is not None and scale and scale > 1e-6:
        pelvis_tilt_ratio = abs(left_hip[1] - right_hip[1]) / scale

    return {
        "shoulder_width_ratio": shoulder_width_ratio,
        "shoulder_dy": shoulder_dy,
        "shrug_proxy_ratio": shrug_proxy_ratio,
        "elbow_drift_ratio": elbow_drift_ratio,
        "wrist_stack_ratio": wrist_stack_ratio,
        "ankle_span_ratio": ankle_span_ratio,
        "knee_span_ratio": knee_span_ratio,
        "pelvis_tilt_ratio": pelvis_tilt_ratio,
    }


def subject_box(landmarks: Sequence) -> list[float] | None:
    """可信点的归一化主体框 ``[x1, y1, x2, y2]``；无可信点时 None。"""
    points = []
    for name in LM:
        point = _point(landmarks, name)
        if point is not None:
            points.append((point[0], point[1]))
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def _normalized_landmarks(landmarks: Sequence) -> dict:
    """归一化 2D 坐标（以肩中点为原点、肩髋距离为 1），可选 z 原样保留。

    这是"归一化 2D landmarks"的车体坐标系：平移与尺度不变，便于后续模型
    或阈值在不同机位/画幅下复用。
    """
    shoulder = _midpoint(
        _point(landmarks, "left_shoulder"), _point(landmarks, "right_shoulder")
    )
    hip = _midpoint(_point(landmarks, "left_hip"), _point(landmarks, "right_hip"))
    scale = _distance(shoulder, hip)
    if shoulder is None or hip is None or scale is None or scale < 1e-6:
        return {}
    normalized = {}
    for name in LM:
        point = _point(landmarks, name)
        if point is None:
            continue
        normalized[name] = (
            (point[0] - shoulder[0]) / scale,
            (point[1] - shoulder[1]) / scale,
            point[2],
        )
    return normalized


def frame_features(
    landmarks,
    *,
    frame_index: int | None = None,
    timestamp_ms: int | None = None,
    include_landmarks: bool = False,
) -> dict:
    """单帧特征向量（§5.4 的"帧内"部分）。

    Parameters
    ----------
    landmarks : 序列 ``[(x, y, z, visibility), ...]``（MediaPipe 33 点顺序），
        也接受任何长度足够的序列/元组；长度为 0 或短于
        :data:`MIN_LANDMARKS` 时返回 ``available=False`` 标记。
    frame_index / timestamp_ms : 可选的溯源信息，原样回填。
    include_landmarks : 为 True 时附带 ``landmarks_normalized`` 明细，供
        动作专属评估器直接计算（默认关闭以保持向量精简）。

    Returns
    -------
    dict
        ``available=True`` 时包含归一化坐标、关键关节角、对称性、躯干方向与
        身体尺度、visibility 统计、主体框、时序量占位（见
        :func:`frame_features_sequence`），以及 ``feature_version``。
        三角点退化（共线/遮挡）时对应键为 ``None``——**不填 0 冒充测量值**。
        ``available=False`` 时只有 ``reason_unavailable`` 等诊断字段。

    本函数不做时序计算，也不抛异常：不可用即不可用。
    """
    if landmarks is None:
        return _unavailable("no_landmarks", frame_index=frame_index)
    try:
        count = len(landmarks)
    except TypeError:
        return _unavailable("landmarks_not_sequence", frame_index=frame_index)
    if count == 0:
        return _unavailable("no_landmarks", frame_index=frame_index)
    if count < MIN_LANDMARKS:
        return _unavailable(
            "landmarks_too_short",
            frame_index=frame_index,
            landmark_count=count,
            required_count=MIN_LANDMARKS,
        )

    angles = key_joint_angles(landmarks)
    visibility = visibility_summary(landmarks)
    scale = body_scale(landmarks)
    torso = torso_inclination(landmarks)
    torso_dx, torso_dy = torso_direction(landmarks)
    box = subject_box(landmarks)

    features = {
        "available": True,
        "feature_version": FEATURE_VERSION,
        "frame_index": frame_index,
        "timestamp_ms": timestamp_ms,
        "landmark_count": count,
        "body_scale": scale,
        "torso_inclination_deg": torso,
        "torso_dx": torso_dx,
        "torso_dy": torso_dy,
        # --- 左右对称性（度）。None 表示该机位/该帧测不了，不是 0。 --------
        "knee_symmetry_deg": _symmetry(
            angles["left_knee_angle_deg"], angles["right_knee_angle_deg"]
        ),
        "hip_symmetry_deg": _symmetry(
            angles["left_hip_angle_deg"], angles["right_hip_angle_deg"]
        ),
        "elbow_symmetry_deg": _symmetry(
            angles["left_elbow_angle_deg"], angles["right_elbow_angle_deg"]
        ),
        "shoulder_symmetry_deg": _symmetry(
            angles["left_shoulder_angle_deg"], angles["right_shoulder_angle_deg"]
        ),
        "wrist_visibility": _mean_pair(
            _landmark_visibility(landmarks, "left_wrist"),
            _landmark_visibility(landmarks, "right_wrist"),
        ),
        # --- 时序量占位（单帧无定义；由 frame_features_sequence 填充） -----
        "velocity_main": 0.0,
        "acceleration_main": 0.0,
        "subject_box_motion": 0.0,
        "torso_angular_velocity_deg_s": 0.0,
        # --- 离散的"变换"（供规则与模型共用，不参与时序） -----------------
        "landmarks_normalized_count": len(_normalized_landmarks(landmarks)),
    }
    features.update(angles)
    features.update(visibility)
    features.update(derived_ratios(landmarks))
    features["subject_box"] = box
    if include_landmarks:
        features["landmarks_normalized"] = _normalized_landmarks(landmarks)
    return features


def frame_features_sequence(
    frames: Sequence[Sequence],
    *,
    fps: float,
    timestamp_ms: Sequence[int] | None = None,
    include_landmarks: bool = False,
) -> list[dict]:
    """逐帧特征 + 一阶速度/二阶变化/主体框运动量（§5.4 的时序部分）。

    时基由 ``fps`` 决定（``dt = 1 / fps``）；``fps`` 非正数时返回每帧标
    ``available=False`` 的列表，避免除零或伪造速度。

    一阶/二阶量只对**相邻且都可用**的帧计算；跨不可用帧不做插值、不做跨段
    连接（不"补"出一段并不存在的运动）。单帧调用时这些键保持 0.0。

    本函数不导入 numpy，也不做任何模型推理。
    """
    if not _finite(fps) or float(fps) <= 0:
        return [_unavailable("invalid_fps", frame_index=index)
                for index in range(len(frames or []))]
    dt = 1.0 / float(fps)
    stamps = list(timestamp_ms) if timestamp_ms is not None else None

    output: list[dict] = []
    previous = None
    for index, landmarks in enumerate(frames or []):
        stamp = stamps[index] if stamps and index < len(stamps) else None
        current = frame_features(
            landmarks,
            frame_index=index,
            timestamp_ms=stamp,
            include_landmarks=include_landmarks,
        )
        if current.get("available") and previous is not None:
            _apply_temporal(current, previous, dt)
        output.append(current)
        previous = current if current.get("available") else None
    return output


def _apply_temporal(current: dict, previous: dict, dt: float) -> None:
    """把一阶/二阶差分写进 ``current``（就地修改，纯函数式语义）。"""
    main_key = "knee_angle_deg"
    if current.get(main_key) is None:
        main_key = "elbow_angle_deg"
    value = current.get(main_key)
    previous_value = previous.get(main_key)
    if _finite(value) and _finite(previous_value):
        velocity = (float(value) - float(previous_value)) / dt
        current["velocity_main"] = velocity
        previous_velocity = previous.get("velocity_main")
        if _finite(previous_velocity):
            current["acceleration_main"] = (velocity - float(previous_velocity)) / dt

    torso = current.get("torso_inclination_deg")
    previous_torso = previous.get("torso_inclination_deg")
    if _finite(torso) and _finite(previous_torso):
        current["torso_angular_velocity_deg_s"] = (
            float(torso) - float(previous_torso)
        ) / dt

    box = current.get("subject_box")
    previous_box = previous.get("subject_box")
    if box and previous_box and len(box) == 4 and len(previous_box) == 4:
        # 主体框运动量＝框中心位移 + 尺度变化，均以身体尺度（若可得）归一化。
        scale = current.get("body_scale") or 1.0
        center_shift = math.hypot(
            (box[0] + box[2]) / 2.0 - (previous_box[0] + previous_box[2]) / 2.0,
            (box[1] + box[3]) / 2.0 - (previous_box[1] + previous_box[3]) / 2.0,
        )
        width_now = max(1e-6, box[2] - box[0])
        width_before = max(1e-6, previous_box[2] - previous_box[0])
        scale_change = abs(width_now - width_before) / width_before
        current["subject_box_motion"] = float(center_shift / max(scale, 1e-6)
                                             + scale_change)


__all__ = [
    "FEATURE_VERSION",
    "LM",
    "MIN_LANDMARKS",
    "VISIBILITY_MIN",
    "body_scale",
    "frame_features",
    "frame_features_sequence",
    "joint_angle",
    "key_joint_angles",
    "subject_box",
    "torso_direction",
    "torso_inclination",
    "torso_vertical_offset",
    "visibility_summary",
]
