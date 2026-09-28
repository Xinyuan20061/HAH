"""Trace backbone tensor shapes at every stage (random input)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import torch

from healthmate_worker.models.slowfast_r50 import ResNet3dSlowFast

net = ResNet3dSlowFast().eval()
x = torch.randn(1, 3, 32, 224, 224)

slow = torch.nn.functional.interpolate(x, scale_factor=(1 / 8, 1, 1), mode="nearest")
fast = torch.nn.functional.interpolate(x, scale_factor=(1 / 1, 1, 1), mode="nearest")
print("after temporal split: slow", tuple(slow.shape), "fast", tuple(fast.shape))

slow = net.slow_path.conv1(slow)
fast = net.fast_path.conv1(fast)
print("after conv1:        slow", tuple(slow.shape), "fast", tuple(fast.shape))
slow = net.slow_path.maxpool(slow)
fast = net.fast_path.maxpool(fast)
print("after maxpool:      slow", tuple(slow.shape), "fast", tuple(fast.shape))
slow = torch.cat((slow, net.slow_path.conv1_lateral(fast)), dim=1)
print("after lateral1 cat: slow", tuple(slow.shape))

for i, layer_name in enumerate(net.slow_path.res_layers):
    slow = getattr(net.slow_path, layer_name)(slow)
    fast = getattr(net.fast_path, layer_name)(fast)
    print(f"after {layer_name}:       slow", tuple(slow.shape), "fast", tuple(fast.shape))
    if i != len(net.slow_path.res_layers) - 1:
        lat = net.slow_path.lateral_connections[i]
        lf = getattr(net.slow_path, lat)(fast)
        print(f"   {lat} ->", tuple(lf.shape))
        slow = torch.cat((slow, lf), dim=1)
        print("   cat slow", tuple(slow.shape))
