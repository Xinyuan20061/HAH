"""Runtime for the local SlowFast Kinetics-400 recognizer.

Loads the model once (lazy singleton, thread-safe) and maps Kinetics labels to
HealthMate exercise types. Everything degrades gracefully when the checkpoint
is absent, so the rest of the pipeline keeps working without this model.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from ..config import settings

_LABELS_PATH = Path(__file__).with_name("kinetics400_labels.txt")
_lock = threading.Lock()
_model = None

# Worker-side concurrency gate: SlowFast on CPU is heavy, so at most ONE
# inference may run at a time. The job loop is already sequential, but this
# semaphore documents and enforces the constraint even if the worker later
# parallelizes job processing. Acquire it around every model.predict.
SLOWFAST_SEMAPHORE = threading.Semaphore(1)

# Kinetics-400 labels that map onto a HealthMate exercise with a full analyzer
# (squat / pushup / lunge) or a known display slug shown in the mini-program.
# Only the first three get a dedicated count+score; the rest are recognized
# without scoring and surface as a familiar exercise name.
KINETICS_TO_EXERCISE = {
    "squat": "squat",
    "push up": "pushup",
    "lunge": "lunge",
    "pull ups": "pullup",
    "bench pressing": "chest_press",
    "deadlifting": "deadlift",
    "situp": "situp",
}


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
