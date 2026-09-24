from app.core.time import utc_iso
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.api.deps import current_user
from app.core.database import get_db
from app.models import EvaluationBenchmark
from app.services.evaluation import dashboard

router = APIRouter(prefix="/evaluation", tags=["evaluation"])


class BenchmarkIn(BaseModel):
    task_type: str = Field(min_length=1, max_length=60)
    metric_name: str = Field(min_length=1, max_length=100)
    value: float
    unit: str = Field(default="%", max_length=30)
    sample_size: int = Field(default=0, ge=0, le=1000000)
    notes: str = Field(default="", max_length=1000)


@router.get("/dashboard")
def get_dashboard(
    days: int = 30, user=Depends(current_user), db: Session = Depends(get_db)
):
    return dashboard(db, user.id, max(1, min(365, days)))


@router.post("/benchmarks")
def add_benchmark(
    body: BenchmarkIn, user=Depends(current_user), db: Session = Depends(get_db)
):
    item = EvaluationBenchmark(user_id=user.id, **body.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return {"ok": True, "id": item.id, "measured_at": utc_iso(item.created_at)}
