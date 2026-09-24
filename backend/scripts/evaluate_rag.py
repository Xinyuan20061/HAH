from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.core.database import build_engine
from app.services.rag.evaluation import (
    attach_provenance,
    evaluate_database,
    render_markdown,
)


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {error.msg}") from error
        if not isinstance(item, dict):
            raise ValueError(f"{path}:{line_number}: each line must be a JSON object")
        rows.append(item)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the active HealthMate knowledge retriever.")
    parser.add_argument("--queries", required=True, type=Path, help="Fixed JSONL query set")
    parser.add_argument("--database-url", help="Read-only benchmark database URL; defaults to app configuration")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=Path("rag-benchmark-results"))
    args = parser.parse_args()

    query_path = args.queries.resolve()
    query_bytes = query_path.read_bytes()
    cases = read_jsonl(query_path)
    engine = build_engine(args.database_url or settings.effective_database_url)
    try:
        with Session(engine) as db:
            report = evaluate_database(db, cases, args.top_k)
    finally:
        engine.dispose()
    attach_provenance(report, query_bytes)

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "report.json"
    markdown_path = output_dir / "report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))
    print(f"Reports: {json_path} | {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
