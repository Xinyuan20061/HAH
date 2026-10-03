"""Rolling re-planning with a visible diff (capability plan §7.5).

Rules enforced by the code, not by a prompt:

* **completed items are frozen** — a replan never removes or rewrites something the
  user already did;
* **an unfinished item is not a failure** — it is kept by default;
* every change carries a reason, and the whole change set is returned as a diff for
  the user to confirm before anything is written.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import business_today
from app.models import HealthPlan, HealthPlanItem
from app.services.planning.contracts import (
    PlanCandidate,
    PlanContext,
    PlanDiff,
    PlanDiffEntry,
    PlanRequest,
)
from app.services.planning.library import display_name
from app.services.planning.solver import solve_weekly_plan

# How far ahead a replan may touch. Items beyond the horizon are untouched so a
# replan cannot quietly rewrite the whole plan.
REPLAN_HORIZON_DAYS = 7


def _split(
    db: Session, plan_id: int
) -> tuple[list[HealthPlanItem], list[HealthPlanItem]]:
    rows = db.scalars(
        select(HealthPlanItem)
        .where(HealthPlanItem.plan_id == plan_id)
        .order_by(HealthPlanItem.planned_date, HealthPlanItem.id)
    ).all()
    completed = [row for row in rows if row.done or row.completed_at is not None]
    open_items = [row for row in rows if not (row.done or row.completed_at is not None)]
    return list(completed), list(open_items)


def build_diff(
    request: PlanRequest,
    context: PlanContext,
    *,
    current_open: list[HealthPlanItem],
    candidate: PlanCandidate,
    reasons: list[str] | None = None,
    completed_count: int = 0,
) -> PlanDiff:
    """Diff a solved candidate against the currently open items.

    Matching is by ``planned_date + exercise_id`` so an item that merely moved keeps
    its identity, which is what makes the diff readable.
    """
    today = business_today()
    horizon = (today + timedelta(days=REPLAN_HORIZON_DAYS)).isoformat()

    existing: dict[tuple[str, str], HealthPlanItem] = {}
    for row in current_open:
        if row.planned_date > horizon:
            continue
        existing[(row.planned_date, row.title)] = row

    adds: list[PlanDiffEntry] = []
    keeps: list[PlanDiffEntry] = []
    removes: list[PlanDiffEntry] = []

    planned: set[tuple[str, str]] = set()
    for item in candidate.items:
        planned_date = (today + timedelta(days=item.day_index)).isoformat()
        if planned_date > horizon:
            continue
        key = (planned_date, item.title)
        planned.add(key)
        entry = PlanDiffEntry(
            op="add" if key not in existing else "keep",
            planned_date=planned_date,
            exercise_id=item.exercise_id,
            title=item.title,
            reason=item.reason,
        )
        (adds if entry.op == "add" else keeps).append(entry)

    for key, row in existing.items():
        if key in planned:
            continue
        removes.append(
            PlanDiffEntry(
                op="remove",
                planned_date=row.planned_date,
                exercise_id="",
                title=row.title,
                reason="重规划中不再需要；未完成不等于失败，可保留原项目",
            )
        )

    return PlanDiff(
        adds=adds,
        removes=removes,
        keeps=keeps,
        reasons=list(reasons or []),
        frozen_completed=completed_count,
    )


def replan(
    db: Session,
    *,
    user_id: int,
    plan_id: int,
    request: PlanRequest,
    context: PlanContext,
    reasons: list[str] | None = None,
) -> dict:
    """Compute a replan proposal. **Does not write anything.**

    Writing happens only through the ``plan.replan.apply`` Action after the user
    confirms the diff, which is what keeps "the system replanned my week" from
    being something that happens without consent.
    """
    plan = db.get(HealthPlan, plan_id)
    if plan is None or plan.user_id != user_id:
        return {"found": False, "reason": "plan_not_found"}

    completed, open_items = _split(db, plan_id)
    candidate = solve_weekly_plan(request, context)
    diff = build_diff(
        request,
        context,
        current_open=open_items,
        candidate=candidate,
        reasons=reasons,
        completed_count=len(completed),
    )
    return {
        "found": True,
        "plan_id": plan_id,
        "candidate": candidate.model_dump(mode="json"),
        "diff": diff.model_dump(mode="json"),
        "frozen_completed": len(completed),
        "open_items": len(open_items),
        "requires_confirmation": True,
        "policy": diff.policy,
    }


def apply_diff(
    db: Session, *, user_id: int, plan_id: int, diff: PlanDiff
) -> dict:
    """Write an already-confirmed diff.

    Only ``adds`` and ``removes`` are actioned; ``keeps`` and every completed item
    are untouched by construction.
    """
    plan = db.get(HealthPlan, plan_id)
    if plan is None or plan.user_id != user_id:
        raise LookupError("plan_not_found")

    completed, open_items = _split(db, plan_id)
    frozen = {(row.planned_date, row.title) for row in completed}

    added = 0
    for entry in diff.adds:
        if (entry.planned_date, entry.title) in frozen:
            # Never rewrite something the user already completed.
            continue
        existing = next(
            (
                row
                for row in open_items
                if row.planned_date == entry.planned_date and row.title == entry.title
            ),
            None,
        )
        if existing is not None:
            continue
        db.add(
            HealthPlanItem(
                plan_id=plan_id,
                user_id=user_id,
                planned_date=entry.planned_date,
                category="exercise",
                title=entry.title,
                description=entry.reason,
                target_json="{}",
                done=False,
            )
        )
        added += 1

    removed = 0
    for entry in diff.removes:
        for row in open_items:
            if (row.planned_date, row.title) != (entry.planned_date, entry.title):
                continue
            if row.done or row.completed_at is not None:
                continue
            db.delete(row)
            removed += 1
    db.commit()
    return {
        "added": added,
        "removed": removed,
        "frozen_completed": len(completed),
        "kept": len(diff.keeps),
    }


def suggestions_from_state(
    context: PlanContext, *, goal: str = "fitness"
) -> list[str]:
    """Human-readable reasons a replan is being proposed, from real state only."""
    reasons: list[str] = []
    state = context.health_state
    if state is not None:
        debt = state.numeric("sleep_debt_7d")
        if debt is not None and debt >= 5:
            reasons.append(f"近 7 日睡眠债 {debt}h，降低下一次负荷或改恢复")
        adherence = state.numeric("plan_adherence_7d")
        if adherence is not None and adherence < 0.5:
            reasons.append("计划完成率偏低，先减少复杂度而不是增加提醒")
    if context.motion_focus:
        reasons.append(
            "动作反馈指出的薄弱项：" + "、".join(context.motion_focus[:3])
        )
    if not reasons:
        reasons.append("按当前记录保持既定安排")
    return reasons


__all__ = [
    "REPLAN_HORIZON_DAYS",
    "apply_diff",
    "build_diff",
    "replan",
    "suggestions_from_state",
]
