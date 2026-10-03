"""Information-maximising clarification engine (capability plan §6.3).

The product idea is that the system knows *what it does not know*. Given a draft
estimate, it asks at most two questions, chosen to shrink the calorie interval the
most, and never asks something the photo already answers.

    score(question) = expected_range_reduction * answerability * relevance

All three factors are explicit numbers here (not a model output), so the choice of
question is reviewable, and the cap is a constant rather than a prompt instruction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FoodClarificationQuestion, FoodReference, UserFoodPrior
from app.services.food.references import match_reference

MAX_QUESTIONS = 2

# Two different errors are being reduced, so they get two gates:
#   * a portion question shrinks the *interval* (random error);
#   * an oil/sauce question removes a *systematic bias* a photo can never show.
# A bias question's interval effect is inherently smaller, so gating it on the
# interval threshold would silently drop it — which is exactly how "we only ever
# asked about rice" happens.
MIN_USEFUL_REDUCTION = 0.08
MIN_USEFUL_BIAS_REDUCTION = 0.04


@dataclass(frozen=True)
class ClarificationOption:
    key: str
    label: str
    mass_factor: float = 1.0
    oil_g_per_100g: float = 0.0
    sugar_g_per_100g: float = 0.0
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "effect": {
                "mass_factor": self.mass_factor,
                "oil_g_per_100g": self.oil_g_per_100g,
                "sugar_g_per_100g": self.sugar_g_per_100g,
            },
            "note": self.note,
        }


@dataclass
class ClarificationQuestion:
    question_id: str
    kind: str
    prompt: str
    options: list[ClarificationOption]
    expected_range_reduction: float
    score: float = 0.0
    target_item: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "kind": self.kind,
            "prompt": self.prompt,
            "options": [item.as_dict() for item in self.options],
            "expected_range_reduction": round(self.expected_range_reduction, 4),
            "target_item": self.target_item,
        }


@dataclass
class ClarificationPlan:
    questions: list[ClarificationQuestion]
    skipped: list[dict] = field(default_factory=list)
    policy: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "questions": [item.as_dict() for item in self.questions],
            "skipped": self.skipped,
            "max_questions": MAX_QUESTIONS,
            "policy": self.policy,
        }


# --- question generators --------------------------------------------------- #

PORTION_OPTIONS = (
    ClarificationOption("half", "约半份", 0.5),
    ClarificationOption("one", "约一份", 1.0),
    ClarificationOption("one_and_half", "约一份半", 1.5),
)
OIL_OPTIONS = (
    ClarificationOption("steamed", "清蒸/水煮", 1.0, 0.0),
    ClarificationOption("normal", "普通用油炒制", 1.0, 5.0),
    ClarificationOption("heavy", "明显偏油或油炸", 1.0, 12.0),
)
SAUCE_OPTIONS = (
    ClarificationOption("none", "没有额外酱汁", 1.0, 0.0),
    ClarificationOption("some", "有一些酱汁", 1.0, 3.0, 1.0),
    ClarificationOption("lots", "酱汁较多", 1.0, 6.0, 2.0),
)
STAPLE_OPTIONS = (
    ClarificationOption("half_bowl", "半碗", 0.5),
    ClarificationOption("one_bowl", "一碗", 1.0),
    ClarificationOption("two_bowls", "两碗", 2.0),
)

STAPLE_GROUPS = {"staple"}
OIL_SENSITIVE_GROUPS = {"vegetable", "meat", "seafood", "soy"}
SAUCE_SENSITIVE_GROUPS = {"meat", "seafood", "vegetable"}
CONTAINER_GROUPS = {"staple", "meat", "seafood", "vegetable", "soy", "dairy"}


def _reduction(options: tuple[ClarificationOption, ...], *, sensitive: bool) -> float:
    """Expected interval shrink from a *portion* question.

    Only meaningful for options that vary the mass; an oil/sauce option set leaves
    ``mass_factor`` at 1.0 and therefore reduces the interval by nothing. Those
    questions are scored by ``_bias_reduction`` instead.
    """
    spread = max(item.mass_factor for item in options) - min(
        item.mass_factor for item in options
    )
    base = spread / (1.0 + spread)
    return round(base * (0.6 if sensitive else 0.85), 4)


# Reference mass used to size a bias question when the draft has no mass yet:
# the effect of oil is judged per typical serving, not per the unknown portion.
REFERENCE_MASS_G = 200.0


def _bias_reduction(
    options: tuple[ClarificationOption, ...], reference: FoodReference | None
) -> float:
    """Expected reduction of *systematic bias*, as a share of the item's calories.

    An oil/sauce question removes error that a photo can never show and that a
    portion question cannot cover. It is scored from the table: the calorie share of
    the largest adjustment on offer, relative to the food's own calories at a
    reference mass. Computed, not asserted.
    """
    if reference is None:
        return 0.0
    base_calories = float(reference.calories_per_100g) * REFERENCE_MASS_G / 100.0
    if base_calories <= 0:
        return 0.0
    worst_oil = max((item.oil_g_per_100g for item in options), default=0.0)
    worst_sugar = max((item.sugar_g_per_100g for item in options), default=0.0)
    added = (
        worst_oil * REFERENCE_MASS_G / 100.0 * 8.99
        + worst_sugar * REFERENCE_MASS_G / 100.0 * 4.0
    )
    if added <= 0:
        return 0.0
    share = added / (base_calories + added)
    return round(min(0.9, share), 4)


def propose_questions(
    db: Session,
    *,
    user_id: int,
    items: list[dict],
    has_mass: bool,
    cooking_known: bool,
    context_key: str = "default",
) -> ClarificationPlan:
    """Choose at most ``MAX_QUESTIONS`` questions, best expected reduction first."""
    from app.models import User  # noqa: F401  (keeps the FK import explicit)

    candidates: list[ClarificationQuestion] = []
    skipped: list[dict] = []

    matched: list[tuple[str, FoodReference]] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("label") or "").strip()
        if not name:
            continue
        reference = match_reference(db, name)
        if reference is None:
            skipped.append(
                {
                    "reason": "unmapped_food",
                    "item": name,
                    "note": "食物不在审核库中，先请用户确认是哪一种食物",
                }
            )
            continue
        matched.append((name, reference))

    # An unmapped item is the largest uncertainty by far: ask what it is first.
    if skipped:
        return ClarificationPlan(
            questions=[
                ClarificationQuestion(
                    question_id="q_unmapped_food",
                    kind="food_variant",
                    prompt="照片里的食物无法对应到食物库，它更接近下面哪一类？",
                    options=[
                        ClarificationOption("staple", "主食（米饭/面/馒头）"),
                        ClarificationOption("meat", "肉类"),
                        ClarificationOption("vegetable", "蔬菜"),
                        ClarificationOption("mixed_dish", "混合菜品"),
                    ],
                    expected_range_reduction=0.5,
                    score=1.0,
                    target_item=skipped[0]["item"],
                )
            ],
            skipped=skipped,
            policy=(
                "无法对应到审核食物库时，先确认食物类别，而不是套用相近食物的营养值。"
            ),
        )

    # 1. Portion question, only when the mass is genuinely unknown. Asking about a
    #    quantity the draft already carries (or that a usable personal prior
    #    already answers) wastes one of the two slots.
    for name, reference in matched:
        if has_mass:
            continue
        if _has_personal_prior(db, user_id, reference.food_key, context_key):
            skipped.append(
                {
                    "reason": "observed_or_known",
                    "item": name,
                    "note": "已有可用的个人份量先验，按先验给出初始建议，不重复追问",
                }
            )
            continue
        options = (
            STAPLE_OPTIONS if reference.food_group in STAPLE_GROUPS else PORTION_OPTIONS
        )
        candidates.append(
            ClarificationQuestion(
                question_id=f"q_portion_{reference.food_key}",
                kind="portion",
                prompt=f"「{name}」大约是多少？",
                options=list(options),
                expected_range_reduction=_reduction(options, sensitive=False),
                target_item=name,
            )
        )

    # 2. Cooking-oil question for dishes whose oil is invisible in a photo.
    if not cooking_known:
        for name, reference in matched:
            if reference.food_group not in OIL_SENSITIVE_GROUPS:
                continue
            candidates.append(
                ClarificationQuestion(
                    question_id=f"q_oil_{reference.food_key}",
                    kind="cooking_oil",
                    prompt=f"「{name}」的做法更接近哪一种？",
                    options=list(OIL_OPTIONS),
                    expected_range_reduction=_bias_reduction(OIL_OPTIONS, reference),
                    target_item=name,
                )
            )

    # 3. Hidden sauce question for dishes commonly served with sauce.
    for name, reference in matched:
        if reference.food_group not in SAUCE_SENSITIVE_GROUPS:
            continue
        candidates.append(
            ClarificationQuestion(
                question_id=f"q_sauce_{reference.food_key}",
                kind="hidden_sauce",
                prompt=f"「{name}」之外还有酱汁或汤汁需要一起记录吗？",
                options=list(SAUCE_OPTIONS),
                expected_range_reduction=_bias_reduction(SAUCE_OPTIONS, reference),
                target_item=name,
            )
        )

    # 4. Container / staple amount, only when the container is not visible.
    for name, reference in matched:
        if reference.food_group not in CONTAINER_GROUPS or reference.food_group in STAPLE_GROUPS:
            continue
        if has_mass:
            continue
        candidates.append(
            ClarificationQuestion(
                question_id=f"q_container_{reference.food_key}",
                kind="portion",
                prompt=f"「{name}」大约占容器多少？",
                options=list(PORTION_OPTIONS),
                expected_range_reduction=_reduction(PORTION_OPTIONS, sensitive=False) * 0.8,
                target_item=name,
            )
        )

    for index, question in enumerate(candidates):
        question.score = round(
            question.expected_range_reduction * _answerability(question), 4
        )
        del index
    candidates.sort(key=lambda item: (-item.score, item.question_id))

    chosen = _select(candidates)
    return ClarificationPlan(
        questions=chosen,
        skipped=skipped,
        policy=(
            f"最多追问 {MAX_QUESTIONS} 个问题。份量问题按「预期区间缩减」排序；"
            "用油/酱汁问题按「预期系统性偏差缩减」单独设阈值并保留一个位置，"
            "因为这类偏差照片无法观察、且不会被份量问题覆盖。"
            "照片已能观察到的信息不追问；用户不回答时保留宽区间，不生成伪精确点值。"
        ),
    )


def _select(candidates: list[ClarificationQuestion]) -> list[ClarificationQuestion]:
    """Pick up to ``MAX_QUESTIONS`` questions, guaranteeing kind diversity.

    Picking purely by expected reduction always favours portion questions (their
    spread is larger), which silently drops the oil/sauce questions — and those are
    the ones that remove a *systematic* bias a photo can never show. One slot is
    therefore reserved for them.
    """
    eligible = [
        item
        for item in candidates
        if item.expected_range_reduction
        >= (
            MIN_USEFUL_BIAS_REDUCTION
            if item.kind in {"cooking_oil", "hidden_sauce"}
            else MIN_USEFUL_REDUCTION
        )
    ]
    if not eligible:
        return []

    portion_kinds = {"portion", "staple_amount"}
    bias_kinds = {"cooking_oil", "hidden_sauce"}

    best_portion = next((item for item in eligible if item.kind in portion_kinds), None)
    best_bias = next((item for item in eligible if item.kind in bias_kinds), None)

    chosen: list[ClarificationQuestion] = []
    if best_portion is not None:
        chosen.append(best_portion)
    if best_bias is not None and len(chosen) < MAX_QUESTIONS:
        chosen.append(best_bias)

    # Fill any remaining slot with the best question not already chosen.
    for item in eligible:
        if len(chosen) >= MAX_QUESTIONS:
            break
        if item not in chosen:
            chosen.append(item)

    chosen.sort(key=lambda item: (-item.score, item.question_id))
    return chosen[:MAX_QUESTIONS]


def _answerability(question: ClarificationQuestion) -> float:
    """How easily a user can answer. Static per kind, declared, not learned."""
    return {
        "portion": 1.0,
        "cooking_oil": 0.9,
        "hidden_sauce": 0.85,
        "staple_amount": 1.0,
        "food_variant": 0.7,
    }.get(question.kind, 0.8)


def _has_personal_prior(
    db: Session, user_id: int, food_key: str, context_key: str
) -> bool:
    from app.services.food.calculator import MIN_PRIOR_SAMPLES

    row = db.scalar(
        select(UserFoodPrior).where(
            UserFoodPrior.user_id == user_id,
            UserFoodPrior.food_key == food_key,
            UserFoodPrior.context_key == context_key,
        )
    )
    return bool(row and row.sample_count >= MIN_PRIOR_SAMPLES)


def persist_questions(
    db: Session,
    *,
    analysis_id: int,
    user_id: int,
    plan: ClarificationPlan,
) -> list[FoodClarificationQuestion]:
    """Store the asked questions so the answers are auditable later."""
    rows: list[FoodClarificationQuestion] = []
    existing = {
        row.question_id: row
        for row in db.scalars(
            select(FoodClarificationQuestion).where(
                FoodClarificationQuestion.analysis_id == analysis_id
            )
        ).all()
    }
    for rank, question in enumerate(plan.questions, start=1):
        row = existing.get(question.question_id)
        if row is None:
            row = FoodClarificationQuestion(
                analysis_id=analysis_id,
                user_id=user_id,
                question_id=question.question_id,
                kind=question.kind,
                prompt=question.prompt,
                options_json=json.dumps(
                    [item.as_dict() for item in question.options], ensure_ascii=False
                ),
                expected_range_reduction=question.expected_range_reduction,
                rank=rank,
            )
            db.add(row)
        rows.append(row)
    db.commit()
    return rows


def apply_answers(
    options: list[ClarificationOption], option_key: str
) -> ClarificationOption | None:
    for option in options:
        if option.key == option_key:
            return option
    return None
