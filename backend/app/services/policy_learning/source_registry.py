"""Server-side source resolvers for evidence-gated policy learning.

The mini-program may choose a record, but it cannot submit the value that is
allowed to update a belief.  Each resolver checks ownership, revision,
deletion/availability and the observation window, then derives the metric from
the current row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DietRecord, ExerciseRecord, HealthCheckIn, PlanTaskState


class SourceResolutionError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class ResolvedPoint:
    source_type: str
    source_id: str
    source_revision: int
    observed_at: datetime
    metric_key: str
    metric_version: str
    value: float
    evidence_grade: str  # observed | user_confirmed | self_report


def _source_id(raw: str, kind: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise SourceResolutionError("EVIDENCE_SOURCE_INVALID", "证据来源编号无效") from None
    if value < 1:
        raise SourceResolutionError("EVIDENCE_SOURCE_INVALID", "证据来源编号无效")
    return value


def _window_check(observed_at: datetime, window: tuple[datetime, datetime]) -> None:
    start, end = window
    if observed_at < start or observed_at > end:
        raise SourceResolutionError("EVIDENCE_SOURCE_OUTSIDE_WINDOW", "证据不在本周期观察窗口内")


def _revision(row, requested: int) -> int:
    current = int(getattr(row, "version", 1) or 1)
    if requested != current:
        raise SourceResolutionError("EVIDENCE_SOURCE_STALE", "这条记录已更新，请重新选择")
    return current


def _owned(db: Session, model, user_id: int, source_id: str):
    row = db.get(model, _source_id(source_id, model.__name__))
    if row is None or row.user_id != user_id:
        raise SourceResolutionError("EVIDENCE_SOURCE_NOT_FOUND", "证据来源不存在或不属于当前用户")
    return row


def _diet(db: Session, user_id: int, source_id: str, revision: int, metric: str, metric_version: str, window):
    row = _owned(db, DietRecord, user_id, source_id)
    rev = _revision(row, revision)
    _window_check(row.recorded_at, window)
    if metric == "record_burden":
        item_count = len(row.items)
        if item_count < 1:
            raise SourceResolutionError("EVIDENCE_SOURCE_UNAVAILABLE", "这条饮食记录没有可核验的食材明细")
        return ResolvedPoint("diet", str(row.id), rev, row.recorded_at, metric, metric_version, float(item_count), "observed")
    fields = {"calories": row.calories, "protein": row.protein, "carbs": row.carbs, "fat": row.fat, "fiber": row.fiber, "weight_g": row.weight_g}
    if metric not in fields:
        raise SourceResolutionError("EVIDENCE_METRIC_UNSUPPORTED", "该饮食记录不支持当前指标")
    return ResolvedPoint("diet", str(row.id), rev, row.recorded_at, metric, metric_version, float(fields[metric]), "observed")


def _exercise(db: Session, user_id: int, source_id: str, revision: int, metric: str, metric_version: str, window):
    row = _owned(db, ExerciseRecord, user_id, source_id)
    rev = _revision(row, revision)
    _window_check(row.recorded_at, window)
    fields = {"duration_min": row.duration_min, "calories_burned": row.calories_burned}
    if metric not in fields:
        raise SourceResolutionError("EVIDENCE_METRIC_UNSUPPORTED", "该运动记录不支持当前指标")
    return ResolvedPoint("exercise", str(row.id), rev, row.recorded_at, metric, metric_version, float(fields[metric]), "observed")


def _checkin(db: Session, user_id: int, source_id: str, revision: int, metric: str, metric_version: str, window):
    row = _owned(db, HealthCheckIn, user_id, source_id)
    rev = _revision(row, revision)
    # Check-ins store a business date rather than a timestamp.  Interpret it at
    # midnight UTC for the source contract; the client cannot override it.
    try:
        observed_at = datetime.fromisoformat(row.record_date)
    except (TypeError, ValueError):
        raise SourceResolutionError("EVIDENCE_SOURCE_INVALID", "打卡日期无效") from None
    _window_check(observed_at, window)
    fields = {"water_ml": row.water_ml, "sleep_hours": row.sleep_hours, "weight_kg": row.weight_kg, "steps": row.steps}
    if metric not in fields:
        raise SourceResolutionError("EVIDENCE_METRIC_UNSUPPORTED", "该打卡记录不支持当前指标")
    return ResolvedPoint("checkin", str(row.id), rev, observed_at, metric, metric_version, float(fields[metric]), "observed")


def _plan_task(db: Session, user_id: int, source_id: str, revision: int, metric: str, metric_version: str, window):
    row = _owned(db, PlanTaskState, user_id, source_id)
    rev = _revision(row, revision)
    try:
        observed_at = datetime.fromisoformat(row.record_date)
    except (TypeError, ValueError):
        raise SourceResolutionError("EVIDENCE_SOURCE_INVALID", "计划任务日期无效") from None
    _window_check(observed_at, window)
    if metric != "completion_fraction":
        raise SourceResolutionError("EVIDENCE_METRIC_UNSUPPORTED", "该计划任务不支持当前指标")
    return ResolvedPoint("plan_task", str(row.id), rev, observed_at, metric, metric_version, 1.0 if row.done else 0.0, "observed")


_RESOLVERS: dict[str, Callable] = {
    "diet": _diet,
    "exercise": _exercise,
    "checkin": _checkin,
    "plan_task": _plan_task,
}


def resolve_point(db: Session, user_id: int, ref, expected_metric: str, window: tuple[datetime, datetime]) -> ResolvedPoint:
    """Resolve one client-selected source through the reviewed allowlist."""
    if ref.source_type not in _RESOLVERS:
        raise SourceResolutionError("EVIDENCE_SOURCE_UNTRUSTED", "该来源未被审核，不能进入个人策略证据")
    resolver = _RESOLVERS[ref.source_type]
    return resolver(db, user_id, ref.source_id, ref.source_revision, expected_metric, ref.metric_version, window)
