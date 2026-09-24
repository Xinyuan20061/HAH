import json
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy.orm import Session

from app.models import KnowledgeDocument
from app.services.rag.evaluation import evaluate_database, evaluate_rag_results, render_markdown


def source(key: str, complete: bool = True) -> dict:
    return {
        "source_key": key,
        "title": "权威资料",
        "organization": "权威机构",
        "url": "https://example.org/source" if complete else "http://example.org/source",
        "section": "核心信息",
        "excerpt": "审核过的知识摘要。",
    }


def cases():
    return [
        {
            "query_id": "positive-one",
            "query": "每周运动多久",
            "should_retrieve": True,
            "relevant_source_keys": ["source-a"],
            "category": "activity",
        },
        {
            "query_id": "positive-multiple",
            "query": "怎么保持健康",
            "should_retrieve": True,
            "relevant_source_keys": ["source-b", "source-c"],
            "category": "general",
        },
        {
            "query_id": "negative-rejected",
            "query": "Python怎么排序",
            "should_retrieve": False,
            "relevant_source_keys": [],
            "category": "out_of_scope",
        },
        {
            "query_id": "negative-false-positive",
            "query": "修理链条",
            "should_retrieve": False,
            "relevant_source_keys": [],
            "category": "out_of_scope",
        },
    ]


def test_rag_metrics_separate_positive_hits_and_negative_rejection():
    results = {
        "positive-one": [source("source-a")],
        "positive-multiple": [source("source-c"), source("unrelated")],
        "negative-rejected": [],
        "negative-false-positive": [source("unrelated")],
    }
    report = evaluate_rag_results(
        cases(), results, {"source-a", "source-b", "source-c", "unrelated"}, top_k=3
    )
    assert report["retrieval"] == {
        "hit_rate_at_1_pct": 100.0,
        "hit_rate_at_3_pct": 100.0,
        "mrr": 1.0,
        "mean_relevant_recall_at_3_pct": 75.0,
    }
    assert report["negative_queries"] == {
        "rejection_rate_pct": 50.0,
        "false_positive_rate_pct": 50.0,
    }
    assert report["source_integrity"]["complete_provenance_rate_pct"] == 100.0
    assert "模型回答事实正确率" in report["claims_not_measured"]


def test_rag_metrics_validate_queries_and_provenance():
    invalid = [
        {
            "query_id": "bad",
            "query": "健康",
            "should_retrieve": True,
            "relevant_source_keys": [],
        }
    ]
    with pytest.raises(ValueError, match="positive query requires"):
        evaluate_rag_results(invalid, {})
    report = evaluate_rag_results(
        [
            {
                "query_id": "q",
                "query": "膳食怎么搭配",
                "should_retrieve": True,
                "relevant_source_keys": ["diet"],
            }
        ],
        {"q": [source("diet", complete=False)]},
        {"diet"},
    )
    assert report["source_integrity"]["complete_provenance_rate_pct"] == 0.0


def test_rag_database_evaluation_uses_stable_source_keys(api, migrated_engine):
    with Session(migrated_engine) as db:
        db.add_all(
            [
                KnowledgeDocument(
                    source_key="who-test",
                    title="成年人身体活动指南",
                    organization="世界卫生组织",
                    source_url="https://example.org/who",
                    source_published_at="2020-01-01",
                    section="成年人",
                    content="成年人每周进行中等强度有氧活动，并进行力量训练。",
                    tags_json='["每周运动","有氧","力量训练"]',
                    active=True,
                ),
                KnowledgeDocument(
                    source_key="diet-test",
                    title="平衡膳食指南",
                    organization="权威机构",
                    source_url="https://example.org/diet",
                    source_published_at="2022-01-01",
                    section="膳食",
                    content="食物多样，少盐少油。",
                    tags_json='["膳食","营养"]',
                    active=True,
                ),
            ]
        )
        db.commit()
        report = evaluate_database(
            db,
            [
                {
                    "query_id": "weekly",
                    "query": "每周应该运动多久",
                    "should_retrieve": True,
                    "relevant_source_keys": ["who-test"],
                    "category": "activity",
                },
                {
                    "query_id": "code",
                    "query": "Python列表如何排序",
                    "should_retrieve": False,
                    "relevant_source_keys": [],
                    "category": "out_of_scope",
                },
            ],
            top_k=2,
        )
    assert report["retrieval"]["hit_rate_at_1_pct"] == 100.0
    assert report["negative_queries"]["rejection_rate_pct"] == 100.0
    assert report["knowledge_snapshot"]["active_document_count"] == 2
    assert len(report["knowledge_snapshot"]["sha256"]) == 64
    markdown = render_markdown(report)
    assert "检索命中不等于模型回答正确" in markdown


def test_rag_evaluation_cli_writes_reproducible_reports(api, migrated_engine, tmp_path):
    with Session(migrated_engine) as db:
        db.add(
            KnowledgeDocument(
                source_key="cli-who",
                title="每周身体活动指南",
                organization="世界卫生组织",
                source_url="https://example.org/cli-who",
                source_published_at="2020-01-01",
                section="成年人",
                content="成年人每周应进行中等强度身体活动。",
                tags_json='["每周运动","身体活动"]',
                active=True,
            )
        )
        db.commit()
    queries = tmp_path / "queries.jsonl"
    queries.write_text(
        json.dumps(
            {
                "query_id": "weekly",
                "query": "每周运动多久",
                "should_retrieve": True,
                "relevant_source_keys": ["cli-who"],
                "category": "activity",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "output"
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "evaluate_rag.py"),
            "--queries",
            str(queries),
            "--database-url",
            str(migrated_engine.url),
            "--output-dir",
            str(output),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["retrieval"]["hit_rate_at_1_pct"] == 100.0
    assert len(report["provenance"]["queries_sha256"]) == 64
    assert len(report["provenance"]["retriever_config_sha256"]) == 64
    assert "检索命中不等于模型回答正确" in (output / "report.md").read_text(
        encoding="utf-8"
    )
