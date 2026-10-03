"""Feature registry (capability plan §4.3/§4.5).

Every derived value is declared once with:

* a stable ``key`` and a ``version`` — bump the version whenever the formula
  changes, because two versions must never be compared as if equal;
* the ``sources`` it reads — which drives incremental invalidation (a diet edit
  does not recompute sleep debt);
* a pure ``compute`` function taking a ``FeatureContext`` and returning a
  ``StateValue``.

Adding a feature means adding a declaration here. Nothing else needs to know.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import BUSINESS_TZ, business_today, naive_utc, utc_now
from app.models import (
    AgentMicroExperiment,
    DietRecord,
    ExerciseRecord,
    HealthCheckIn,
    HealthGoalSetting,
    MotionAnalysisFeedback,
    MotionAnalysisRun,
    PlanTaskState,
)
from app.services.health_data import daily_facts
from app.services.health_state.contracts import (
    EvidenceRef,
    StateValue,
    confidence_from_coverage,
)

FEATURE_VERSION = "1.0.0"


@dataclass
class FeatureContext:
    """Everything a feature may read. No DB writes, no model calls."""

    db: Session
    user_id: int
    window_days: int = 7
    end: date | None = None
    _days: list[dict] | None = None

    @property
    def end_date(self) -> date:
        return self.end or business_today()

    @property
    def days(self) -> list[dict]:
        if self._days is None:
            start = self.end_date.fromordinal(
                self.end_date.toordinal() - (self.window_days - 1)
            )
            self._days = daily_facts(self.db, self.user_id, start, self.end_date)
        return self._days

    def observed(self, key: str) -> list[dict]:
        return [row for row in self.days if row.get("observed", {}).get(key)]


def _ref(source_type: str, source_id: str, observed_at, trace_id=None):
    return EvidenceRef(
        source_type=source_type,  # type: ignore[arg-type]
        source_id=str(source_id),
        observed_at=_as_datetime(observed_at),
        trace_id=trace_id,
    )


def _as_datetime(value) -> datetime:
    """Coerce a day-level fact into a real UTC datetime.

    A ``daily_facts`` row is keyed by calendar date, but evidence timestamps must
    be datetimes; a daily fact is attributed to midday in the business timezone so
    it never claims a false precision at 00:00 or 23:59.
    """
    if isinstance(value, datetime):
        return naive_utc(value)
    if isinstance(value, date):
        local = datetime(value.year, value.month, value.day, 12, 0, tzinfo=BUSINESS_TZ)
        return naive_utc(local)
    return utc_now()


def _day_ref(source_type: str, day: str) -> EvidenceRef:
    """Evidence for a day-keyed fact (``YYYY-MM-DD`` from ``daily_facts``)."""
    try:
        parsed = date.fromisoformat(str(day))
    except ValueError:
        return _ref(source_type, str(day), utc_now())
    return _ref(source_type, str(day), parsed)


def _value(
    ctx: FeatureContext,
    key: str,
    value,
    *,
    unit: str = "",
    evidence_type: str = "derived",
    observed_days: int,
    evidence: list[EvidenceRef] | None = None,
    limitations: list[str] | None = None,
) -> StateValue:
    return StateValue(
        key=key,
        value=value,
        unit=unit,
        evidence_type=evidence_type,  # type: ignore[arg-type]
        confidence_level=confidence_from_coverage(observed_days),
        evidence=evidence or [],
        limitations=limitations or [],
        valid_from=utc_now(),
        observed_days=observed_days,
        window_days=ctx.window_days,
        feature_version=FEATURE_VERSION,
    )


# --------------------------------------------------------------------------- #
# Observed values
# --------------------------------------------------------------------------- #

def _sleep_last_night(ctx: FeatureContext) -> StateValue:
    checks = ctx.observed("checkin")
    last = checks[-1] if checks else None
    hours = last.get("sleep_hours") if last else None
    return _value(
        ctx,
        "sleep_hours_last",
        hours,
        unit="h",
        evidence_type="observed",
        observed_days=1 if hours is not None else 0,
        evidence=[_day_ref("checkin", last["date"])] if last else [],
        limitations=[] if hours is not None else ["最近一次身体状态记录没有睡眠数据"],
    )


def _weight_latest(ctx: FeatureContext) -> StateValue:
    checks = [row for row in ctx.observed("checkin") if row.get("weight_kg")]
    last = checks[-1] if checks else None
    return _value(
        ctx,
        "weight_kg_latest",
        last.get("weight_kg") if last else None,
        unit="kg",
        evidence_type="observed",
        observed_days=1 if last else 0,
        evidence=[_day_ref("checkin", last["date"])] if last else [],
        limitations=[] if last else ["窗口内没有体重记录"],
    )


def _diet_intake_avg(ctx: FeatureContext) -> StateValue:
    days = [row for row in ctx.observed("diet")]
    values = [float(row["calories"] or 0) for row in days]
    avg = round(sum(values) / len(values), 1) if values else None
    return _value(
        ctx,
        "diet_calories_avg",
        avg,
        unit="kcal",
        evidence_type="observed",
        observed_days=len(days),
        evidence=[_day_ref("diet_record", row["date"]) for row in days],
        limitations=(
            []
            if days
            else ["窗口内没有饮食记录，无法给出平均摄入；缺失日不按 0 处理"]
        ),
    )


def _exercise_frequency(ctx: FeatureContext) -> StateValue:
    days = [row for row in ctx.observed("exercise")]
    return _value(
        ctx,
        "exercise_days",
        len(days),
        unit="day",
        evidence_type="observed",
        observed_days=len(days),
        evidence=[_day_ref("exercise_record", row["date"]) for row in days],
    )


# --------------------------------------------------------------------------- #
# Derived values
# --------------------------------------------------------------------------- #

def _sleep_debt_7d(ctx: FeatureContext) -> StateValue:
    """Sleep debt is only meaningful over nights that were actually recorded."""
    target = ctx.db.scalar(
        select(HealthGoalSetting).where(HealthGoalSetting.user_id == ctx.user_id)
    )
    target_hours = float(target.sleep_target) if target else 8.0
    rows = [
        row
        for row in ctx.observed("checkin")
        if row.get("sleep_hours") is not None
    ]
    if not rows:
        return _value(
            ctx,
            "sleep_debt_7d",
            None,
            unit="h",
            observed_days=0,
            limitations=["窗口内没有睡眠记录，缺失日不按 0 处理，因此无法计算睡眠债"],
        )
    debt = round(sum(max(0.0, target_hours - float(row["sleep_hours"])) for row in rows), 1)
    return _value(
        ctx,
        "sleep_debt_7d",
        debt,
        unit="h",
        observed_days=len(rows),
        evidence=[_day_ref("checkin", row["date"]) for row in rows],
        limitations=(
            [f"仅统计 {len(rows)}/{ctx.window_days} 个有睡眠记录的日子；缺口以目标 {target_hours}h 为基准"]
            if len(rows) < ctx.window_days
            else []
        ),
    )


def _exercise_gap_days(ctx: FeatureContext) -> StateValue:
    exercises = ctx.db.scalars(
        select(ExerciseRecord)
        .where(ExerciseRecord.user_id == ctx.user_id)
        .order_by(ExerciseRecord.recorded_at.desc())
        .limit(1)
    ).all()
    if not exercises:
        return _value(
            ctx,
            "exercise_gap_days",
            None,
            unit="day",
            observed_days=0,
            limitations=["没有任何运动记录，无法计算断档天数（不假设为 0）"],
        )
    last_day = exercises[0].recorded_at
    gap = (utc_now().date() - naive_utc(last_day).date()).days
    return _value(
        ctx,
        "exercise_gap_days",
        max(0, gap),
        unit="day",
        observed_days=1,
        evidence=[_ref("exercise_record", exercises[0].id, last_day)],
    )


def _diet_record_coverage_7d(ctx: FeatureContext) -> StateValue:
    days = ctx.observed("diet")
    ratio = round(len(days) / ctx.window_days, 3) if ctx.window_days else 0.0
    return _value(
        ctx,
        "diet_record_coverage_7d",
        ratio,
        unit="ratio",
        observed_days=len(days),
        evidence=[_day_ref("diet_record", row["date"]) for row in days],
        limitations=(
            [f"仅 {len(days)}/{ctx.window_days} 天有饮食记录"]
            if len(days) < ctx.window_days
            else []
        ),
    )


def _plan_adherence_7d(ctx: FeatureContext) -> StateValue:
    rows = ctx.db.scalars(
        select(PlanTaskState).where(PlanTaskState.user_id == ctx.user_id)
    ).all()
    start = ctx.end_date.fromordinal(
        ctx.end_date.toordinal() - (ctx.window_days - 1)
    ).isoformat()
    window = [row for row in rows if row.record_date >= start]
    total = len(window)
    if not total:
        return _value(
            ctx,
            "plan_adherence_7d",
            None,
            unit="ratio",
            observed_days=0,
            limitations=["窗口内没有计划任务，无法计算完成率"],
        )
    done = sum(1 for row in window if row.done)
    return _value(
        ctx,
        "plan_adherence_7d",
        round(done / total, 3),
        unit="ratio",
        observed_days=total,
        evidence=[
            _day_ref("plan", row.record_date) for row in window[:20]
        ],
    )


def _motion_quality_trend(ctx: FeatureContext) -> StateValue:
    """Progress is only comparable within one exercise AND one model version.

    Capability plan §4.5: after a model version change the old scores must not be
    compared with the new ones, so the feature reports the version it measured and
    refuses to produce a trend when versions are mixed.
    """
    rows = ctx.db.execute(
        select(MotionAnalysisRun, MotionAnalysisFeedback)
        .join(
            MotionAnalysisFeedback,
            MotionAnalysisFeedback.run_id == MotionAnalysisRun.id,
        )
        .where(MotionAnalysisRun.user_id == ctx.user_id)
        .order_by(MotionAnalysisRun.created_at.desc())
        .limit(40)
    ).all()
    grouped: dict[tuple[str, str], list[dict]] = {}
    mixed_versions: set[str] = set()
    for run, feedback in rows:
        result = feedback.result if isinstance(feedback.result, dict) else {}
        recognition = result.get("recognition") or {}
        canonical = recognition.get("canonical_id")
        score = (result.get("score") or {}).get("overall")
        if not canonical or score is None:
            continue
        version = run.effective_pipeline_version or run.pipeline_version or "unknown"
        grouped.setdefault((canonical, version), []).append(
            {
                "run_id": run.id,
                "score": float(score),
                "at": run.created_at,
                "trace_id": (result.get("_meta") or {}).get("cache_key"),
            }
        )
        mixed_versions.add(canonical)

    usable = {
        key: items
        for key, items in grouped.items()
        if len(items) >= 3
    }
    if not usable:
        return _value(
            ctx,
            "motion_quality_trend",
            None,
            observed_days=0,
            evidence=[],
            limitations=[
                "没有满足「同一动作 + 同一模型版本 + 至少 3 次」条件的分析，"
                "因此不生成进步结论"
            ],
        )
    best = max(usable.items(), key=lambda item: len(item[1]))
    (exercise, version), items = best
    ordered = sorted(items, key=lambda item: item["at"])
    delta = round(ordered[-1]["score"] - ordered[0]["score"], 1)
    return _value(
        ctx,
        "motion_quality_trend",
        delta,
        unit="pt",
        evidence=[
            _ref("motion_analysis", item["run_id"], item["at"]) for item in ordered
        ],
        observed_days=len(ordered),
        limitations=[
            f"仅比较同动作({exercise})同模型版本({version})的 {len(ordered)} 次分析；"
            "不同版本或不同机位的分数不可直接比较"
        ],
    )


def _load_recovery_ratio(ctx: FeatureContext) -> StateValue:
    """A deliberately conservative ratio; unavailable when either side is missing."""
    exercise_days = ctx.observed("exercise")
    load = sum(float(row["exercise_min"] or 0) for row in exercise_days)
    sleep_rows = [
        float(row["sleep_hours"])
        for row in ctx.observed("checkin")
        if row.get("sleep_hours") is not None
    ]
    if not exercise_days or not sleep_rows:
        return _value(
            ctx,
            "load_recovery_ratio",
            None,
            observed_days=0,
            limitations=["运动或睡眠任一侧缺失，无法给出负荷/恢复比"],
        )
    recovery = sum(sleep_rows) / len(sleep_rows)
    if recovery <= 0:
        return _value(
            ctx,
            "load_recovery_ratio",
            None,
            observed_days=len(sleep_rows),
            limitations=["睡眠均值为 0，比值无意义"],
        )
    return _value(
        ctx,
        "load_recovery_ratio",
        round(load / recovery, 2),
        unit="min/h",
        observed_days=min(len(exercise_days), len(sleep_rows)),
        evidence=[
            _day_ref("exercise_record", row["date"]) for row in exercise_days
        ],
        limitations=["仅用于保守排序，不作为疲劳或损伤判断依据"],
    )


def _data_reliability_score(ctx: FeatureContext) -> StateValue:
    """Coverage-derived reliability. Never a model's own confidence."""
    observed = {
        "checkin": len(ctx.observed("checkin")),
        "diet": len(ctx.observed("diet")),
        "exercise": len(ctx.observed("exercise")),
    }
    keys = ("checkin", "diet", "exercise")
    score = round(sum(observed[key] for key in keys) / (len(keys) * ctx.window_days), 3)
    return _value(
        ctx,
        "data_reliability_score",
        score,
        unit="ratio",
        observed_days=sum(observed.values()),
        limitations=[
            "该分数只反映记录覆盖度，不代表模型准确率",
            f"覆盖情况：{observed}",
        ],
    )


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class FeatureDefinition:
    key: str
    version: str
    sources: tuple[str, ...]
    compute: Callable[[FeatureContext], StateValue]
    title: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


