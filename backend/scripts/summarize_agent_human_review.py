from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.agent.human_review import (
    aggregate_human_reviews,
    render_human_review_markdown,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and summarize a completed Agent human-review worksheet.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    with args.input.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    report = aggregate_human_reviews(rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "report.json"
    markdown_path = args.output_dir / "report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_human_review_markdown(report), encoding="utf-8")
    print(render_human_review_markdown(report))
    print(f"Reports: {json_path.resolve()} | {markdown_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
