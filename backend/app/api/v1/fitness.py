from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.database import get_db
from app.services.motion_profile import motion_history, motion_profile
from app.services.agent.tools import read_context
from app.services.training_semantics import (
    exercise_effect_profile,
    get_training_intent,
    motion_semantic_result,
    recommend_exercises,
    save_training_intent,
)
from app.services.dataset_registry import list_datasets
from app.services.model_governance import model_readiness

router = APIRouter(prefix="/fitness", tags=["fitness-profile"])


class TrainingIntentIn(BaseModel):
    target_body_parts: list[str] = Field(default_factory=list, max_length=8)
    goals: list[str] = Field(default_factory=list, max_length=5)
    constraints: list[str] = Field(default_factory=list, max_length=10)
    preferred_equipment: list[str] = Field(default_factory=list, max_length=10)
    notes: str = Field(default="", max_length=1000)


@router.get("/motion-profile")
def get_motion_profile(
    days: int = Query(default=30, ge=7, le=365),
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    return motion_profile(db, user.id, days)


@router.get("/motion-history")
def get_motion_history(
    limit: int = Query(default=20, ge=1, le=100),
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    return {"items": motion_history(db, user.id, limit)}


@router.get("/training-adjustment")
def get_training_adjustment(user=Depends(current_user), db: Session = Depends(get_db)):
    return read_context(db, user)["training_adjustment"]


@router.get("/training-intent")
def training_intent(user=Depends(current_user), db: Session = Depends(get_db)):
    return get_training_intent(db, user.id)


@router.put("/training-intent")
def update_training_intent(
    body: TrainingIntentIn,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    if any(len(item) > 80 for item in body.constraints + body.preferred_equipment):
        raise HTTPException(422, "限制条件或器械名称过长")
    try:
        return save_training_intent(
            db,
            user.id,
            target_body_parts=body.target_body_parts,
            goals=body.goals,
            constraints=body.constraints,
            preferred_equipment=body.preferred_equipment,
            notes=body.notes,
        )
    except ValueError:
        raise HTTPException(422, "训练部位或目标不在当前知识本体中") from None


@router.get("/exercise-effects/{exercise_type}")
def get_exercise_effects(
    exercise_type: str,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    result = exercise_effect_profile(db, exercise_type)
    if not result.get("available"):
        raise HTTPException(404, "动作效果知识尚未收录")
    return result


@router.get("/exercise-recommendations")
def get_exercise_recommendations(
    limit: int = Query(default=6, ge=1, le=20),
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    return recommend_exercises(db, user.id, limit)


@router.get("/motion-semantics/{job_id}")
def get_motion_semantics(
    job_id: int,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    result = motion_semantic_result(db, user.id, job_id)
    if not result:
        raise HTTPException(404, "动作语义结果不存在")
    return result


@router.get("/dataset-registry")
def get_dataset_registry(user=Depends(current_user), db: Session = Depends(get_db)):
    return list_datasets(db)


@router.get("/model-readiness")
def get_model_readiness(user=Depends(current_user), db: Session = Depends(get_db)):
    return model_readiness(db)
