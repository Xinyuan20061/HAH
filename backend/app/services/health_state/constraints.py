"""Hard and soft constraints derived from real conditions (capability plan §4.3).

Constraints are the mechanism that keeps the Agent honest: a *hard* constraint
blocks an automatic plan/action, a *soft* one only biases ranking. Nothing here is
speculative — each constraint traces to a rule hit, a missing measurement engine, a
user-stated exclusion, or insufficient coverage.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AgentMicroExperiment,
    HealthProfile,
    MotionAnalysisFeedback,
    MotionAnalysisRun,
    UserTrainingIntent,
)
from app.services.health_state.contracts import HealthConstraint
from app.services.health_state.features import FeatureContext

# Coverage below this means "not enough recorded days to justify an automatic
# plan change"; the system must ask for records instead of guessing.
MIN_RELIABLE_COVERAGE = 0.3
# Same-exercise analyses needed before a trend may drive a plan change.
MIN_TREND_SAMPLES = 3


def _excluded_exercises(intent) -> list[str]:
    """Exercises the user excluded, read from the stored training intent.

    ``UserTrainingIntent.constraints_json`` is a JSON list whose entries may be a
    plain string or an object. Both shapes are accepted so an older client cannot
    silently lose a user's exclusion; anything unparseable is ignored rather than
    guessed at.
    """
    import json

    try:
        raw = json.loads(intent.constraints_json or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for entry in raw:
        candidate = ""
        if isinstance(entry, str):
            candidate = entry
        elif isinstance(entry, dict):
            for key in ("exercise_id", "exercise", "key", "value", "id"):
                value = entry.get(key)
                if isinstance(value, str) and value:
                    candidate = value
                    break
        candidate = candidate.strip()
        if candidate:
            out.append(candidate)
    return sorted(set(out))


def _rule_flags(db: Session, user_id: int) -> list[str]:
    """Safety-rule keys that fired in the recent window.

    Only the *category* is used; the matched text and any private excerpt stay in
    the safety audit, never in the state snapshot.
    """
    from app.models import SafetyEvent

    rows = db.scalars(
        select(SafetyEvent)
        .where(SafetyEvent.user_id == user_id, SafetyEvent.severity.in_(("high", "critical")))
        .order_by(SafetyEvent.created_at.desc())
        .limit(5)
    ).all()
    return sorted({row.category for row in rows if row.category})


def _out_of_catalog_exercises(db: Session, user_id: int) -> list[str]:
    """Exercises the user analysed but the catalogue cannot measure."""
    from app.services.motion import catalog

    rows = db.execute(
        select(MotionAnalysisRun, MotionAnalysisFeedback)
        .join(
            MotionAnalysisFeedback,
            MotionAnalysisFeedback.run_id == MotionAnalysisRun.id,
        )
        .where(MotionAnalysisRun.user_id == user_id)
        .order_by(MotionAnalysisRun.created_at.desc())
        .limit(20)
    ).all()
    out: set[str] = set()
    for _run, feedback in rows:
        result = feedback.result if isinstance(feedback.result, dict) else {}
        canonical = (result.get("recognition") or {}).get("canonical_id")
        if canonical and catalog.get_action(canonical) is None:
            out.add(canonical)
    return sorted(out)


def build_constraints(
    db: Session, user_id: int, ctx: FeatureContext, values: dict
) -> list[HealthConstraint]:
    constraints: list[HealthConstraint] = []

    for category in _rule_flags(db, user_id):
        constraints.append(
            HealthConstraint(
                key=f"safety_rule:{category}",
                severity="hard",
                source="safety_rule",
                description="近期命中安全规则，暂停自动计划与强度建议，转为谨慎提示或转介。",
            )
        )

    reliability = values.get("data_reliability_score")
    if reliability is not None and (reliability.value or 0) < MIN_RELIABLE_COVERAGE:
        constraints.append(
            HealthConstraint(
                key="insufficient_record_coverage",
                severity="hard",
                source="data_coverage",
                description="记录覆盖不足，不自动调整计划；应先补齐记录或由用户主动要求。",
            )
        )

    trend = values.get("motion_quality_trend")
    if trend is not None and trend.value is None:
        limitations = " ".join(trend.limitations)
        if "至少 3 次" in limitations or "3 次" in limitations:
            constraints.append(
                HealthConstraint(
                    key="motion_trend_insufficient",
                    severity="soft",
                    source="motion_evidence",
                    description=(
                        f"同动作同版本分析不足 {MIN_TREND_SAMPLES} 次，不加负荷、不声称进步。"
                    ),
                )
            )

    for exercise in _out_of_catalog_exercises(db, user_id):
        constraints.append(
            HealthConstraint(
                key=f"unmeasurable_exercise:{exercise}",
                severity="hard",
                source="motion_catalog",
                description=f"动作 {exercise} 没有登记的测量器，禁止据此产生数值评分或自动加负荷。",
            )
        )

    profile = db.scalar(select(HealthProfile).where(HealthProfile.user_id == user_id))
    if profile is None:
        constraints.append(
            HealthConstraint(
                key="profile_incomplete",
                severity="soft",
                source="profile",
                description="健康档案未完成，个性化建议范围受限。",
            )
        )

    intent = db.scalar(
        select(UserTrainingIntent).where(UserTrainingIntent.user_id == user_id)
    )
    if intent is not None:
        for item in _excluded_exercises(intent):
            constraints.append(
                HealthConstraint(
                    key=f"user_excluded_exercise:{item}",
                    severity="hard",
                    source="user_preference",
                    description=f"用户明确排除的动作 {item} 永不自动加入计划。",
                )
            )

    experiment = db.scalar(
        select(AgentMicroExperiment).where(
            AgentMicroExperiment.user_id == user_id,
            AgentMicroExperiment.status == "active",
        )
    )
    if experiment is not None:
        constraints.append(
            HealthConstraint(
                key="experiment_active",
                severity="soft",
                source="experiment",
                description=(
                    f"微实验 {experiment.insight_code} 进行中；一次只改变一个主要行为，"
                    "避免同时引入互斥改动。"
                ),
            )
        )

    return constraints
