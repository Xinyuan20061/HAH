"""Full state-dict comparison: every tensor incl. BN running stats."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import torch

from healthmate_worker.models.slowfast_r50 import SlowFastKinetics400

CKPT = r"D:\HealthMateData\motion\pretrained\slowfast_r50_8xb8-8x8x1-steplr-256e_kinetics400-rgb_20220818-b62a501f.pth"
LABELS = str(ROOT / "healthmate_worker" / "models" / "kinetics400_labels.txt")

raw = torch.load(CKPT, map_location="cpu", weights_only=True)
sd = dict(raw.get("state_dict", raw))
model = SlowFastKinetics400(CKPT, LABELS)
mine = dict(model.named_parameters())
mine_buffers = dict(model.named_buffers())

# Remap checkpoint keys to model keys
remapped = {}
for k, v in sd.items():
    if k.startswith("backbone."):
        remapped[k] = v
    elif k.startswith("cls_head.fc_cls."):
        remapped["cls_head." + k[len("cls_head.fc_cls."):]] = v

print("checkpoint tensors:", len(remapped), "model params+buffers:",
      len(mine) + len(mine_buffers))

worst = 0.0
worst_key = None
for k, v in remapped.items():
    target = mine.get(k, mine_buffers.get(k))
    if target is None:
        print("MISSING in model:", k)
        continue
    if target.shape != v.shape:
        print("SHAPE MISMATCH:", k, tuple(target.shape), tuple(v.shape))
        continue
    d = (target.float() - v.float()).abs().max().item()
    if d > worst:
        worst, worst_key = d, k

print(f"worst diff = {worst:.3e} at {worst_key}")

# Confirm BN training flags
bns = [m for m in model.modules() if isinstance(m, torch.nn.BatchNorm3d)]
print("num BN3d:", len(bns), "any training=True:", any(m.training for m in bns))
