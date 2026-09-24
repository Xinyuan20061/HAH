"""Training utilities for the skeleton multi-label model.

This module is imported only by the dedicated training script. The normal Worker
does not require PyTorch unless a trained semantic artifact is configured.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random
from typing import Iterable

import numpy as np

from ..datasets.manifest import manifest_sha256, validate_manifest, verify_no_leakage
from .skeleton_schema import (
    LABEL_SCHEMA_VERSION,
    MOVEMENT_LABELS,
    REGION_LABELS,
    load_pose_array,
)
from .stgcn_multilabel import SkeletonMultiLabelNet


def _torch():
    try:
        import torch
        from torch import nn
        from torch.utils.data import DataLoader, Dataset
    except ImportError:
        raise RuntimeError(
            "PyTorch is required for training; install requirements-training.txt first"
        ) from None
    return torch, nn, DataLoader, Dataset


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _resolve_pose_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        raise ValueError("pose path escapes dataset root") from None
    if not path.is_file():
        raise ValueError(f"pose file does not exist: {relative}")
    return path


def _label_vector(values: Iterable[str], labels: tuple[str, ...]) -> np.ndarray:
    selected = set(values)
    unknown = sorted(selected - set(labels))
    if unknown:
        raise ValueError(f"manifest contains unsupported semantic labels: {unknown}")
    return np.asarray([float(label in selected) for label in labels], dtype=np.float32)


def _binary_metrics(truth: np.ndarray, probabilities: np.ndarray, thresholds: np.ndarray) -> dict:
    predicted = probabilities >= thresholds[None, :]
    actual = truth >= 0.5
    tp = int(np.logical_and(actual, predicted).sum())
    fp = int(np.logical_and(~actual, predicted).sum())
    fn = int(np.logical_and(actual, ~predicted).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    micro_f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    per_label = []
    for index in range(actual.shape[1]):
        label_tp = int(np.logical_and(actual[:, index], predicted[:, index]).sum())
        label_fp = int(np.logical_and(~actual[:, index], predicted[:, index]).sum())
        label_fn = int(np.logical_and(actual[:, index], ~predicted[:, index]).sum())
        p = label_tp / (label_tp + label_fp) if label_tp + label_fp else 0.0
        r = label_tp / (label_tp + label_fn) if label_tp + label_fn else 0.0
        per_label.append(2 * p * r / (p + r) if p + r else 0.0)
    return {
        "micro_f1_pct": round(micro_f1 * 100, 2),
        "macro_f1_pct": round(float(np.mean(per_label)) * 100, 2),
        "exact_match_pct": round(float(np.all(actual == predicted, axis=1).mean()) * 100, 2),
    }


def _calibrate_thresholds(truth: np.ndarray, probabilities: np.ndarray) -> np.ndarray:
    thresholds = np.full(truth.shape[1], 0.5, dtype=np.float32)
    for label_index in range(truth.shape[1]):
        best = (-1.0, 0.5)
        actual = truth[:, label_index] >= 0.5
        for threshold in np.linspace(0.15, 0.85, 15):
            predicted = probabilities[:, label_index] >= threshold
            tp = int(np.logical_and(actual, predicted).sum())
            fp = int(np.logical_and(~actual, predicted).sum())
            fn = int(np.logical_and(actual, ~predicted).sum())
            precision = tp / (tp + fp) if tp + fp else 0.0
            recall = tp / (tp + fn) if tp + fn else 0.0
            f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
            candidate = (f1, -abs(float(threshold) - 0.5))
            if candidate > (best[0], -abs(best[1] - 0.5)):
                best = (f1, float(threshold))
        thresholds[label_index] = best[1]
    return thresholds


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def train_semantic_model(
    *,
    manifest_path: Path,
    dataset_root: Path,
    output_path: Path,
    epochs: int = 30,
    batch_size: int = 16,
    learning_rate: float = 1e-3,
    sequence_frames: int = 64,
    seed: int = 20260921,
    hidden: int = 64,
) -> dict:
    torch, nn, DataLoader, Dataset = _torch()
    if epochs < 1 or batch_size < 1 or learning_rate <= 0:
        raise ValueError("epochs, batch_size, and learning_rate must be positive")
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    rows = validate_manifest(_read_jsonl(manifest_path))
    verify_no_leakage(rows)
    train_rows = [row for row in rows if row["split"] == "train"]
    validation_rows = [row for row in rows if row["split"] == "validation"]
    if not train_rows or not validation_rows:
        raise ValueError("manifest requires non-empty train and validation subject splits")
    if any(not row.get("pose_path") for row in train_rows + validation_rows):
        raise ValueError("training requires pose_path for every train/validation sample")
    root = dataset_root.resolve()

    class PoseDataset(Dataset):
        def __init__(self, items, augment=False):
            self.items = items
            self.augment = augment

        def __len__(self):
            return len(self.items)

        def __getitem__(self, index):
            row = self.items[index]
            sequence = load_pose_array(
                _resolve_pose_path(root, row["pose_path"]), sequence_frames
            ).astype(np.float32)
            if self.augment and random.random() < 0.5:
                sequence = sequence.copy()
                sequence[:, :, 0] *= -1
            if self.augment:
                noise = np.random.normal(0, 0.008, sequence[:, :, :2].shape).astype(np.float32)
                sequence[:, :, :2] += noise * (sequence[:, :, 2:3] >= 0.2)
            return (
                torch.from_numpy(sequence),
                torch.from_numpy(_label_vector(row["movement_patterns"], MOVEMENT_LABELS)),
                torch.from_numpy(_label_vector(row["observed_regions"], REGION_LABELS)),
            )

    train_loader = DataLoader(PoseDataset(train_rows, True), batch_size=batch_size, shuffle=True)
    validation_loader = DataLoader(PoseDataset(validation_rows), batch_size=batch_size, shuffle=False)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = SkeletonMultiLabelNet(hidden=hidden).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=1e-4)
    loss_function = nn.BCEWithLogitsLoss()
    history = []
    best_state = None
    best_loss = float("inf")
    for epoch in range(1, epochs + 1):
        model.train()
        training_loss = 0.0
        for sequence, movement, regions in train_loader:
            sequence, movement, regions = sequence.to(device), movement.to(device), regions.to(device)
            optimizer.zero_grad(set_to_none=True)
            movement_logits, region_logits, _ = model(sequence)
            loss = loss_function(movement_logits, movement) + loss_function(region_logits, regions)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            optimizer.step()
            training_loss += float(loss.detach().cpu()) * len(sequence)
        model.eval()
        validation_loss = 0.0
        with torch.inference_mode():
            for sequence, movement, regions in validation_loader:
                sequence, movement, regions = sequence.to(device), movement.to(device), regions.to(device)
                movement_logits, region_logits, _ = model(sequence)
                loss = loss_function(movement_logits, movement) + loss_function(region_logits, regions)
                validation_loss += float(loss.cpu()) * len(sequence)
        training_loss /= len(train_rows)
        validation_loss /= len(validation_rows)
        history.append({"epoch": epoch, "train_loss": round(training_loss, 6), "validation_loss": round(validation_loss, 6)})
        if validation_loss < best_loss:
            best_loss = validation_loss
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
    model.load_state_dict(best_state)
    model.cpu().eval()
    movement_probabilities, region_probabilities = [], []
    movement_truth, region_truth = [], []
    with torch.inference_mode():
        for sequence, movement, regions in validation_loader:
            movement_logits, region_logits, _ = model(sequence)
            movement_probabilities.append(torch.sigmoid(movement_logits).numpy())
            region_probabilities.append(torch.sigmoid(region_logits).numpy())
            movement_truth.append(movement.numpy())
            region_truth.append(regions.numpy())
    movement_probabilities = np.concatenate(movement_probabilities)
    region_probabilities = np.concatenate(region_probabilities)
    movement_truth = np.concatenate(movement_truth)
    region_truth = np.concatenate(region_truth)
    movement_thresholds = _calibrate_thresholds(movement_truth, movement_probabilities)
    region_thresholds = _calibrate_thresholds(region_truth, region_probabilities)
    validation_metrics = {
        "movement_patterns": _binary_metrics(movement_truth, movement_probabilities, movement_thresholds),
        "observed_regions": _binary_metrics(region_truth, region_probabilities, region_thresholds),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    scripted = torch.jit.script(model)
    scripted.save(str(output_path))
    artifact_hash = _sha256(output_path)
    metadata = {
        "model_key": "skeleton-stgcn-multilabel",
        "version": "1.0.0",
        "implementation": "stgcn_multilabel_v1",
        "label_schema_version": LABEL_SCHEMA_VERSION,
        "movement_labels": list(MOVEMENT_LABELS),
        "region_labels": list(REGION_LABELS),
        "movement_thresholds": dict(zip(MOVEMENT_LABELS, movement_thresholds.round(4).tolist())),
        "region_thresholds": dict(zip(REGION_LABELS, region_thresholds.round(4).tolist())),
        "sequence_frames": sequence_frames,
        "artifact_sha256": artifact_hash,
        "manifest_sha256": manifest_sha256(rows),
        "sample_counts": {"train": len(train_rows), "validation": len(validation_rows)},
        "validation_metrics": validation_metrics,
        "training": {
            "epochs": epochs,
            "batch_size": batch_size,
            "learning_rate": learning_rate,
            "seed": seed,
            "device": str(device),
            "history": history,
        },
        "claims_scope": "指标仅适用于所登记验证划分；模型不测量肌肉激活、训练效果或医学风险。",
    }
    sidecar = output_path.with_suffix(output_path.suffix + ".json")
    sidecar.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


# ---------------------------------------------------------------------------
# SlowFast R50 six-action video recognition (REMAINING_WORK #1)
# ---------------------------------------------------------------------------

SLOWFAST_ACTION_LABELS = (
    "squat",
    "pushup",
    "lunge",
    "leg_abduction",
    "arm_abduction",
    "arm_vw",
)

# Kinetics-400 normalization used by the official MMAction2 SlowFast recipe,
# BGR channel order (OpenCV frames). See .mim/configs/_base_/models/slowfast_r50.py.
SLOWFAST_MEAN = np.asarray([123.675, 116.28, 103.53], dtype=np.float32)
SLOWFAST_STD = np.asarray([58.395, 57.12, 57.375], dtype=np.float32)


def _cv2():
    try:
        import cv2
    except ImportError:
        raise RuntimeError("opencv-python is required for video training") from None
    return cv2


def _validate_slowfast_manifest(rows: list[dict]) -> None:
    """Validate the video manifest without importing skeleton-only validators."""
    for index, row in enumerate(rows):
        for field in ("video_path", "first_frame", "last_frame", "exercise_type", "split"):
            if not row.get(field):
                raise ValueError(f"manifest row {index} missing field: {field}")
        if row["exercise_type"] not in SLOWFAST_ACTION_LABELS:
            raise ValueError(f"manifest row {index} unsupported exercise_type: {row['exercise_type']}")
        if row["split"] not in ("train", "validation", "test"):
            raise ValueError(f"manifest row {index} unsupported split: {row['split']}")
        if int(row["first_frame"]) < 0 or int(row["last_frame"]) <= int(row["first_frame"]):
            raise ValueError(f"manifest row {index} invalid frame range")
    subjects_by_split: dict[str, set] = {}
    for row in rows:
        subjects_by_split.setdefault(row["split"], set()).add(row["subject_id"])
    for split_a, split_b in (("train", "validation"), ("train", "test"), ("validation", "test")):
        overlap = subjects_by_split.get(split_a, set()) & subjects_by_split.get(split_b, set())
        if overlap:
            raise ValueError(f"subject leakage between {split_a} and {split_b}: {sorted(overlap)[:5]}")
    return rows


class _VideoClipDataset:
    """Sample ``clip_frames`` frames evenly from a REHAB24 video clip.

    Returns a normalized ``(3, T, H, W)`` float32 tensor in BGR channel order
    plus the action label index. Frames are decoded by random access, one
    ``cv2.VideoCapture`` per item (safe with a single worker). Implements the
    torch ``Dataset`` protocol structurally so PyTorch stays lazily imported.
    """

    def __init__(
        self,
        items: list[dict],
        clip_frames: int = 32,
        input_size: int = 224,
        scale: int = 256,
        augment: bool = False,
        seed: int = 0,
    ):
        self.items = items
        self.clip_frames = clip_frames
        self.input_size = input_size
        self.scale = scale
        self.augment = augment
        self.rng = random.Random(seed)

    def __len__(self):
        return len(self.items)

    def _read_frames(self, row: dict):
        cv2 = _cv2()
        capture = cv2.VideoCapture(row["video_path"])
        if not capture.isOpened():
            raise ValueError(f"cannot open video: {row['video_path']}")
        first, last = int(row["first_frame"]), int(row["last_frame"])
        indices = np.linspace(first, last, self.clip_frames).round().astype(int).tolist()
        frames = []
        fallback = None
        for frame_index in indices:
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = capture.read()
            if not ok or frame is None:
                if fallback is None:
                    raise ValueError(f"frame {frame_index} unreadable in {row['video_path']}")
                frame = fallback
            else:
                fallback = frame
            frames.append(frame)
        capture.release()
        return frames

    def _process_frame(self, frame):
        cv2 = _cv2()
        height, width = frame.shape[:2]
        if height < width:
            new_height, new_width = self.scale, int(width * self.scale / height)
        else:
            new_width, new_height = self.scale, int(height * self.scale / width)
        frame = cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_LINEAR)
        size = self.input_size
        if self.augment:
            top = self.rng.randint(0, new_height - size) if new_height > size else 0
            left = self.rng.randint(0, new_width - size) if new_width > size else 0
        else:
            top = (new_height - size) // 2
            left = (new_width - size) // 2
        frame = frame[top:top + size, left:left + size]
        if self.augment and self.rng.random() < 0.5:
            frame = frame[:, ::-1]
        return frame

    def __getitem__(self, index):
        torch, _, _, _ = _torch()
        row = self.items[index]
        frames = self._read_frames(row)
        processed = np.stack([self._process_frame(frame) for frame in frames], axis=0)
        tensor = torch.from_numpy(processed).permute(3, 0, 1, 2).float()  # (3, T, H, W)
        tensor = (tensor - torch.from_numpy(SLOWFAST_MEAN).view(3, 1, 1, 1)) / torch.from_numpy(
            SLOWFAST_STD
        ).view(3, 1, 1, 1)
        label = SLOWFAST_ACTION_LABELS.index(row["exercise_type"])
        return tensor, label


def _apply_slowfast_freeze(model, strategy: str) -> None:
    """Freeze backbone parameters per strategy and put frozen BN3d in eval mode.

    ``head`` freezes the whole backbone (weights loaded from Kinetics-400 stay
    fixed); ``partial`` additionally keeps ``layer4`` and ``layer3_lateral``
    trainable. ``none`` keeps everything trainable.
    """
    if strategy == "none":
        return
    _, nn, _, _ = _torch()
    frozen: set[str] = set()
    for name, parameter in model.backbone.named_parameters():
        if strategy == "head":
            frozen.add(name)
        elif strategy == "partial":
            if not (name.startswith("layer4") or "layer3_lateral" in name):
                frozen.add(name)
        else:
            raise ValueError(f"unknown freeze strategy: {strategy}")
    for name, parameter in model.backbone.named_parameters():
        if name in frozen:
            parameter.requires_grad = False
    for name, module in model.backbone.named_modules():
        if isinstance(module, nn.BatchNorm3d) and not any(
            parameter.requires_grad for parameter in module.parameters()
        ):
            module.eval()


def _softmax_predictions(logits: np.ndarray) -> np.ndarray:
    shifted = logits - np.max(logits, axis=1, keepdims=True)
    exp = np.exp(shifted)
    return exp / exp.sum(axis=1, keepdims=True)


def _slowfast_metrics(truth: np.ndarray, probabilities: np.ndarray) -> dict:
    predicted = probabilities.argmax(axis=1)
    top1 = float((predicted == truth).mean())
    per_class = {}
    for index, label in enumerate(SLOWFAST_ACTION_LABELS):
        mask = truth == index
        per_class[label] = round(float((predicted[mask] == index).mean()), 4) if mask.any() else None
    return {"top1_pct": round(top1 * 100, 2), "per_class": per_class}


def train_slowfast_six_action(
    *,
    manifest_path: Path,
    output_path: Path,
    pretrained_path: Path,
    epochs: int = 12,
    batch_size: int = 4,
    learning_rate: float = 5e-4,
    clip_frames: int = 32,
    input_size: int = 224,
    freeze: str = "head",
    device: str | None = None,
    seed: int = 20260923,
) -> dict:
    """Fine-tune the SlowFast R50 backbone on REHAB24-6.

    ``--pretrained`` is mandatory: the fine-tune always starts from the
    official Kinetics-400 checkpoint; training from scratch is refused by
    design (REMAINING_WORK #1 gate).
    """
    if not pretrained_path.is_file():
        raise ValueError(f"pretrained checkpoint required: {pretrained_path}")
    if epochs < 1 or batch_size < 1 or learning_rate <= 0:
        raise ValueError("epochs, batch_size, and learning_rate must be positive")
    if freeze not in ("head", "partial", "none"):
        raise ValueError(f"unknown freeze strategy: {freeze}")
    torch, nn, DataLoader, Dataset = _torch()
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    rows = _validate_slowfast_manifest(_read_jsonl(manifest_path))
    train_rows = [row for row in rows if row["split"] == "train"]
    validation_rows = [row for row in rows if row["split"] == "validation"]
    if not train_rows or not validation_rows:
        raise ValueError("manifest requires non-empty train and validation subject splits")
    for row in train_rows + validation_rows:
        if not Path(row["video_path"]).is_file():
            raise ValueError(f"video file does not exist: {row['video_path']}")

    from .slowfast_r50 import SlowFastSixAction

    selected_device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
    model = SlowFastSixAction(pretrained_path=str(pretrained_path)).to(selected_device)
    _apply_slowfast_freeze(model, freeze)

    counts = np.asarray(
        [sum(1 for row in train_rows if row["exercise_type"] == label) for label in SLOWFAST_ACTION_LABELS],
        dtype=np.float32,
    )
    class_weight = torch.from_numpy(counts.sum() / (counts * len(SLOWFAST_ACTION_LABELS))).to(selected_device)

    def worker_seed(worker_id: int) -> None:
        torch.manual_seed(seed + worker_id)

    train_loader = DataLoader(
        _VideoClipDataset(train_rows, clip_frames, input_size, augment=True, seed=seed),
        batch_size=batch_size,
        shuffle=True,
        num_workers=0,
        worker_init_fn=worker_seed,
    )
    validation_loader = DataLoader(
        _VideoClipDataset(validation_rows, clip_frames, input_size, augment=False, seed=seed),
        batch_size=batch_size,
        shuffle=False,
        num_workers=0,
    )
    parameters = [parameter for parameter in model.parameters() if parameter.requires_grad]
    optimizer = torch.optim.AdamW(parameters, lr=learning_rate, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    loss_function = nn.CrossEntropyLoss(weight=class_weight)

    history = []
    best_state = None
    best_top1 = -1.0
    for epoch in range(1, epochs + 1):
        model.train()
        training_loss = 0.0
        for clips, labels in train_loader:
            clips, labels = clips.to(selected_device), labels.to(selected_device)
            optimizer.zero_grad(set_to_none=True)
            loss = loss_function(model(clips), labels)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(parameters, 5.0)
            optimizer.step()
            training_loss += float(loss.detach().cpu()) * len(clips)
        model.eval()
        truth, probabilities = [], []
        validation_loss = 0.0
        with torch.inference_mode():
            for clips, labels in validation_loader:
                clips, labels = clips.to(selected_device), labels.to(selected_device)
                logits = model(clips)
                validation_loss += float(loss_function(logits, labels).cpu()) * len(clips)
                truth.append(labels.cpu().numpy())
                probabilities.append(_softmax_predictions(logits.cpu().numpy()))
        truth = np.concatenate(truth)
        probabilities = np.concatenate(probabilities)
        metrics = _slowfast_metrics(truth, probabilities)
        training_loss /= len(train_rows)
        validation_loss /= len(validation_rows)
        history.append(
            {
                "epoch": epoch,
                "train_loss": round(training_loss, 6),
                "validation_loss": round(validation_loss, 6),
                "validation_top1_pct": metrics["top1_pct"],
                "lr": scheduler.get_last_lr()[0],
            }
        )
        if metrics["top1_pct"] > best_top1:
            best_top1 = metrics["top1_pct"]
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
        scheduler.step()

    model.load_state_dict(best_state)
    model.cpu().eval()
    truth, probabilities = [], []
    with torch.inference_mode():
        for clips, labels in validation_loader:
            logits = model(clips)
            truth.append(labels.numpy())
            probabilities.append(_softmax_predictions(logits.numpy()))
    validation_metrics = _slowfast_metrics(np.concatenate(truth), np.concatenate(probabilities))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    model.eval()
    example = torch.zeros(1, 3, clip_frames, input_size, input_size)
    scripted = torch.jit.trace(model, example, check_trace=False)
    scripted.save(str(output_path))
    artifact_hash = _sha256(output_path)
    metadata = {
        "model_key": "slowfast-r50-six-action",
        "version": "1.0.0",
        "implementation": "slowfast_r50_six_action_v1",
        "labels": list(SLOWFAST_ACTION_LABELS),
        "clip_frames": clip_frames,
        "input_size": input_size,
        "backbone": {
            "architecture": "SlowFast R50 (mmaction2-compatible pure PyTorch)",
            "pretrained": "kinetics400 slowfast_r50_8xb8-8x8x1-steplr-256e",
            "pretrained_sha256": "b62a501f",
        },
        "freeze": freeze,
        "artifact_sha256": artifact_hash,
        "manifest_sha256": manifest_sha256(rows),
        "sample_counts": {"train": len(train_rows), "validation": len(validation_rows)},
        "validation_metrics": validation_metrics,
        "training": {
            "epochs": epochs,
            "batch_size": batch_size,
            "learning_rate": learning_rate,
            "clip_frames": clip_frames,
            "input_size": input_size,
            "seed": seed,
            "device": str(selected_device),
            "history": history,
        },
        "claims_scope": (
            "指标仅适用于已登记的 REHAB24-6 验证划分（按受试者隔离）；"
            "模型为研究原型，不构成医疗诊断或训练效果评估。"
        ),
    }
    sidecar = output_path.with_suffix(output_path.suffix + ".json")
    sidecar.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata
