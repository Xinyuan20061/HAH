"""Diet / exercise record endpoints (spec §5.3, §5.4, §6.3).

Contract highlights:

* ``GET /diet/records`` is cursor-paginated and filterable by date range and meal
  type; the ordering key is ``recorded_at DESC, id DESC``.
* ``GET /diet/records/{id}`` returns one owned record; a foreign or missing id is
  always 404 so ids cannot be enumerated across accounts.
* ``PATCH /diet/records/{id}`` requires the current integer ``version`` and
  updates the *existing* timeline row instead of appending a second event.
* ``DELETE /diet/records/{id}`` clears the timeline row, marks its
  ``FoodAnalysisSession`` as ``record_deleted`` and writes a business audit row.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time
from datetime import time as dtime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, delete as sql_delete, or_, select, update
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.database import get_db
from app.core.pagination import InvalidCursor, decode_cursor, encode_cursor
from app.core.time import BUSINESS_TZ, naive_utc, utc_now
from app.models import (
    AgentActionAudit,
    DietRecord,
    ExerciseRecord,
    FoodAnalysisSession,
    HealthTimelineEvent,
)
from app.schemas.errors import ApiException
from app.schemas.records import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    DietIn,
    DietOut,
    DietRecordPage,
    DietRecordPatch,
    ExerciseIn,
    ExerciseOut,
    MEAL_TYPES,
)
from app.services.evaluation import record_metric
from app.services.health_state.invalidation import (
    SOURCE_DIET,
    SOURCE_EXERCISE,
    record_changed,
)
from app.services.timeline import add_event

router = APIRouter(tags=["records"])

SOURCE_LABELS = {
    "manual": "手动记录",
    "ai_vision": "图片估算，已确认",
    "ai_vision_corrected": "图片估算，已人工修改",
    "legacy": "历史记录",
}


def _not_found() -> ApiException:
    # Cross-user resources are indistinguishable from missing ones (spec §5.2).
    return ApiException(404, "DIET_RECORD_NOT_FOUND", "饮食记录不存在")


def _timeline_payload(record: DietRecord) -> dict:
    return {
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
        "items": record.items,
        "source": record.source,
        "vision_analysis_id": record.vision_analysis_id,
    }


def _view(record: DietRecord) -> DietOut:
    out = DietOut.model_validate(record)
    out.source_label = SOURCE_LABELS.get(record.source, "手动记录")
    return out


def _day_bounds(value: date) -> tuple[datetime, datetime]:
    """Interpret a YYYY-MM-DD filter in the user's business timezone (UTC+8)."""
    start = datetime.combine(value, dtime.min, tzinfo=BUSINESS_TZ)
    end = datetime.combine(value, dtime.max, tzinfo=BUSINESS_TZ)
    return naive_utc(start), naive_utc(end)


def _parse_date(raw: str | None, field: str) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ApiException(
            422,
            "VALIDATION_ERROR",
            "日期格式应为 YYYY-MM-DD",
            details={"field": field},
        ) from None


@router.post("/diet/records", response_model=DietOut)
def add_diet(body: DietIn, user=Depends(current_user), db: Session = Depends(get_db)):
    data = body.model_dump(exclude={"items"})
    record = DietRecord(
        user_id=user.id,
        **data,
        items_json=json.dumps(
            [item.model_dump() for item in body.items], ensure_ascii=False
        ),
    )
    db.add(record)
    db.flush()
    add_event(
        db,
        user.id,
        "diet",
        _timeline_payload(record),
        source=record.source,
        ref_type="diet",
        ref_id=record.id,
        occurred_at=record.recorded_at,
    )
    db.commit()
    db.refresh(record)
    # Capability plan §4.5: the persisted features derived from diet records are now
    # stale and are removed before anything reads them again.
    invalidated = record_changed(db, user.id, SOURCE_DIET)
    view = _view(record)
    view.state_invalidated = list(invalidated.get("affected", []))
    return view


@router.get("/diet/records", response_model=DietRecordPage)
def list_diet(
    user=Depends(current_user),
    db: Session = Depends(get_db),
    limit: int | None = Query(default=None),
    cursor: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    meal_type: str | None = Query(default=None),
):
    if limit is None:
        effective = DEFAULT_PAGE_LIMIT
    elif limit < 1 or limit > MAX_PAGE_LIMIT:
        raise ApiException(
            422,
            "VALIDATION_ERROR",
            f"limit 必须在 1-{MAX_PAGE_LIMIT} 之间",
            details={"limit": limit, "max_limit": MAX_PAGE_LIMIT},
        )
    else:
        effective = limit

    if meal_type is not None and meal_type not in MEAL_TYPES:
        raise ApiException(422, "VALIDATION_ERROR", "餐次不在允许范围内")

    stmt = select(DietRecord).where(DietRecord.user_id == user.id)

    start = _parse_date(date_from, "date_from")
    if start is not None:
        stmt = stmt.where(DietRecord.recorded_at >= _day_bounds(start)[0])
    end = _parse_date(date_to, "date_to")
    if end is not None:
        stmt = stmt.where(DietRecord.recorded_at <= _day_bounds(end)[1])
    if meal_type is not None:
        stmt = stmt.where(DietRecord.meal_type == meal_type)

    if cursor:
        try:
            sort_value, row_id = decode_cursor(cursor)
        except InvalidCursor as exc:
            raise ApiException(422, "INVALID_CURSOR", str(exc)) from None
        # Strictly "after" the cursor position in (recorded_at DESC, id DESC).
        stmt = stmt.where(
            or_(
                DietRecord.recorded_at < sort_value,
                and_(
                    DietRecord.recorded_at == sort_value, DietRecord.id < row_id
                ),
            )
        )

    rows = db.scalars(
        stmt.order_by(DietRecord.recorded_at.desc(), DietRecord.id.desc()).limit(
            effective + 1
        )
    ).all()
    has_more = len(rows) > effective
    rows = rows[:effective]
    next_cursor = (
        encode_cursor(sort_value=rows[-1].recorded_at, row_id=rows[-1].id)
        if has_more and rows
        else None
    )
    return DietRecordPage(
        items=[_view(row) for row in rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/diet/records/{record_id}", response_model=DietOut)
def read_diet(
    record_id: int, user=Depends(current_user), db: Session = Depends(get_db)
):
    record = db.get(DietRecord, record_id)
    if record is None or record.user_id != user.id:
        raise _not_found()
    return _view(record)


@router.patch("/diet/records/{record_id}", response_model=DietOut)
def patch_diet(
    record_id: int,
    body: DietRecordPatch,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    record = db.get(DietRecord, record_id)
    if record is None or record.user_id != user.id:
        raise _not_found()

    submitted = body.model_dump(exclude_unset=True, exclude={"version", "items"})
    changed_fields = sorted(
        field
        for field, value in submitted.items()
        if getattr(record, field) != value
    )
    if body.items is not None:
        submitted["items_json"] = json.dumps(
            [item.model_dump() for item in body.items], ensure_ascii=False
        )
        changed_fields.append("items")

    if not changed_fields:
        return _view(record)

    submitted["version"] = record.version + 1
    submitted["updated_at"] = utc_now()
    # Reflect the accepted change on the in-memory row so the timeline payload
    # below describes the *new* state; the CAS guard is what protects the write.
    for field, value in submitted.items():
        if field not in {"version", "updated_at"}:
            setattr(record, field, value)
    # Compare-and-set: a stale version affects 0 rows and never overwrites a
    # change made in another page (spec §5.4).
    result = db.execute(
        update(DietRecord)
        .where(
            DietRecord.id == record_id,
            DietRecord.user_id == user.id,
            DietRecord.version == body.version,
        )
        .values(**submitted)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        db.rollback()
        current = db.get(DietRecord, record_id)
        raise ApiException(
            409,
            "DIET_RECORD_VERSION_CONFLICT",
            "记录已在其他页面修改，请刷新后重试",
            details={"current_version": current.version if current else None},
        )

    # Rewrite the existing timeline row; never append a second event.
    db.execute(
        update(HealthTimelineEvent)
        .where(
            HealthTimelineEvent.user_id == user.id,
            HealthTimelineEvent.ref_type == "diet",
            HealthTimelineEvent.ref_id == record_id,
            HealthTimelineEvent.event_type == "diet",
        )
        .values(
            payload_json=json.dumps(_timeline_payload(record), ensure_ascii=False),
            occurred_at=record.recorded_at,
            source=record.source,
            updated_at=utc_now(),
        )
        .execution_options(synchronize_session=False)
    )
    # Only the changed field names are recorded: never the new content.
    record_metric(
        db,
        user.id,
        "diet_record_updated",
        1,
        "count",
        "diet_record",
        True,
        {"changed_fields": changed_fields, "record_id": record_id},
    )
    db.commit()
    db.refresh(record)
    invalidated = record_changed(db, user.id, SOURCE_DIET)
    view = _view(record)
    view.state_invalidated = list(invalidated.get("affected", []))
    return view


@router.delete("/diet/records/{record_id}")
def delete_diet(
    record_id: int, user=Depends(current_user), db: Session = Depends(get_db)
):
    record = db.get(DietRecord, record_id)
    if record is None or record.user_id != user.id:
        raise _not_found()

    # A source analysis must never be able to finalize onto a deleted record.
    analyses = db.scalars(
        select(FoodAnalysisSession).where(
            FoodAnalysisSession.finalized_record_id == record.id
        )
    ).all()
    for analysis in analyses:
        analysis.finalized_record_id = None
        analysis.status = "record_deleted"
        db.add(analysis)
    # The record's own timeline rows go away with it (spec §6.3.4).
    db.execute(
        sql_delete(HealthTimelineEvent).where(
            HealthTimelineEvent.user_id == user.id,
            HealthTimelineEvent.ref_type == "diet",
            HealthTimelineEvent.ref_id == record.id,
        )
    )
    audit = AgentActionAudit(
        user_id=user.id,
        action_key="diet.record.delete",
        risk_level="medium",
        requires_confirmation=True,
        status="executed",
        input_json=json.dumps(
            {"record_id": record.id, "meal_type": record.meal_type},
            ensure_ascii=False,
        ),
        output_json=json.dumps({"deleted": True}),
    )
    db.add(audit)
    db.flush()
    add_event(
        db,
        user.id,
        "diet_deleted",
        {"record_id": record.id, "name": record.name},
        source="user",
        ref_type="diet_deleted",
        ref_id=record.id,
    )
    db.delete(record)
    db.commit()
    invalidated = record_changed(db, user.id, SOURCE_DIET)
    return {
        "ok": True,
        "record_id": record_id,
        "audit_id": audit.id,
        "state_invalidated": invalidated.get("affected", []),
    }


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
    record_changed(db, user.id, SOURCE_EXERCISE)
    return x


@router.get("/exercise/records", response_model=list[ExerciseOut])
def list_exercise(user=Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(
        select(ExerciseRecord)
        .where(ExerciseRecord.user_id == user.id)
        .order_by(ExerciseRecord.recorded_at.desc(), ExerciseRecord.id.desc())
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
        record_changed(db, user.id, SOURCE_EXERCISE)
    else:
        raise ApiException(404, "EXERCISE_RECORD_NOT_FOUND", "运动记录不存在")
    return {"ok": True}
