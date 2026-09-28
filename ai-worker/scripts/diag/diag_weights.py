"""Verify weights actually match the checkpoint after loading."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import torch

from healthmate_worker.models.slowfast_r50 import SlowFastKinetics400

CKPT = r"D:\HealthMateData\motion\pretrained\slowfast_r50_8xb8-8x8x1-steplr-256e_kinetics400-rgb_20220818-b62a501f.pth"
LABELS = str(ROOT / "healthmate_worker" / "models" / "kinetics400_labels.txt")

raw = torch.load(CKPT, map_location="cpu", weights_only=True)
sd = raw.get("state_dict", raw)
model = SlowFastKinetics400(CKPT, LABELS)

checks = [
    ("cls_head.weight", "cls_head.fc_cls.weight"),
    ("cls_head.bias", "cls_head.fc_cls.bias"),
    ("backbone.slow_path.conv1.conv.weight", "backbone.slow_path.conv1.conv.weight"),
    ("backbone.fast_path.conv1.conv.weight", "backbone.fast_path.conv1.conv.weight"),
    ("backbone.slow_path.layer4.0.conv2.conv.weight",
     "backbone.slow_path.layer4.0.conv2.conv.weight"),
]
for mine, theirs in checks:
    a = dict(model.named_parameters())[mine]
    b = sd[theirs]
    print(f"{mine:55s} shape={tuple(a.shape)} maxdiff={(a-b).abs().max().item():.3e}")
