from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.services.health import today_summary, get_goal_settings
from app.services.health_data import period_snapshot
from app.services.weekly_facts import build_weekly_facts
from app.services.timeline import unified_timeline
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
    """
    context = {
        "profile": profile_fact(user),
        "today": today_summary(db, user.id, user.profile),
        "goals": get_goal_settings(db, user.id, user.profile),
        "recent_7d": period_snapshot(db, user.id, 7),
        "weekly_facts": build_weekly_facts(user, db, 7, persist=False),
        "recent_events": unified_timeline(db, user.id, 7)[:30],
        "motion_profile": motion_profile(db, user.id, 30),
        "training_intent": get_training_intent(db, user.id),
    }
    context["exercise_recommendations"] = recommend_exercises(db, user.id, 6)
    context["training_adjustment"] = build_training_adjustment(context)
    context["proactive_insights"] = build_proactive_insights(context)
    return context
