from pydantic import BaseModel, Field


class CheckInIn(BaseModel):
    water_ml: int = Field(0, ge=0, le=10000)
    sleep_hours: float = Field(0, ge=0, le=24)
    weight_kg: float = Field(0, ge=0, le=400)
    steps: int = Field(0, ge=0, le=200000)
    mood: str = Field("normal", max_length=20)


class PlanTaskIn(BaseModel):
    done: bool


class CustomPlanIn(BaseModel):
    title: str = Field(min_length=1, max_length=60)
    description: str = Field("", max_length=300)
    task_type: str = Field("other", max_length=20)
