"""Hard-constraint filtering and validation (capability plan §7.3).

The planner's legality guarantee lives here, not in a prompt. A hard constraint
either holds or the plan is rejected; the model never gets to trade one away.

Each rule reports *which* constraint it checked, so a rejection is explainable and
the tests can assert on rule names rather than on messages.
"""

from __future__ import annotations

from app.services.health_state.contracts import HealthStateSnapshot
from app.services.planning.contracts import (
    MAX_ITEMS_PER_SESSION,
    PlanCandidate,
    PlanContext,
    PlanItemDraft,
    PlanRequest,
    ValidationReport,
)
from app.services.planning.library import EXERCISES, get_exercise

# A muscle group trained hard today should not be loaded hard again the next day.
RECOVERY_GAP_DAYS = 1
# Above this many minutes in one session the plan is not credible for a beginner.
SESSION_OVERFLOW_TOLERANCE = 1.15
# Bodyweight is always available: "I have dumbbells" does not mean "I have nothing
# else", and requiring the user to also list bodyweight produced empty plans.
IMPLICIT_EQUIPMENT = frozenset({"bodyweight"})
# Non-exercise plan items (sleep, hydration, light activity) require no equipment.
RECOVERY_ITEM_ID = "recovery_item"


def effective_equipment(request: PlanRequest) -> set[str]:
    return set(request.equipment) | set(IMPLICIT_EQUIPMENT)


def _blocked_keys(state: HealthStateSnapshot | None) -> set[str]:
    if state is None:
        return set()
    return set(state.block_keys())


def _training_items(plan: PlanCandidate) -> list[PlanItemDraft]:
    """Only real training load is gated by data coverage.

    A recovery/habit suggestion (sleep earlier, drink water, light activity) puts no
    load on the body, so insufficient records must not make it "illegal" — that
    would leave a new user with no help at all. Coverage gates *training*, and the
    safety red-flag rule gates everything.
    """
    return [item for item in plan.items if item.category == "exercise"]


def hard_violations(
    request: PlanRequest, context: PlanContext, plan: PlanCandidate
) -> list[str]:
    """Return the list of violated hard constraints (empty means legal)."""
    violations: list[str] = []
    blocked = _blocked_keys(context.health_state)

    # 1. Safety red flags stop automatic planning entirely.
    safety = {key for key in blocked if key.startswith("safety_rule:")}
    if safety:
        violations.append(f"safety_red_flag:{','.join(sorted(safety))}")

    # 2. Insufficient coverage: no training load on thin data. Behavioural
    #    suggestions stay available, so a recovery-only plan remains legal.
    training = _training_items(plan)
    if "insufficient_record_coverage" in blocked and training:
        violations.append("insufficient_record_coverage")

    # 3. User exclusions are permanent.
    for exercise_id in plan.exercise_ids():
        if exercise_id == RECOVERY_ITEM_ID:
            continue
        if exercise_id in request.excluded_exercises:
            violations.append(f"user_excluded:{exercise_id}")
        if f"user_excluded_exercise:{exercise_id}" in blocked:
            violations.append(f"user_excluded:{exercise_id}")

    # 4. Equipment must be available.
    available = effective_equipment(request)
    for exercise_id in plan.exercise_ids():
        if exercise_id == RECOVERY_ITEM_ID:
            continue
        exercise = get_exercise(exercise_id)
        if exercise is None:
            violations.append(f"unknown_exercise:{exercise_id}")
            continue
        required = exercise.requires()
        if not required <= available:
            violations.append(f"equipment_unavailable:{exercise_id}")

    # 5. An exercise with no registered measurer must not receive an auto load
    #    increase (the capability gate, expressed as a planning rule).
    for exercise_id in plan.exercise_ids():
        if exercise_id == RECOVERY_ITEM_ID:
            continue
        if f"unmeasurable_exercise:{exercise_id}" in blocked:
            exercise = get_exercise(exercise_id)
            if exercise is not None and exercise.load == "high":
                violations.append(f"unmeasurable_high_load:{exercise_id}")

    # 6. Recovery-priority state forbids high intensity.
    if "recovery_priority" in blocked or context.recovery_constraints:
        for item in plan.items:
            exercise = get_exercise(item.exercise_id)
            if exercise is not None and exercise.load == "high":
                violations.append(f"high_intensity_while_recovering:{item.exercise_id}")

    # 7. Session length must fit the user's time.
    limit = int(request.minutes_per_session * SESSION_OVERFLOW_TOLERANCE)
    for day, minutes in plan.session_minutes().items():
        if minutes > limit:
            violations.append(f"session_too_long:day{day}:{minutes}>{limit}")

    # 8. Item count sanity (training items only: a day of habits is not a session).
    per_day: dict[int, int] = {}
    for item in training:
        per_day[item.day_index] = per_day.get(item.day_index, 0) + 1
    for day, count in per_day.items():
        if count > MAX_ITEMS_PER_SESSION:
            violations.append(f"too_many_items:day{day}:{count}")

    # 9. The requested number of distinct training days must be honoured.
    training_days = {item.day_index for item in training}
    if len(training_days) > request.days_per_week:
        violations.append(
            f"more_days_than_requested:{len(training_days)}>{request.days_per_week}"
        )

    # 10. Same muscle group loaded hard on adjacent days.
    heavy_by_day: dict[int, set[str]] = {}
    for item in training:
        exercise = get_exercise(item.exercise_id)
        if exercise is None or exercise.load != "high":
            continue
        heavy_by_day.setdefault(item.day_index, set()).update(exercise.focus)
    for day, focus in heavy_by_day.items():
        for offset in range(1, RECOVERY_GAP_DAYS + 1):
            other = heavy_by_day.get(day + offset)
            if other and (focus & other):
                violations.append(f"no_recovery_gap:day{day}->{day + offset}")

    return sorted(set(violations))


