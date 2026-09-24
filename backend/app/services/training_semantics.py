from __future__ import annotations

import json
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now, utc_iso
from app.models import (
    FitnessConcept,
    FitnessRelation,
    MotionSemanticAnalysis,
    UserTrainingIntent,
)


BODY_PARTS = {
    "chest": "胸部",
    "back": "背部",
    "shoulders": "肩部",
    "arms": "手臂",
    "core": "核心",
    "quadriceps": "股四头肌",
    "glutes": "臀部",
    "hamstrings": "腘绳肌",
}
TRAINING_GOALS = {
    "strength": "力量",
    "hypertrophy": "增肌",
    "muscular_endurance": "肌耐力",
    "balance": "平衡与稳定",
    "core_stability": "核心稳定",
}
EXERCISE_KEYS = {
    "squat": "exercise:squat",
    "pushup": "exercise:pushup",
    "lunge": "exercise:lunge",
    "leg_abduction": "exercise:leg_abduction",
    "arm_abduction": "exercise:arm_abduction",
    "arm_vw": "exercise:arm_vw",
}


def _loads_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return [str(item) for item in parsed if isinstance(item, str)] if isinstance(parsed, list) else []


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def intent_catalog() -> dict:
    return {
        "body_parts": [{"key": key, "name": name} for key, name in BODY_PARTS.items()],
        "goals": [{"key": key, "name": name} for key, name in TRAINING_GOALS.items()],
        "notice": "训练意图由用户确认；系统不会仅凭视频猜测主观意图。",
    }


def get_training_intent(db: Session, user_id: int) -> dict:
    row = db.scalar(select(UserTrainingIntent).where(UserTrainingIntent.user_id == user_id))
    if not row:
        return {
            "confirmed": False,
            "target_body_parts": [],
            "goals": [],
            "constraints": [],
            "preferred_equipment": [],
            "notes": "",
            "source": None,
            "confirmed_at": None,
            "catalog": intent_catalog(),
        }
    return {
        "confirmed": True,
        "target_body_parts": _loads_list(row.target_body_parts_json),
        "goals": _loads_list(row.goals_json),
        "constraints": _loads_list(row.constraints_json),
        "preferred_equipment": _loads_list(row.preferred_equipment_json),
        "notes": row.notes,
        "source": row.source,
        "confirmed_at": utc_iso(row.confirmed_at),
        "catalog": intent_catalog(),
    }


def save_training_intent(
    db: Session,
    user_id: int,
    *,
    target_body_parts: list[str],
    goals: list[str],
    constraints: list[str],
    preferred_equipment: list[str],
    notes: str,
) -> dict:
    targets = list(dict.fromkeys(target_body_parts))
    normalized_goals = list(dict.fromkeys(goals))
    invalid_targets = sorted(set(targets) - set(BODY_PARTS))
    invalid_goals = sorted(set(normalized_goals) - set(TRAINING_GOALS))
    if invalid_targets or invalid_goals:
        raise ValueError("unsupported training intent value")
    row = db.scalar(select(UserTrainingIntent).where(UserTrainingIntent.user_id == user_id))
    if not row:
        row = UserTrainingIntent(user_id=user_id)
        db.add(row)
    row.target_body_parts_json = _dump(targets)
    row.goals_json = _dump(normalized_goals)
    row.constraints_json = _dump(list(dict.fromkeys(constraints)))
    row.preferred_equipment_json = _dump(list(dict.fromkeys(preferred_equipment)))
    row.notes = notes.strip()[:1000]
    row.source = "user_confirmed"
    row.confirmed_at = utc_now()
    db.commit()
    db.refresh(row)
    return get_training_intent(db, user_id)


def _concepts(db: Session, keys: set[str]) -> dict[str, FitnessConcept]:
    if not keys:
        return {}
    rows = db.scalars(
        select(FitnessConcept).where(
            FitnessConcept.concept_key.in_(keys), FitnessConcept.active.is_(True)
        )
    ).all()
    return {row.concept_key: row for row in rows}


