"""Versioned, template-scoped knowledge bindings for acquisition decisions.

The project measurement threshold is an engineering rule frozen in the user's
protocol. External health guidance can explain context and boundaries, but it
does not author or modify that threshold.
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KnowledgeDocument


KNOWLEDGE_REGISTRY_VERSION = "policy-knowledge-registry-v1"

TEMPLATE_KNOWLEDGE = {
    "session_duration": {
        "measurement_rule": {
            "rule_id": "session-duration-execution-and-paired-burden",
            "version": "1.0.0",
            "source_type": "engineering_measurement_rule",
            "definition": (
                "执行目标和配对观察要求来自用户确认并冻结的周期协议；"
                "执行达标不推出健康结果支持，外部指南不改写该协议门槛。"
            ),
            "applicability": "仅用于本项目 session_duration 周期的测量与证据门控",
            "exclusions": ["医学诊断", "训练处方", "个体健康效果概率"],
        },
        "external_guidance": [
            {
                "source_key": "who-pa-adults-2020",
                "applicability": "一般成年人身体活动背景说明",
                "exclusions": ["个体化医疗建议", "项目执行比例阈值", "个人负担观察值"],
            },
            {
                "source_key": "who-pa-general-2024",
                "applicability": "一般身体活动与减少久坐的背景说明",
                "exclusions": ["个体化医疗建议", "项目执行比例阈值", "个人负担观察值"],
            },
        ],
    }
}


def _canonical_hash(value: object) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def current_knowledge_contract(db: Session, template_id: str) -> dict:
    """Return the live reviewed binding and its availability, without mutation."""
    template = TEMPLATE_KNOWLEDGE.get(template_id)
    if template is None:
        body = {
            "schema_version": KNOWLEDGE_REGISTRY_VERSION,
            "template_id": template_id,
            "status": "unsupported",
            "measurement_rule": None,
            "external_guidance": [],
        }
        return {"contract": body, "contract_hash": _canonical_hash(body), "available": False}

    keys = [item["source_key"] for item in template["external_guidance"]]
    documents = db.scalars(select(KnowledgeDocument).where(
        KnowledgeDocument.source_key.in_(keys))).all()
    by_key = {document.source_key: document for document in documents}
    bound = []
    available = True
    for requirement in template["external_guidance"]:
        document = by_key.get(requirement["source_key"])
        if document is None:
            available = False
            bound.append({**requirement, "status": "missing", "content_hash": None,
                          "reviewed_at": None, "source_published_at": None,
                          "title": None, "organization": None, "source_url": None})
            continue
        content_hash = hashlib.sha256((document.content or "").encode("utf-8")).hexdigest()
        reviewed_at = document.reviewed_at.isoformat() if document.reviewed_at else None
        status = "active_reviewed" if document.active and document.reviewed_at else (
            "inactive" if not document.active else "unreviewed")
        available = available and status == "active_reviewed"
        bound.append({
            **requirement,
            "status": status,
            "source_key": document.source_key,
            "title": document.title,
            "organization": document.organization,
            "source_url": document.source_url,
            "source_published_at": document.source_published_at or None,
            "section": document.section,
            "content_hash": content_hash,
            "reviewed_at": reviewed_at,
        })

    body = {
        "schema_version": KNOWLEDGE_REGISTRY_VERSION,
        "template_id": template_id,
        "status": "available" if available else "blocked",
        "measurement_rule": template["measurement_rule"],
        "external_guidance": sorted(bound, key=lambda item: item["source_key"]),
        "may_fill_personal_facts": False,
        "may_change_frozen_thresholds": False,
    }
    return {"contract": body, "contract_hash": _canonical_hash(body),
            "available": available}
