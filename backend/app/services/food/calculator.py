"""Deterministic nutrition calculation (capability plan §6.4).

The single rule this module enforces:

    final nutrient values are computed here, from the audited table and a mass the
    user confirmed — never copied from a vision model's free text.

Everything else follows from that:

* an unmapped food item produces **no** nutrient total (it is reported as
  unmapped) instead of borrowing the nearest row's numbers;
* cooking oil/sauce is added as an explicit, named adjustment so "why is this
  dish 200 kcal higher" is answerable;
* a range is produced from the mass uncertainty, so the output is an interval
  until the user confirms a portion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FoodReference, UserFoodPrior
from app.services.food.references import (
    match_reference,
    resolve_cooking_adjustment,
)

# A confirmed portion still carries a spread: the same "one bowl" differs between
# people. These multipliers bound the honest interval.
PORTION_LOW_FACTOR = 0.8
PORTION_HIGH_FACTOR = 1.25

# Minimum confirmations before a personal portion influences the initial guess
# (capability plan §6.5).
MIN_PRIOR_SAMPLES = 3

NUTRIENT_FIELDS = ("calories", "protein", "carbs", "fat", "fiber")


@dataclass
class NutrientTotal:
    calories: float = 0.0
    protein: float = 0.0
    carbs: float = 0.0
    fat: float = 0.0
    fiber: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {name: round(getattr(self, name), 1) for name in NUTRIENT_FIELDS}

    def scaled(self, factor: float) -> "NutrientTotal":
        return NutrientTotal(
            **{name: getattr(self, name) * factor for name in NUTRIENT_FIELDS}
        )

    def plus(self, other: "NutrientTotal") -> "NutrientTotal":
        return NutrientTotal(
            **{
                name: getattr(self, name) + getattr(other, name)
                for name in NUTRIENT_FIELDS
            }
        )


@dataclass
class ItemCalculation:
    """One item's deterministic result plus how it was produced."""

    name: str
    food_key: str | None
    matched_by: str  # exact | alias | unmapped
    mass_g: float
    mass_range_g: tuple[float, float]
    nutrients: NutrientTotal
    nutrient_range: NutrientTotal
    cooking: str = ""
    oil_g: float = 0.0
    sugar_g: float = 0.0
    assumptions: list[dict] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "food_key": self.food_key,
            "matched_by": self.matched_by,
            "mass_g": round(self.mass_g, 1),
            "mass_range_g": [round(self.mass_range_g[0], 1), round(self.mass_range_g[1], 1)],
            "nutrients": self.nutrients.as_dict(),
            "nutrient_range": self.nutrient_range.as_dict(),
            "cooking": self.cooking,
            "oil_g": round(self.oil_g, 1),
            "sugar_g": round(self.sugar_g, 1),
            "assumptions": self.assumptions,
            "limitations": self.limitations,
        }


@dataclass
class FoodCalculation:
    items: list[ItemCalculation]
    totals: NutrientTotal
    total_range: NutrientTotal
    unmapped: list[str]
    assumptions: list[dict]
    limitations: list[str]
    table_source_id: str = ""
    table_reviewed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "items": [item.as_dict() for item in self.items],
            "totals": self.totals.as_dict(),
            "total_range": self.total_range.as_dict(),
            "unmapped": self.unmapped,
            "assumptions": self.assumptions,
            "limitations": self.limitations,
            "table_source_id": self.table_source_id,
            "table_reviewed": self.table_reviewed,
            "policy": (
                "最终营养值由本地审核食物库 × 用户确认份量确定性计算得出；"
                "视觉模型只负责把可见项映射到 food_key，其自由文本数值不参与计算。"
            ),
        }


def _nutrients_for(reference: FoodReference, mass_g: float) -> NutrientTotal:
    factor = max(0.0, mass_g) / 100.0
    return NutrientTotal(
        calories=float(reference.calories_per_100g) * factor,
        protein=float(reference.protein_per_100g) * factor,
        carbs=float(reference.carbs_per_100g) * factor,
        fat=float(reference.fat_per_100g) * factor,
        fiber=float(reference.fiber_per_100g) * factor,
    )


