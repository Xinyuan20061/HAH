"""Quick experiment: short window + brightness enhancement on a dark clip."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2
import numpy as np
import torch

from healthmate_worker.models.slowfast_r50 import (
    KINETICS400_MEAN,
    KINETICS400_STD,
    SlowFastKinetics400,
)

CKPT = r"D:\HealthMateData\motion\pretrained\slowfast_r50_8xb8-8x8x1-steplr-256e_kinetics400-rgb_20220818-b62a501f.pth"
LABELS = str(ROOT / "healthmate_worker" / "models" / "kinetics400_labels.txt")
VIDEO = r"D:\HealthMateData\motion\raw\rehab24-6\Ex1\PM_000-Camera17-30fps.mp4"


def read_frames(path):
    cap = cv2.VideoCapture(path)
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    return frames


def prep(frame, enhance):
    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    if enhance:
        lab = cv2.cvtColor(frame, cv2.COLOR_RGB2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        l = clahe.apply(l)
        frame = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2RGB)
    h, w = frame.shape[:2]
    scale = 256 / min(h, w)
    nw = max(224, int(round(w * scale)))
    nh = max(224, int(round(h * scale)))
    frame = cv2.resize(frame, (nw, nh))
    top, left = (nh - 224) // 2, (nw - 224) // 2
    frame = frame[top : top + 224, left : left + 224].astype(np.float32)
    mean = np.asarray(KINETICS400_MEAN, dtype=np.float32)
    std = np.asarray(KINETICS400_STD, dtype=np.float32)
    return (frame - mean) / std


def main():
    model = SlowFastKinetics400(CKPT, LABELS)
    frames = read_frames(VIDEO)
    n = len(frames)
    center = int(n * 0.6)
    lo, hi = max(0, center - 150), min(n, center + 150)
    idx = np.linspace(lo, hi - 1, 32).round().astype(int)
    for enhance in (False, True):
        clip = np.stack([prep(frames[i], enhance) for i in idx])
        tensor = torch.from_numpy(clip).permute(3, 0, 1, 2).unsqueeze(0)
        result = model.predict(tensor, topk=4)
        tag = "enhanced" if enhance else "plain   "
        tops = ", ".join(
            f"{c['label']} {c['probability']:.2f}" for c in result["candidates"]
        )
        print(tag, tops)


if __name__ == "__main__":
    main()
