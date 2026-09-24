from __future__ import annotations

import json
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExerciseResource


EXERCISE_KEYWORDS = {
    "squat": ("深蹲", "蹲", "squat"),
    "pushup": ("俯卧撑", "伏地挺身", "pushup", "push-up"),
    "lunge": ("弓步", "箭步", "lunge"),
}


def detect_exercise_types(query: str) -> list[str]:
    text = (query or "").lower()
    return [
        key
        for key, words in EXERCISE_KEYWORDS.items()
        if any(word in text for word in words)
    ]


def serialize_resource(item: ExerciseResource) -> dict:
    try:
        tags = json.loads(item.tags_json or "[]")
    except (TypeError, ValueError):
        tags = []
    return {
        "id": item.id,
        "exercise_type": item.exercise_type,
        "title": item.title,
        "platform": item.platform,
        "url": item.url,
        "difficulty": item.difficulty,
        "tags": tags if isinstance(tags, list) else [],
        "quality_score": item.quality_score,
        "summary": item.summary,
        "source": "curated_database",
    }


def recommend_resources(db: Session, query: str, limit: int = 3) -> list[dict]:
    exercise_types = detect_exercise_types(query)
    if not exercise_types:
        return []
    rows = db.scalars(
        select(ExerciseResource)
        .where(
            ExerciseResource.active.is_(True),
            ExerciseResource.exercise_type.in_(exercise_types),
        )
        .order_by(ExerciseResource.quality_score.desc(), ExerciseResource.id)
        .limit(max(1, min(limit, 10)))
    ).all()
    return [serialize_resource(item) for item in rows]
