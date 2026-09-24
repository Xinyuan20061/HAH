"""Optional TorchScript semantic inference with deterministic rule fallback."""

from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path

from ..config import settings
from ..processors.semantic_features import infer_compositional_semantics
from .skeleton_schema import (
    LABEL_SCHEMA_VERSION,
    MOVEMENT_LABELS,
    REGION_LABELS,
    build_skeleton_tensor,
)


MOVEMENT_NAMES = {
    "knee_dominant": "膝主导屈伸",
    "bilateral_lower_body": "双侧下肢模式",
    "unilateral_lower_body": "单侧下肢模式",
    "elbow_flexion_extension": "肘关节屈伸",
    "horizontal_upper_body": "水平体位上肢动作",
    "static_or_low_amplitude": "静态或低幅度动作",
}
REGION_NAMES = {
    "lower_body": "下肢",
    "upper_body": "上肢",
    "trunk_stability": "躯干稳定",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _configured_path() -> Path | None:
    value = settings.semantic_model_path.strip()
    if not value:
        return None
    path = Path(value).expanduser().resolve()
    return path


@lru_cache(maxsize=2)
def _load_model(path_text: str, expected_sha256: str):
    try:
        import torch
    except ImportError:
        raise RuntimeError("semantic model configured but PyTorch is not installed") from None
    path = Path(path_text)
    if not path.is_file():
        raise RuntimeError("semantic model file does not exist")
    actual_hash = _sha256(path)
    if expected_sha256 and actual_hash.lower() != expected_sha256.lower():
        raise RuntimeError("semantic model SHA-256 mismatch")
    metadata_path = path.with_suffix(path.suffix + ".json")
    if not metadata_path.is_file():
        raise RuntimeError("semantic model metadata sidecar is missing")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("label_schema_version") != LABEL_SCHEMA_VERSION:
        raise RuntimeError("semantic model label schema is incompatible")
    if tuple(metadata.get("movement_labels", [])) != MOVEMENT_LABELS:
        raise RuntimeError("semantic model movement labels are incompatible")
    if tuple(metadata.get("region_labels", [])) != REGION_LABELS:
        raise RuntimeError("semantic model region labels are incompatible")
    if metadata.get("artifact_sha256") and metadata["artifact_sha256"] != actual_hash:
        raise RuntimeError("semantic model sidecar hash does not match artifact")
    model = torch.jit.load(str(path), map_location="cpu")
    model.eval()
    return model, metadata, actual_hash


def semantic_model_status() -> dict:
    path = _configured_path()
    if path is None:
        return {"configured": False, "available": False, "mode": "rule_fallback", "reason": "SEMANTIC_MODEL_PATH 未配置"}
    try:
        _, metadata, actual_hash = _load_model(str(path), settings.semantic_model_sha256.strip())
        return {
            "configured": True,
            "available": True,
            "mode": "trained_model",
            "model_key": metadata.get("model_key"),
            "version": metadata.get("version"),
            "sha256": actual_hash,
        }
    except (RuntimeError, ValueError, OSError, json.JSONDecodeError) as exc:
        return {"configured": True, "available": False, "mode": "rule_fallback", "reason": str(exc)[:240]}


def _trained_semantics(sample_sets: dict[str, list[dict]]) -> dict:
    path = _configured_path()
    if path is None:
        raise RuntimeError("semantic model is not configured")
    model, metadata, actual_hash = _load_model(str(path), settings.semantic_model_sha256.strip())
    import numpy as np
    import torch
    rows = max((rows for rows in sample_sets.values() if isinstance(rows, list)), key=len, default=[])
    sequence = build_skeleton_tensor(rows, int(metadata.get("sequence_frames", 64)))
    tensor = torch.from_numpy(sequence[None].astype(np.float32))
    with torch.inference_mode():
        movement_logits, region_logits, _ = model(tensor)
        movement_probabilities = torch.sigmoid(movement_logits)[0].cpu().tolist()
        region_probabilities = torch.sigmoid(region_logits)[0].cpu().tolist()
    movement_thresholds = metadata.get("movement_thresholds", {})
    region_thresholds = metadata.get("region_thresholds", {})
    patterns = [
        {
            "key": label,
            "name": MOVEMENT_NAMES[label],
            "score": round(probability * 100, 1),
            "evidence": "骨骼时序多标签模型输出",
        }
        for label, probability in zip(MOVEMENT_LABELS, movement_probabilities)
        if probability >= float(movement_thresholds.get(label, 0.5))
    ]
    regions = [
        {
            "key": label,
            "name": REGION_NAMES[label],
            "basis": "骨骼时序多标签模型输出",
        }
        for label, probability in zip(REGION_LABELS, region_probabilities)
        if probability >= float(region_thresholds.get(label, 0.5))
    ]
    confidence = max([*movement_probabilities, *region_probabilities], default=0.0)
    if confidence < settings.semantic_model_min_confidence:
        raise RuntimeError("semantic model confidence below configured acceptance threshold")
    rule_context = infer_compositional_semantics(sample_sets)
    return {
        "available": bool(patterns or regions),
        "method": "stgcn_multilabel_v1",
        "confidence": round(float(confidence), 3),
        "orientation": rule_context.get("orientation", {}),
        "laterality": rule_context.get("laterality", {}),
        "movement_patterns": sorted(patterns, key=lambda item: (-item["score"], item["key"])),
        "observed_regions": regions,
        "evidence": {
            "model_key": metadata.get("model_key"),
            "version": metadata.get("version"),
            "artifact_sha256": actual_hash,
            "sequence_frames": len(sequence),
        },
        "scope": "骨骼时序多标签预测，不代表肌肉激活、实际训练效果或医学风险",
    }


def infer_motion_semantics(sample_sets: dict[str, list[dict]]) -> dict:
    if _configured_path() is None:
        return infer_compositional_semantics(sample_sets)
    try:
        return _trained_semantics(sample_sets)
    except (RuntimeError, ValueError, OSError, json.JSONDecodeError) as exc:
        fallback = infer_compositional_semantics(sample_sets)
        fallback["model_fallback"] = {"used": True, "reason": str(exc)[:240]}
        return fallback
