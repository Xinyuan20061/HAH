from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from app.api.deps import current_user
from app.core.database import get_db
from app.models import DietRecord, ExerciseRecord, FoodAnalysisSession
from app.services.timeline import add_event
from app.schemas.records import DietIn, DietOut, ExerciseIn, ExerciseOut

router = APIRouter(tags=["records"])


@router.post("/diet/records", response_model=DietOut)
def add_diet(body: DietIn, user=Depends(current_user), db: Session = Depends(get_db)):
    x = DietRecord(user_id=user.id, **body.model_dump())
    db.add(x)
    db.flush()
    add_event(
        db,
        user.id,
        "diet",
        body.model_dump(mode="json"),
        source=getattr(body, "source", "manual"),
        ref_type="diet",
        ref_id=x.id,
        occurred_at=x.recorded_at,
    )
    db.commit()
    db.refresh(x)
    return x


@router.get("/diet/records", response_model=list[DietOut])
def list_diet(user=Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(
        select(DietRecord)
        .where(DietRecord.user_id == user.id)
        .order_by(DietRecord.recorded_at.desc())
        .limit(50)
    ).all()


@router.delete("/diet/records/{record_id}")
def delete_diet(
    record_id: int, user=Depends(current_user), db: Session = Depends(get_db)
):
    x = db.get(DietRecord, record_id)
    if x and x.user_id == user.id:
        db.execute(
            update(FoodAnalysisSession)
            .where(FoodAnalysisSession.finalized_record_id == x.id)
            .values(finalized_record_id=None, status="record_deleted")
        )
        add_event(
            db,
            user.id,
            "diet_deleted",
            {"record_id": x.id, "name": x.name},
            source="user",
            ref_type="diet",
            ref_id=x.id,
        )
        db.delete(x)
        db.commit()
    else:
        raise HTTPException(404, "饮食记录不存在")
    return {"ok": True}


@router.post("/exercise/records", response_model=ExerciseOut)
def add_exercise(
    body: ExerciseIn, user=Depends(current_user), db: Session = Depends(get_db)
):
    x = ExerciseRecord(user_id=user.id, **body.model_dump())
    db.add(x)
    db.flush()
    add_event(
        db,
        user.id,
        "exercise",
        body.model_dump(mode="json"),
        ref_type="exercise",
        ref_id=x.id,
        occurred_at=x.recorded_at,
    )
    db.commit()
    db.refresh(x)
    return x


@router.get("/exercise/records", response_model=list[ExerciseOut])
def list_exercise(user=Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(
        select(ExerciseRecord)
        .where(ExerciseRecord.user_id == user.id)
        .order_by(ExerciseRecord.recorded_at.desc())
        .limit(50)
    ).all()


@router.delete("/exercise/records/{record_id}")
def delete_exercise(
    record_id: int, user=Depends(current_user), db: Session = Depends(get_db)
):
    x = db.get(ExerciseRecord, record_id)
    if x and x.user_id == user.id:
        add_event(
            db,
            user.id,
            "exercise_deleted",
            {"record_id": x.id, "name": x.name},
            source="user",
            ref_type="exercise",
            ref_id=x.id,
        )
        db.delete(x)
        db.commit()
    else:
        raise HTTPException(404, "运动记录不存在")
    return {"ok": True}
