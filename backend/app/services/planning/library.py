"""Exercise library metadata for the planner (capability plan §7.3).

The motion catalog describes what can be *recognised*; the planner needs to know
what an exercise *requires* — equipment, movement pattern, difficulty. Those are
separate concerns, so they live here and are keyed by the catalog's canonical id,
with an explicit ``in_catalog`` flag so a planning-only entry can never be
presented as a measurable exercise.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.motion import catalog

MovementPattern = str  # squat | hinge | push | pull | carry | core | cardio | mobility

# Planning metadata. ``equipment`` values are matched against the request's set;
# "bodyweight" is always available.
_EXERCISES: tuple[dict, ...] = (
    dict(id="squat", pattern="squat", equipment=("bodyweight",), difficulty=2,
         minutes_per_set=2, load="medium", focus=("legs", "glutes", "core")),
    dict(id="pushup", pattern="push", equipment=("bodyweight",), difficulty=2,
         minutes_per_set=2, load="medium", focus=("chest", "shoulders", "core")),
    dict(id="lunge", pattern="squat", equipment=("bodyweight",), difficulty=2,
         minutes_per_set=2, load="medium", focus=("legs", "glutes", "balance")),
    dict(id="glute_bridge", pattern="hinge", equipment=("bodyweight",), difficulty=1,
         minutes_per_set=2, load="low", focus=("glutes", "lower_back", "core")),
    dict(id="plank", pattern="core", equipment=("bodyweight",), difficulty=2,
         minutes_per_set=1, load="low", focus=("core", "shoulders")),
    dict(id="bird_dog", pattern="core", equipment=("bodyweight",), difficulty=1,
         minutes_per_set=2, load="low", focus=("core", "lower_back", "balance")),
    dict(id="superman", pattern="hinge", equipment=("bodyweight",), difficulty=1,
         minutes_per_set=1, load="low", focus=("lower_back", "glutes")),
    dict(id="wall_sit", pattern="squat", equipment=("bodyweight",), difficulty=1,
         minutes_per_set=1, load="low", focus=("legs", "endurance")),
    dict(id="leg_abduction", pattern="mobility", equipment=("bodyweight",), difficulty=1,
         minutes_per_set=2, load="low", focus=("glutes", "hip_stability")),
    dict(id="arm_abduction", pattern="push", equipment=("bodyweight",), difficulty=1,
         minutes_per_set=2, load="low", focus=("shoulders",)),
    dict(id="arm_vw", pattern="push", equipment=("bodyweight",), difficulty=1,
         minutes_per_set=2, load="low", focus=("shoulders", "upper_back")),
    dict(id="bicep_curl", pattern="pull", equipment=("dumbbell", "band"), difficulty=1,
         minutes_per_set=2, load="low", focus=("arms",)),
    dict(id="lateral_raise", pattern="push", equipment=("dumbbell", "band"), difficulty=1,
         minutes_per_set=2, load="low", focus=("shoulders",)),
    dict(id="shoulder_press", pattern="push", equipment=("dumbbell", "band"), difficulty=2,
         minutes_per_set=2, load="medium", focus=("shoulders", "arms")),
    dict(id="dumbbell_row", pattern="pull", equipment=("dumbbell", "band"), difficulty=2,
         minutes_per_set=2, load="medium", focus=("upper_back", "arms")),
    dict(id="dumbbell_deadlift", pattern="hinge", equipment=("dumbbell", "barbell"), difficulty=2,
         minutes_per_set=2, load="medium", focus=("glutes", "lower_back", "legs")),
    dict(id="goblet_squat", pattern="squat", equipment=("dumbbell", "kettlebell"), difficulty=2,
         minutes_per_set=2, load="medium", focus=("legs", "glutes", "core")),
    dict(id="bench_press", pattern="push", equipment=("barbell", "bench"), difficulty=3,
         minutes_per_set=3, load="high", focus=("chest", "shoulders", "arms")),
    dict(id="deadlift", pattern="hinge", equipment=("barbell",), difficulty=3,
         minutes_per_set=3, load="high", focus=("glutes", "lower_back", "legs")),
    dict(id="pullup", pattern="pull", equipment=("bar",), difficulty=3,
         minutes_per_set=2, load="high", focus=("upper_back", "arms")),
    dict(id="band_pull_apart", pattern="pull", equipment=("band",), difficulty=1,
         minutes_per_set=2, load="low", focus=("upper_back", "posture")),
    dict(id="face_pull", pattern="pull", equipment=("band", "cable"), difficulty=2,
         minutes_per_set=2, load="low", focus=("upper_back", "posture")),
    dict(id="cat_cow", pattern="mobility", equipment=("bodyweight",), difficulty=1,
         minutes_per_set=2, load="low", focus=("mobility", "lower_back")),
    dict(id="hip_flexor_stretch", pattern="mobility", equipment=("bodyweight",), difficulty=1,
         minutes_per_set=2, load="low", focus=("mobility", "hip")),
    dict(id="thoracic_rotation", pattern="mobility", equipment=("bodyweight",), difficulty=1,
         minutes_per_set=2, load="low", focus=("mobility", "posture")),
    dict(id="brisk_walk", pattern="cardio", equipment=("bodyweight",), difficulty=1,
         minutes_per_set=10, load="low", focus=("endurance", "recovery")),
    dict(id="easy_cycle", pattern="cardio", equipment=("bike",), difficulty=1,
         minutes_per_set=10, load="low", focus=("endurance", "recovery")),
)

# Recovery-oriented plan items that are not exercises (sleep, hydration, ...).
RECOVERY_ITEMS: tuple[dict, ...] = (
    dict(
        title="提前入睡 30 分钟",
        category="sleep",
        duration_min=5,
        reason="近 7 日睡眠债偏离目标，优先补回睡眠而不是加量",
    ),
    dict(
        title="全天分次补水到目标",
        category="habit",
        duration_min=5,
        reason="饮水记录缺口较大，先补齐基础习惯",
    ),
    dict(
        title="10 分钟轻活动与拉伸",
        category="recovery",
        duration_min=10,
        reason="恢复优先日只做轻活动，避免高强度叠加疲劳",
    ),
)


@dataclass(frozen=True)
class Exercise:
    id: str
    pattern: str
    equipment: tuple[str, ...]
    difficulty: int
    minutes_per_set: int
    load: str
    focus: tuple[str, ...]
    in_catalog: bool

    def requires(self) -> set[str]:
        return set(self.equipment)


def _build() -> dict[str, Exercise]:
    out: dict[str, Exercise] = {}
    for row in _EXERCISES:
        action = catalog.get_action(row["id"])
        out[row["id"]] = Exercise(
            id=row["id"],
            pattern=row["pattern"],
            equipment=tuple(row["equipment"]),
            difficulty=int(row["difficulty"]),
            minutes_per_set=int(row["minutes_per_set"]),
            load=str(row["load"]),
            focus=tuple(row["focus"]),
            in_catalog=action is not None,
        )
    return out


EXERCISES: dict[str, Exercise] = _build()

# Every planning-only id is visible so an audit can flag a "measurable" claim that
# the motion catalog does not actually support.
PLANNING_ONLY_IDS: tuple[str, ...] = tuple(
    sorted(key for key, value in EXERCISES.items() if not value.in_catalog)
)


def get_exercise(exercise_id: str) -> Exercise | None:
    return EXERCISES.get(exercise_id)


def display_name(exercise_id: str) -> str:
    action = catalog.get_action(exercise_id)
    if action:
        return str(action["name_zh"])
    return exercise_id.replace("_", " ")


def bodyweight_ids() -> tuple[str, ...]:
    return tuple(
        sorted(key for key, value in EXERCISES.items() if value.equipment == ("bodyweight",))
    )


__all__ = [
    "EXERCISES",
    "PLANNING_ONLY_IDS",
    "RECOVERY_ITEMS",
    "Exercise",
    "bodyweight_ids",
    "display_name",
    "get_exercise",
]
