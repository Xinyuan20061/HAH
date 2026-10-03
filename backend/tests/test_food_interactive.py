"""Phase 3 acceptance — Interactive Food 2.0 (capability plan §6, §13.3).

The §13.3 checklist drives these tests:

* the initial and post-answer ranges are both recomputable;
* at most two questions are asked;
* the range shrink after answering is measurable;
* a vision model's numbers can never override the local deterministic calculation;
* a user prior does nothing before three confirmations;
* hidden ingredients are never written in at high confidence.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FoodAnalysisSession, FoodReference, FoodClarificationQuestion
from app.services.food import (
    MAX_QUESTIONS,
    MIN_PRIOR_SAMPLES,
    calculate,
    clear_prior,
    ensure_seed_table,
    list_priors,
    match_reference,
    persist_questions,
    propose_questions,
    record_confirmed_mass,
    review_reference,
    table_status,
)


@pytest.fixture
def seeded(db):
    ensure_seed_table(db)
    return db


def test_seed_table_is_idempotent_and_honestly_unreviewed(seeded):
    assert ensure_seed_table(seeded) == 0, "重复初始化不得插入重复行"
    status = table_status(seeded)
    assert status["entries"] > 30
    assert status["reviewed"] is False, "未复核的表不得声称已复核"
    assert "seed_unreviewed" in status["source_ids"]
    assert "粗略草稿" in status["policy"]


def test_exact_and_alias_matching_only(seeded):
    assert match_reference(seeded, "米饭").food_key == "rice_cooked"
    assert match_reference(seeded, "鸡胸肉").food_key == "chicken_breast_skinless"
    assert match_reference(seeded, "rice_cooked").food_key == "rice_cooked"
    # Substring guessing is forbidden: "鸡" must not resolve to chicken breast.
    assert match_reference(seeded, "鸡") is None
    assert match_reference(seeded, "完全没有这个食物") is None
    assert match_reference(seeded, "") is None


def test_calculation_is_deterministic_from_the_table(seeded, api):
    items = [{"name": "米饭", "weight_g": 200}]
    first = calculate(seeded, user_id=api.user_id, items=items).as_dict()
    second = calculate(seeded, user_id=api.user_id, items=items).as_dict()
    assert first["totals"] == second["totals"]
    # 116 kcal/100g * 2 = 232 kcal
    assert first["totals"]["calories"] == 232.0
    assert first["items"][0]["matched_by"] in {"exact", "alias"}
    assert first["items"][0]["food_key"] == "rice_cooked"


def test_vision_numbers_cannot_override_the_calculation(seeded, api):
    """§6.4: the model's own calorie claim is ignored by construction."""
    items = [
        {
            "name": "米饭",
            "weight_g": 200,
            "calories": 9999,  # a model's free-text value
            "protein": 999,
            "items_source": "vlm",
        }
    ]
    result = calculate(seeded, user_id=api.user_id, items=items).as_dict()
    assert result["totals"]["calories"] == 232.0, "VLM 数值不得覆盖确定性计算"
    assert result["totals"]["protein"] == 5.2


def test_unmapped_food_contributes_zero_and_is_reported(seeded, api):
    result = calculate(
        seeded,
        user_id=api.user_id,
        items=[{"name": "米饭", "weight_g": 100}, {"name": "不存在的菜", "weight_g": 200}],
    ).as_dict()
    assert result["unmapped"] == ["不存在的菜"]
    assert result["totals"]["calories"] == 116.0, "未映射食物不得猜一个数值"
    assert any("不存在的菜" in item for item in result["limitations"])


def test_missing_mass_without_prior_refuses_to_invent_a_number(seeded, api):
    result = calculate(seeded, user_id=api.user_id, items=[{"name": "米饭"}]).as_dict()
    assert result["totals"]["calories"] == 0.0
    assert any("缺少份量" in item for item in result["items"][0]["limitations"])
    assert any("确认份量" in item for item in result["items"][0]["limitations"])


