# -*- coding: utf-8 -*-
"""Merge shard predictions into predictions.jsonl and render the report.

Run after all three shards complete:
  ai-worker/.venv/Scripts/python.exe scripts/merge_kinetics_shards.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".." / "benchmark-results" / "motion-v2-kinetics"

SHARDS = [
    OUT / "shard1.jsonl",
    OUT / "shard2.jsonl",
    OUT / "shard3.jsonl",
    OUT / "shard4.jsonl",
    OUT / "shard5.jsonl",
    OUT / "shard6.jsonl",
]


def main() -> int:
    rows_by_id = {}
    merged_any = False
    for path in SHARDS:
        if not path.is_file():
            continue
        merged_any = True
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            prev = rows_by_id.get(row["sample_id"])
            # Keep a completed result over a failed one; later shards win ties.
            if prev is not None and prev.get("status") == "completed":
                continue
            if prev is not None and row.get("status") != "completed":
                continue
            rows_by_id[row["sample_id"]] = row
    if not merged_any:
        print("no shard files found", file=sys.stderr)
        return 2
    predictions = OUT / "predictions.jsonl"
    predictions.write_text(
        "".join(
            json.dumps(rows_by_id[sid], ensure_ascii=False) + "\n"
            for sid in sorted(rows_by_id)
        ),
        encoding="utf-8",
    )
    print(f"merged {len(rows_by_id)} samples -> {predictions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
