"""Runtime for the local SlowFast Kinetics-400 recognizer.

Loads the model once (lazy singleton, thread-safe) and maps Kinetics labels to
HealthMate exercise types. Everything degrades gracefully when the checkpoint
is absent, so the rest of the pipeline keeps working without this model.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from .. import catalog_data
from ..config import settings

_LABELS_PATH = Path(__file__).with_name("kinetics400_labels.txt")
_lock = threading.Lock()
_model = None

# Worker-side concurrency gate: SlowFast on CPU is heavy, so at most ONE
# inference may run at a time. The job loop is already sequential, but this
# semaphore documents and enforces the constraint even if the worker later
# parallelizes job processing. Acquire it around every model.predict.
SLOWFAST_SEMAPHORE = threading.Semaphore(1)

# Kinetics-400 label -> canonical action id. This used to be a hand-maintained
# 7-entry table here; it now derives from the generated shared catalog
# (catalog_data.KINETICS_TO_ID, single source of truth per spec §6.1/§6.2).
# Labels the catalog does not onboard resolve to None and are kept as raw visual
# reference — they are NEVER force-mapped to a legacy six-class action.
KINETICS_TO_EXERCISE: dict[str, Optional[str]] = dict(catalog_data.KINETICS_TO_ID)


def map_kinetics_label(label: str) -> Optional[str]:
    """Map a raw Kinetics-400 label to a canonical action id, or None.

    None means "the catalog has no exact mapping": the label is retained as a
    visual reference (canonical_id=null in the V2 receipt), not force-mapped.
    """
    if not label:
        return None
    return catalog_data.KINETICS_TO_ID.get(label.strip())


def kinetics_label_zh(label: str) -> str:
    """Return the catalog Chinese display name for a mapped Kinetics label.

    Unmapped labels fall back to "" (the standalone job keeps its curated display
    table for those); this helper only reflects the catalog-owned names.
    """
    canonical = map_kinetics_label(label)
    if not canonical:
        return ""
    for action in catalog_data.ACTIONS:
        if action["id"] == canonical:
            return str(action.get("name_zh") or "")
    return ""


def kinetics400_available() -> bool:
    """True only when a checkpoint file is configured and present."""
    return bool(settings.kinetics400_checkpoint.strip()) and Path(
        settings.kinetics400_checkpoint
    ).is_file()


def get_kinetics400():
    """Return the shared SlowFastKinetics400 model, or None if unavailable."""
    global _model
    if not kinetics400_available():
        return None
    if _model is None:
        with _lock:
            if _model is None:
                import torch

                from .slowfast_r50 import SlowFastKinetics400

                model = SlowFastKinetics400(
                    settings.kinetics400_checkpoint, str(_LABELS_PATH)
                )
                model.eval()
                model.to(settings.kinetics400_device)
                torch.set_grad_enabled(False)
                _model = model
    return _model


def slugify_kinetics(label: str) -> str:
    """Turn a Kinetics label into a stable exercise-type-like slug."""
    return label.strip().lower().replace(" ", "_").replace("-", "_")


def recognize_video_kinetics400(video_path) -> Optional[dict]:
    """Run Kinetics-400 on a video; return prediction dict or None.

    The returned dict adds ``mapped_exercise`` (an analyzer-backed exercise
    type when one exists, else None) and ``exercise_slug``.
    """
    model = get_kinetics400()
    if model is None:
        return None
    from .kinetics_clip import build_clip

    with SLOWFAST_SEMAPHORE:
        clip, _ = build_clip(str(video_path))
        result = model.predict(clip, topk=5)
    label = result["top_label"]
    result["mapped_exercise"] = KINETICS_TO_EXERCISE.get(label)
    result["exercise_slug"] = slugify_kinetics(label)
    return result