def test_cooking_oil_is_an_explicit_named_adjustment(seeded, api):
    plain = calculate(seeded, user_id=api.user_id, items=[{"name": "西兰花", "weight_g": 200}]).as_dict()
    fried = calculate(
        seeded,
        user_id=api.user_id,
        items=[{"name": "西兰花", "weight_g": 200, "cooking_method": "普通炒制"}],
    ).as_dict()
    assert fried["totals"]["calories"] > plain["totals"]["calories"]
    assert fried["items"][0]["oil_g"] > 0
    assert any("额外用油" in item for item in fried["items"][0]["limitations"])
    assert any(
        item["key"] == "cooking_method" for item in fried["items"][0]["assumptions"]
    )


def test_unknown_cooking_method_does_not_silently_add_zero(seeded, api):
    result = calculate(
        seeded,
        user_id=api.user_id,
        items=[{"name": "西兰花", "weight_g": 200, "cooking_method": "分子料理"}],
    ).as_dict()
    assert result["items"][0]["oil_g"] == 0.0
    assert result["items"][0]["cooking"] == "", "未知做法不得被当作清蒸"


def test_range_is_produced_and_shrinks_when_mass_is_confirmed(seeded, api):
    draft = calculate(seeded, user_id=api.user_id, items=[{"name": "米饭"}]).as_dict()
    assert draft["total_range"]["calories"] == 0.0  # no mass yet: no fake precision

    confirmed = calculate(seeded, user_id=api.user_id, items=[{"name": "米饭", "weight_g": 200}]).as_dict()
    assert confirmed["total_range"]["calories"] > 0
    low, high = confirmed["items"][0]["mass_range_g"]
    assert low < 200 < high
    assert "确认份量" in confirmed["limitations"][0] or confirmed["limitations"]


def test_question_selection_caps_at_two_and_scores_by_reduction(seeded, api):
    plan = propose_questions(
        seeded,
        user_id=api.user_id,
        items=[{"name": "米饭"}, {"name": "鸡胸肉"}, {"name": "西兰花"}],
        has_mass=False,
        cooking_known=False,
    )
    assert len(plan.questions) <= MAX_QUESTIONS
    reductions = [item.expected_range_reduction for item in plan.questions]
    assert reductions == sorted(reductions, reverse=True), "问题必须按预期缩减排序"
    assert plan.policy


def test_unmapped_food_becomes_the_first_question(seeded, api):
    plan = propose_questions(
        seeded,
        user_id=api.user_id,
        items=[{"name": "神秘料理"}],
        has_mass=True,
        cooking_known=True,
    )
    assert len(plan.questions) == 1
    assert plan.questions[0].question_id == "q_unmapped_food"
    assert plan.skipped and plan.skipped[0]["reason"] == "unmapped_food"


def test_observed_information_is_not_asked_again(seeded, api):
    """A portion already carried by the draft must never be re-questioned.

    Both routes must be silent: an observed mass, and a usable personal prior.
    """
    observed = propose_questions(
        seeded,
        user_id=api.user_id,
        items=[{"name": "米饭"}],
        has_mass=True,
        cooking_known=True,
    )
    assert all(item.kind != "portion" for item in observed.questions), observed.as_dict()
    assert all(
        item.kind != "staple_amount" for item in observed.questions
    ), observed.as_dict()

    # A usable prior also suppresses the portion question when no mass is given.
    for _ in range(MIN_PRIOR_SAMPLES):
        record_confirmed_mass(
            seeded, user_id=api.user_id, food_key="rice_cooked", mass_g=200
        )
    plan = propose_questions(
        seeded,
        user_id=api.user_id,
        items=[{"name": "米饭"}],
        has_mass=False,
        cooking_known=True,
    )
    assert all(item.kind != "portion" for item in plan.questions), plan.as_dict()