def _oil_nutrients(oil_g: float) -> NutrientTotal:
    """Added cooking oil, computed from the table's own oil row."""
    factor = max(0.0, oil_g) / 100.0
    return NutrientTotal(calories=899.0 * factor, fat=99.9 * factor)


def _sugar_nutrients(sugar_g: float) -> NutrientTotal:
    factor = max(0.0, sugar_g) / 100.0
    return NutrientTotal(calories=400.0 * factor, carbs=99.9 * factor)


def personal_prior_mass(
    db: Session, user_id: int, food_key: str, context_key: str = "default"
) -> tuple[float | None, int]:
    """Return (median mass, sample count) when the prior is usable.

    Below ``MIN_PRIOR_SAMPLES`` the prior is ignored, so one unusual meal can
    never become this user's assumed portion (capability plan §6.5).
    """
    row = db.scalar(
        select(UserFoodPrior).where(
            UserFoodPrior.user_id == user_id,
            UserFoodPrior.food_key == food_key,
            UserFoodPrior.context_key == context_key,
        )
    )
    if row is None or row.sample_count < MIN_PRIOR_SAMPLES:
        return None, row.sample_count if row else 0
    return float(row.median_mass_g), row.sample_count


def calculate(
    db: Session,
    *,
    user_id: int,
    items: list[dict],
    cooking_method: str = "",
    context_key: str = "default",
) -> FoodCalculation:
    """Compute nutrients for a corrected draft.

    ``items`` entries are expected to carry ``name``, ``weight_g``/``mass_g`` and
    optionally ``cooking_method``. Anything the table cannot map is reported in
    ``unmapped`` and contributes **zero** to the totals, with a limitation that
    says so.
    """
    calculations: list[ItemCalculation] = []
    unmapped: list[str] = []
    assumptions: list[dict] = []
    limitations: list[str] = []
    source_ids: set[str] = set()
    any_unreviewed = False

    for entry in items:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("label") or "").strip()
        if not name:
            continue
        reference = match_reference(db, name)
        mass = entry.get("weight_g", entry.get("mass_g"))
        try:
            mass_g = float(mass) if mass not in (None, "") else 0.0
        except (TypeError, ValueError):
            mass_g = 0.0

        if reference is None:
            unmapped.append(name)
            calculations.append(
                ItemCalculation(
                    name=name,
                    food_key=None,
                    matched_by="unmapped",
                    mass_g=mass_g,
                    mass_range_g=(mass_g, mass_g),
                    nutrients=NutrientTotal(),
                    nutrient_range=NutrientTotal(),
                    limitations=[
                        f"「{name}」不在审核食物库中，因此不计入营养合计；"
                        "请从库中选择相近食物或由营养师补充条目"
                    ],
                )
            )
            continue

        source_ids.add(reference.source_id or "unknown")
        if reference.reviewed_at is None:
            any_unreviewed = True

        matched_by = "exact" if reference.name_zh == name else "alias"
        item_assumptions: list[dict] = []
        if mass_g <= 0:
            prior_mass, sample_count = personal_prior_mass(
                db, user_id, reference.food_key, context_key
            )
            if prior_mass is not None:
                mass_g = prior_mass
                item_assumptions.append(
                    {
                        "key": "portion",
                        "value": round(prior_mass, 1),
                        "source": "user_history",
                        "confidence_level": "medium",
                        "note": f"依据你过去 {sample_count} 次确认的个人份量",
                    }
                )
            else:
                # No mass and no usable prior: refuse to invent one.
                calculations.append(
                    ItemCalculation(
                        name=name,
                        food_key=reference.food_key,
                        matched_by=matched_by,
                        mass_g=0.0,
                        mass_range_g=(0.0, 0.0),
                        nutrients=NutrientTotal(),
                        nutrient_range=NutrientTotal(),
                        limitations=[
                            f"「{name}」缺少份量，无法计算营养值；"
                            f"需要你确认份量后再计算（个人份量样本 {sample_count}/{MIN_PRIOR_SAMPLES}）"
                        ],
                    )
                )
                continue

        oil_g = 0.0
        sugar_g = 0.0
        cooking_label = ""
        effective_cooking = str(
            entry.get("cooking_method") or cooking_method or ""
        ).strip()
        if effective_cooking:
            adjustment = resolve_cooking_adjustment(reference, effective_cooking)
            if adjustment is not None:
                oil_g = float(adjustment.get("oil_g_per_100g") or 0.0) * mass_g / 100.0
                sugar_g = (
                    float(adjustment.get("sugar_g_per_100g") or 0.0) * mass_g / 100.0
                )
                cooking_label = str(adjustment.get("label") or effective_cooking)
                item_assumptions.append(
                    {
                        "key": "cooking_method",
                        "value": cooking_label,
                        "source": "user_answer" if entry.get("cooking_method") else "visual",
                        "confidence_level": "medium",
                        "note": f"按「{cooking_label}」估算额外用油 {oil_g:.1f}g",
                    }
                )

        base = _nutrients_for(reference, mass_g)
        total = base.plus(_oil_nutrients(oil_g)).plus(_sugar_nutrients(sugar_g))
        low = total.scaled(PORTION_LOW_FACTOR)
        high = total.scaled(PORTION_HIGH_FACTOR)

        item_limitations: list[str] = []
        if reference.reviewed_at is None:
            item_limitations.append(
                f"食物库条目 {reference.food_key} 尚未经营养师逐项复核，数值为参考均值"
            )
        if oil_g > 0:
            item_limitations.append(
                f"额外用油 {oil_g:.1f}g 是按做法估算的，实际用油无法从照片观察"
            )
        if sugar_g > 0:
            item_limitations.append(f"额外糖分 {sugar_g:.1f}g 是按做法估算的")

        calculations.append(
            ItemCalculation(
                name=name,
                food_key=reference.food_key,
                matched_by=matched_by,
                mass_g=mass_g,
                mass_range_g=(mass_g * PORTION_LOW_FACTOR, mass_g * PORTION_HIGH_FACTOR),
                nutrients=total,
                nutrient_range=NutrientTotal(
                    calories=high.calories - low.calories,
                    protein=high.protein - low.protein,
                    carbs=high.carbs - low.carbs,
                    fat=high.fat - low.fat,
                    fiber=high.fiber - low.fiber,
                ),
                cooking=cooking_label,
                oil_g=oil_g,
                sugar_g=sugar_g,
                assumptions=item_assumptions,
                limitations=item_limitations,
            )
        )
        assumptions.extend(item_assumptions)
        limitations.extend(item_limitations)

    totals = NutrientTotal()
    for item in calculations:
        totals = totals.plus(item.nutrients)
    total_low = totals.scaled(PORTION_LOW_FACTOR)
    total_high = totals.scaled(PORTION_HIGH_FACTOR)
    total_range = NutrientTotal(
        calories=total_high.calories - total_low.calories,
        protein=total_high.protein - total_low.protein,
        carbs=total_high.carbs - total_low.carbs,
        fat=total_high.fat - total_low.fat,
        fiber=total_high.fiber - total_low.fiber,
    )

    if unmapped:
        limitations.append(
            "以下食物不在审核食物库中，未计入合计：" + "、".join(unmapped)
        )
    if any_unreviewed:
        limitations.append(
            "食物库条目尚未逐项营养师复核；当前只能称为粗略草稿，不能称为营养分析"
        )
    if not limitations:
        limitations.append("数值由审核食物库与确认份量确定性计算得出")

    return FoodCalculation(
        items=calculations,
        totals=totals,
        total_range=total_range,
        unmapped=unmapped,
        assumptions=assumptions,
        limitations=sorted(set(limitations)),
        table_source_id=",".join(sorted(source_ids)),
        table_reviewed=bool(source_ids) and not any_unreviewed,
    )
