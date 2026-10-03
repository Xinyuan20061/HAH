"""Personal health state contracts (capability plan §4.2).

The point of these types is that every value can answer three questions:

1. *How was it produced?* — ``evidence_type`` separates an observed record from a
   model inference from a user confirmation.
2. *How much should it be trusted?* — ``confidence_level`` plus ``limitations``,
   and ``observed_days`` so a 3-of-7-day average is never presented as a week.
3. *What is it made of?* — ``evidence`` references the concrete rows, and
   ``feature_version`` stops two definitions from being compared as if equal.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EvidenceSourceType = Literal[
    "profile",
    "checkin",
    "diet_record",
    "exercise_record",
    "motion_analysis",
    "food_analysis",
    "goal",
    "experiment",
    "plan",
]
EvidenceType = Literal["observed", "derived", "model_inferred", "user_confirmed"]
ConfidenceLevel = Literal["high", "medium", "low", "unavailable"]
ConstraintSeverity = Literal["hard", "soft"]

STATE_VERSION = "1.0.0"

# Confidence is a function of coverage, never of a model's self-reported score
# (capability plan §4.3: "不使用模型自报 confidence 直接替代").
CONFIDENCE_HIGH_MIN_DAYS = 6
CONFIDENCE_MEDIUM_MIN_DAYS = 3


class EvidenceRef(BaseModel):
    """A concrete, replayable pointer to the data a value came from."""

    model_config = ConfigDict(extra="forbid")

    source_type: EvidenceSourceType
    source_id: str
    observed_at: datetime
    trace_id: str | None = None


class StateValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    value: float | int | str | bool | None = None
    unit: str = ""
    evidence_type: EvidenceType = "derived"
    confidence_level: ConfidenceLevel = "unavailable"
    evidence: list[EvidenceRef] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    valid_from: datetime
    valid_until: datetime | None = None
    # How many days actually contributed. A 7-day window with 2 observed days is
    # reported as such instead of being shown as a full week.
    observed_days: int = 0
    window_days: int = 7
    feature_version: str = "1.0.0"


class HealthConstraint(BaseModel):
    """A rule the planning/decision layer must respect.

    ``hard`` constraints may never be traded away; ``soft`` ones only bias the
    ranking. ``source`` says where the constraint came from so a user can always
    see why something was excluded.
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    severity: ConstraintSeverity
    source: str
    description: str


class HealthStateSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = STATE_VERSION
    as_of: datetime
    window_days: int = 7
    values: dict[str, StateValue] = Field(default_factory=dict)
    constraints: list[HealthConstraint] = Field(default_factory=list)
    missingness: dict[str, int] = Field(default_factory=dict)
    active_actions: list[str] = Field(default_factory=list)
    snapshot_hash: str = ""

    def sorted_items(self) -> list[tuple[str, StateValue]]:
        return sorted(self.values.items())

    def value(self, key: str) -> StateValue | None:
        return self.values.get(key)

    def numeric(self, key: str) -> float | None:
        item = self.values.get(key)
        if item is None or isinstance(item.value, bool):
            return None
        if isinstance(item.value, (int, float)):
            return float(item.value)
        return None

    def hard_constraints(self) -> list[HealthConstraint]:
        return [item for item in self.constraints if item.severity == "hard"]

    def block_keys(self) -> list[str]:
        """Constraint keys that must prevent an automatic plan/action."""
        return [item.key for item in self.hard_constraints()]

    def to_persistable(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "as_of": self.as_of.isoformat(),
            "window_days": self.window_days,
            "values": {
                key: value.model_dump(mode="json")
                for key, value in self.sorted_items()
            },
            "constraints": [item.model_dump(mode="json") for item in self.constraints],
            "missingness": self.missingness,
            "active_actions": self.active_actions,
            "snapshot_hash": self.snapshot_hash,
        }


def confidence_from_coverage(observed_days: int) -> ConfidenceLevel:
    """Coverage-based confidence; deliberately ignores any model self-score."""
    if observed_days >= CONFIDENCE_HIGH_MIN_DAYS:
        return "high"
    if observed_days >= CONFIDENCE_MEDIUM_MIN_DAYS:
        return "medium"
    if observed_days >= 1:
        return "low"
    return "unavailable"
