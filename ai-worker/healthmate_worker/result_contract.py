"""Worker-side mirror of the MotionWorkerResultV2 receipt contract (§10/§5).

The worker runs this locally before POSTing ``/worker/jobs/{id}/complete`` so a
structurally invalid receipt never costs a round trip and never loops on a
guaranteed-422. Keep the invariants in lockstep with the backend V2 schema
(owned by package E) and bump ``SCHEMA_VERSION`` together.

V2 invariants vs v1:
  * Receipt groups: video_quality / subject / pose_evidence / recognition_candidates
    / frames / measurements.
  * ``pose_evidence.available`` means "pose measurements exist", NOT "recognized
    a six-class exercise"; ``measurements.available`` is independent.
  * ``frames[]`` is the generic evidence pool, kept even on six-class rejection,
    sorted by real timestamp ascending.
  * Previews travel by REFERENCE only (preview_asset_id + hash + timestamp +
    dimensions). The receipt carries NO image bytes (no image_b64).
"""

from __future__ import annotations

import math

from .config import settings
from .errors import ProcessingError

SCHEMA_VERSION = "motion-worker-v2"

_REQUIRED_GROUPS = (
    "video_quality",
    "subject",
    "pose_evidence",
    "recognition_candidates",
    "frames",
    "measurements",
)


def _reject(field_path: str, reason: str) -> None:
    # Permanent: resending the same receipt will always 422. Do not retry.
    raise ProcessingError(
        "motion_result_schema_invalid",
        f"本地回执校验失败 {field_path}: {reason}",
        retryable=False,
    )


def validate_motion_result_local(result: dict) -> dict:
    """Pre-validate a MotionWorkerResultV2 receipt before upload. Raises."""
    if result.get("schema_version") != SCHEMA_VERSION:
        _reject("schema_version", "缺少或不匹配的 schema_version")

    list_groups = {"recognition_candidates", "frames"}
    for group in _REQUIRED_GROUPS:
        expected = list if group in list_groups else dict
        if not isinstance(result.get(group), expected):
            _reject(group, f"缺少回执分组 {group}")

    vq = result["video_quality"]
    if not isinstance(vq.get("decoded_ok"), bool):
        _reject("video_quality.decoded_ok", "缺少解码状态")
    dur = vq.get("duration_ms")
    if not isinstance(dur, (int, float)) or not math.isfinite(dur) or dur < 0:
        _reject("video_quality.duration_ms", "时长无效")

    pe = result["pose_evidence"]
    if not isinstance(pe.get("available"), bool):
        _reject("pose_evidence.available", "缺少姿态测量状态")

    measurements = result["measurements"]
    if not isinstance(measurements.get("available"), bool):
        _reject("measurements.available", "缺少测量状态")
    if measurements["available"]:
        reps = measurements.get("reps")
        if type(reps) is not int or not 0 <= reps <= 10000:
            _reject("measurements.reps", "动作次数无效")
        if not measurements.get("exercise_id"):
            _reject("measurements.exercise_id", "有测量时必须给出 exercise_id")

    for c in result["recognition_candidates"]:
        if not isinstance(c, dict):
            _reject("recognition_candidates", "候选项格式无效")
        for key in ("source", "source_label", "raw_score", "score_type"):
            if key not in c:
                _reject(f"recognition_candidates.{key}", "候选项缺少命名空间字段")
        # canonical_id may legitimately be None (unmapped Kinetics label).

    frames = result["frames"]
    if not isinstance(frames, list) or len(frames) > 200:
        _reject("frames", "证据帧格式无效或超出上限")
    last_ts = -1.0
    preview_count = 0
    for index, frame in enumerate(frames):
        if not isinstance(frame, dict):
            _reject(f"frames[{index}]", "证据帧必须是对象")
        if "image_b64" in frame:
            _reject(f"frames[{index}]", "回执不得携带图像字节，只传预览引用")
        ts = frame.get("timestamp_ms")
        if not isinstance(ts, (int, float)) or not math.isfinite(ts) or ts < 0:
            _reject(f"frames[{index}].timestamp_ms", "证据帧时间无效")
        if ts < last_ts:
            _reject(f"frames[{index}]", "证据帧必须按真实时间升序")
        last_ts = float(ts)
        if not frame.get("frame_id"):
            _reject(f"frames[{index}].frame_id", "证据帧缺少 frame_id")
        if frame.get("preview_asset_id"):
            preview_count += 1
            if preview_count > int(settings.motion_display_preview_count):
                _reject(f"frames[{index}]", "展示预览帧数超出上限")
            size = frame.get("preview_bytes")
            if isinstance(size, int) and size > int(settings.motion_preview_max_bytes):
                _reject(f"frames[{index}].preview_bytes", "单帧预览超出字节上限")
            if not frame.get("preview_sha256"):
                _reject(f"frames[{index}].preview_sha256", "预览缺少哈希引用")
            dims = frame.get("preview_dimensions") or {}
            if not (
                isinstance(dims, dict) and dims.get("width") and dims.get("height")
            ):
                _reject(f"frames[{index}].preview_dimensions", "预览缺少尺寸引用")

    # A rejected/unrecognised result must still never carry a quality score.
    has_recognition = any(
        c.get("source") == "pose" for c in result["recognition_candidates"]
    )
    if not has_recognition and measurements["available"]:
        _reject("measurements", "未被本地姿态接受的结果不得输出计数/评分")
    return result
