"""Inspect backbone pooled features and logits distribution."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import torch

from healthmate_worker.models.kinetics_clip import build_clip
from healthmate_worker.models.slowfast_r50 import SlowFastKinetics400

CKPT = r"D:\HealthMateData\motion\pretrained\slowfast_r50_8xb8-8x8x1-steplr-256e_kinetics400-rgb_20220818-b62a501f.pth"
LABELS = str(ROOT / "healthmate_worker" / "models" / "kinetics400_labels.txt")
VIDEO = sys.argv[1]

model = SlowFastKinetics400(CKPT, LABELS)
clip, frames = build_clip(VIDEO)

with torch.no_grad():
    feats = model.features(clip)
    logits = model.cls_head(feats)
    probs = torch.softmax(logits, dim=1)[0]

print("pooled feature: shape", tuple(feats.shape),
      f"mean={feats.mean():.3f} std={feats.std():.3f} min={feats.min():.2f} max={feats.max():.2f}")
print("logits: mean", round(logits.mean().item(), 3),
      "std", round(logits.std().item(), 3),
      "min", round(logits.min().item(), 2), "max", round(logits.max().item(), 2))
print("arm wrestling (6) prob =", round(probs[6].item(), 4),
      "logit =", round(logits[0, 6].item(), 3))
top = probs.topk(8)
for p, i in zip(top.values.tolist(), top.indices.tolist()):
    print(f"  {i:3d} {model.labels[i]:25s} {p:.4f}")
