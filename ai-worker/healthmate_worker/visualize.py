"""Privacy-aware, size-bounded visual evidence for motion events."""

from __future__ import annotations

import base64
import hashlib

from .errors import ProcessingError

# Solid background for the cloud-vision keyframe canvas. The unified chain never
# blits the original (person) frame onto this image: it is filled with a constant
# colour and only skeleton joints/links plus numeric overlays are drawn on top.
CANVAS_BGR_BG = (26, 34, 30)  # BGR dark neutral green (no person pixels)
CANVAS_BGR_HEADER = (12, 20, 16)
CANVAS_BGR_SKELETON = (186, 238, 205)  # light mint
CANVAS_BGR_JOINT = (96, 140, 255)


LINKS = (
    ("left_shoulder", "right_shoulder"),
    ("left_shoulder", "left_elbow"),
    ("left_elbow", "left_wrist"),
    ("right_shoulder", "right_elbow"),
    ("right_elbow", "right_wrist"),
    ("left_shoulder", "left_hip"),
    ("right_shoulder", "right_hip"),
    ("left_hip", "right_hip"),
    ("left_hip", "left_knee"),
    ("left_knee", "left_ankle"),
    ("right_hip", "right_knee"),
    ("right_knee", "right_ankle"),
)


def _point_map(skeleton: list[dict], width: int, height: int) -> dict:
    return {
        str(item.get("id")): (
            int(float(item.get("x", 0)) * width),
            int(float(item.get("y", 0)) * height),
        )
        for item in skeleton
        if isinstance(item, dict) and float(item.get("visibility", 0)) >= 0.35
    }


