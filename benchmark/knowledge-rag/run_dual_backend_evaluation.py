"""P3-B4: dual-backend RAG evaluation on the frozen 61-query set.

Runs the same frozen query set twice -- once with the ONNX semantic model
available, once with it forced unavailable (hash fallback). Reports retrieval
Hit@K, out-of-scope rejection and the ACTUAL embedding backend used per mode
(spec B1: a fallback run must never inherit ONNX-mode scores). Database is an
isolated SQLite migrated to head (seeded reviewed knowledge), never production.

Usage:
  backend/.venv/Scripts/python.exe benchmark/knowledge-rag/run_dual_backend_evaluation.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from unittest import mock

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPT_DIR.parents[1] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.services.rag import embeddings
from app.services.rag.evaluation import evaluate_database
from app.services.rag.service import retrieval_meta

SEED = 20261005


def read_jsonl(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _migrated_sqlite(tmp_root: pathlib.Path) -> str:
    url = f"sqlite:///{tmp_root / 'bench.db'}"
    # Run alembic in a subprocess with DATABASE_URL overridden: migrations/env.py
    # always reads settings.effective_database_url, which prefers this env var
    # over .env. This guarantees the benchmark NEVER touches production.
    env = {**os.environ, "DATABASE_URL": url.replace("%", "%%"), "ENV": "test"}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND_ROOT), env=env, check=True,
        capture_output=True,
    )
    return url


def run_mode(engine_url: str, queries: list[dict], *, force_fallback: bool) -> dict:
    context = (mock.patch.object(embeddings, "model_available", return_value=False)
               if force_fallback else nullcontext())
    with context:
        # Clear the embed cache so mode 2 cannot reuse mode 1 (ONNX) vectors;
        # otherwise the fallback scores would be fake.
        embeddings.embed.cache_clear()
        engine = create_engine(engine_url)
        with Session(engine) as db:
            # Warm up embedding backend so the reported backend matches the path
            # that actually produced the vectors.
            embeddings.embed("warmup")
            report = evaluate_database(db, queries, top_k=3)
            meta = retrieval_meta(db)
        engine.dispose()
    return {"report": report, "actual_embedding_backend": meta["embedding_backend"],
            "knowledge_snapshot_hash": meta["knowledge_snapshot_hash"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Dual-backend frozen RAG evaluation (P3-B4)")
    parser.add_argument("--queries", type=pathlib.Path,
                        default=pathlib.Path(__file__).resolve().parents[1] / "rag_queries.jsonl")
    parser.add_argument("--output-dir", type=pathlib.Path,
                        default=pathlib.Path(__file__).resolve().parent / "results" / f"seed-{SEED}")
    args = parser.parse_args()

    queries = read_jsonl(args.queries)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        url = _migrated_sqlite(pathlib.Path(tmp))
        onnx = run_mode(url, queries, force_fallback=False)
        fallback = run_mode(url, queries, force_fallback=True)

    query_hash = hashlib.sha256(
        json.dumps(queries, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    frozen = {
        "schema_version": "knowledge-rag-dual-backend-v1",
        "seed": SEED,
        "dataset": str(args.queries.resolve()),
        "query_hash": query_hash,
        "query_count": len(queries),
        "modes": {"onnx_requested": onnx, "hash_fallback_requested": fallback},
    }
    out = args.output_dir / "dual_backend_report.json"
    out.write_text(json.dumps(frozen, ensure_ascii=False, indent=2), encoding="utf-8")

    def metrics(mode: dict) -> dict:
        r = mode["report"]
        return {
            "actual_backend": mode["actual_embedding_backend"],
            "hit_rate_at_1_pct": r["retrieval"]["hit_rate_at_1_pct"],
            "hit_rate_at_3_pct": r["retrieval"]["hit_rate_at_3_pct"],
            "negative_rejection_rate_pct": r["negative_queries"]["rejection_rate_pct"],
            "active_documents": r["knowledge_snapshot"]["active_document_count"],
        }

    summary = {
        "query_hash": query_hash,
        "query_count": len(queries),
        "onnx": metrics(onnx),
        "hash_fallback": metrics(fallback),
        "note": "检索分数不等于最终回答准确率；最终回答双人盲评在 P3 交付文档另记。",
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
