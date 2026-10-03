"""Plan contracts (capability plan §7.2/§7.4).

The plan is *solved*, not generated: a deterministic candidate generator plus a
validator guarantees legality, and the model only explains the result. These types
are the interface between those two halves.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.health_state.contracts import HealthStateSnapshot

GoalType = Literal["fat_loss", "strength", "fitness", "posture", "maintain"]
IntensityPreference = Literal["gentle", "standard"]

PLANNER_VERSION = "plan-solver-1.0.0"

MIN_MINUTES_PER_SESSION = 10
MAX_MINUTES_PER_SESSION = 120
MAX_ITEMS_PER_SESSION = 8


class PlanRequest(BaseModel):
    """What the user asked for. Validated, not interpreted."""

    model_config = ConfigDict(extra="forbid")

    goal: GoalType = "fitness"
    days_per_week: int = Field(default=3, ge=1, le=7)
    minutes_per_session: int = Field(
        default=30, ge=MIN_MINUTES_PER_SESSION, le=MAX_MINUTES_PER_SESSION
    )
    equipment: set[str] = Field(default_factory=lambda: {"bodyweight"})
    preferred_days: list[int] = Field(default_factory=list)
    excluded_exercises: set[str] = Field(default_factory=set)
    intensity_preference: IntensityPreference = "standard"


class PlanContext(BaseModel):
    """Everything the solver may consider. Read-only."""

    model_config = ConfigDict(extra="forbid")

    health_state: HealthStateSnapshot | None = None
    motion_focus: list[str] = Field(default_factory=list)
    recent_load: dict[str, float] = Field(default_factory=dict)
    recovery_constraints: list[str] = Field(default_factory=list)
    adherence_history: dict[str, float] = Field(default_factory=dict)


class PlanItemDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day_index: int = Field(ge=0, le=6)
    exercise_id: str
    title: str
    category: str = "exercise"
    sets: int = Field(default=3, ge=1, le=10)
    reps: int | None = Field(default=None, ge=1, le=100)
    duration_min: int = Field(default=8, ge=1, le=90)
    intensity: IntensityPreference = "standard"
    reason: str = ""
    target: dict[str, Any] = Field(default_factory=dict)


class PlanCandidate(BaseModel):
    """A legal weekly plan produced by the solver."""

    model_config = ConfigDict(extra="forbid")

    planner_version: str = PLANNER_VERSION
    goal: GoalType
    days_per_week: int
    minutes_per_session: int
    items: list[PlanItemDraft] = Field(default_factory=list)
    score: float = 0.0
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    legal: bool = False
    violations: list[str] = Field(default_factory=list)
    alternatives: list["PlanCandidate"] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def session_minutes(self) -> dict[int, int]:
        out: dict[int, int] = {}
        for item in self.items:
            out[item.day_index] = out.get(item.day_index, 0) + item.duration_min
        return out

    def exercise_ids(self) -> list[str]:
        return [item.exercise_id for item in self.items]


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal: bool
    hard_violations: list[str] = Field(default_factory=list)
    soft_warnings: list[str] = Field(default_factory=list)
    checked_constraints: list[str] = Field(default_factory=list)


class PlanDiffEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["add", "remove", "keep"]
    planned_date: str
    exercise_id: str
    title: str
    reason: str = ""


class PlanDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adds: list[PlanDiffEntry] = Field(default_factory=list)
    removes: list[PlanDiffEntry] = Field(default_factory=list)
    keeps: list[PlanDiffEntry] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    frozen_completed: int = 0
    policy: str = (
        "重规划只调整未来项目：已完成项目冻结，未完成项目不判定为失败；"
        "每次变更都给出 diff 与原因，并由用户确认后才写入。"
    )


PlanCandidate.model_rebuild()
