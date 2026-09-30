"""Video -> SlowFast clip preprocessing (MMAction2-style, OpenCV).

The official SlowFast pipeline samples ``clip_len=32`` frames with
``frame_interval=2`` from a ~64-frame window; the backbone derives the slow
pathway internally (nearest temporal downsample by 8). For videos shorter than
64 frames we re-sample 32 frames across the whole clip with linspace. Each frame
is resized so the short side is 256, center-cropped to 224x224, BGR->RGB and
Kinetics mean/std normalized.
"""

from __future__ import annotations

import cv2
import numpy as np
import torch

from .slowfast_r50 import KINETICS400_MEAN, KINETICS400_STD

CLIP_LEN = 32
FRAME_INTERVAL = 2
WINDOW_FRAMES = (CLIP_LEN - 1) * FRAME_INTERVAL + 2  # 64


def _read_frames(video_path: str) -> list[np.ndarray]:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"cannot open video: {video_path}")
    frames: list[np.ndarray] = []
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frames.append(frame)
    finally:
        cap.release()
    if not frames:
        raise ValueError(f"no frames decoded: {video_path}")
    return frames


def _sample_indices(num_frames: int) -> list[int]:
    if num_frames >= WINDOW_FRAMES:
        start = (num_frames - WINDOW_FRAMES) // 2
        return [start + FRAME_INTERVAL * j for j in range(CLIP_LEN)]
    return [int(i) for i in np.linspace(0, num_frames - 1, CLIP_LEN).round().astype(int)]


def _resize_crop(frame: np.ndarray, resize: int, size: int) -> np.ndarray:
    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    height, width = frame.shape[:2]
    scale = resize / min(height, width)
    new_w = max(size, int(round(width * scale)))
    new_h = max(size, int(round(height * scale)))
    frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    top = (new_h - size) // 2
    left = (new_w - size) // 2
    return frame[top : top + size, left : left + size]


def build_clip(
    video_path: str,
    resize: int = 256,
    size: int = 224,
) -> tuple[torch.Tensor, int]:
    """Return (clip [1,3,32,size,size], total frame count)."""
    frames = _read_frames(video_path)
    indices = _sample_indices(len(frames))
    selected = [_resize_crop(frames[i], resize, size) for i in indices]
    arr = np.stack(selected).astype(np.float32)  # T,H,W,3
    mean = np.asarray(KINETICS400_MEAN, dtype=np.float32)
    std = np.asarray(KINETICS400_STD, dtype=np.float32)
    arr = (arr - mean) / std
    clip = torch.from_numpy(arr).permute(3, 0, 1, 2).unsqueeze(0).contiguous()
    return clip, len(frames)


def sample_indices_for(num_frames: int) -> list[int]:
    """Public accessor for the SlowFast temporal sample indices."""
    return _sample_indices(num_frames)


def clip_tensor_from_selected(
    selected_frames: list[np.ndarray],
    resize: int = 256,
    size: int = 224,
) -> torch.Tensor:
    """Build a normalized SlowFast clip tensor from already-decoded frames.

    ``selected_frames`` must be BGR frames in temporal order at the indices
    returned by :func:`sample_indices_for`. This lets the unified chain decode
    the video once and reuse the same frames for pose + Kinetics, instead of
    opening the file a second time.
    """
    cropped = [_resize_crop(f, resize, size) for f in selected_frames]
    arr = np.stack(cropped).astype(np.float32)  # T,H,W,3 RGB
    mean = np.asarray(KINETICS400_MEAN, dtype=np.float32)
    std = np.asarray(KINETICS400_STD, dtype=np.float32)
    arr = (arr - mean) / std
    return torch.from_numpy(arr).permute(3, 0, 1, 2).unsqueeze(0).contiguous()
