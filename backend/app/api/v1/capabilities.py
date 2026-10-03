"""Gold-tier status and capability-honesty endpoints (capability plan §5.11/§13.6).

Read-only, and intentionally blunt: this is where the product's claim surface is
readable, so "Gold" can never be a marketing string the code does not back.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.database import get_db
from app.schemas.errors import ApiException
from app.services.motion.gold_eval import gold_status
from app.services.planning.library import PLANNING_ONLY_IDS
from app.services.food.references import table_status
from app.services.food.references import ensure_seed_table

router = APIRouter(prefix="/capabilities", tags=["capabilities"])


@router.get("/honesty")
def capability_honesty(user=Depends(current_user), db: Session = Depends(get_db)):
    """The single place that answers "what may we claim right now?"."""
    motion = gold_status(db)
    food = table_status(db)
    return {
        "motion": {
            "gold_exercises": motion["gold_exercises"],
            "evaluator_version": motion["evaluator_version"],
            "worker_package": motion["worker_package"],
            "policy": motion["policy"],
        },
        "food": {
            "entries": food["entries"],
            "reviewed": food["reviewed"],
            "reviewed_entries": food["reviewed_entries"],
            "policy": food["policy"],
        },
        "planning": {
            "planning_only_exercises": list(PLANNING_ONLY_IDS),
            "policy": (
                "planning-only 动作没有登记的测量器：可以排进计划与讲解，"
                "但不得显示数值评分，也不得据其自动加负荷。"
            ),
        },
        "claim_rules": [
            "未通过 Gold 门禁的动作只能称「基于可见画面的一般反馈」。",
            "食物库条目未逐项复核前只能称「粗略草稿」，不得称营养分析。",
            "未测量的能力一律视为不可用，并显示原因。",
            "自动化测试通过不等于模型准确率达标。",
        ],
    }


@router.get("/motion-gold")
def motion_gold_status(user=Depends(current_user), db: Session = Depends(get_db)):
    return gold_status(db)


@router.get("/food-table")
def food_table_status(user=Depends(current_user), db: Session = Depends(get_db)):
    ensure_seed_table(db)
    return table_status(db)


@router.post("/motion-gold/{analysis_id}/evaluate")
def evaluate_motion_gold(
    analysis_id: int, user=Depends(current_user), db: Session = Depends(get_db)
):
    """Run the Gold evaluator for one owned run and persist the gate result.

    Requires the worker package to be importable; otherwise the row records
    ``unavailable`` with the reason instead of guessing.
    """
    from app.models import MotionAnalysisRun
    from app.services.motion.gold_eval import evaluate_run_gold

    run = db.get(MotionAnalysisRun, analysis_id)
    if run is None or run.user_id != user.id:
        raise ApiException(404, "MOTION_ANALYSIS_NOT_FOUND", "动作分析不存在")
    row = evaluate_run_gold(db, run)
    return {
        "analysis_id": run.id,
        "tier": row.tier,
        "available": row.available,
        "reason": row.reason_unavailable,
        "reps": row.reps,
        "hold_seconds": row.hold_seconds,
        "gate": row.gate_json,
    }
