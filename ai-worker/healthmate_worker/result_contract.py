"""Worker-side mirror of the motion receipt contract.

This is the SAME contract the backend enforces
(``backend/app/schemas/worker.py::MotionWorkerResultV1``). The worker runs it
locally before POSTing ``/worker/jobs/{id}/complete`` so a structurally invalid
receipt never costs a round trip and never loops on a guaranteed-422.

Keep the field set and invariants in lockstep with the backend contract; bump
``SCHEMA_VERSION`` together.
"""

from __future__ import annotations

import base64
import binascii
import math

from .errors import ProcessingError

SCHEMA_VERSION = "motion-worker-result-v1"


def _reject(field_path: str, reason: str) -> None:
    # Permanent: resending the same receipt will always 422. Do not retry.
    raise ProcessingError(
        "motion_result_schema_invalid",
        f"本地回执校验失败 {field_path}: {reason}",
        retryable=False,
    )


def validate_motion_result_local(result: dict) -> dict:
    """Pre-validate a motion receipt before upload. Raises ProcessingError."""
    if result.get("schema_version") != SCHEMA_VERSION:
        _reject("schema_version", "缺少或不匹配的 schema_version")

    pose = result.get("pose")
    if not isinstance(pose, dict) or not isinstance(pose.get("available"), bool):
        _reject("pose.available", "缺少可评价状态")
    if not pose["available"]:
        if not pose.get("message"):
            _reject("pose.message", "不可评价时必须提供原因")
    else:
        reps = pose.get("reps")
        rate = pose.get("keypoint_valid_rate")
        if type(reps) is not int or not 0 <= reps <= 10000:
            _reject("pose.reps", "动作次数无效")
        if (
            type(rate) not in {int, float}
            or isinstance(rate, bool)
            or not math.isfinite(rate)
            or not 0 <= rate <= 1
        ):
            _reject("pose.keypoint_valid_rate", "关键点有效率无效")

    frames = result.get("frames", [])
    if not isinstance(frames, list) or len(frames) > 200:
        _reject("frames", "关键帧格式无效")
    preview_count = 0
    for index, frame in enumerate(frames):
        if not isinstance(frame, dict) or not frame.get("event") or frame.get("url"):
            _reject(f"frames[{index}]", "关键帧必须有事件且不得带外部 URL")
        ts = frame.get("timestamp")
        if type(ts) not in {int, float} or not math.isfinite(ts) or ts < 0:
            _reject(f"frames[{index}].timestamp", "关键帧时间无效")
        image_b64 = frame.get("image_b64")
        if image_b64 is None:
            continue
        preview_count += 1
        if preview_count > 4 or not isinstance(image_b64, str):
            _reject(f"frames[{index}].image_b64", "预览数量/类型无效")
        if frame.get("image_mime") != "image/jpeg":
            _reject(f"frames[{index}].image_mime", "预览必须是 JPEG")
        try:
            raw = base64.b64decode(image_b64, validate=True)
        except (ValueError, binascii.Error):
            _reject(f"frames[{index}].image_b64", "预览编码无效")
        if (
            len(raw) > 80 * 1024
            or not raw.startswith(b"\xff\xd8")
            or not raw.endswith(b"\xff\xd9")
        ):
            _reject(f"frames[{index}].image_b64", "预览必须是 80KB 内 JPEG")

    recognition = result.get("recognition")
    if isinstance(recognition, dict):
        accepted = recognition.get("accepted")
        if accepted is True and recognition.get("selected_type") is None:
            _reject("recognition.selected_type", "已识别但缺少 selected_type")
        if accepted is False and recognition.get("selected_type") is not None:
            _reject("recognition.selected_type", "拒识不得给出 selected_type")
        score = result.get("score") or {}
        if accepted is False and (score.get("available") or pose.get("available")):
            _reject("recognition", "拒识结果不得生成动作评分")
    return result
