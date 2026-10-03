"""Candidate generation and weekly-plan solving (capability plan §7.1/§7.4).

    Health State → candidate activities → hard filter → soft scoring → weekly solve
    → deterministic validation → the Agent explains the result

The solver is deliberately small: a bounded beam search over day/exercise choices,
scoring with explicit named weights. No integer-programming dependency, no model
call, and the same inputs always produce the same plan.
"""

from __future__ import annotations

from itertools import combinations

from app.services.planning.constraints import (
    legal_exercise_ids,
    validate_plan,
)
from app.services.planning.contracts import (
    PLANNER_VERSION,
    PlanCandidate,
    PlanContext,
    PlanItemDraft,
    PlanRequest,
)
from app.services.planning.library import (
    EXERCISES,
    RECOVERY_ITEMS,
    display_name,
    get_exercise,
)

BEAM_WIDTH = 8
# Soft-objective weights. Named and inspectable: the breakdown is returned to the
# caller so "why this plan" is answerable without asking a model.
WEIGHTS = {
    "goal_alignment": 1.0,
    "movement_balance": 0.7,
    "motion_focus_relevance": 0.9,
    "preference_match": 0.5,
    "adherence_probability": 0.6,
    "variety": 0.8,
    "fatigue_risk": -0.8,
    "complexity_cost": -0.4,
}

GOAL_PATTERNS: dict[str, tuple[str, ...]] = {
    "fat_loss": ("cardio", "squat", "hinge", "core"),
    "strength": ("squat", "hinge", "push", "pull"),
    "fitness": ("squat", "push", "pull", "core", "cardio"),
    "posture": ("pull", "mobility", "core"),
    "maintain": ("squat", "push", "pull", "core", "mobility", "cardio"),
}

# Motion focus keys map onto library focus tags so a measured weakness can steer
# the plan without any model involvement.
FOCUS_TO_TAGS: dict[str, tuple[str, ...]] = {
    "depth_consistency": ("legs", "glutes"),
    "knee_tracking": ("legs", "glutes"),
    "trunk_control": ("core",),
    "elbow_drift": ("arms",),
    "range_of_motion": ("shoulders", "arms", "mobility"),
    "shoulder_shrug": ("shoulders", "upper_back"),
    "body_line": ("core", "posture"),
    "hip_stability": ("glutes", "hip_stability"),
}


def _days(request: PlanRequest) -> list[int]:
    if request.preferred_days:
        days = sorted({day for day in request.preferred_days if 0 <= day <= 6})
        if days:
            return days[: request.days_per_week]
    count = max(1, min(7, request.days_per_week))
    if count == 1:
        return [0]
    # Spread the sessions across the week deterministically.
    return sorted({round(index * 6 / (count - 1)) for index in range(count)})


def _focus_tags(context: PlanContext) -> set[str]:
    tags: set[str] = set()
    for key in context.motion_focus:
        tags.update(FOCUS_TO_TAGS.get(key, ()))
    return tags


