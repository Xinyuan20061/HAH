from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.database import get_db
from app.services.exercise_resources import recommend_resources

router = APIRouter(prefix="/exercise-resources", tags=["exercise-resources"])


@router.get("")
def list_exercise_resources(
    query: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=3, ge=1, le=10),
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    return {
        "resources": recommend_resources(db, query, limit),
        "url_policy": "curated_database_only",
    }
