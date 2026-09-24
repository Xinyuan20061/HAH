from app.core.time import UTCDateTime
from pydantic import BaseModel, Field, ConfigDict


class DietIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    meal_type: str = Field(default="other", max_length=20)
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


class DietOut(DietIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    recorded_at: UTCDateTime
    items: list[dict] = Field(default_factory=list, max_length=12)


class ExerciseIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    duration_min: int = Field(ge=1, le=600)
    calories_burned: float = Field(0, ge=0, le=5000)
    intensity: str = Field(default="medium", max_length=20)


class ExerciseOut(ExerciseIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    recorded_at: UTCDateTime
