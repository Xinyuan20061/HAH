from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.services.health import today_summary, get_goal_settings
from app.services.health_data import period_snapshot
from app.services.health_state import DEFAULT_WINDOW_DAYS, build_snapshot
from app.services.weekly_facts import build_weekly_facts
from app.services.motion_profile import motion_profile
from app.services.training_adjustment import build_training_adjustment
from app.services.training_semantics import get_training_intent, recommend_exercises
from app.services.agent.proactive import build_proactive_insights


def profile_fact(user):
    p = user.profile
    if not p:
        return {"completed": False}
    context = {
        "completed": True,
        "age": p.age,
        "height_cm": p.height_cm,
        "weight_kg": p.weight_kg,
        "goal_type": p.goal_type,
        "activity_level": p.activity_level,
        "diet_preference": p.diet_preference,
        "allergies": p.allergies,
    }
    return context


def read_context(db: Session, user) -> dict:
    """Read-only tool bundle exposed to the Health Agent.

    Keeping this layer deterministic makes it easy to audit exactly which facts were
    provided to the LLM and prevents the model from inventing database reads.

    Capability plan §4.4: ``state`` is now the single computation site. The older
    flat keys are kept for compatibility, but they are projections of the snapshot
    rather than independent recomputations, so the same fact cannot be derived two
    different ways.
    """
    from app.harness.plugins import (
        capability_scope_granted,
        health_state_excluded_sources,
        record_capability_api_access,
    )

    excluded_sources = health_state_excluded_sources(db, user.id)
    snapshot = build_snapshot(
        db, user.id, window_days=DEFAULT_WINDOW_DAYS, excluded_sources=excluded_sources
    )
    motion_allowed = "motion_analysis" not in excluded_sources
    plan_goals_allowed = "user_preference" not in excluded_sources
    plan_outcomes_allowed = not {"plan", "experiment"}.intersection(excluded_sources)
    health_goals_allowed = capability_scope_granted(
        db, user.id, "health_state", "health.goals.read"
    )
    preferences_allowed = capability_scope_granted(
        db, user.id, "plan_outcome", "user.preferences.read"
    )
    if motion_allowed:
        record_capability_api_access(
            db, user.id, "motion_evidence", route="agent_context.read", allowed=True,
        )
    if plan_goals_allowed or plan_outcomes_allowed or preferences_allowed:
        record_capability_api_access(
            db, user.id, "plan_outcome", route="agent_context.read", allowed=True,
        )
    if preferences_allowed:
        # Preference memory only enters the model when the user granted the
        # matching plan capability scope.
        from app.services.agent.outcome import memory_view
        memory = memory_view(db, user.id)
    else:
        memory = None
    context = {
        "profile": profile_fact(user),
        "today": today_summary(
            db,
            user.id,
            user.profile,
            include_plan=plan_outcomes_allowed,
            include_goal_targets=health_goals_allowed,
        ),
        "goals": (
            get_goal_settings(db, user.id, user.profile)
            if health_goals_allowed
            else {}
        ),
        "recent_7d": period_snapshot(db, user.id, 7, include_plan=plan_outcomes_allowed),
        "weekly_facts": build_weekly_facts(
            user,
            db,
            7,
            persist=False,
            include_plan=plan_outcomes_allowed,
            include_goal_targets=health_goals_allowed,
        ),
        "motion_profile": motion_profile(db, user.id, 30) if motion_allowed else {},
        "training_intent": get_training_intent(db, user.id) if plan_goals_allowed else {},
        # The versioned state layer: values + confidence + evidence + constraints.
        "state": snapshot.to_persistable(),
        "constraints": [item.model_dump() for item in snapshot.constraints],
        "state_blocked": bool(snapshot.hard_constraints()),
    }
    if preferences_allowed and isinstance(memory, dict):
        context["memory"] = memory
        context["preferences"] = memory.get("effective", {})
    context["exercise_recommendations"] = (
        recommend_exercises(db, user.id, 6)
        if plan_goals_allowed
        else {"items": [], "message": "训练偏好未授权，本次不生成动作匹配推荐。"}
    )
    context["training_adjustment"] = build_training_adjustment(context)
    context["proactive_insights"] = build_proactive_insights(context)
    return context
