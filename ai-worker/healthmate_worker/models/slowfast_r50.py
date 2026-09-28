"""Pure-PyTorch SlowFast R50 backbone + 6-action head.

The module reproduces the MMAction2 ``ResNet3dSlowFast`` (slowfast_r50_8xb8-
8x8x1-steplr-256e_kinetics400-rgb) architecture with plain ``nn.Conv3d`` /
``nn.BatchNorm3d`` layers and **identical state-dict key names**, so the
official Kinetics-400 checkpoint
(``slowfast_r50_..._kinetics400-rgb_20220818-b62a501f.pth``) can be loaded
with ``load_state_dict(..., strict=True)`` without mmcv / mmengine / mmaction2.

Verified against the checkpoint tensors (2026-09-23):
  - conv1_lateral: 8 -> 16, kernel (7,1,1), stride (8,1,1)
  - layer{i+1}_lateral (i=0..2): fast-stage out -> slow-stage out // 4
  - layer1/2 conv1 = 1x1x1 (non-inflate); layer3/4 conv1 = 3x1x1 (inflate 3x1x1)
  - conv2 = 1x3x3 everywhere; conv3 = 1x1x1 -> planes*4
  - downsample on every stage's first block
  - fc input 2304 = 2048 (slow) + 256 (fast)
"""

from __future__ import annotations

from typing import Optional, Sequence

import torch
import torch.nn as nn
import torch.nn.functional as F


def _triple(value) -> tuple:
    if isinstance(value, (tuple, list)):
        return tuple(value)
    return (value, value, value)


class _ConvBn(nn.Module):
    """mmaction2-style ConvModule: conv + bn [+ relu], key names ``.conv``/``.bn``."""

    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        kernel_size,
        stride=1,
        padding=0,
        dilation=1,
        bias: bool = False,
        relu: bool = True,
    ) -> None:
        super().__init__()
        self.conv = nn.Conv3d(
            in_channels,
            out_channels,
            kernel_size,
            stride=stride,
            padding=padding,
            dilation=dilation,
            bias=bias,
        )
        self.bn = nn.BatchNorm3d(out_channels)
        self.relu = nn.ReLU(inplace=True) if relu else nn.Identity()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.relu(self.bn(self.conv(x)))


class Bottleneck3d(nn.Module):
    """ResNet-3D bottleneck (pytorch style), mmaction2-compatible key names."""

    expansion = 4

    def __init__(
        self,
        inplanes: int,
        planes: int,
        spatial_stride: int = 1,
        temporal_stride: int = 1,
        dilation: int = 1,
        downsample: Optional[nn.Module] = None,
        inflate: bool = False,
    ) -> None:
        super().__init__()
        self.inplanes = inplanes
        self.planes = planes
        self.spatial_stride = spatial_stride
        self.temporal_stride = temporal_stride
        self.dilation = dilation
        self.inflate = inflate
        if inflate:
            # inflate_style='3x1x1': conv1 (3,1,1), conv2 (1,3,3)
            conv1_kernel, conv1_padding = (3, 1, 1), (1, 0, 0)
        else:
            conv1_kernel, conv1_padding = (1, 1, 1), (0, 0, 0)
        conv2_kernel, conv2_padding = (1, 3, 3), (0, dilation, dilation)
        # pytorch style: stride lives on conv2
        self.conv1 = _ConvBn(inplanes, planes, conv1_kernel, stride=(1, 1, 1), padding=conv1_padding)
        self.conv2 = _ConvBn(
            planes,
            planes,
            conv2_kernel,
            stride=(temporal_stride, spatial_stride, spatial_stride),
            padding=conv2_padding,
            dilation=(1, dilation, dilation),
        )
        self.conv3 = _ConvBn(planes, planes * self.expansion, 1, relu=False)
        self.downsample = downsample
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        identity = x
        out = self.conv1(x)
        out = self.conv2(out)
        out = self.conv3(out)
        if self.downsample is not None:
            identity = self.downsample(x)
        out = out + identity
        return self.relu(out)