def exercise_effect_profile(db: Session, exercise: str) -> dict:
    exercise_key = exercise if exercise.startswith("exercise:") else f"exercise:{exercise}"
    source = db.scalar(
        select(FitnessConcept).where(
            FitnessConcept.concept_key == exercise_key,
            FitnessConcept.concept_type == "exercise",
            FitnessConcept.active.is_(True),
        )
    )
    if not source:
        return {
            "available": False,
            "exercise_key": exercise_key,
            "message": "知识图谱尚无该动作节点",
        }
    relations = db.scalars(
        select(FitnessRelation).where(
            FitnessRelation.source_key == exercise_key,
            FitnessRelation.active.is_(True),
        )
    ).all()
    concepts = _concepts(db, {row.target_key for row in relations})
    grouped: dict[str, list[dict]] = defaultdict(list)
    for relation in relations:
        target = concepts.get(relation.target_key)
        if not target:
            continue
        grouped[relation.relation_type].append(
            {
                "key": target.concept_key.split(":", 1)[-1],
                "concept_key": target.concept_key,
                "name": target.name_zh,
                "role": relation.role or None,
                "weight": round(float(relation.weight), 3),
                "review_status": relation.review_status,
                "source_url": relation.source_url,
            }
        )
    for values in grouped.values():
        values.sort(key=lambda item: (-item["weight"], item["concept_key"]))
    return {
        "available": True,
        "exercise_key": source.concept_key,
        "exercise_name": source.name_zh,
        "analyzer_support": source.analyzer_support,
        "target_body_parts": grouped.get("targets", []),
        "movement_patterns": grouped.get("has_pattern", []),
        "compatible_goals": grouped.get("supports_goal", []),
        "inference_scope": "知识图谱映射的可能训练部位与兼容目标，不是肌电测量或实际训练效果",
        "knowledge_status": "seed_pending_expert_review"
        if any(
            item["review_status"] != "reviewed"
            for items in grouped.values()
            for item in items
        )
        else "reviewed",
    }


def recommend_exercises(db: Session, user_id: int, limit: int = 6) -> dict:
    intent = get_training_intent(db, user_id)
    desired_targets = {f"body:{key}" for key in intent["target_body_parts"]}
    desired_goals = {f"goal:{key}" for key in intent["goals"]}
    desired = desired_targets | desired_goals
    if not desired:
        return {
            "intent": intent,
            "items": [],
            "message": "请先确认想练的部位或训练目标",
            "method": "fitness_knowledge_graph_v1",
        }
    relations = db.scalars(
        select(FitnessRelation).where(
            FitnessRelation.target_key.in_(desired), FitnessRelation.active.is_(True)
        )
    ).all()
    scores: dict[str, float] = defaultdict(float)
    reasons: dict[str, list[str]] = defaultdict(list)
    concepts = _concepts(db, {row.target_key for row in relations})
    for relation in relations:
        multiplier = 1.0 if relation.relation_type == "targets" else 0.55
        scores[relation.source_key] += float(relation.weight) * multiplier
        target = concepts.get(relation.target_key)
        if target:
            reasons[relation.source_key].append(
                ("目标部位：" if relation.relation_type == "targets" else "兼容目标：")
                + target.name_zh
            )
    exercise_nodes = _concepts(db, set(scores))
    ranked = sorted(scores, key=lambda key: (-scores[key], key))[: max(1, min(limit, 20))]
    items = []
    for key in ranked:
        node = exercise_nodes.get(key)
        if not node:
            continue
        items.append(
            {
                "exercise_key": key,
                "exercise_type": key.split(":", 1)[-1],
                "exercise_name": node.name_zh,
                "match_score": round(min(100, scores[key] * 55), 1),
                "reasons": list(dict.fromkeys(reasons[key])),
                "video_analysis_supported": node.analyzer_support,
            }
        )
    return {
        "intent": intent,
        "items": items,
        "message": "推荐来自结构化动作效果关系；训练强度、组次和个体限制仍需另行确认。",
        "method": "fitness_knowledge_graph_v1",
    }


