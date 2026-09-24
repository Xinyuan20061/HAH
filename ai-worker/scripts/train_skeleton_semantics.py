"""Train and export the optional ST-GCN-lite multi-label semantic model."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from healthmate_worker.models.training import train_semantic_model


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("dataset_root", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--sequence-frames", type=int, default=64)
    parser.add_argument("--hidden", type=int, default=64)
    parser.add_argument("--seed", type=int, default=20260921)
    args = parser.parse_args()
    result = train_semantic_model(
        manifest_path=args.manifest,
        dataset_root=args.dataset_root,
        output_path=args.output,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        sequence_frames=args.sequence_frames,
        hidden=args.hidden,
        seed=args.seed,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
