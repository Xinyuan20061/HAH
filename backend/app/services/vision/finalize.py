"""Food-analysis finalize service (spec §6.4).

Extracted from the endpoint so both ``POST /vision/food-analysis/{id}/finalize``
and the ``diet.ai.finalize`` Action executor use exactly one implementation of
"confirm the corrected draft into a diet record".
"""

from __future__ import annotations

import json

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import DietRecord, FoodAnalysisSession
from app.schemas.errors import ApiException
from app.services.evaluation import record_metric
from app.services.timeline import add_event


def infer_meal_type() -> str:
    """Server-side *default only*; a stated user choice always wins (§6.5)."""
    hour = (utc_now().hour + 8) % 24  # business timezone (UTC+8)
    if 5 <= hour < 10:
        return "breakfast"
    if 10 <= hour < 15:
        return "lunch"
    if 17 <= hour < 21:
        return "dinner"
    return "snack"


def diet_record_snapshot(record: DietRecord) -> dict:
    """The minimal record view the client needs right after a write."""
    return {
        "id": record.id,
        "name": record.name,
        "meal_type": record.meal_type,
        "calories": record.calories,
        "protein": record.protein,
        "carbs": record.carbs,
        "fat": record.fat,
        "fiber": record.fiber,
        "portion": record.portion,
        "cooking_method": record.cooking_method,
        "weight_g": record.weight_g,
        "source": record.source,
        "vision_analysis_id": record.vision_analysis_id,
        "recorded_at": record.recorded_at.isoformat() + "Z"
        if record.recorded_at
        else None,
        "version": record.version,
    }


def _loads(value: str) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def finalize_food_analysis(
    db: Session,
    *,
    user_id: int,
    analysis_id: int,
    meal_type: str | None,
    confirmed: bool,
) -> dict:
    """Confirm a corrected draft into a diet record.

    Guarantees:
      * a repeated call returns ``already_finalized=true`` and the *same* record;
      * the response carries the record snapshot;
      * an explicit user confirmation is mandatory — the model can never finalize.
    """
    session = db.scalar(
        select(FoodAnalysisSession)
        .where(FoodAnalysisSession.id == analysis_id)
        .with_for_update()
    )
    if session is None or session.user_id != user_id:
        raise ApiException(404, "FOOD_ANALYSIS_NOT_FOUND", "识餐记录不存在")
    if session.status == "record_deleted":
        raise ApiException(
            409,
            "FOOD_ANALYSIS_RECORD_DELETED",
            "该识餐对应的记录已删除，请手动新增饮食记录",
        )
    if session.finalized_record_id:
        existing = db.get(DietRecord, session.finalized_record_id)
        return {
            "record": diet_record_snapshot(existing) if existing else None,
            "analysis_id": session.id,
            "already_finalized": True,
            "action_audit_id": None,
        }

    data = (
        _loads(session.corrected_json)
        if session.corrected_json
        else _loads(session.initial_json)
    )
    chosen_meal = meal_type or infer_meal_type()
    corrected = bool(session.corrected_json)

    def perform() -> dict:
        # Claim the session first: a concurrent confirm loses the CAS and reads
        # the winner's record instead of creating a duplicate.
        changed = db.execute(
            update(FoodAnalysisSession)
            .where(
                FoodAnalysisSession.id == session.id,
                FoodAnalysisSession.status.in_(["analyzed", "corrected"]),
            )
            .values(status="finalizing"),
            execution_options={"synchronize_session": False},
        ).rowcount
        if not changed:
            db.refresh(session)
            if session.finalized_record_id:
                winner = db.get(DietRecord, session.finalized_record_id)
                return {
                    "record": diet_record_snapshot(winner) if winner else None,
                    "analysis_id": session.id,
                    "already_finalized": True,
                }
            raise ApiException(
                409, "FOOD_ANALYSIS_FINALIZING", "识餐记录正在保存，请稍后查看"
            )

        items = data.get("items") or []
        record = DietRecord(
            user_id=user_id,
            name=str(data.get("dish_name") or "AI识别餐食")[:120],
            meal_type=chosen_meal,
            calories=float(data.get("calories") or 0),
            protein=float(data.get("protein") or 0),
            carbs=float(data.get("carbs") or 0),
            fat=float(data.get("fat") or 0),
            fiber=float(data.get("fiber") or 0),
            portion=str(data.get("portion") or "")[:120],
            cooking_method=str(data.get("cooking_method") or "")[:120],
            weight_g=float(data.get("weight_g") or data.get("estimated_weight_g") or 0),
            source="ai_vision_corrected" if corrected else "ai_vision",
            vision_analysis_id=session.id,
            items_json=json.dumps(items[:12], ensure_ascii=False),
        )
        db.add(record)
        db.flush()
        session.finalized_record_id = record.id
        session.status = "finalized"
        db.add(session)
        add_event(
            db,
            user_id,
            "diet",
            {
                "name": record.name,
                "meal_type": record.meal_type,
                "calories": record.calories,
                "protein": record.protein,
                "carbs": record.carbs,
                "fat": record.fat,
                "fiber": record.fiber,
                "portion": record.portion,
                "cooking_method": record.cooking_method,
                "weight_g": record.weight_g,
                "analysis_id": session.id,
                "item_count": len(items),
            },
            source=record.source,
            ref_type="diet",
            ref_id=record.id,
            occurred_at=record.recorded_at,
        )
        add_event(
            db,
            user_id,
            "food_analysis_finalized",
            {
                "analysis_id": session.id,
                "record_id": record.id,
                "corrected": corrected,
            },
            source="user",
            ref_type="food_analysis",
            ref_id=session.id,
        )
        record_metric(
            db,
            user_id,
            "food_finalized",
            1,
            "count",
            "vision_feedback",
            True,
            {"corrected": corrected, "meal_type": chosen_meal},
        )
        db.flush()
        return {
            "record": diet_record_snapshot(record),
            "analysis_id": session.id,
            "corrected": corrected,
        }

    from app.services.agent.actions import execute_action

    action = execute_action(
        db,
        user_id,
        "diet.ai.finalize",
        perform,
        confirmed=confirmed,
        source="user",
        run_id=None,
        input_data={
            "analysis_id": session.id,
            "dish_name": data.get("dish_name"),
            "meal_type": chosen_meal,
            "corrected": corrected,
        },
    )
    if not action["executed"]:
        return {"ok": False, **action}
    result = action["result"] or {}
    return {
        "ok": True,
        "already_finalized": bool(result.get("already_finalized", False)),
        "analysis_id": session.id,
        "action_audit_id": action["audit_id"],
        "record": result.get("record"),
    }
