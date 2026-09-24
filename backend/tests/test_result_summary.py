# -*- coding: utf-8 -*-
"""Unit tests for the DeepSeek result-summary generator (no network)."""

import asyncio

from app.services import result_summary
from app.services.result_summary import (
    _food_facts,
    _motion_facts,
    attach_summary_into_result,
    generate_result_summary,
)


class _FakeAI:
    async def chat(self, system, message):
        return type("AI", (), {"text": "这餐总体均衡，热量接近今日目标，蛋白质略低，建议下一餐加一份豆制品。"})()


class _FakeUnavailable:
    async def chat(self, system, message):
        raise RuntimeError("down")


def _run(coro):
    return asyncio.run(coro)


def _user():
    return type("U", (), {"id": 1, "profile": None})()


def test_generate_food_summary_returns_text(monkeypatch):
    monkeypatch.setattr(result_summary, "get_provider", lambda user=None: _FakeAI())
    monkeypatch.setattr(
        result_summary, "_goal_context", lambda db, user_id, profile: "今日目标：热量 2000 kcal。"
    )
    result = {
        "dish_name": "番茄鸡蛋面",
        "calories": 520,
        "calorie_range_low": 440,
        "calorie_range_high": 600,
        "protein": 18,
        "carbs": 70,
        "fat": 14,
        "confidence": 0.8,
    }
    text = _run(generate_result_summary(None, _user(), result))
    assert isinstance(text, str) and len(text) > 10


def test_generate_motion_summary_returns_text(monkeypatch):
    monkeypatch.setattr(result_summary, "get_provider", lambda user=None: _FakeAI())
    monkeypatch.setattr(
        result_summary, "_goal_context", lambda db, user_id, profile: "今日目标：热量 2000 kcal。"
    )
    result = {
        "pose": {"exercise_type": "squat", "reps": 12, "available": True},
        "score": {"completeness": 88, "stability": 90, "rhythm_control": 80, "risk_index": 6, "overall": 86},
        "recognition": {"selected_type": "squat", "reason": "匹配度最高", "method": "rule_feature_matching_v1"},
    }
    text = _run(generate_result_summary(None, _user(), result))
    assert isinstance(text, str) and len(text) > 10


def test_generate_summary_none_on_provider_failure(monkeypatch):
    monkeypatch.setattr(result_summary, "get_provider", lambda user=None: _FakeUnavailable())
    monkeypatch.setattr(
        result_summary, "_goal_context", lambda db, user_id, profile: "今日目标：热量 2000 kcal。"
    )
    result = {"dish_name": "米饭", "calories": 200}
    assert _run(generate_result_summary(None, _user(), result)) is None


def test_generate_summary_none_for_unsupported_result(monkeypatch):
    monkeypatch.setattr(result_summary, "get_provider", lambda user=None: _FakeAI())
    assert _run(generate_result_summary(None, _user(), {"x": 1})) is None


def test_facts_and_attach():
    food = _food_facts({"dish_name": "粥", "calories": 300, "calorie_range_low": 250, "calorie_range_high": 350, "protein": 8, "carbs": 60, "fat": 3, "confidence": 0.7})
    assert "粥" in food and "300" in food
    motion = _motion_facts({"pose": {"exercise_type": "squat", "reps": 10}, "score": {"overall": 82}, "recognition": {"reason": "ok", "method": "rule_feature_matching_v1"}})
    assert "squat" in motion
    result = {}
    attach_summary_into_result(result, "一段总结")
    assert result["summary"] == "一段总结"
