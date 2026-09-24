from pydantic import BaseModel, Field, model_validator


class FoodItemCorrection(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    portion: str = Field(default="", max_length=120)
    portion_basis: str = Field(default="", max_length=240)
    weight_g: float = Field(default=0, ge=0, le=5000)
    calories: float = Field(default=0, ge=0, le=5000)
    calorie_range_low: float | None = Field(default=None, ge=0, le=5000)
    calorie_range_high: float | None = Field(default=None, ge=0, le=5000)
    protein: float = Field(default=0, ge=0, le=500)
    carbs: float = Field(default=0, ge=0, le=1000)
    fat: float = Field(default=0, ge=0, le=500)
    fiber: float = Field(default=0, ge=0, le=200)
    confidence: float = Field(default=0.5, ge=0, le=1)
    evidence: str = Field(default="", max_length=300)
    in_image: bool = True

    @model_validator(mode="after")
    def normalize_range(self):
        if self.calorie_range_low is None:
            self.calorie_range_low = round(max(0, self.calories * 0.8), 1)
        if self.calorie_range_high is None:
            self.calorie_range_high = round(min(5000, self.calories * 1.2), 1)
        if not self.calorie_range_low <= self.calories <= self.calorie_range_high:
            raise ValueError("逐项热量必须位于估算区间内")
        return self


class FoodCorrectionIn(BaseModel):
    dish_name: str = Field(min_length=1, max_length=120)
    portion: str = Field(default="", max_length=120)
    cooking_method: str = Field(default="", max_length=120)
    weight_g: float = Field(default=0, ge=0, le=5000)
    calories: float = Field(default=0, ge=0, le=5000)
    protein: float = Field(default=0, ge=0, le=500)
    carbs: float = Field(default=0, ge=0, le=1000)
    fat: float = Field(default=0, ge=0, le=500)
    fiber: float = Field(default=0, ge=0, le=200)
    items: list[FoodItemCorrection] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def totals_follow_items(self):
        if not self.items:
            return self
        self.calories = round(sum(x.calories for x in self.items), 1)
        self.weight_g = round(sum(x.weight_g for x in self.items), 1)
        self.protein = round(sum(x.protein for x in self.items), 1)
        self.carbs = round(sum(x.carbs for x in self.items), 1)
        self.fat = round(sum(x.fat for x in self.items), 1)
        self.fiber = round(sum(x.fiber for x in self.items), 1)
        return self


class FoodFinalizeIn(BaseModel):
    meal_type: str = Field(
        default="other", pattern="^(breakfast|lunch|dinner|snack|other)$"
    )
    confirmed: bool = False