FEATURES: tuple[FeatureDefinition, ...] = (
    FeatureDefinition(
        "sleep_hours_last", FEATURE_VERSION, ("checkin",), _sleep_last_night, "最近睡眠时长"
    ),
    FeatureDefinition(
        "weight_kg_latest", FEATURE_VERSION, ("checkin",), _weight_latest, "最近体重"
    ),
    FeatureDefinition(
        "diet_calories_avg", FEATURE_VERSION, ("diet_record",), _diet_intake_avg, "平均摄入"
    ),
    FeatureDefinition(
        "exercise_days", FEATURE_VERSION, ("exercise_record",), _exercise_frequency, "运动天数"
    ),
    FeatureDefinition(
        "sleep_debt_7d", FEATURE_VERSION, ("checkin", "goal"), _sleep_debt_7d, "睡眠债"
    ),
    FeatureDefinition(
        "exercise_gap_days",
        FEATURE_VERSION,
        ("exercise_record",),
        _exercise_gap_days,
        "运动断档",
    ),
    FeatureDefinition(
        "diet_record_coverage_7d",
        FEATURE_VERSION,
        ("diet_record",),
        _diet_record_coverage_7d,
        "饮食记录覆盖",
    ),
    FeatureDefinition(
        "plan_adherence_7d", FEATURE_VERSION, ("plan",), _plan_adherence_7d, "计划完成率"
    ),
    FeatureDefinition(
        "motion_quality_trend",
        FEATURE_VERSION,
        ("motion_analysis",),
        _motion_quality_trend,
        "动作质量趋势",
    ),
    FeatureDefinition(
        "load_recovery_ratio",
        FEATURE_VERSION,
        ("exercise_record", "checkin"),
        _load_recovery_ratio,
        "负荷恢复比",
    ),
    FeatureDefinition(
        "data_reliability_score",
        FEATURE_VERSION,
        ("checkin", "diet_record", "exercise_record"),
        _data_reliability_score,
        "数据可信度",
    ),
)

