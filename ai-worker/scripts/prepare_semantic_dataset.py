"""Validate a licensed local manifest and assign leakage-safe subject splits."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from healthmate_worker.datasets import assign_subject_splits, manifest_sha256, summarize_manifest


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("annotations", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seed", default="healthmate-v1")
    parser.add_argument("--unseen-family", action="append", default=[])
    args = parser.parse_args()
    rows = assign_subject_splits(
        read_jsonl(args.annotations), seed=args.seed, unseen_action_families=set(args.unseen_family)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({**summarize_manifest(rows), "sha256": manifest_sha256(rows)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
