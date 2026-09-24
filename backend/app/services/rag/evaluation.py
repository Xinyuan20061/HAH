from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KnowledgeDocument
from app.services.rag.service import (
    MIN_RETRIEVAL_SCORE,
    QUERY_EXPANSIONS,
    RETRIEVER_VERSION,
    search_knowledge,
)


SCHEMA_VERSION = "healthmate-rag-benchmark-v1"


def _percent(numerator: int | float, denominator: int | float) -> float | None:
    return round(numerator / denominator * 100, 2) if denominator else None


def validate_cases(cases: list[dict]) -> None:
    seen: set[str] = set()
    for index, case in enumerate(cases, 1):
        query_id = case.get("query_id")
        if not isinstance(query_id, str) or not query_id.strip():
            raise ValueError(f"query line {index}: query_id must be a non-empty string")
        if query_id in seen:
            raise ValueError(f"query line {index}: duplicate query_id {query_id}")
        seen.add(query_id)
        if not isinstance(case.get("query"), str) or len(case["query"].strip()) < 2:
            raise ValueError(f"query line {index}: query must contain at least 2 characters")
        if not isinstance(case.get("should_retrieve"), bool):
            raise ValueError(f"query line {index}: should_retrieve must be boolean")
        relevant = case.get("relevant_source_keys", [])
        if not isinstance(relevant, list) or any(not isinstance(key, str) or not key for key in relevant):
            raise ValueError(f"query line {index}: relevant_source_keys must be a string list")
        if case["should_retrieve"] and not relevant:
            raise ValueError(f"query line {index}: positive query requires relevant_source_keys")
        if not case["should_retrieve"] and relevant:
            raise ValueError(f"query line {index}: negative query cannot have relevant_source_keys")
        if not isinstance(case.get("category", "general"), str):
            raise ValueError(f"query line {index}: category must be a string")


def _provenance_complete(item: dict) -> bool:
    url = item.get("url")
    return all(
        isinstance(item.get(field), str) and bool(item[field].strip())
        for field in ("source_key", "title", "organization", "section", "excerpt")
    ) and isinstance(url, str) and bool(re.match(r"^https://", url))


def evaluate_rag_results(
    cases: list[dict],
    results_by_query_id: dict[str, list[dict]],
    known_source_keys: set[str] | None = None,
    top_k: int = 3,
) -> dict:
    validate_cases(cases)
    if top_k < 1 or top_k > 20:
        raise ValueError("top_k must be between 1 and 20")
    known_source_keys = known_source_keys or set()
    expected_keys = {
        key for case in cases for key in case.get("relevant_source_keys", [])
    }
    unknown_expected = sorted(expected_keys - known_source_keys) if known_source_keys else []
    positives = [case for case in cases if case["should_retrieve"]]
    negatives = [case for case in cases if not case["should_retrieve"]]
    hits_at_1 = hits_at_k = 0
    reciprocal_ranks: list[float] = []
    relevant_recalls: list[float] = []
    rejected_negatives = 0
    provenance_total = provenance_complete = 0
    category_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"positive": 0, "hit": 0, "negative": 0, "rejected": 0}
    )
    details = []

    for case in cases:
        raw_results = results_by_query_id.get(case["query_id"], [])
        results = raw_results[:top_k] if isinstance(raw_results, list) else []
        retrieved_keys = [
            item.get("source_key")
            for item in results
            if isinstance(item, dict) and isinstance(item.get("source_key"), str)
        ]
        for item in results:
            if isinstance(item, dict):
                provenance_total += 1
                provenance_complete += int(_provenance_complete(item))
        category = case.get("category") or "general"
        relevant = set(case.get("relevant_source_keys", []))
        if case["should_retrieve"]:
            category_stats[category]["positive"] += 1
            matching_ranks = [index for index, key in enumerate(retrieved_keys, 1) if key in relevant]
            first_rank = min(matching_ranks) if matching_ranks else None
            hit = first_rank is not None
            hits_at_1 += int(first_rank == 1)
            hits_at_k += int(hit)
            category_stats[category]["hit"] += int(hit)
            reciprocal_ranks.append(1 / first_rank if first_rank else 0)
            relevant_recalls.append(len(relevant & set(retrieved_keys)) / len(relevant))
            detail = {
                "query_id": case["query_id"],
                "query": case["query"],
                "category": category,
                "should_retrieve": True,
                "relevant_source_keys": sorted(relevant),
                "retrieved_source_keys": retrieved_keys,
                "first_relevant_rank": first_rank,
                "hit": hit,
            }
        else:
            category_stats[category]["negative"] += 1
            rejected = len(results) == 0
            rejected_negatives += int(rejected)
            category_stats[category]["rejected"] += int(rejected)
            detail = {
                "query_id": case["query_id"],
                "query": case["query"],
                "category": category,
                "should_retrieve": False,
                "retrieved_source_keys": retrieved_keys,
                "rejected": rejected,
            }
        details.append(detail)

    by_category = {
        category: {
            **counts,
            "positive_hit_rate_pct": _percent(counts["hit"], counts["positive"]),
            "negative_rejection_rate_pct": _percent(counts["rejected"], counts["negative"]),
        }
        for category, counts in sorted(category_stats.items())
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "query_count": len(cases),
        "positive_query_count": len(positives),
        "negative_query_count": len(negatives),
        "top_k": top_k,
        "retrieval": {
            "hit_rate_at_1_pct": _percent(hits_at_1, len(positives)),
            f"hit_rate_at_{top_k}_pct": _percent(hits_at_k, len(positives)),
            "mrr": round(sum(reciprocal_ranks) / len(reciprocal_ranks), 4) if reciprocal_ranks else None,
            f"mean_relevant_recall_at_{top_k}_pct": round(sum(relevant_recalls) / len(relevant_recalls) * 100, 2) if relevant_recalls else None,
        },
        "negative_queries": {
            "rejection_rate_pct": _percent(rejected_negatives, len(negatives)),
            "false_positive_rate_pct": _percent(len(negatives) - rejected_negatives, len(negatives)),
        },
        "source_integrity": {
            "returned_item_count": provenance_total,
            "complete_provenance_rate_pct": _percent(provenance_complete, provenance_total),
            "unknown_expected_source_keys": unknown_expected,
        },
        "by_category": by_category,
        "details": details,
        "claims_not_measured": [
            "模型回答事实正确率",
            "引用片段是否充分支持模型最终表述",
            "医疗建议的个体适用性",
        ],
    }


