# -*- coding: utf-8 -*-
"""One-shot verification: DeepSeek vision review channel answers on a skeleton image."""
import base64
import io
import sys

import cv2
import numpy as np

sys.path.insert(0, r"C:\HealthMate\ai-worker")
from healthmate_worker.processors.motion_review import _request_review  # noqa: E402


def make_squat_stick() -> bytes:
    """Draw a squat-like stick figure on dark canvas -> JPEG bytes."""
    canvas = np.zeros((480, 640, 3), dtype=np.uint8)
    pts = {
        "left_shoulder": (300, 120), "right_shoulder": (340, 120),
        "left_elbow": (260, 200), "right_elbow": (380, 200),
        "left_wrist": (240, 260), "right_wrist": (400, 260),
        "left_hip": (310, 250), "right_hip": (330, 250),
        "left_knee": (280, 360), "right_knee": (360, 360),
        "left_ankle": (290, 440), "right_ankle": (350, 440),
    }
    links = [
        ("left_shoulder", "right_shoulder"), ("left_shoulder", "left_elbow"),
        ("left_elbow", "left_wrist"), ("right_shoulder", "right_elbow"),
        ("right_elbow", "right_wrist"), ("left_shoulder", "left_hip"),
        ("right_shoulder", "right_hip"), ("left_hip", "right_hip"),
        ("left_hip", "left_knee"), ("left_knee", "left_ankle"),
        ("right_hip", "right_knee"), ("right_knee", "right_ankle"),
    ]
    for a, b in links:
        cv2.line(canvas, pts[a], pts[b], (93, 238, 183), 4, cv2.LINE_AA)
    for name, pt in pts.items():
        focus = any(t in name for t in ("knee", "hip", "elbow"))
        cv2.circle(canvas, pt, 8 if focus else 5, (63, 116, 255) if focus else (244, 249, 247), -1, cv2.LINE_AA)
    ok, buf = cv2.imencode(".jpg", canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    if not ok:
        raise SystemExit("jpeg encode failed")
    return buf.tobytes()


def main():
    jpeg = make_squat_stick()
    frame_b64 = base64.b64encode(jpeg).decode("ascii")
    summary = (
        "squat: 膝角 中位 115.0° 范围 62.0°；躯干倾角 中位 12.0°\n"
        "pushup: 肘角 中位 175.0° 范围 4.0°"
    )
    suffix = "\n本地引擎判定为 squat，请确认是否合理；若明显不符，给出你认为最可能的动作。"
    review = _request_review([frame_b64], summary, suffix)
    print("REVIEW_OK", review)


if __name__ == "__main__":
    main()