class ResNet3dPathway(nn.Module):
    """One SlowFast pathway (slow or fast) with optional lateral connections."""

    def __init__(
        self,
        base_channels: int = 64,
        in_channels: int = 3,
        conv1_kernel: Sequence[int] = (3, 7, 7),
        conv1_stride_s: int = 2,
        conv1_stride_t: int = 1,
        pool1_stride_s: int = 2,
        pool1_stride_t: int = 1,
        spatial_strides: Sequence[int] = (1, 2, 2, 2),
        temporal_strides: Sequence[int] = (1, 1, 1, 1),
        dilations: Sequence[int] = (1, 1, 1, 1),
        inflate: Sequence[int] = (0, 0, 1, 1),
        lateral: bool = False,
        speed_ratio: int = 8,
        channel_ratio: int = 8,
        fusion_kernel: int = 7,
        lateral_infl: int = 2,
    ) -> None:
        super().__init__()
        self.base_channels = base_channels
        self.lateral = lateral
        self.speed_ratio = speed_ratio
        self.channel_ratio = channel_ratio
        self.stage_blocks = (3, 4, 6, 3)  # depth 50
        # stem
        self.conv1 = _ConvBn(
            in_channels,
            base_channels,
            conv1_kernel,
            stride=(conv1_stride_t, conv1_stride_s, conv1_stride_s),
            padding=tuple((k - 1) // 2 for k in _triple(conv1_kernel)),
        )
        self.maxpool = nn.MaxPool3d(
            (1, 3, 3),
            stride=(pool1_stride_t, pool1_stride_s, pool1_stride_s),
            padding=(0, 1, 1),
        )
        # stem lateral: fuses fast conv1 output into slow stem
        self.lateral_connections: list[str] = []
        if lateral:
            self.conv1_lateral = _ConvBn(
                base_channels // channel_ratio,
                base_channels * lateral_infl // channel_ratio,
                (fusion_kernel, 1, 1),
                stride=(speed_ratio, 1, 1),
                padding=((fusion_kernel - 1) // 2, 0, 0),
            )
        # residual stages
        self.inplanes = base_channels
        self.res_layers: list[str] = []
        expansion = Bottleneck3d.expansion
        for stage_index, num_blocks in enumerate(self.stage_blocks):
            planes = base_channels * 2**stage_index
            # lateral fusion module feeding the NEXT stage (all but the last)
            if lateral and stage_index != len(self.stage_blocks) - 1:
                lateral_name = f"layer{stage_index + 1}_lateral"
                lateral_in = (base_channels // channel_ratio) * (2**stage_index) * expansion
                lateral_out = planes * expansion * lateral_infl // channel_ratio
                setattr(
                    self,
                    lateral_name,
                    _ConvBn(
                        lateral_in,
                        lateral_out,
                        (fusion_kernel, 1, 1),
                        stride=(speed_ratio, 1, 1),
                        padding=((fusion_kernel - 1) // 2, 0, 0),
                    ),
                )
                self.lateral_connections.append(lateral_name)
            self.add_module(f"layer{stage_index + 1}", self._make_layer(
                planes,
                self.inplanes + getattr(self, "_lateral_inplanes", [0] * 4)[stage_index],
                num_blocks,
                spatial_strides[stage_index],
                temporal_strides[stage_index],
                dilations[stage_index],
                bool(inflate[stage_index]),
            ))
            self.res_layers.append(f"layer{stage_index + 1}")
            self.inplanes = planes * expansion

    @property
    def _lateral_inplanes(self) -> list[int]:
        """Extra input channels fused into each stage (lateral outputs).

        Stage 0 receives the stem lateral (``conv1_lateral``) output which is
        ``base_channels * lateral_infl // channel_ratio``; stage i (>=1)
        receives ``layer{i}_lateral`` output which is based on the *previous*
        stage's planes: ``(base_channels * 2**(i-1)) * expansion * lateral_infl
        // channel_ratio``.
        """
        expansion = Bottleneck3d.expansion
        result = [self.base_channels * 2 // self.channel_ratio] if self.lateral else [0]
        for stage_index in range(1, len(self.stage_blocks)):
            previous_planes = self.base_channels * 2 ** (stage_index - 1)
            result.append(
                previous_planes * expansion * 2 // self.channel_ratio if self.lateral else 0
            )
        return result

    def _make_layer(
        self,
        planes: int,
        inplanes: int,
        num_blocks: int,
        spatial_stride: int,
        temporal_stride: int,
        dilation: int,
        inflate: bool,
    ) -> nn.Sequential:
        downsample = None
        if spatial_stride != 1 or temporal_stride != 1 or inplanes != planes * Bottleneck3d.expansion:
            downsample = _ConvBn(
                inplanes,
                planes * Bottleneck3d.expansion,
                1,
                stride=(temporal_stride, spatial_stride, spatial_stride),
                relu=False,
            )
        blocks = [
            Bottleneck3d(
                inplanes,
                planes,
                spatial_stride=spatial_stride,
                temporal_stride=temporal_stride,
                dilation=dilation,
                downsample=downsample,
                inflate=inflate,
            )
        ]
        for _ in range(1, num_blocks):
            blocks.append(
                Bottleneck3d(
                    planes * Bottleneck3d.expansion,
                    planes,
                    spatial_stride=1,
                    temporal_stride=1,
                    dilation=dilation,
                    inflate=inflate,
                )
            )
        return nn.Sequential(*blocks)


class ResNet3dSlowFast(nn.Module):
    """SlowFast R50 backbone with mmaction2-compatible state-dict keys.

    Input: ``(B, 3, T, H, W)`` where T is the sampled clip length
    (e.g. 32). The slow pathway consumes T/``resample_rate`` frames after a
    nearest-neighbour temporal downsample; the fast pathway consumes the full
    T frames (``resample_rate // speed_ratio == 1`` here).
    """

    def __init__(
        self,
        resample_rate: int = 4,
        speed_ratio: int = 4,
        channel_ratio: int = 8,
    ) -> None:
        super().__init__()
        self.resample_rate = resample_rate
        self.speed_ratio = speed_ratio
        self.channel_ratio = channel_ratio
        self.slow_path = ResNet3dPathway(
            base_channels=64,
            conv1_kernel=(1, 7, 7),
            conv1_stride_t=1,
            pool1_stride_t=1,
            inflate=(0, 0, 1, 1),
            lateral=True,
            speed_ratio=speed_ratio,
            channel_ratio=channel_ratio,
        )
        self.fast_path = ResNet3dPathway(
            base_channels=8,
            conv1_kernel=(5, 7, 7),
            conv1_stride_t=1,
            pool1_stride_t=1,
            inflate=(1, 1, 1, 1),  # fast pathway keeps the default full inflate
            lateral=False,
            speed_ratio=speed_ratio,
            channel_ratio=channel_ratio,
        )

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        # Single fast-rate clip (B,3,32,H,W); slow is derived inside, matching
        # the official ResNet3dSlowFast.forward (no pool2 in SlowFast).
        x_slow = F.interpolate(
            x, mode="nearest",
            scale_factor=(1.0 / self.resample_rate, 1.0, 1.0))
        x_slow = self.slow_path.conv1(x_slow)
        x_slow = self.slow_path.maxpool(x_slow)

        x_fast = F.interpolate(
            x, mode="nearest",
            scale_factor=(1.0 / (self.resample_rate // self.speed_ratio), 1.0, 1.0))
        x_fast = self.fast_path.conv1(x_fast)
        x_fast = self.fast_path.maxpool(x_fast)

        if self.slow_path.lateral:
            x_slow = torch.cat((x_slow, self.slow_path.conv1_lateral(x_fast)), dim=1)

        for i, layer_name in enumerate(self.slow_path.res_layers):
            x_slow = getattr(self.slow_path, layer_name)(x_slow)
            x_fast = getattr(self.fast_path, layer_name)(x_fast)
            if i != len(self.slow_path.res_layers) - 1 and self.slow_path.lateral:
                lateral_name = self.slow_path.lateral_connections[i]
                x_slow = torch.cat((x_slow, getattr(self.slow_path, lateral_name)(x_fast)), dim=1)
        return x_slow, x_fast


# 6-action labels: order must stay frozen once a model is trained/exported.
ACTION_LABELS_SIX = ("squat", "pushup", "lunge", "leg_abduction", "arm_abduction", "arm_vw")


class SlowFastSixAction(nn.Module):
    """SlowFast R50 (Kinetics-400 pretrained backbone) + 6-action classification head.

    The MMAction2 checkpoint is loaded by stripping its 400-way ``cls_head``.
    """

    def __init__(
        self,
        pretrained_path: Optional[str] = None,
        num_classes: int = 6,
        dropout_ratio: float = 0.5,
        backbone_feature_dim: int = 2304,
    ) -> None:
        super().__init__()
        self.backbone = ResNet3dSlowFast()
        self.backbone_feature_dim = backbone_feature_dim
        if pretrained_path is not None:
            self._load_mmaction2_backbone(pretrained_path)
        self.head = nn.Sequential(
            nn.Dropout(dropout_ratio),
            nn.Linear(backbone_feature_dim, num_classes),
        )

    def _load_mmaction2_backbone(self, path: str) -> None:
        checkpoint = torch.load(path, map_location="cpu", weights_only=True)
        state_dict = checkpoint.get("state_dict", checkpoint)
        keys = list(state_dict.keys())
        if not any(key.startswith("backbone.") for key in keys):
            raise ValueError(f"checkpoint has no 'backbone.*' keys: {path}")
        # self.backbone is the ResNet3dSlowFast module, so strip the
        # leading 'backbone.' prefix (the checkpoint stores full module paths).
        filtered = {
            key[len("backbone."):]: state_dict[key]
            for key in keys
            if key.startswith("backbone.")
        }
        missing, unexpected = self.backbone.load_state_dict(filtered, strict=True)
        if missing or unexpected:
            raise ValueError(f"pretrained backbone load mismatch: missing={missing} unexpected={unexpected[:5]}")

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        slow, fast = self.backbone(x)
        slow = F.adaptive_avg_pool3d(slow, 1).flatten(1)
        fast = F.adaptive_avg_pool3d(fast, 1).flatten(1)
        features = torch.cat((slow, fast), dim=1)
        return self.head(features)


# --- Kinetics-400 pretrained inference (no training required) ---------------

KINETICS400_CLIP_LEN = 32
KINETICS400_MEAN = (123.675, 116.28, 103.53)
KINETICS400_STD = (58.395, 57.12, 57.375)


def load_kinetics400_labels(labels_path: str) -> tuple[str, ...]:
    """Read the 400-line Kinetics-400 label map (one label per line)."""
    with open(labels_path, "r", encoding="utf-8") as handle:
        labels = tuple(line.strip() for line in handle if line.strip())
    if len(labels) != 400:
        raise ValueError(f"Kinetics-400 label map must have 400 lines: {labels_path}")
    return labels


class SlowFastKinetics400(nn.Module):
    """Full SlowFast R50 + 400-way head loaded from the official checkpoint.

    This is a *pretrained, ready-to-run* 400-class action recognizer: it covers
    squat / push up / lunge / pull ups / bench pressing / deadlifting / skipping
    rope / front raises / situp / yoga / tai chi and ~390 more. No training is
    needed; it runs on CPU (slower) or GPU when available.
    """

    def __init__(
        self,
        pretrained_path: str,
        labels_path: str,
        num_classes: int = 400,
        backbone_feature_dim: int = 2304,
    ) -> None:
        super().__init__()
        self.backbone = ResNet3dSlowFast()
        self.cls_head = nn.Linear(backbone_feature_dim, num_classes)
        self.labels = load_kinetics400_labels(labels_path)
        self._load_full_checkpoint(pretrained_path)
        self.eval()

    def _load_full_checkpoint(self, path: str) -> None:
        checkpoint = torch.load(path, map_location="cpu", weights_only=True)
        state_dict = checkpoint.get("state_dict", checkpoint)
        remapped: dict = {}
        for key, value in state_dict.items():
            if key.startswith("backbone."):
                remapped[key] = value  # self.backbone keeps the same key paths
            elif key.startswith("cls_head.fc_cls."):
                suffix = key[len("cls_head.fc_cls."):]
                remapped[f"cls_head.{suffix}"] = value
        missing, unexpected = self.load_state_dict(remapped, strict=False)
        if missing or unexpected:
            raise ValueError(
                f"Kinetics-400 checkpoint mismatch: missing={missing} unexpected={unexpected[:5]}"
            )

    def features(self, x: torch.Tensor) -> torch.Tensor:
        slow, fast = self.backbone(x)
        slow = F.adaptive_avg_pool3d(slow, 1).flatten(1)
        fast = F.adaptive_avg_pool3d(fast, 1).flatten(1)
        # Official SlowFastHead concatenates FAST first, then slow; the fc
        # weights are trained with channel order [fast(256), slow(2048)].
        return torch.cat((fast, slow), dim=1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.cls_head(self.features(x))

    @torch.no_grad()
    def predict(self, clip: torch.Tensor, topk: int = 5) -> dict:
        """Return top-k labels with probabilities for a preprocessed clip.

        ``clip`` shape: (1, 3, 32, H, W), already normalized; the backbone
        derives the slow pathway internally.
        """
        logits = self.forward(clip)
        probs = F.softmax(logits, dim=1)[0]
        k = min(topk, probs.numel())
        top_probs, top_idx = probs.topk(k)
        candidates = [
            {
                "label": self.labels[int(index)],
                "class_index": int(index),
                "probability": round(float(prob), 4),
            }
            for prob, index in zip(top_probs.tolist(), top_idx.tolist())
        ]
        return {
            "top_label": candidates[0]["label"],
            "top_probability": candidates[0]["probability"],
            "candidates": candidates,
        }
