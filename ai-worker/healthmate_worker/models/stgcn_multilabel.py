"""A compact graph-temporal multi-label network.

PyTorch is deliberately optional for the normal Worker installation. Importing
this module is safe without torch; constructing the model then raises a clear error.
"""

from __future__ import annotations

from .skeleton_schema import JOINT_NAMES, MOVEMENT_LABELS, REGION_LABELS

try:
    import torch
    from torch import nn
except ImportError:  # pragma: no cover - exercised in the default non-training env
    torch = None
    nn = None


EDGES = (
    (0, 1), (0, 2), (2, 4), (1, 3), (3, 5),
    (0, 6), (1, 7), (6, 7), (6, 8), (8, 10), (7, 9), (9, 11),
)


def adjacency_matrix():
    if torch is None:
        raise RuntimeError("PyTorch is required for ST-GCN training")
    matrix = torch.eye(len(JOINT_NAMES), dtype=torch.float32)
    for left, right in EDGES:
        matrix[left, right] = 1
        matrix[right, left] = 1
    degree = matrix.sum(dim=1).clamp(min=1).pow(-0.5)
    return degree[:, None] * matrix * degree[None, :]


if nn is not None:

    class GraphTemporalBlock(nn.Module):
        def __init__(self, in_channels: int, out_channels: int, dropout: float = 0.1):
            super().__init__()
            self.project = nn.Conv2d(in_channels, out_channels, kernel_size=1)
            self.temporal = nn.Conv2d(
                out_channels,
                out_channels,
                kernel_size=(5, 1),
                padding=(2, 0),
                groups=out_channels,
            )
            self.norm = nn.BatchNorm2d(out_channels)
            self.activation = nn.GELU()
            self.dropout = nn.Dropout(dropout)
            self.residual = (
                nn.Identity()
                if in_channels == out_channels
                else nn.Conv2d(in_channels, out_channels, kernel_size=1)
            )

        def forward(self, x, adjacency):
            residual = self.residual(x)
            x = torch.einsum("nctv,vw->nctw", x, adjacency)
            x = self.project(x)
            x = self.temporal(x)
            return self.dropout(self.activation(self.norm(x + residual)))


    class SkeletonMultiLabelNet(nn.Module):
        def __init__(self, hidden: int = 64, dropout: float = 0.15):
            super().__init__()
            self.register_buffer("adjacency", adjacency_matrix())
            self.input_norm = nn.BatchNorm1d(len(JOINT_NAMES) * 3)
            self.blocks = nn.ModuleList(
                [
                    GraphTemporalBlock(3, hidden, dropout),
                    GraphTemporalBlock(hidden, hidden, dropout),
                    GraphTemporalBlock(hidden, hidden * 2, dropout),
                ]
            )
            self.embedding = nn.Sequential(
                nn.Linear(hidden * 2, hidden), nn.GELU(), nn.Dropout(dropout)
            )
            self.movement_head = nn.Linear(hidden, len(MOVEMENT_LABELS))
            self.region_head = nn.Linear(hidden, len(REGION_LABELS))

        def forward(self, sequence):
            # Input: [batch, frames, joints, x/y/visibility]
            batch, frames, joints, channels = sequence.shape
            flat = sequence.reshape(batch, frames, joints * channels).transpose(1, 2)
            flat = self.input_norm(flat).transpose(1, 2)
            x = flat.reshape(batch, frames, joints, channels).permute(0, 3, 1, 2)
            for block in self.blocks:
                x = block(x, self.adjacency)
            pooled = x.mean(dim=(2, 3))
            embedding = self.embedding(pooled)
            return self.movement_head(embedding), self.region_head(embedding), embedding

else:

    class SkeletonMultiLabelNet:  # pragma: no cover - simple dependency guard
        def __init__(self, *args, **kwargs):
            raise RuntimeError("PyTorch is required for ST-GCN training")
