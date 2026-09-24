from pydantic import BaseModel, Field, ConfigDict


class ProfileIn(BaseModel):
    gender: str = Field(default="unspecified", max_length=20)
    age: int = Field(20, ge=12, le=100)
    height_cm: float = Field(170, ge=100, le=230)
    weight_kg: float = Field(65, ge=25, le=300)
    goal_type: str = Field(default="maintain", max_length=30)
    activity_level: str = Field(default="moderate", max_length=30)
    diet_preference: str = Field(default="", max_length=255)
    allergies: str = Field(default="", max_length=255)


class ProfileOut(ProfileIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    nickname: str
    avatar_url: str
