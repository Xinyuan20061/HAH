"""Interactive Food 2.0 endpoints (capability plan §6.3/§6.5).

Contract:

* ``GET  /food/analysis/{id}/questions`` — at most two questions, ranked by the
  expected reduction of the *specific* error they address; persisted for audit;
* ``POST /food/analysis/{id}/answers``   — record the answers, then recompute;
* ``GET  /food/references``              — the audited table's honest status;
* ``GET/PUT/DELETE /food/priors``        — personal portion priors (view/clear).
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.database import get_db
from app.core.time import utc_now
from app.models import FoodAnalysisSession, FoodClarificationQuestion, UserFoodPrior
from app.schemas.errors import ApiException
from app.services.food import (
    MAX_QUESTIONS,
    calculate,
    clear_prior,
    ensure_seed_table,
    list_priors,
    match_reference,
    persist_questions,
    propose_questions,
    record_confirmed_mass,
    table_status,
)

router = APIRouter(prefix="/food", tags=["food"])


class AnswerIn(BaseModel):
    question_id: str = Field(min_length=1, max_length=60)
    option_key: str = Field(min_length=1, max_length=60)


class AnswersIn(BaseModel):
    answers: list[AnswerIn] = Field(default_factory=list, max_length=MAX_QUESTIONS)


class PriorIn(BaseModel):
    food_key: str = Field(min_length=1, max_length=80)
    mass_g: float = Field(gt=0, le=5000)
    context_key: str = Field(default="default", max_length=60)


def _owned_session(db: Session, user_id: int, analysis_id: int) -> FoodAnalysisSession:
    session = db.get(FoodAnalysisSession, analysis_id)
    if session is None or session.user_id != user_id:
        raise ApiException(404, "FOOD_ANALYSIS_NOT_FOUND", "识餐记录不存在")
    return session


def _draft_items(session: FoodAnalysisSession) -> tuple[list[dict], bool, bool]:
    """Draft items plus (has_mass, cooking_known) derived from the draft itself."""
    try:
        payload = json.loads(
            (session.corrected_json or "") or (session.initial_json or "{}")
        )
    except (TypeError, ValueError):
        payload = {}
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        items = []
    has_mass = any(
        isinstance(item, dict) and float(item.get("weight_g") or 0) > 0 for item in items
    )
    cooking_known = bool(
        isinstance(payload, dict) and str(payload.get("cooking_method") or "").strip()
    )
    return [item for item in items if isinstance(item, dict)], has_mass, cooking_known


@router.get("/references")
def food_references(user=Depends(current_user), db: Session = Depends(get_db)):
    ensure_seed_table(db)
    status = table_status(db)
    return {
        **status,
        "max_questions": MAX_QUESTIONS,
        "note": (
            "最终营养值由本表 × 用户确认份量确定性计算；视觉模型的自由文本数值不参与计算。"
        ),
    }


@router.get("/lookup")
def food_lookup(
    name: str = Query(min_length=1, max_length=120),
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    """Exact/alias lookup only — an unmapped label stays unmapped."""
    ensure_seed_table(db)
    row = match_reference(db, name)
    if row is None:
        return {"matched": False, "reason": "not_in_audited_table"}
    return {
        "matched": True,
        "food_key": row.food_key,
        "name_zh": row.name_zh,
        "food_group": row.food_group,
        "per_100g": {
            "calories": row.calories_per_100g,
            "protein": row.protein_per_100g,
            "carbs": row.carbs_per_100g,
            "fat": row.fat_per_100g,
            "fiber": row.fiber_per_100g,
        },
        "reviewed": row.reviewed_at is not None,
    }


@router.get("/analysis/{analysis_id}/questions")
def analysis_questions(
    analysis_id: int, user=Depends(current_user), db: Session = Depends(get_db)
):
    ensure_seed_table(db)
    session = _owned_session(db, user.id, analysis_id)
    items, has_mass, cooking_known = _draft_items(session)
    plan = propose_questions(
        db,
        user_id=user.id,
        items=items,
        has_mass=has_mass,
        cooking_known=cooking_known,
    )
    rows = persist_questions(
        db, analysis_id=session.id, user_id=user.id, plan=plan
    )
    return {
        **plan.as_dict(),
        "analysis_id": session.id,
        "persisted": len(rows),
        "draft": {
            "item_count": len(items),
            "has_mass": has_mass,
            "cooking_known": cooking_known,
        },
    }


@router.post("/analysis/{analysis_id}/answers")
def submit_answers(
    analysis_id: int,
    body: AnswersIn,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    """Record answers and recompute nutrients deterministically."""
    ensure_seed_table(db)
    session = _owned_session(db, user.id, analysis_id)
    rows = {
        row.question_id: row
        for row in db.scalars(
            select(FoodClarificationQuestion).where(
                FoodClarificationQuestion.analysis_id == session.id
            )
        ).all()
    }
    applied: list[dict] = []
    mass_updates: list[tuple[str, float]] = []
    for answer in body.answers:
        row = rows.get(answer.question_id)
        if row is None:
            raise ApiException(
                404,
                "QUESTION_NOT_FOUND",
                "该问题不属于本次识餐",
                details={"proposal_id": answer.question_id},
            )
        valid = {str(option.get("key")) for option in row.options}
        if answer.option_key not in valid:
            raise ApiException(
                422,
                "INVALID_OPTION",
                "选项不在该问题的候选内",
                details={"field": answer.question_id},
            )
        row.answer_option_key = answer.option_key
        row.answered_at = utc_now()
        db.add(row)
        payload = next(
            (option for option in row.options if str(option.get("key")) == answer.option_key),
            {},
        )
        effect = (payload or {}).get("effect") or {}
        applied.append(
            {
                "question_id": answer.question_id,
                "kind": row.kind,
                "option_key": answer.option_key,
                "effect": effect,
            }
        )

    items, _has_mass, cooking_known = _draft_items(session)
    # Apply the answered mass factors to the draft before calculating.
    for index, item in enumerate(items):
        for entry in applied:
            effect = entry["effect"]
            factor = float(effect.get("mass_factor") or 1.0)
            if factor != 1.0 and float(item.get("weight_g") or 0) > 0:
                item["weight_g"] = round(float(item["weight_g"]) * factor, 1)
        items[index] = item
    db.commit()

    result = calculate(db, user_id=user.id, items=items)
    return {
        "ok": True,
        "analysis_id": session.id,
        "applied": applied,
        "calculation": result.as_dict(),
        "mass_updates": mass_updates,
    }


@router.post("/analysis/{analysis_id}/calculate")
def recalculate(
    analysis_id: int, user=Depends(current_user), db: Session = Depends(get_db)
):
    """Recompute without asking anything — the deterministic path on its own."""
    ensure_seed_table(db)
    session = _owned_session(db, user.id, analysis_id)
    items, _has_mass, cooking_known = _draft_items(session)
    draft = {}
    try:
        draft = json.loads((session.corrected_json or "") or (session.initial_json or "{}"))
    except (TypeError, ValueError):
        draft = {}
    cooking = str(draft.get("cooking_method") or "") if isinstance(draft, dict) else ""
    del cooking_known
    result = calculate(
        db, user_id=user.id, items=items, cooking_method=cooking
    )
    return {"ok": True, "analysis_id": session.id, "calculation": result.as_dict()}


@router.get("/priors")
def food_priors(user=Depends(current_user), db: Session = Depends(get_db)):
    return {
        "priors": list_priors(db, user.id),
        "note": (
            "个人份量先验仅在至少 3 次确认后影响初始建议，不跳过本次确认，"
            "也不改变食物库数值。"
        ),
    }


@router.post("/priors")
def add_food_prior(
    body: PriorIn, user=Depends(current_user), db: Session = Depends(get_db)
):
    ensure_seed_table(db)
    if match_reference(db, body.food_key) is None:
        raise ApiException(
            404, "FOOD_NOT_IN_TABLE", "食物不在审核食物库中，无法建立先验"
        )
    result = record_confirmed_mass(
        db,
        user_id=user.id,
        food_key=body.food_key,
        mass_g=body.mass_g,
        context_key=body.context_key,
    )
    return {"ok": bool(result.get("recorded")), **result}


@router.delete("/priors/{food_key}")
def delete_food_prior(
    food_key: str,
    user=Depends(current_user),
    db: Session = Depends(get_db),
    context_key: str = Query(default=""),
):
    removed = clear_prior(db, user.id, food_key, context_key or None)
    if not removed:
        raise ApiException(404, "PRIOR_NOT_FOUND", "没有这条个人份量记录")
    return {"ok": True, "removed": removed}
