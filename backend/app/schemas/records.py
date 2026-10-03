"""Diet / exercise record contracts (spec §5.3, §5.4, §6.3).

``MealType`` and ``FoodItem`` are the single frozen vocabulary: the same literal
set is enforced by the database CHECK constraint added in migration 0026, so the
application layer and the database can never disagree about a meal name.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.time import UTCDateTime

MealType = Literal["breakfast", "lunch", "dinner", "snack", "other"]
MEAL_TYPES: tuple[str, ...] = ("breakfast", "lunch", "dinner", "snack", "other")

MAX_FOOD_ITEMS = 12
MAX_PAGE_LIMIT = 50
DEFAULT_PAGE_LIMIT = 20


class FoodItem(BaseModel):
    """One corrected item inside a meal draft.

    Extra keys are rejected: an unknown key would otherwise be persisted into
    ``items_json`` and silently shown back to the user as if it were measured.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    portion: str = Field(default="", max_length=120)
    portion_basis: str = Field(default="", max_length=300)
    weight_g: float = Field(default=0, ge=0, le=5000)
    calories: float = Field(default=0, ge=0, le=5000)
    calorie_range_low: float | None = Field(default=None, ge=0, le=5000)
    calorie_range_high: float | None = Field(default=None, ge=0, le=5000)
    protein: float = Field(default=0, ge=0, le=500)
    carbs: float = Field(default=0, ge=0, le=1000)
    fat: float = Field(default=0, ge=0, le=500)
    fiber: float = Field(default=0, ge=0, le=200)
    confidence: float = Field(default=0, ge=0, le=1)
    evidence: str = Field(default="", max_length=300)
    in_image: bool = False


class DietIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    meal_type: MealType = "other"
    calories: float = Field(ge=0, le=5000)
    protein: float = Field(0, ge=0, le=500)
    carbs: float = Field(0, ge=0, le=1000)
    fat: float = Field(0, ge=0, le=500)
    source: str = Field(default="manual", max_length=20)
    portion: str = Field(default="", max_length=120)
    cooking_method: str = Field(default="", max_length=120)
    weight_g: float = Field(0, ge=0, le=5000)
    fiber: float = Field(0, ge=0, le=200)
    vision_analysis_id: int | None = None
    items: list[FoodItem] = Field(default_factory=list, max_length=MAX_FOOD_ITEMS)

    @field_validator("meal_type")
    @classmethod
    def _known_meal(cls, value: str) -> str:
        if value not in MEAL_TYPES:
            raise ValueError("meal_type 必须是 breakfast/lunch/dinner/snack/other")
        return value


class DietOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    meal_type: str
    calories: float
    protein: float
    carbs: float
    fat: float
    fiber: float = 0
    portion: str = ""
    cooking_method: str = ""
    weight_g: float = 0
    source: str = "manual"
    vision_analysis_id: int | None = None
    # The provenance the client is allowed to render: "manual" | "ai_vision" |
    # "ai_vision_corrected". Never a provider name or an internal confidence.
    source_label: str = ""
    items: list[dict] = Field(default_factory=list, max_length=MAX_FOOD_ITEMS)
    recorded_at: UTCDateTime
    version: int = 1
    # Capability plan §4.5: after a write, the state features derived from this record
    # are invalidated and recomputed. Reporting which ones makes the propagation
    # observable to the client instead of an invisible side effect. Empty on reads.
    state_invalidated: list[str] = Field(default_factory=list)


class DietRecordPatch(BaseModel):
    """Partial update. ``version`` is required and must match the stored row."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    meal_type: MealType | None = None
    calories: float | None = Field(default=None, ge=0, le=5000)
    protein: float | None = Field(default=None, ge=0, le=500)
    carbs: float | None = Field(default=None, ge=0, le=1000)
    fat: float | None = Field(default=None, ge=0, le=500)
    fiber: float | None = Field(default=None, ge=0, le=200)
    portion: str | None = Field(default=None, max_length=120)
    cooking_method: str | None = Field(default=None, max_length=120)
    weight_g: float | None = Field(default=None, ge=0, le=5000)
    items: list[FoodItem] | None = Field(default=None, max_length=MAX_FOOD_ITEMS)
    recorded_at: UTCDateTime | None = None

    @field_validator("meal_type")
    @classmethod
    def _known_meal(cls, value: str | None) -> str | None:
        if value is not None and value not in MEAL_TYPES:
            raise ValueError("meal_type 必须是 breakfast/lunch/dinner/snack/other")
        return value


class DietRecordPage(BaseModel):
    items: list[DietOut]
    next_cursor: str | None = None
    has_more: bool = False


class ExerciseIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    duration_min: int = Field(ge=1, le=600)
    calories_burned: float = Field(0, ge=0, le=5000)
    intensity: str = Field(default="medium", max_length=20)


class ExerciseOut(ExerciseIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    recorded_at: UTCDateTime