def _score(
    request: PlanRequest, context: PlanContext, items: list[PlanItemDraft]
) -> tuple[float, dict[str, float]]:
    wanted = set(GOAL_PATTERNS.get(request.goal, ()))
    focus_tags = _focus_tags(context)

    goal_alignment = 0.0
    movement_balance = 0.0
    focus_relevance = 0.0
    preference_match = 0.0
    fatigue_risk = 0.0
    complexity_cost = 0.0

    patterns: set[str] = set()
    exercises_used: list[str] = []
    for item in items:
        exercise = get_exercise(item.exercise_id)
        if exercise is None:
            # Non-exercise plan items (recovery/habit) carry no load and no cost.
            continue
        exercises_used.append(item.exercise_id)
        patterns.add(exercise.pattern)
        if exercise.pattern in wanted:
            goal_alignment += 1.0
        if focus_tags & set(exercise.focus):
            focus_relevance += 1.0
        if exercise.difficulty >= 3:
            complexity_cost += 1.0
        if exercise.load == "high":
            fatigue_risk += 1.0
        if request.intensity_preference == "gentle" and exercise.load != "low":
            fatigue_risk += 0.5

    # Variety: a plan that repeats the same movement every session is not a plan.
    # Without this the beam search picked the narrowest set, because fewer distinct
    # exercises scored identically and sorted first.
    distinct = len(set(exercises_used))
    variety = distinct / max(1, len(exercises_used)) if exercises_used else 0.0

    movement_balance = len(patterns) / max(1, len(wanted))
    if request.preferred_days:
        used = {item.day_index for item in items}
        preference_match = len(used & set(request.preferred_days)) / max(
            1, len(request.preferred_days)
        )
    adherence = context.adherence_history.get("plan_adherence_7d")
    adherence_probability = adherence if isinstance(adherence, (int, float)) else 0.5

    def _norm(value: float, total: int) -> float:
        return round(value / max(1, total), 4)

    total_items = max(1, len(items))
    breakdown = {
        "goal_alignment": _norm(goal_alignment, total_items),
        "movement_balance": round(min(1.0, movement_balance), 4),
        "motion_focus_relevance": _norm(focus_relevance, total_items),
        "preference_match": round(preference_match, 4),
        "adherence_probability": round(float(adherence_probability), 4),
        "variety": round(variety, 4),
        "fatigue_risk": _norm(fatigue_risk, total_items),
        "complexity_cost": _norm(complexity_cost, total_items),
    }
    score = sum(WEIGHTS[key] * value for key, value in breakdown.items())
    return round(score, 4), breakdown


def _build_items(
    request: PlanRequest,
    context: PlanContext,
    allowed: list[str],
    days: list[int],
) -> list[list[PlanItemDraft]]:
    """Return candidate per-day item sets that already fit the time budget."""
    budget = request.minutes_per_session
    recovery_first = bool(context.recovery_constraints)
    pool = [
        exercise_id
        for exercise_id in allowed
        if not recovery_first or (get_exercise(exercise_id) or EXERCISES["squat"]).load != "high"
    ]
    if not pool:
        pool = allowed[:]

    per_day: list[list[PlanItemDraft]] = []
    for day in days:
        sets: list[list[PlanItemDraft]] = []
        # Try 2..4 exercise combinations, longest that fits first.
        for size in (4, 3, 2):
            for combo in combinations(sorted(pool), size):
                items: list[PlanItemDraft] = []
                total = 0
                for exercise_id in combo:
                    exercise = get_exercise(exercise_id)
                    if exercise is None:
                        continue
                    duration = max(3, exercise.minutes_per_set * 2)
                    if total + duration > budget:
                        break
                    total += duration
                    items.append(
                        PlanItemDraft(
                            day_index=day,
                            exercise_id=exercise_id,
                            title=display_name(exercise_id),
                            category="exercise",
                            sets=2 if exercise.load == "low" else 3,
                            reps=12 if exercise.pattern != "cardio" else None,
                            duration_min=duration,
                            intensity=(
                                "gentle"
                                if exercise.load == "low"
                                else request.intensity_preference
                            ),
                            reason=(
                                f"针对{request.goal}的{exercise.pattern}训练；"
                                f"器械需求 {sorted(exercise.requires())}"
                            ),
                            target={"focus": list(exercise.focus), "load": exercise.load},
                        )
                    )
                if items:
                    sets.append(items)
                if len(sets) >= BEAM_WIDTH:
                    break
            if sets:
                break
        if not sets:
            # No exercise fits the budget: fall back to a recovery/habit item so the
            # plan is never silently empty.
            template = RECOVERY_ITEMS[day % len(RECOVERY_ITEMS)]
            sets = [
                [
                    PlanItemDraft(
                        day_index=day,
                        exercise_id="recovery_item",
                        title=template["title"],
                        category=template["category"],
                        sets=1,
                        reps=None,
                        duration_min=min(template["duration_min"], budget),
                        intensity="gentle",
                        reason=template["reason"],
                    )
                ]
            ]
        per_day.append(sets)
    return per_day


