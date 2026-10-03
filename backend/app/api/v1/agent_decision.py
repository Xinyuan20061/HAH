"""Agent decision, capability and planning endpoints (plan §4.4/§7.4/§8.2).

Read-only except for the planning apply/replan actions, which go through the
unified Action proposal protocol and therefore always require user confirmation.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.database import get_db
from app.harness.plugins import authorize_capability, health_state_excluded_sources, record_capability_api_access
from app.schemas.errors import ApiException
from app.services.agent.capability_graph import capability_graph, manifest, unavailable_reasons
from app.services.agent.decision import decide, next_best_action
from app.services.agent.proactive import PROACTIVE_CODES, build_proactive_insights
from app.services.agent.tools import read_context
from app.services.planning.contracts import PlanContext, PlanRequest
from app.services.planning.replan import replan, suggestions_from_state
from app.services.planning.solver import simulate
from app.services.health_state import build_snapshot

router = APIRouter(prefix="/agent", tags=["health-agent"])
plan_router = APIRouter(prefix="/plan", tags=["plan-solver"])


def _require_plan_capability(db: Session, user_id: int, *, operation: str, scopes: tuple[str, ...], route: str) -> None:
    result = authorize_capability(db, user_id, "plan_outcome", operation, scopes, "new_work")
    record_capability_api_access(
        db, user_id, "plan_outcome", route=route,
        allowed=bool(result.get("allowed")), reason=result.get("reason"),
    )
    if result.get("allowed"):
        return
    reason = result.get("reason")
    code = "PLUGIN_ACTIONS_DISABLED" if reason == "proposals_disabled" else "PLUGIN_SCOPE_NOT_GRANTED" if reason == "scope_not_granted" else "PLUGIN_DISABLED"
    db.commit()
    raise ApiException(409, code, "计划与结果能力未开启或授权范围不足；请在健康能力中检查配置")


class PlanRequestIn(BaseModel):
    goal: str = "fitness"
    days_per_week: int = Field(default=3, ge=1, le=7)
    minutes_per_session: int = Field(default=30, ge=10, le=120)
    equipment: list[str] = Field(default_factory=lambda: ["bodyweight"])
    preferred_days: list[int] = Field(default_factory=list)
    excluded_exercises: list[str] = Field(default_factory=list)
    intensity_preference: str = "standard"

    def to_domain(self) -> PlanRequest:
        try:
            return PlanRequest(
                goal=self.goal,  # type: ignore[arg-type]
                days_per_week=self.days_per_week,
                minutes_per_session=self.minutes_per_session,
                equipment=set(self.equipment),
                preferred_days=list(self.preferred_days),
                excluded_exercises=set(self.excluded_exercises),
                intensity_preference=self.intensity_preference,  # type: ignore[arg-type]
            )
        except Exception as exc:  # noqa: BLE001 - pydantic detail is not user text
            raise ApiException(
                422, "VALIDATION_ERROR", f"计划参数不合法：{type(exc).__name__}"
            ) from None


def _plan_context(db: Session, user) -> PlanContext:
    context = read_context(db, user)
    insights = context.get("proactive_insights") or build_proactive_insights(context)
    motion_focus: list[str] = []
    for item in insights.get("insights", []):
        for key in item.get("evidence", []) or []:
            if isinstance(key, str) and key.endswith("_consistency"):
                motion_focus.append(key)
    state = build_snapshot(
        db, user.id, persist=False,
        excluded_sources=health_state_excluded_sources(db, user.id),
    )
    recovery: list[str] = []
    debt = state.numeric("sleep_debt_7d")
    if debt is not None and debt >= 5:
        recovery.append("sleep_debt")
    adherence = state.numeric("plan_adherence_7d")
    return PlanContext(
        health_state=state,
        motion_focus=sorted(set(motion_focus)),
        recovery_constraints=recovery,
        adherence_history={"plan_adherence_7d": adherence} if adherence is not None else {},
    )


# --------------------------------------------------------------------------- #
# Capability graph (§8.2)
# --------------------------------------------------------------------------- #

@router.get("/capabilities")
def agent_capabilities(user=Depends(current_user), db: Session = Depends(get_db)):
    graph = capability_graph(db, user_id=user.id)
    return {
        "capabilities": manifest(graph),
        "unavailable": unavailable_reasons(graph),
        "policy": (
            "能力可用性来自 Worker 上报与已通过的评测报告；未测量一律视为不可用，"
            "不把配置项当成能力。"
        ),
    }


# --------------------------------------------------------------------------- #
# Decision Contract / Next Best Action (§8.3/§8.4)
# --------------------------------------------------------------------------- #

@router.get("/decision")
def agent_decision(
    user=Depends(current_user),
    db: Session = Depends(get_db),
    window_days: int = Query(default=7, ge=1, le=90),
):
    _require_plan_capability(
        db, user.id, operation="read",
        scopes=("health.profile.read", "health.records.read", "plan.goals.read", "plan.outcomes.read"),
        route="decision",
    )
    context = read_context(db, user)
    insights = context.get("proactive_insights") or build_proactive_insights(context)
    signals = [
        item
        for item in insights.get("insights", [])
        if item.get("code") in PROACTIVE_CODES
    ]
    contract = decide(db, user.id, window_days=window_days, signals=signals)
    payload = contract.as_dict()
    db.commit()
    return payload


@router.get("/next-action")
def agent_next_action(
    user=Depends(current_user),
    db: Session = Depends(get_db),
    window_days: int = Query(default=7, ge=1, le=90),
):
    _require_plan_capability(
        db, user.id, operation="read",
        scopes=("health.profile.read", "health.records.read", "plan.goals.read", "plan.outcomes.read"),
        route="next_action",
    )
    payload = next_best_action(db, user.id, window_days=window_days)
    db.commit()
    return payload


# --------------------------------------------------------------------------- #
# Planning: solve (read-only) -> replan (diff) -> apply (Action, confirmed)
# --------------------------------------------------------------------------- #

@plan_router.post("/solve")
def plan_solve(
    body: PlanRequestIn,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    _require_plan_capability(
        db, user.id, operation="read",
        scopes=("health.profile.read", "health.records.read", "plan.goals.read", "plan.outcomes.read"),
        route="plan_solve",
    )
    request = body.to_domain()
    context = _plan_context(db, user)
    payload = simulate(request, context)
    db.commit()
    return payload


@plan_router.get("/current/proposal")
def plan_replan_proposal(
    user=Depends(current_user),
    db: Session = Depends(get_db),
    plan_id: int = Query(...),
    goal: str = Query(default="fitness"),
    days_per_week: int = Query(default=3, ge=1, le=7),
    minutes_per_session: int = Query(default=30, ge=10, le=120),
):
    """A replan **proposal**: a diff the user must confirm. Writes nothing."""
    _require_plan_capability(
        db, user.id, operation="propose",
        scopes=("health.profile.read", "health.records.read", "plan.goals.read", "plan.outcomes.read", "plan.proposals"),
        route="plan_replan_proposal",
    )
    request = PlanRequest(
        goal=goal,  # type: ignore[arg-type]
        days_per_week=days_per_week,
        minutes_per_session=minutes_per_session,
    )
    context = _plan_context(db, user)
    reasons = suggestions_from_state(context, goal=goal)
    payload = replan(
        db,
        user_id=user.id,
        plan_id=plan_id,
        request=request,
        context=context,
        reasons=reasons,
    )
    if not payload.get("found"):
        raise ApiException(404, "PLAN_NOT_FOUND", "计划不存在")
    db.commit()
    return payload
