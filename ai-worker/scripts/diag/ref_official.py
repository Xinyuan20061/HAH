"""Build the OFFICIAL mmaction2 SlowFast (final 8x8x1 config) and run the demo.

Cross-check: feed my preprocessed clip to the official model. If the official
model recognizes arm wrestling, my bug is the resample/speed parameters.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import torch  # noqa: F401  (registers torch ops)
import mmaction  # noqa: F401  (registers models)
from mmaction.registry import MODELS
from mmengine.registry import init_default_scope
from mmengine.runner import load_checkpoint

init_default_scope("mmaction")

from healthmate_worker.models.kinetics_clip import build_clip

CKPT = r"D:\HealthMateData\motion\pretrained\slowfast_r50_8xb8-8x8x1-steplr-256e_kinetics400-rgb_20220818-b62a501f.pth"
LABELS = str(ROOT / "healthmate_worker" / "models" / "kinetics400_labels.txt")
VIDEO = sys.argv[1]

cfg = dict(
    type="Recognizer3D",
    backbone=dict(
        type="ResNet3dSlowFast",
        resample_rate=4,
        speed_ratio=4,
        channel_ratio=8,
        slow_pathway=dict(
            type="resnet3d", depth=50, lateral=True,
            conv1_kernel=(1, 7, 7), conv1_stride_t=1, pool1_stride_t=1,
            inflate=(0, 0, 1, 1), fusion_kernel=7, lateral_norm=True),
        fast_pathway=dict(
            type="resnet3d", depth=50, lateral=False, base_channels=8,
            conv1_kernel=(5, 7, 7), conv1_stride_t=1, pool1_stride_t=1),
    ),
    cls_head=dict(
        type="SlowFastHead", in_channels=2304, num_classes=400,
        spatial_type="avg", dropout_ratio=0.5, average_clips="prob"),
    data_preprocessor=dict(
        type="ActionDataPreprocessor",
        mean=[123.675, 116.28, 103.53],
        std=[58.395, 57.12, 57.375],
        format_shape="NCTHW"),
)

model = MODELS.build(cfg)
load_checkpoint(model, CKPT, map_location="cpu")
model.eval()

clip, frames = build_clip(VIDEO)
print("clip", tuple(clip.shape), "frames", frames)
with torch.no_grad():
    feats = model.backbone(clip)
    logits = model.cls_head(feats)

scores = logits[0].softmax(-1)
labels = [line.strip() for line in open(LABELS, encoding="utf-8")]
top = scores.topk(6)
print("OFFICIAL model output:")
for p, i in zip(top.values.tolist(), top.indices.tolist()):
    print(f"  {i:3d} {labels[i]:25s} {p:.4f}")