def solve_weekly_plan(
    request: PlanRequest, context: PlanContext
) -> PlanCandidate:
    """Solve a legal weekly plan; illegal candidates are returned but flagged."""
    allowed, rejected = legal_exercise_ids(request, context)
    days = _days(request)
    notes = [f"被硬约束排除的动作：{len(rejected)} 个"] if rejected else []

    if not allowed:
        recovery = PlanCandidate(
            goal=request.goal,
            days_per_week=request.days_per_week,
            minutes_per_session=request.minutes_per_session,
            legal=False,
            notes=notes
            + ["没有通过硬约束的动作，改为恢复/习惯建议，不安排训练负荷"],
        )
        recovery.items = [
            PlanItemDraft(
                day_index=days[0],
                exercise_id="recovery_item",
                title=RECOVERY_ITEMS[0]["title"],
                category=RECOVERY_ITEMS[0]["category"],
                sets=1,
                duration_min=min(RECOVERY_ITEMS[0]["duration_min"], request.minutes_per_session),
                intensity="gentle",
                reason=RECOVERY_ITEMS[0]["reason"],
            )
        ]
        report = validate_plan(request, context, recovery)
        recovery.legal = report.legal
        recovery.violations = report.hard_violations
        return recovery

    per_day_options = _build_items(request, context, allowed, days)

    # Beam search: grow one day at a time, keeping the best partial plans.
    beam: list[tuple[float, list[PlanItemDraft], dict[str, float]]] = [(0.0, [], {})]
    for options in per_day_options:
        grown: list[tuple[float, list[PlanItemDraft], dict[str, float]]] = []
        for _score_value, items, _breakdown in beam:
            for option in options:
                candidate_items = items + option
                value, breakdown = _score(request, context, candidate_items)
                grown.append((value, candidate_items, breakdown))
        grown.sort(key=lambda entry: (-entry[0], len(entry[1])))
        beam = grown[:BEAM_WIDTH]

    best_score, best_items, best_breakdown = beam[0]
    candidate = PlanCandidate(
        planner_version=PLANNER_VERSION,
        goal=request.goal,
        days_per_week=request.days_per_week,
        minutes_per_session=request.minutes_per_session,
        items=best_items,
        score=best_score,
        score_breakdown=best_breakdown,
        notes=notes,
    )
    report = validate_plan(request, context, candidate)
    candidate.legal = report.legal
    candidate.violations = report.hard_violations
    if report.soft_warnings:
        candidate.notes.extend(report.soft_warnings)

    # A second, gentler legal alternative (capability plan §7.1: "两档选择").
    if report.legal:
        gentle_request = request.model_copy(
            update={"intensity_preference": "gentle", "minutes_per_session": max(10, request.minutes_per_session - 10)}
        )
        gentle_items = [
            item.model_copy(
                update={
                    "sets": max(1, item.sets - 1),
                    "duration_min": max(3, item.duration_min - 2),
                    "intensity": "gentle",
                }
            )
            for item in best_items
        ]
        gentle = PlanCandidate(
            planner_version=PLANNER_VERSION,
            goal=request.goal,
            days_per_week=request.days_per_week,
            minutes_per_session=gentle_request.minutes_per_session,
            items=gentle_items,
        )
        gentle.score, gentle.score_breakdown = _score(request, context, gentle_items)
        gentle_report = validate_plan(gentle_request, context, gentle)
        gentle.legal = gentle_report.legal
        gentle.violations = gentle_report.hard_violations
        if gentle.legal:
            candidate.alternatives = [gentle]

    return candidate


def simulate(
    request: PlanRequest, context: PlanContext
) -> dict:
    """Read-only what-if used by the ``plan.simulate`` tool (never writes)."""
    candidate = solve_weekly_plan(request, context)
    report = validate_plan(request, context, candidate)
    return {
        "candidate": candidate.model_dump(mode="json"),
        "validation": report.model_dump(mode="json"),
        "alternatives": [item.model_dump(mode="json") for item in candidate.alternatives],
        "policy": (
            "模拟是只读的：不写入计划、不创建提案；真正应用仍需用户确认的 "
            "plan.apply / plan.replan.apply。"
        ),
    }
