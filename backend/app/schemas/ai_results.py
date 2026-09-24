"""Untrusted worker/provider results are validated before creating health records."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FoodItem(BaseModel):
    """One auditable component of a meal estimate."""

    model_config = ConfigDict(strict=True, allow_inf_nan=False, extra="ignore")
    name: str = Field(min_length=1, max_length=120)
    portion: str = Field(default="", max_length=120)
    portion_basis: str = Field(default="", max_length=240)
    weight_g: float = Field(default=0, ge=0, le=5000)
    calories: float = Field(ge=0, le=5000)
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
    def estimate_range(self):
        if self.calorie_range_low is None:
            self.calorie_range_low = round(max(0, self.calories * 0.8), 1)
        if self.calorie_range_high is None:
            self.calorie_range_high = round(min(5000, self.calories * 1.2), 1)
        if not self.calorie_range_low <= self.calories <= self.calorie_range_high:
            raise ValueError("逐项热量必须位于逐项估算区间内")
        return self


class FoodResult(BaseModel):
    model_config = ConfigDict(strict=True, allow_inf_nan=False, extra="ignore")
    dish_name: str = Field(min_length=1, max_length=120)
    calories: float = Field(ge=0, le=5000)
    protein: float = Field(ge=0, le=500)
    carbs: float = Field(ge=0, le=1000)
    fat: float = Field(ge=0, le=500)
    fiber: float = Field(default=0, ge=0, le=200)
    estimated_weight_g: float = Field(default=0, ge=0, le=5000)
    confidence: float = Field(ge=0, le=1)
    portion: str = Field(default="", max_length=120)
    cooking_method: str = Field(default="", max_length=120)
    tips: list[str] = Field(default_factory=list, max_length=6)
    provider: str = Field(default="local-vlm", max_length=40)
    model: str = Field(default="", max_length=120)
    source: str = Field(default="local", pattern="^(local|cloud)$")
    image_sha256: str = Field(default="", max_length=64)
    is_estimate: bool = True
    warning: str = Field(default="", max_length=500)
    visible_items: list[str] = Field(default_factory=list, max_length=8)
    portion_basis: str = Field(default="", max_length=240)
    calorie_range_low: float | None = Field(default=None, ge=0, le=5000)
    calorie_range_high: float | None = Field(default=None, ge=0, le=5000)
    uncertainty_reasons: list[str] = Field(default_factory=list, max_length=6)
    items: list[FoodItem] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def estimate_notice(self):
        self.is_estimate = True
        if any(
            len(item) > 160
            for item in [*self.tips, *self.visible_items, *self.uncertainty_reasons]
        ):
            raise ValueError("列表条目过长")
        if not self.items:
            self.items = [
                FoodItem(
                    name=self.dish_name,
                    portion=self.portion,
                    portion_basis=self.portion_basis,
                    weight_g=self.estimated_weight_g,
                    calories=self.calories,
                    calorie_range_low=self.calorie_range_low,
                    calorie_range_high=self.calorie_range_high,
                    protein=self.protein,
                    carbs=self.carbs,
                    fat=self.fat,
                    fiber=self.fiber,
                    confidence=self.confidence,
                    evidence="旧版整体估算，尚未拆分为独立食材证据。",
                    in_image=False,
                )
            ]
        item_total = round(sum(item.calories for item in self.items), 1)
        tolerance = max(20.0, self.calories * 0.1)
        if abs(item_total - self.calories) > tolerance:
            raise ValueError("整份热量与逐项热量合计偏差超过容差")
        self.calories = item_total
        self.calorie_range_low = round(
            sum(float(item.calorie_range_low or 0) for item in self.items), 1
        )
        self.calorie_range_high = round(
            sum(float(item.calorie_range_high or 0) for item in self.items), 1
        )
        self.estimated_weight_g = round(sum(item.weight_g for item in self.items), 1)
        self.protein = round(sum(item.protein for item in self.items), 1)
        self.carbs = round(sum(item.carbs for item in self.items), 1)
        self.fat = round(sum(item.fat for item in self.items), 1)
        self.fiber = round(sum(item.fiber for item in self.items), 1)
        if not self.calorie_range_low <= self.calories <= self.calorie_range_high:
            raise ValueError("热量估算必须位于热量区间内")
        if not self.visible_items:
            self.visible_items = [item.name for item in self.items if item.in_image]
        if not self.portion_basis:
            self.portion_basis = "依据图片可见份量估算，缺少比例尺时需人工确认。"
        if self.confidence < 0.6:
            self.warning = "识别置信度较低，请校正食物、份量与营养估算后再保存。"
        elif not self.warning:
            self.warning = "营养值为图片估算，请按实际份量校正。"
        return self
