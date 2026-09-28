"""Verify the Kinetics-400 SlowFast model on real REHAB24-6 clips (CPU/GPU).

Loads the official pretrained checkpoint (no training) and runs recognition on
the first clip of each Ex1..Ex6 folder, printing the top-3 predictions.

Usage:
  ai-worker/.venv/Scripts/python.exe ai-worker/scripts/verify_kinetics400.py
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from healthmate_worker.models.kinetics_clip import build_clip  # noqa: E402
from healthmate_worker.models.slowfast_r50 import SlowFastKinetics400  # noqa: E402

CHECKPOINT = r"D:\HealthMateData\motion\pretrained\slowfast_r50_8xb8-8x8x1-steplr-256e_kinetics400-rgb_20220818-b62a501f.pth"
LABELS = str(ROOT / "healthmate_worker" / "models" / "kinetics400_labels.txt")
VIDEO_ROOT = Path(r"D:\HealthMateData\motion\raw\rehab24-6")


def main() -> int:
    t0 = time.time()
    model = SlowFastKinetics400(CHECKPOINT, LABELS)
    print(f"model loaded in {time.time() - t0:.1f}s (400 classes)")

    for ex in sorted(VIDEO_ROOT.glob("Ex*")):
        video = next(
            (p for p in ex.rglob("*") if p.suffix.lower() in {".mp4", ".avi", ".mov"}),
            None,
        )
        if video is None:
            continue
        t1 = time.time()
        clip, frame_count = build_clip(str(video))
        result = model.predict(clip, topk=3)
        elapsed = time.time() - t1
        tops = ", ".join(
            f"{c['label']} {c['probability']:.2f}" for c in result["candidates"]
        )
        print(
            f"{ex.name} ({frame_count} frames, {elapsed:.1f}s): {tops}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
