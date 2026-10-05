"""P3 knowledge governance: migration 0039, deployment run markers, advisory conflicts."""

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import (
    KnowledgeClaim,
    KnowledgeConflictReview,
    KnowledgeDocument,
    KnowledgeReviewEvent,
)
from app.services.rag.governance import (
    claims_for_source_keys,
    conflict_status_for_claims,
    CONFLICT_NOTICES,
)
from app.services.rag.service import knowledge_snapshot_hash, retrieval_meta, search_knowledge


def _seed_documents(db: Session) -> list[str]:
    # Child tables first to satisfy foreign keys (claims/review events reference
    # knowledge_documents.source_key).
    db.execute(delete(KnowledgeReviewEvent))
    db.execute(delete(KnowledgeConflictReview))
    db.execute(delete(KnowledgeClaim))
    db.execute(delete(KnowledgeDocument))
    rows = [
        KnowledgeDocument(
            source_key="gov-who", title="成年人身体活动指南",
            organization="世界卫生组织", source_url="https://example.org/who",
            source_published_at="2020-01-01", section="成年人",
            content="成年人每周应进行中等强度有氧活动并配合力量训练。",
            tags_json='["每周运动","有氧","力量训练"]', active=True,
            population="一般成年人", exclusions="无", reviewer="审核人A",
            content_sha256="a" * 64,
        ),
        KnowledgeDocument(
            source_key="gov-diet", title="平衡膳食指南",
            organization="权威机构", source_url="https://example.org/diet",
            source_published_at="2022-01-01", section="膳食",
            content="食物多样，少盐少油。", tags_json='["膳食","营养"]', active=True,
            population="一般成年人", exclusions="无", reviewer="审核人A",
            content_sha256="b" * 64,
        ),
    ]
    db.add_all(rows)
    db.commit()
    return ["gov-who", "gov-diet"]


def test_migration_0039_creates_governance_tables_and_audit_columns(migrated_engine):
    from sqlalchemy import inspect
    with Session(migrated_engine) as db:
        tables = set(inspect(db.get_bind()).get_table_names())
        assert {"knowledge_claims", "knowledge_conflict_reviews", "knowledge_review_events"} <= tables
        assert db.scalar(select(KnowledgeDocument).limit(1)) is not None  # migration seeds exist
        cols = set(KnowledgeDocument.__table__.columns.keys())
        assert {"population", "exclusions", "reviewer", "review_expires_at", "content_sha256"} <= cols


def test_retrieval_meta_reports_deployment_backend_and_snapshot_hash(api, migrated_engine):
    with Session(migrated_engine) as db:
        _seed_documents(db)
        meta = retrieval_meta(db)
        assert meta["version"] == "audited_hybrid_v2"
        assert meta["embedding_backend"] in {"onnx_bge", "hash_fallback"}
        assert len(meta["knowledge_snapshot_hash"]) == 64
        assert meta["knowledge_snapshot_hash"] == knowledge_snapshot_hash(db)
        # changing an active chunk changes the snapshot hash
        before = meta["knowledge_snapshot_hash"]
        doc = db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.source_key == "gov-who"))
        doc.content = "成年人每周应进行中等强度有氧活动。"
        db.commit()
        assert knowledge_snapshot_hash(db) != before


def test_knowledge_search_api_carries_run_markers(api, migrated_engine):
    with Session(migrated_engine) as db:
        _seed_documents(db)
    resp = api.get("/api/v1/knowledge/search", params={"query": "每周运动多久", "limit": 3})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["retrieval"] == "audited_hybrid_v2"
    assert payload["embedding_backend"] in {"onnx_bge", "hash_fallback"}
    assert len(payload["knowledge_snapshot_hash"]) == 64
    assert "disclaimer" in payload
    # legacy field "retrieval" remains; old clients parse unchanged
    assert payload["items"] and payload["items"][0]["source_key"] in {"gov-who", "gov-diet"}


def test_serialized_document_carries_review_audit_fields(api, migrated_engine):
    with Session(migrated_engine) as db:
        _seed_documents(db)
        items = search_knowledge(db, "成年人力量训练", top_k=1)
    assert items
    first = items[0]
    assert first["reviewer"] == "审核人A"
    assert first["population"] == "一般成年人"
    assert first["exclusions"] == "无"
    assert len(first["content_sha256"]) == 64
    assert "review_expires_at" in first


def test_claims_and_conflict_status_advisory_only(migrated_engine):
    with Session(migrated_engine) as db:
        claim_a = KnowledgeClaim(
            claim_id="claim-a", source_key="gov-who",
            behavior="每周有氧活动", outcome="心肺健康改善", direction="increase",
            strength="moderate", qualifier="一般成年人", review_state="reviewed",
            reviewed_by="审核人A", version_hash="ha" * 32, active=True,
        )
        claim_b = KnowledgeClaim(
            claim_id="claim-b", source_key="gov-diet",
            behavior="少盐饮食", outcome="血压风险降低", direction="decrease",
            strength="moderate", qualifier="一般成年人", review_state="reviewed",
            reviewed_by="审核人A", version_hash="hb" * 32, active=True,
        )
        db.add_all([claim_a, claim_b])
        db.commit()
        ids = {claim_a.id, claim_b.id}
        db.add(KnowledgeConflictReview(
            claim_a_id=claim_a.id, claim_b_id=claim_b.id,
            conflict_status="potential_conflict", scope="训练建议",
            reviewed_by="审核人A"))
        db.commit()
        claims = claims_for_source_keys(db, ["gov-who", "gov-diet"])
        assert [c["claim_id"] for c in claims] == ["claim-a", "claim-b"]
        assert conflict_status_for_claims(db, claims) == "potential_conflict"
        assert CONFLICT_NOTICES["potential_conflict"]
        # no reviews at all -> not assessed (never claims proven-free)
        db.execute(delete(KnowledgeConflictReview))
        db.commit()
        assert conflict_status_for_claims(db, claims) == "not_assessed"


def test_knowledge_tool_returns_spec_audit_fields(migrated_engine):
    from app.harness.policy_tools import _knowledge
    from types import SimpleNamespace
    with Session(migrated_engine) as db:
        _seed_documents(db)
        ctx = SimpleNamespace(db=db)
        out = _knowledge(ctx, {"query": "每周运动多久", "template_id": "session_duration"})
    assert out["content_is_data"] is True
    assert out["retrieval"]["version"] == "audited_hybrid_v2"
    assert out["retrieval"]["embedding_backend"] in {"onnx_bge", "hash_fallback"}
    assert out["retrieval"]["knowledge_snapshot_hash"]
    assert out["allowed_use"] == "explanation_only"
    assert out["may_fill_personal_facts"] is False
    assert isinstance(out["claims"], list)
    assert out["conflict_status"] in {
        "not_assessed", "no_conflict_found_in_reviewed_claims",
        "potential_conflict", "reviewed_resolved", "insufficient_sources",
        "no_sources_to_compare",
    }
    assert "limits" in out