def soft_warnings(
    request: PlanRequest, context: PlanContext, plan: PlanCandidate
) -> list[str]:
    warnings: list[str] = []
    if request.preferred_days:
        used = {item.day_index for item in plan.items}
        missing = sorted(set(request.preferred_days) - used)
        if missing:
            warnings.append(f"preferred_days_unused:{missing}")
    if context.motion_focus:
        covered = {
            focus
            for item in plan.items
            for focus in (get_exercise(item.exercise_id).focus if get_exercise(item.exercise_id) else ())
        }
        uncovered = [item for item in context.motion_focus if item not in covered]
        if uncovered:
            warnings.append(f"motion_focus_uncovered:{uncovered}")
    return warnings


def validate_plan(
    request: PlanRequest, context: PlanContext, plan: PlanCandidate
) -> ValidationReport:
    """The single validator every candidate must pass before it is offered."""
    violations = hard_violations(request, context, plan)
    warnings = soft_warnings(request, context, plan)
    checked = [
        "safety_red_flag",
        "insufficient_record_coverage",
        "user_excluded",
        "equipment_unavailable",
        "unmeasurable_high_load",
        "high_intensity_while_recovering",
        "session_too_long",
        "too_many_items",
        "more_days_than_requested",
        "no_recovery_gap",
    ]
    return ValidationReport(
        legal=not violations,
        hard_violations=violations,
        soft_warnings=warnings,
        checked_constraints=checked,
    )


def legal_exercise_ids(
    request: PlanRequest, context: PlanContext
) -> tuple[list[str], list[dict]]:
    """Exercises that survive the hard filter, plus the reasons others were cut."""
    blocked = _blocked_keys(context.health_state)
    if {key for key in blocked if key.startswith("safety_rule:")} or (
        "insufficient_record_coverage" in blocked
    ):
        return [], [
            {
                "reason": "hard_constraint",
                "detail": (
                    "存在安全规则命中或记录覆盖不足，不自动排计划，"
                    "改为提示补齐记录或转介"
                ),
            }
        ]

    allowed: list[str] = []
    rejected: list[dict] = []
    available = effective_equipment(request)
    for exercise_id, exercise in sorted(EXERCISES.items()):
        if exercise_id in request.excluded_exercises:
            rejected.append({"exercise_id": exercise_id, "reason": "user_excluded"})
            continue
        if f"user_excluded_exercise:{exercise_id}" in blocked:
            rejected.append({"exercise_id": exercise_id, "reason": "user_excluded"})
            continue
        if not exercise.requires() <= available:
            rejected.append({"exercise_id": exercise_id, "reason": "equipment_unavailable"})
            continue
        allowed.append(exercise_id)
    return allowed, rejected
