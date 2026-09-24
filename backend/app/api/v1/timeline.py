from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.api.deps import current_user
from app.core.database import get_db
from app.services.timeline import (
    unified_timeline,
    timeline_daily,
    backfill_legacy_events,
)

router = APIRouter(prefix="/timeline", tags=["timeline"])


@router.get("")
def timeline(
    days: int = Query(7, ge=1, le=90),
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    return {"days": days, "items": unified_timeline(db, user.id, days)}


@router.get("/daily")
def daily(
    days: int = Query(7, ge=1, le=90),
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    return {
        "days": days,
        "items": timeline_daily(db, user.id, days),
        "missing_is_null": True,
    }


@router.post("/backfill")
def backfill(user=Depends(current_user), db: Session = Depends(get_db)):
    return {"ok": True, "created_events": backfill_legacy_events(db, user.id)}
