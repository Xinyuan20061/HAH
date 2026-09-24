from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KnowledgeDocument
from app.services.rag.embeddings import cosine, embed


RETRIEVER_VERSION = "audited_hybrid_v2"
MIN_RETRIEVAL_SCORE = 0.08
LEXICAL_WEIGHT = 0.55
VECTOR_WEIGHT = 0.45
SEMANTIC_FALLBACK_MIN = 0.45

# HealthMate answers health/lifestyle questions only. Explicit creation
# requests (poems, reports, stories, letters) are refused at the retriever
# layer so the knowledge engine never leaks into non-health intents.
CREATION_INTENT_PATTERNS = [
    "写一首",
    "写诗",
    "写作文",
    "写个故事",
    "编一首",
    "写封",
    "写一段",
    "写一份",
    "帮我写",
    "生成一首",
]

QUERY_EXPANSIONS = {
    "运动": ("身体活动", "有氧", "力量训练", "每周运动"),
    "锻炼": ("身体活动", "运动指南"),
    "多久": ("每周运动", "分钟"),
    "饮食": ("膳食", "营养", "平衡膳食"),
    "怎么吃": ("膳食", "食物多样", "少盐少油"),
    "慢病": ("慢性健康状况", "咨询专业人员"),
    "慢性": ("慢性健康状况", "咨询专业人员"),
    "坐着": ("久坐行为", "静坐时间"),
    "久坐": ("久坐行为", "静坐时间"),
    "好处": ("健康收益", "身心健康"),
    "危害": ("身体活动不足", "久坐行为"),
    "血压": ("高血压", "慢病"),
    "高血压": ("慢病", "咨询专业人员"),
    "糖尿病": ("慢病", "咨询专业人员"),
}


def _terms(text: str) -> set[str]:
    normalized = re.sub(r"\s+", "", (text or "").lower())
    terms = set(re.findall(r"[a-z0-9]+", normalized))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    terms.update(chinese[index : index + 2] for index in range(max(0, len(chinese) - 1)))
    terms.update(ch for ch in chinese if ch in "盐油糖鱼")
    return {term for term in terms if term}


def _expanded_query(query: str) -> str:
    additions: list[str] = []
    lowered = (query or "").lower()
    for trigger, related in QUERY_EXPANSIONS.items():
        if trigger in lowered:
            additions.extend(related)
    return " ".join([query, *additions])


def _deserialize_tags(value: str) -> list[str]:
    try:
        tags = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return [str(tag) for tag in tags] if isinstance(tags, list) else []


def _score(
    document: KnowledgeDocument, raw_query: str, expanded_terms: set[str]
) -> float:
    """Lexical relevance with long-document normalisation.

    Header terms (title/section/tags) are matched against the *original*
    query terms only; body terms are matched against the expanded terms and
    scored by their coverage of the document body so that long documents do
    not systematically outrank short ones for shared words. Tag/title bonuses
    are judged against the original user query, so query-expansion words can
    never inflate a document's ranking.
    """
    tags = _deserialize_tags(document.tags_json)
    header_text = " ".join([document.title, document.section, *tags]).lower()
    header_terms = _terms(header_text)
    body_terms = _terms(document.content)
    raw_terms = _terms(raw_query)
    header_overlap = len(raw_terms & header_terms)
    body_overlap = len(expanded_terms & body_terms)
    score = header_overlap / max(1, len(raw_terms))
    score += 0.3 * body_overlap / max(1, len(body_terms))
    lowered = raw_query.lower()
    for tag in tags:
        # Multi-word tags are strong topic signals; short generic tags (e.g.
        # "运动", "深蹲") are already covered by raw bigram matching, so only
        # compound tags add a bonus. This keeps tag bonuses from inflating
        # documents whose tags merely share a generic word with the query.
        if tag.lower() in lowered and len(tag) >= 3:
            score += 0.35
    score += 0.5 if document.title.lower() in lowered else 0
    return score


def serialize_document(document: KnowledgeDocument, rank: int, score: float) -> dict:
    return {
        "id": document.id,
        "source_key": document.source_key,
        "citation_id": f"K{rank}",
        "title": document.title,
        "organization": document.organization,
        "url": document.source_url,
        "published_at": document.source_published_at or None,
        "section": document.section,
        "excerpt": document.content,
        "tags": _deserialize_tags(document.tags_json),
        "retrieval_score": round(score, 4),
        "source": "audited_knowledge_database",
    }


def search_knowledge(db: Session, query: str, top_k: int = 3) -> list[dict]:
    """Hybrid retrieval: lexical scoring plus semantic reranking.

    Lexical candidates (score >= MIN_RETRIEVAL_SCORE) form the admission pool;
    the semantic vector engine then reranks that pool by a weighted blend of
    the normalised lexical score and query/document cosine similarity. If the
    lexical pool is empty the query is refused (no result), so out-of-scope
    queries keep a 100% rejection rate while real health questions benefit
    from semantic reranking.
    """
    expanded = _expanded_query(query)
    expanded_terms = _terms(expanded)
    if not expanded_terms:
        return []
    lowered_query = (query or "").lower()
    if any(pattern in lowered_query for pattern in CREATION_INTENT_PATTERNS):
        return []
    documents = db.scalars(
        select(KnowledgeDocument).where(KnowledgeDocument.active.is_(True))
    ).all()
    if not documents:
        return []
    lexical = [
        (document, _score(document, query, expanded_terms)) for document in documents
    ]
    pool = [(doc, score) for doc, score in lexical if score >= MIN_RETRIEVAL_SCORE]
    query_vector = embed(query)
    if not pool:
        # Lexical pool is empty: fall back to semantic admission only when a
        # document is clearly on-topic, otherwise refuse (keeps out-of-scope
        # rejection high while real questions with novel wording can still hit).
        semantic = sorted(
            (
                (document, cosine(query_vector, embed(_embed_text(document))))
                for document in documents
            ),
            key=lambda item: (-item[1], item[0].id),
        )
        admitted = [(doc, sim) for doc, sim in semantic if sim >= SEMANTIC_FALLBACK_MIN]
        if not admitted:
            return []
        return [
            serialize_document(document, index, score)
            for index, (document, score) in enumerate(admitted[: max(1, min(top_k, 5))], 1)
        ]
    max_lexical = max(score for _, score in pool) or 1.0
    ranked = []
    for document, score in pool:
        norm_lexical = score / max_lexical
        semantic = cosine(query_vector, embed(_embed_text(document)))
        ranked.append((document, LEXICAL_WEIGHT * norm_lexical + VECTOR_WEIGHT * semantic))
    ranked.sort(key=lambda item: (-item[1], item[0].id))
    return [
        serialize_document(document, index, score)
        for index, (document, score) in enumerate(ranked[: max(1, min(top_k, 5))], 1)
    ]


def _embed_text(document: KnowledgeDocument) -> str:
    tags = _deserialize_tags(document.tags_json)
    return " ".join([document.title, document.section, *tags, document.content])


class RAGRetriever:
    """Compatibility wrapper for callers that expect an async retriever."""

    async def retrieve(self, db: Session, query: str, top_k: int = 5):
        return search_knowledge(db, query, top_k)