def evaluate_database(db: Session, cases: list[dict], top_k: int = 3) -> dict:
    documents = db.scalars(
        select(KnowledgeDocument).where(KnowledgeDocument.active.is_(True))
    ).all()
    known_keys = {document.source_key for document in documents}
    results = {
        case["query_id"]: search_knowledge(db, case["query"], top_k)
        for case in cases
    }
    report = evaluate_rag_results(cases, results, known_keys, top_k)
    snapshot = [
        {
            "source_key": document.source_key,
            "title": document.title,
            "organization": document.organization,
            "url": document.source_url,
            "published_at": document.source_published_at,
            "section": document.section,
            "content": document.content,
            "tags_json": document.tags_json,
        }
        for document in sorted(documents, key=lambda item: item.source_key)
    ]
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    report["knowledge_snapshot"] = {
        "active_document_count": len(documents),
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "source_keys": sorted(known_keys),
    }
    return report


def render_markdown(report: dict) -> str:
    retrieval = report["retrieval"]
    negatives = report["negative_queries"]
    integrity = report["source_integrity"]
    top_k = report["top_k"]

    def show(value, suffix=""):
        return "暂无样本" if value is None else f"{value}{suffix}"

    lines = [
        "# HealthMate RAG离线评测报告",
        "",
        f"- 查询总数：{report['query_count']}",
        f"- 正向查询：{report['positive_query_count']}",
        f"- 无关查询：{report['negative_query_count']}",
        f"- 活跃知识条目：{report.get('knowledge_snapshot', {}).get('active_document_count', '未记录')}",
        "",
        "## 核心结果",
        "",
        "|指标|结果|",
        "|---|---:|",
        f"|Hit Rate@1|{show(retrieval['hit_rate_at_1_pct'], '%')}|",
        f"|Hit Rate@{top_k}|{show(retrieval[f'hit_rate_at_{top_k}_pct'], '%')}|",
        f"|MRR|{show(retrieval['mrr'])}|",
        f"|Mean Relevant Recall@{top_k}|{show(retrieval[f'mean_relevant_recall_at_{top_k}_pct'], '%')}|",
        f"|无关查询拒绝率|{show(negatives['rejection_rate_pct'], '%')}|",
        f"|无关查询误召回率|{show(negatives['false_positive_rate_pct'], '%')}|",
        f"|来源字段完整率|{show(integrity['complete_provenance_rate_pct'], '%')}|",
        "",
        "## 失败明细",
        "",
    ]
    failures = [
        item
        for item in report["details"]
        if (item["should_retrieve"] and not item["hit"])
        or (not item["should_retrieve"] and not item["rejected"])
    ]
    if failures:
        for item in failures:
            lines.append(
                f"- `{item['query_id']}`：召回 {', '.join(item.get('retrieved_source_keys', [])) or '空'}"
            )
    else:
        lines.append("- 当前查询集没有检索失败。")
    lines.extend(["", "## 本报告没有测量", ""])
    lines.extend(f"- {claim}" for claim in report["claims_not_measured"])
    provenance = report.get("provenance")
    if isinstance(provenance, dict):
        lines.extend(
            [
                "",
                "## 可复现信息",
                "",
                f"- 生成时间（UTC）：{provenance.get('generated_at', '')}",
                f"- 查询集SHA-256：`{provenance.get('queries_sha256', '')}`",
                f"- 知识快照SHA-256：`{report.get('knowledge_snapshot', {}).get('sha256', '')}`",
                f"- 检索器：{provenance.get('retriever', '')}",
                f"- 检索配置SHA-256：`{provenance.get('retriever_config_sha256', '')}`",
            ]
        )
    lines.extend(
        [
            "",
            "> 检索命中不等于模型回答正确；正式答辩还需对最终回答做人工事实与引用支持度评审。",
            "",
        ]
    )
    return "\n".join(lines)


def attach_provenance(report: dict, queries_bytes: bytes) -> dict:
    retriever_config = json.dumps(
        {
            "version": RETRIEVER_VERSION,
            "minimum_score": MIN_RETRIEVAL_SCORE,
            "query_expansions": QUERY_EXPANSIONS,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    report["provenance"] = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "queries_sha256": hashlib.sha256(queries_bytes).hexdigest(),
        "retriever": RETRIEVER_VERSION,
        "retriever_config_sha256": hashlib.sha256(
            retriever_config.encode("utf-8")
        ).hexdigest(),
    }
    return report