FEATURE_KEYS: tuple[str, ...] = tuple(item.key for item in FEATURES)
FEATURES_BY_KEY: dict[str, FeatureDefinition] = {item.key: item for item in FEATURES}

# Reverse index: a source table -> the features that must be recomputed when its
# rows change (capability plan §4.5, incremental invalidation).
SOURCE_TO_FEATURES: dict[str, set[str]] = {}
for _definition in FEATURES:
    for _source in _definition.sources:
        SOURCE_TO_FEATURES.setdefault(_source, set()).add(_definition.key)


def features_for_sources(sources: list[str] | set[str]) -> list[str]:
    """Feature keys affected by a change to the given source domains."""
    out: set[str] = set()
    for source in sources:
        out |= SOURCE_TO_FEATURES.get(source, set())
    return sorted(out)


# Display titles, derived from the registry rather than stored in the snapshot.
#
# Titles are pure presentation: keeping them out of `StateValue` means adding or
# rewording a label cannot change a persisted snapshot or its `snapshot_hash`
# (which is what invalidates caches), and cannot make two identical states compare
# as different.
FEATURE_TITLES: dict[str, str] = {
    definition.key: (definition.title or definition.key) for definition in FEATURES
}


def feature_title(key: str) -> str:
    """Human-readable Chinese title for a feature key (falls back to the key)."""
    return FEATURE_TITLES.get(key, key)


def display_titles() -> dict[str, str]:
    """Key -> title map for clients that render raw state keys."""
    return dict(FEATURE_TITLES)


def compute_all(ctx: FeatureContext) -> dict[str, StateValue]:
    values: dict[str, StateValue] = {}
    for definition in FEATURES:
        values[definition.key] = definition.compute(ctx)
    return values