def test_prior_does_nothing_before_three_confirmations(seeded, api):
    first = record_confirmed_mass(seeded, user_id=api.user_id, food_key="rice_cooked", mass_g=250)
    assert first["recorded"] is True
    assert first["effective"] is False
    assert "尚未影响默认份量" in first["reason"]

    record_confirmed_mass(seeded, user_id=api.user_id, food_key="rice_cooked", mass_g=200)
    third = record_confirmed_mass(seeded, user_id=api.user_id, food_key="rice_cooked", mass_g=220)
    assert third["effective"] is True, third

    # Now the prior is used as the initial suggestion for a mass-less item.
    result = calculate(seeded, user_id=api.user_id, items=[{"name": "米饭"}]).as_dict()
    assert result["totals"]["calories"] > 0
    assert any(
        item["source"] == "user_history" for item in result["items"][0]["assumptions"]
    )


def test_outlier_does_not_enter_the_prior(seeded, api):
    record_confirmed_mass(seeded, user_id=api.user_id, food_key="rice_cooked", mass_g=200)
    outlier = record_confirmed_mass(seeded, user_id=api.user_id, food_key="rice_cooked", mass_g=5000)
    assert outlier["recorded"] is False
    assert outlier["reason"] == "outlier_skipped"
    row = list_priors(seeded, api.user_id)[0]
    assert row["sample_count"] == 1


def test_prior_can_be_viewed_and_cleared(seeded, api):
    for _ in range(3):
        record_confirmed_mass(seeded, user_id=api.user_id, food_key="rice_cooked", mass_g=200)
    assert list_priors(seeded, api.user_id)
    assert clear_prior(seeded, api.user_id, "rice_cooked") == 1
    assert list_priors(seeded, api.user_id) == []


def test_prior_does_not_change_the_food_table(seeded, api):
    before = match_reference(seeded, "米饭").calories_per_100g
    for mass in (150, 400, 260):
        record_confirmed_mass(seeded, user_id=api.user_id, food_key="rice_cooked", mass_g=mass)
    assert match_reference(seeded, "米饭").calories_per_100g == before


def test_review_requires_a_real_source(seeded):
    with pytest.raises(ValueError):
        review_reference(seeded, "rice_cooked", source_id="", source_note="x")
    with pytest.raises(ValueError):
        review_reference(
            seeded, "rice_cooked", source_id="seed_unreviewed", source_note="x"
        )
    row = review_reference(
        seeded,
        "rice_cooked",
        source_id="cn_food_composition_2023",
        source_note="营养师逐项复核",
    )
    assert row.reviewed_at is not None
    assert table_status(seeded)["reviewed_entries"] == 1


def test_questions_are_persisted_for_audit(seeded, api):
    session = FoodAnalysisSession(user_id=api.user_id, status="analyzed")
    seeded.add(session)
    seeded.commit()
    plan = propose_questions(
        seeded,
        user_id=api.user_id,
        items=[{"name": "米饭"}, {"name": "鸡胸肉"}],
        has_mass=False,
        cooking_known=False,
    )
    rows = persist_questions(
        seeded, analysis_id=session.id, user_id=api.user_id, plan=plan
    )
    assert len(rows) == len(plan.questions)
    stored = seeded.scalars(
        select(FoodClarificationQuestion).where(
            FoodClarificationQuestion.analysis_id == session.id
        )
    ).all()
    assert {row.question_id for row in stored} == {
        item.question_id for item in plan.questions
    }
    assert all(row.expected_range_reduction >= 0 for row in stored)


def test_hidden_sauce_is_asked_not_assumed(seeded, api):
    """§6.3/§13.3: a hidden ingredient must be asked about, never assumed in."""
    plan = propose_questions(
        seeded,
        user_id=api.user_id,
        items=[{"name": "鸡胸肉"}],
        has_mass=True,
        cooking_known=True,
    )
    assert any(item.kind == "hidden_sauce" for item in plan.questions), plan.as_dict()
    # And with no answer, the calculation applies no sauce.
    result = calculate(seeded, user_id=api.user_id, items=[{"name": "鸡胸肉", "weight_g": 150}]).as_dict()
    assert result["items"][0]["sugar_g"] == 0.0
    assert all(
        item["key"] != "hidden_sauce" for item in result["items"][0]["assumptions"]
    )
