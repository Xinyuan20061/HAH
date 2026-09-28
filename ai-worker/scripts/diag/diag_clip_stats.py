"""Check clip numeric stats and the exact sampled frames."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2
import numpy as np

from healthmate_worker.models.kinetics_clip import (
    WINDOW_FRAMES,
    _sample_indices,
    build_clip,
)

VIDEO = sys.argv[1]
clip, frames = build_clip(VIDEO)
print("frames", frames, "clip", tuple(clip.shape))
arr = clip[0].numpy()  # 3,T,H,W
for c, name in enumerate("RGB"):
    print(f"channel {name}: mean={arr[c].mean():.3f} std={arr[c].std():.3f} "
          f"min={arr[c].min():.2f} max={arr[c].max():.2f}")

idx = _sample_indices(frames)
print("window frames", WINDOW_FRAMES, "first idx", idx[0], "last idx", idx[-1])

cap = cv2.VideoCapture(VIDEO)
for target, tag in ((idx[0], "win_start"), (idx[len(idx)//2], "win_mid"), (idx[-1], "win_end")):
    cap.set(cv2.CAP_PROP_POS_FRAMES, target)
    ok, f = cap.read()
    if ok:
        out = str(Path(VIDEO).with_suffix(f".{tag}.jpg"))
        cv2.imwrite(out, f)
        print("saved", out)
cap.release()
