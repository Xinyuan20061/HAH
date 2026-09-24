from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


FIELDS = [
    "case_id",
    "expected_intent",
    "prompt",
    "response",
    "source_count",
    "reviewer_slot",
    "reviewer_id",
    "factuality",
    "citation_support",
    "actionability",
    "safety",
    "clarity",
    "critical_error",
    "notes",
]


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{line_number}: invalid JSON") from error
        if not isinstance(item, dict):
            raise ValueError(f"{path}:{line_number}: expected object")
        rows.append(item)
    return rows


def safe_cell(value) -> str:
    text = str(value or "")
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a blinded two-reviewer Agent quality worksheet.")
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--responses", type=Path, help="Optional JSONL with case_id/reply/knowledge_sources")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    cases = read_jsonl(args.cases)
    responses = {}
    if args.responses:
        for item in read_jsonl(args.responses):
            case_id = str(item.get("case_id") or "")
            if not case_id or case_id in responses:
                raise ValueError("responses 必须包含唯一 case_id")
            responses[case_id] = item

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for case in cases:
            case_id = str(case.get("case_id") or "")
            response = responses.get(case_id, {})
            sources = response.get("knowledge_sources") or []
            for slot in ("A", "B"):
                writer.writerow(
                    {
                        "case_id": safe_cell(case_id),
                        "expected_intent": safe_cell(case.get("expected_intent")),
                        "prompt": safe_cell(case.get("message")),
                        "response": safe_cell(response.get("reply")),
                        "source_count": len(sources) if isinstance(sources, list) else 0,
                        "reviewer_slot": slot,
                    }
                )
    print(f"Prepared {len(cases)} cases x 2 reviewers: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