def build_motion_semantics(
    db: Session,
    *,
    user_id: int,
    job_id: int,
    exercise_type: str | None,
    recognition_confidence: float,
    observed_semantics: dict | None = None,
) -> dict:
    profile = (
        exercise_effect_profile(db, EXERCISE_KEYS.get(exercise_type, exercise_type or ""))
        if exercise_type
        else {"available": False, "message": "动作未确认"}
    )
    intent = get_training_intent(db, user_id)
    actual_targets = {
        item["key"] for item in profile.get("target_body_parts", [])
    }
    actual_goals = {item["key"] for item in profile.get("compatible_goals", [])}
    desired_targets = set(intent["target_body_parts"])
    desired_goals = set(intent["goals"])
    target_matches = sorted(desired_targets & actual_targets)
    goal_matches = sorted(desired_goals & actual_goals)
    denominators = len(desired_targets) + len(desired_goals)
    matched = len(target_matches) + len(goal_matches)
    alignment_score = round(matched / denominators * 100, 1) if denominators else None
    if not profile.get("available"):
        status = "unrecognized"
    elif not intent["confirmed"] or not denominators:
        status = "intent_required"
    elif alignment_score >= 75:
        status = "aligned"
    elif alignment_score > 0:
        status = "partially_aligned"
    else:
        status = "not_aligned"
    alignment = {
        "status": status,
        "score": alignment_score,
        "matched_body_parts": [BODY_PARTS[key] for key in target_matches],
        "matched_goals": [TRAINING_GOALS[key] for key in goal_matches],
        "missing_body_parts": [BODY_PARTS[key] for key in sorted(desired_targets - actual_targets)],
        "missing_goals": [TRAINING_GOALS[key] for key in sorted(desired_goals - actual_goals)],
        "message": {
            "unrecognized": "动作尚未确认，无法与训练意图比较。",
            "intent_required": "请先确认想练的部位和训练目标。",
            "aligned": "当前动作与已确认训练意图高度匹配。",
            "partially_aligned": "当前动作覆盖了部分训练意图，可补充其他动作。",
            "not_aligned": "当前动作与已确认训练意图缺少直接匹配。",
        }[status],
    }
    result = {
        "available": bool(profile.get("available")),
        "exercise_key": profile.get("exercise_key"),
        "exercise_name": profile.get("exercise_name"),
        "movement_patterns": profile.get("movement_patterns", []),
        "target_body_parts": profile.get("target_body_parts", []),
        "training_effects": profile.get("compatible_goals", []),
        "goal_alignment": alignment,
        "method": "recognized_exercise_to_fitness_knowledge_graph_v1",
        "confidence": round(max(0.0, min(1.0, float(recognition_confidence))), 3),
        "disclaimer": "训练部位来自知识映射，不能替代肌电测量；实际效果还取决于负荷、组次、强度、恢复与个体差异。",
        "knowledge_status": profile.get("knowledge_status"),
        "observed_motion": observed_semantics or {},
    }
    row = db.scalar(
        select(MotionSemanticAnalysis).where(MotionSemanticAnalysis.job_id == job_id)
    )
    if not row:
        row = MotionSemanticAnalysis(user_id=user_id, job_id=job_id)
        db.add(row)
    row.exercise_key = result["exercise_key"] or ""
    row.movement_patterns_json = _dump(result["movement_patterns"])
    row.target_body_parts_json = _dump(result["target_body_parts"])
    row.training_effects_json = _dump(result["training_effects"])
    row.goal_alignment_json = _dump(alignment)
    row.observed_semantics_json = _dump(observed_semantics or {})
    row.method = result["method"]
    row.confidence = result["confidence"]
    return result


def motion_semantic_result(db: Session, user_id: int, job_id: int) -> dict | None:
    row = db.scalar(
        select(MotionSemanticAnalysis).where(
            MotionSemanticAnalysis.user_id == user_id,
            MotionSemanticAnalysis.job_id == job_id,
        )
    )
    if not row:
        return None
    return {
        "job_id": row.job_id,
        "exercise_key": row.exercise_key or None,
        "movement_patterns": json.loads(row.movement_patterns_json or "[]"),
        "target_body_parts": json.loads(row.target_body_parts_json or "[]"),
        "training_effects": json.loads(row.training_effects_json or "[]"),
        "goal_alignment": json.loads(row.goal_alignment_json or "{}"),
        "observed_motion": json.loads(row.observed_semantics_json or "{}"),
        "method": row.method,
        "confidence": row.confidence,
        "created_at": utc_iso(row.created_at),
    }
