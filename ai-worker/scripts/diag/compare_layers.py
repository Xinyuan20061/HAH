"""Layer-by-layer numerical diff between OFFICIAL mmaction SlowFast and mine.

Both load the same checkpoint and receive the identical clip. We replay the
forward manually, comparing tensors after each module. The FIRST module with a
large diff is the bug.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import torch
import torch.nn.functional as F
import mmaction  # noqa: F401
from mmaction.registry import MODELS
from mmengine.registry import init_default_scope
from mmengine.runner import load_checkpoint

init_default_scope("mmaction")

from healthmate_worker.models.kinetics_clip import build_clip
from healthmate_worker.models.slowfast_r50 import ResNet3dSlowFast

CKPT = r"D:\HealthMateData\motion\pretrained\slowfast_r50_8xb8-8x8x1-steplr-256e_kinetics400-rgb_20220818-b62a501f.pth"
VIDEO = sys.argv[1]

cfg = dict(
    type="Recognizer3D",
    backbone=dict(
        type="ResNet3dSlowFast", resample_rate=4, speed_ratio=4, channel_ratio=8,
        slow_pathway=dict(
            type="resnet3d", depth=50, lateral=True,
            conv1_kernel=(1, 7, 7), conv1_stride_t=1, pool1_stride_t=1,
            inflate=(0, 0, 1, 1), fusion_kernel=7, lateral_norm=True),
        fast_pathway=dict(
            type="resnet3d", depth=50, lateral=False, base_channels=8,
            conv1_kernel=(5, 7, 7), conv1_stride_t=1, pool1_stride_t=1)),
    cls_head=dict(type="SlowFastHead", in_channels=2304, num_classes=400),
    data_preprocessor=None)
recognizer = MODELS.build(cfg)
off = recognizer.backbone
ck0 = torch.load(CKPT, map_location="cpu", weights_only=True)
sd0 = ck0.get("state_dict", ck0)
off_sd = {k[len("backbone."):]: v for k, v in sd0.items() if k.startswith("backbone.")}
om, ou = off.load_state_dict(off_sd, strict=False)
assert not om and not ou, (om, ou)
off.eval()

mine = ResNet3dSlowFast()
ck = torch.load(CKPT, map_location="cpu", weights_only=True)
sd = ck.get("state_dict", ck)
back = {k[len("backbone."):]: v for k, v in sd.items() if k.startswith("backbone.")}
m, u = mine.load_state_dict(back, strict=False)
assert not m and not u, (m, u)
mine.eval()

clip, _ = build_clip(VIDEO)


def diff(name, a, b):
    d = (a - b).abs().max().item()
    flag = "  <<< DIFF" if d > 1e-3 else ""
    print(f"{name:32s} maxdiff={d:.6f} shapes {tuple(a.shape)}{flag}")
    return d


with torch.no_grad():
    # stem interpolation
    os_ = F.interpolate(clip, mode="nearest", scale_factor=(0.25, 1, 1))
    ms_ = F.interpolate(clip, mode="nearest", scale_factor=(1.0 / mine.resample_rate, 1, 1))
    diff("interpolate slow", os_, ms_)

    of_ = F.interpolate(clip, mode="nearest", scale_factor=(1.0, 1, 1))
    mf_ = F.interpolate(clip, mode="nearest",
                        scale_factor=(1.0 / (mine.resample_rate // mine.speed_ratio), 1, 1))
    diff("interpolate fast", of_, mf_)

    # conv1 + maxpool
    os_ = off.slow_path.conv1(os_)
    ms_ = mine.slow_path.conv1(ms_)
    diff("slow conv1", os_, ms_)

    os_ = off.slow_path.maxpool(os_)
    ms_ = mine.slow_path.maxpool(ms_)
    diff("slow maxpool", os_, ms_)

    of_ = off.fast_path.conv1(of_)
    mf_ = mine.fast_path.conv1(mf_)
    diff("fast conv1", of_, mf_)

    of_ = off.fast_path.maxpool(of_)
    mf_ = mine.fast_path.maxpool(mf_)
    diff("fast maxpool", of_, mf_)

    # stem lateral
    olat = off.slow_path.conv1_lateral(of_)
    mlat = mine.slow_path.conv1_lateral(mf_)
    diff("conv1_lateral", olat, mlat)

    os_ = torch.cat((os_, olat), dim=1)
    ms_ = torch.cat((ms_, mlat), dim=1)
    diff("after stem cat", os_, ms_)

    for i in range(4):
        ln = f"layer{i+1}"
        os_ = getattr(off.slow_path, ln)(os_)
        ms_ = getattr(mine.slow_path, ln)(ms_)
        diff(f"slow {ln}", os_, ms_)
        of_ = getattr(off.fast_path, ln)(of_)
        mf_ = getattr(mine.fast_path, ln)(mf_)
        diff(f"fast {ln}", of_, mf_)
        if i != 3:
            latn = f"layer{i+1}_lateral"
            olat = getattr(off.slow_path, latn)(of_)
            mlat = getattr(mine.slow_path, latn)(mf_)
            diff(latn, olat, mlat)
            os_ = torch.cat((os_, olat), dim=1)
            ms_ = torch.cat((ms_, mlat), dim=1)