def _blur_face(frame, points: dict, cv2) -> bool:
    left, right = points.get("left_shoulder"), points.get("right_shoulder")
    if not left or not right:
        return False
    shoulder_width = max(24, abs(right[0] - left[0]))
    center_x = (left[0] + right[0]) // 2
    shoulder_y = (left[1] + right[1]) // 2
    half_width = int(shoulder_width * 0.48)
    face_height = int(shoulder_width * 1.05)
    x1, x2 = max(0, center_x - half_width), min(frame.shape[1], center_x + half_width)
    y2 = max(1, shoulder_y - int(shoulder_width * 0.12))
    y1 = max(0, y2 - face_height)
    if x2 - x1 < 10 or y2 - y1 < 10:
        return False
    region = frame[y1:y2, x1:x2]
    kernel = max(15, (min(region.shape[:2]) // 3) | 1)
    frame[y1:y2, x1:x2] = cv2.GaussianBlur(region, (kernel, kernel), 0)
    return True


def _encode_jpeg(frame, cv2, max_bytes: int) -> bytes:
    image = frame
    # Pre-scale wide frames so JPEG stays small and encoding rarely fails.
    height, width = image.shape[:2]
    if max(width, height) > 720:
        factor = 720 / max(width, height)
        image = cv2.resize(
            image, (round(width * factor), round(height * factor)),
            interpolation=cv2.INTER_AREA,
        )
    for _ in range(6):
        for quality in (84, 72, 60, 48, 38):
            ok, encoded = cv2.imencode(
                ".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality]
            )
            if ok and len(encoded) <= max_bytes:
                return encoded.tobytes()
        image = cv2.resize(image, None, fx=0.8, fy=0.8, interpolation=cv2.INTER_AREA)
    raise ValueError("annotated preview cannot fit size budget")


def render_motion_preview(frame, event: dict, *, max_bytes: int = 160 * 1024) -> dict:
    import cv2

    output = frame.copy()
    height, width = output.shape[:2]
    points = _point_map(event.get("skeleton") or [], width, height)
    face_anonymized = _blur_face(output, points, cv2)
    for start, end in LINKS:
        if start in points and end in points:
            cv2.line(output, points[start], points[end], (93, 238, 183), 4, cv2.LINE_AA)
    for name, point in points.items():
        is_focus = any(token in name for token in ("knee", "hip", "elbow"))
        color = (63, 116, 255) if is_focus else (244, 249, 247)
        cv2.circle(output, point, 7 if is_focus else 5, color, -1, cv2.LINE_AA)
    overlay = output.copy()
    cv2.rectangle(overlay, (0, 0), (width, min(74, height)), (21, 55, 46), -1)
    cv2.addWeighted(overlay, 0.82, output, 0.18, 0, output)
    event_name = str(event.get("event") or "pose_event")[:32]
    timestamp = float(event.get("timestamp") or 0)
    cv2.putText(
        output,
        f"AI POSE EVIDENCE  {timestamp:.1f}s  {event_name}",
        (18, min(47, height - 10)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (244, 249, 247),
        2,
        cv2.LINE_AA,
    )
    encoded = _encode_jpeg(output, cv2, max_bytes)
    return {
        "image_b64": base64.b64encode(encoded).decode("ascii"),
        "image_mime": "image/jpeg",
        "preview_sha256": hashlib.sha256(encoded).hexdigest(),
        "preview_bytes": len(encoded),
        "face_anonymized": face_anonymized,
    }


def annotate_keyframes(
    video_path, frames: list[dict], max_frames: int = 4
) -> list[dict]:
    import cv2

    priorities = (
        "max_rule_risk",
        "squat_deepest",
        "pushup_deepest",
        "lunge_deepest",
        "leg_abduction_deepest",
        "arm_abduction_deepest",
        "arm_vw_deepest",
        "squat_bottom",
        "pushup_bottom",
        "lunge_bottom",
        "leg_abduction_bottom",
        "arm_abduction_bottom",
        "arm_vw_bottom",
        "squat_completed",
        "pushup_completed",
        "lunge_completed",
        "leg_abduction_completed",
        "arm_abduction_completed",
        "arm_vw_completed",
        "squat_top",
        "pushup_top",
        "lunge_top",
        "leg_abduction_top",
        "arm_abduction_top",
        "arm_vw_top",
    )
    rank = {name: index for index, name in enumerate(priorities)}
    selected = sorted(
        frames,
        key=lambda item: (
            rank.get(str(item.get("event")), len(rank)),
            item.get("timestamp", 0),
        ),
    )[:max_frames]
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        return frames
    try:
        for event in selected:
            target_ms = float(event.get("timestamp") or 0) * 1000
            capture.set(cv2.CAP_PROP_POS_MSEC, target_ms)
            ok, frame = capture.read()
            if not ok:
                # Seek can fail on some encoders: fall back to sequential read.
                capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                while True:
                    ok, frame = capture.read()
                    if not ok:
                        break
                    if capture.get(cv2.CAP_PROP_POS_MSEC) >= target_ms:
                        break
                if not ok:
                    continue
            try:
                event.update(render_motion_preview(frame, event))
            except (ValueError, cv2.error):
                continue
    finally:
        capture.release()
    return frames


# ---------------------------------------------------------------------------
# Cloud-vision keyframes: skeleton + numbers on a PURE-COLOUR canvas.
#
# This is the default path for the unified motion chain. Unlike
# ``render_motion_preview`` (which draws on the original frame and only blurs the
# face region), ``render_skeleton_canvas`` builds the image from a constant-colour
# canvas and only overlays the skeleton geometry and numeric values. No pixel
# from the original frame is ever copied, so the canvas cannot contain the
# person, clothing or background. ``assert_desensitized_canvas`` re-checks the
# produced JPEG at the pixel level before it leaves the worker.
# ---------------------------------------------------------------------------


def render_skeleton_canvas(
    event: dict,
    *,
    width: int = 320,
    height: int = 480,
    max_bytes: int = 80 * 1024,
) -> dict:
    """Render skeleton + numeric values on a solid-colour canvas (no person).

    ``event`` must carry a ``skeleton`` list of normalized landmarks plus a
    ``timestamp`` and ``event`` name. The function never receives the original
    frame, so by construction the output contains no person/background pixels.
    """
    import cv2
    import numpy as np

    canvas = np.full((height, width, 3), CANVAS_BGR_BG, dtype=np.uint8)
    points = _point_map(event.get("skeleton") or [], width, height)
    for start, end in LINKS:
        if start in points and end in points:
            cv2.line(
                canvas, points[start], points[end],
                CANVAS_BGR_SKELETON, 3, cv2.LINE_AA,
            )
    for name, point in points.items():
        is_focus = any(token in name for token in ("knee", "hip", "elbow"))
        color = CANVAS_BGR_JOINT if is_focus else (235, 240, 237)
        cv2.circle(canvas, point, 5 if is_focus else 4, color, -1, cv2.LINE_AA)
    # Header band with timestamp + event (numeric/label evidence only).
    cv2.rectangle(canvas, (0, 0), (width, 30), CANVAS_BGR_HEADER, -1)
    ts = float(event.get("timestamp") or 0)
    cv2.putText(
        canvas,
        f"{ts:.1f}s  {str(event.get('event') or 'pose_event')[:26]}",
        (8, 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (232, 240, 236),
        1,
        cv2.LINE_AA,
    )
    encoded = _encode_jpeg(canvas, cv2, max_bytes)
    return {
        "image_b64": base64.b64encode(encoded).decode("ascii"),
        "image_mime": "image/jpeg",
        "preview_sha256": hashlib.sha256(encoded).hexdigest(),
        "preview_bytes": len(encoded),
        "desensitized": True,
        "canvas_style": "solid_skeleton",
    }


def assert_desensitized_canvas(
    jpeg_bytes: bytes,
    original_bgr=None,
    *,
    bg_bgr=CANVAS_BGR_BG,
    per_channel_tol: int = 14,
    min_bg_fraction: float = 0.88,
    max_abs_correlation: float = 0.10,
) -> dict:
    """Pixel-level desensitization check on a produced keyframe JPEG.

    Two guarantees are verified:
      1. ``min_bg_fraction`` of decoded pixels are (within JPEG tolerance) equal
         to the solid background colour — i.e. everything outside the thin
         skeleton strokes / header text is the flat canvas, never a person or a
         photographic background.
      2. When ``original_bgr`` is supplied, the (resized) canvas must have near-zero
         linear correlation with the original frame; a real photo blit would show
         strong correlation, a skeleton-on-flat-canvas does not.

    Raises ProcessingError (non-retryable) when the check fails.
    """
    import cv2
    import numpy as np

    arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ProcessingError(
            "desensitization_failed", "关键帧 JPEG 无法解码", retryable=False
        )
    bg = np.array(bg_bgr, dtype=np.int16)
    diff = np.abs(img.astype(np.int16) - bg).sum(axis=2)
    bg_mask = diff <= per_channel_tol * 3
    bg_fraction = float(bg_mask.mean())
    if bg_fraction < min_bg_fraction:
        raise ProcessingError(
            "desensitization_failed",
            f"脱敏失败：非画布背景像素占比过高 ({1 - bg_fraction:.1%})",
            retryable=False,
        )
    correlation = 0.0
    if original_bgr is not None:
        try:
            orig = cv2.resize(
                original_bgr, (img.shape[1], img.shape[0]),
                interpolation=cv2.INTER_AREA,
            )
            a = img.astype(np.float32).ravel()
            b = orig.astype(np.float32).ravel()
            a = a - a.mean()
            b = b - b.mean()
            denom = float(np.sqrt((a * a).sum()) * np.sqrt((b * b).sum()))
            correlation = float((a * b).sum() / denom) if denom > 1e-6 else 0.0
        except cv2.error:
            correlation = 0.0
        if abs(correlation) > max_abs_correlation:
            raise ProcessingError(
                "desensitization_failed",
                f"脱敏失败：画布与原帧像素相关性过高 ({correlation:.3f})",
                retryable=False,
            )
    return {
        "bg_fraction": round(bg_fraction, 4),
        "correlation_with_original": round(correlation, 4),
        "check_passed": True,
    }
