"""Train and export the SlowFast-R50 six-action video recognition model.

Fine-tunes the official Kinetics-400 SlowFast R50 backbone (pure PyTorch
reimplementation of the MMAction2 architecture, key names aligned) on the
REHAB24-6 video clips. ``--pretrained`` is mandatory and enforced by the
training function: starting from scratch is refused by design.

Usage (with the dedicated training environment, e.g. C:\\HealthMateTraining\\train):
  Scripts\\python.exe scripts\\train_slowfast_six_action.py \
      benchmark\\rehab24_action_manifest.jsonl \
      --output benchmark-results\\motion-v2\\slowfast_r50_six_action.pt \
      --pretrained D:\\HealthMateData\\motion\\pretrained\\slowfast_r50_8xb8-8x8x1-steplr-256e_kinetics400-rgb_20220818-b62a501f.pth \
      --freeze head --epochs 12 --batch-size 4
"""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from healthmate_worker.models.training import train_slowfast_six_action


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pretrained", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--learning-rate", type=float, default=5e-4)
    parser.add_argument("--clip-frames", type=int, default=32)
    parser.add_argument("--input-size", type=int, default=224)
    parser.add_argument("--freeze", choices=("head", "partial", "none"), default="head")
    parser.add_argument("--device", default=None)
    parser.add_argument("--seed", type=int, default=20260923)
    args = parser.parse_args()
    result = train_slowfast_six_action(
        manifest_path=args.manifest,
        output_path=args.output,
        pretrained_path=args.pretrained,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        clip_frames=args.clip_frames,
        input_size=args.input_size,
        freeze=args.freeze,
        device=args.device,
        seed=args.seed,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
