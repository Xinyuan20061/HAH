"""Privacy-aware, size-bounded visual evidence for motion events."""

from __future__ import annotations

import base64
import hashlib


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
