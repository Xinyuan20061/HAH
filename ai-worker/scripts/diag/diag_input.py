"""Diagnose: dump the actual preprocessed frames the model receives."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2
import numpy as np

from healthmate_worker.models.kinetics_clip import build_clip
from healthmate_worker.models.slowfast_r50 import KINETICS400_STD

VIDEO = sys.argv[1]
OUTDIR = Path(sys.argv[2])
OUTDIR.mkdir(parents=True, exist_ok=True)

slow, fast, frames = build_clip(VIDEO)
print("slow", tuple(slow.shape), "fast", tuple(fast.shape), "frames", frames)

mean = np.array([123.675, 116.28, 103.53], dtype=np.float32)
std = np.array(KINETICS400_STD, dtype=np.float32)


def denorm(t):
    # t: 1,3,T,H,W -> list of BGR frames
    arr = t[0].permute(1, 2, 3, 0).cpu().numpy()
    arr = (arr * std + mean).clip(0, 255).astype(np.uint8)
    return [cv2.cvtColor(f, cv2.COLOR_RGB2BGR) for f in arr]


fast_frames = denorm(fast)
slow_frames = denorm(slow)
for i in (0, 8, 16, 24, 31):
    cv2.imwrite(str(OUTDIR / f"fast_{i:02d}.jpg"), fast_frames[i])
for i in range(4):
    cv2.imwrite(str(OUTDIR / f"slow_{i}.jpg"), slow_frames[i])
print("saved frames to", OUTDIR)
# Check slow[j] should match fast[8j] (slow interval 16 = 8 * fast interval 2)
for j in range(4):
    diff = np.abs(
        slow_frames[j].astype(int) - fast_frames[8 * j].astype(int)
    ).mean()
    print(f"slow[{j}] vs fast[{8*j}] mean pixel diff = {diff:.2f}")
