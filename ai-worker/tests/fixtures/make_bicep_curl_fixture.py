# -*- coding: utf-8 -*-
"""Generate a SYNTHETIC "dumbbell bicep curl" offline sample video.

同类离线样例验证，非原始视频：this is a procedurally drawn stick-figure
animation (an upper arm held still, a forearm flexing/extending with a small
"dumbbell" rectangle in the hand). It exists only to exercise the V2 evidence
pipeline end-to-end offline (decode -> generic evidence pool -> preview ->
receipt) without consuming a real user video. It is NOT a claim that the
recognizer reproduces the original screenshot video.

Regenerate (optional):
    ai-worker\\.venv\\Scripts\\python.exe tests\\fixtures\\make_bicep_curl_fixture.py
"""
from pathlib import Path

import cv2
import numpy as np

OUT = Path(__file__).with_name("bicep_curl_offline_sample.mp4")
W, H = 960, 540
FPS = 25
SECONDS = 10
REPS = 4  # four curl cycles over 10s


def draw_frame(t: float) -> np.ndarray:
    img = np.full((H, W, 3), (38, 46, 40), dtype=np.uint8)
    # subtle gym-wall texture (not a flat canvas: keeps the frame "real photo-ish")
    noise = np.random.default_rng(1).integers(0, 18, (H, W, 3), dtype=np.uint8)
    img = cv2.add(img, noise)
    # floor line
    cv2.line(img, (0, 460), (W, 460), (70, 80, 72), 3)

    shoulder = (360, 200)
    elbow = (360, 330)  # upper arm hangs vertical
    # elbow angle: forearm curls up (0 = down, ~140deg flexed)
    phase = (t * REPS / SECONDS) % 1.0
    angle = np.radians(20 + 120 * (0.5 - 0.5 * np.cos(phase * 2 * np.pi)))
    forearm_len = 95
    wrist = (
        int(elbow[0] + forearm_len * np.sin(angle)),
        int(elbow[1] - forearm_len * np.cos(angle)),
    )
    # head + torso
    cv2.circle(img, (360, 150), 26, (190, 200, 205), -1)
    cv2.line(img, shoulder, (360, 360), (190, 200, 205), 6)
    # upper arm
    cv2.line(img, shoulder, elbow, (190, 200, 205), 6)
    # forearm (curls)
    cv2.line(img, elbow, wrist, (190, 200, 205), 6)
    # dumbbell in hand
    cv2.rectangle(img, (wrist[0] - 16, wrist[1] - 8), (wrist[0] + 16, wrist[1] + 8), (60, 60, 220), -1)
    return img


def main() -> Path:
    writer = cv2.VideoWriter(str(OUT), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    assert writer.isOpened(), "fixture writer failed to open"
    total = FPS * SECONDS
    for i in range(total):
        writer.write(draw_frame(i / FPS))
    writer.release()
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, {SECONDS}s @ {FPS}fps)")
    return OUT


if __name__ == "__main__":
    main()
