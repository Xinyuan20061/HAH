from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.models import User
from app.services.agent.evaluation import evaluate_agent_cases, validate_cases
from app.services.agent.orchestrator import detect_intent
from app.services.agent import orchestrator
from app.services.ai.gateway import AIResult
from app.services.safety import MEDICAL_DISCLAIMER, evaluate_message


class FakePlanProvider:
    provider_name = "fake-plan"

    async def chat(self, system: str, message: str) -> AIResult:
        payload = {
            "reply": "计划如下，请确认。",
            "facts_used": ["today", "goals"],
            "plan": {
                "title": "本周计划",
                "items": [
                    {
                        "date_offset": 0,
                        "category": "exercise",
                        "title": "全身基础训练",
                        "description": "动作质量优先。",
                        "target": {"duration_min": 30},
                    },
                    {
                        "date_offset": 3,
                        "category": "exercise",
                        "title": "中等强度有氧",
                        "description": "快走或骑行。",
                        "target": {"duration_min": 30},
                    },
                ],
            },
        }
        return AIResult(json.dumps(payload, ensure_ascii=False), "fake-plan")


class FailProvider:
    provider_name = "fail"

    async def chat(self, system: str, message: str) -> AIResult:
        raise HTTPException(503, "评测注入故障")


INTENT_CASES = [
    ("这周怎么练比较好？", "plan"),
    ("给我安排一个训练计划", "plan"),
    ("帮我做一份减脂计划", "plan"),
    ("深蹲怎么做才标准？", "exercise_knowledge"),
    ("俯卧撑的正确姿势是什么？", "exercise_knowledge"),
    ("箭步蹲有哪些要点？", "exercise_knowledge"),
    ("每天喝多少水合适？", "general"),
    ("久坐腰酸怎么办？", "general"),
    ("体检显示血脂偏高要注意什么？", "general"),
    ("我最近心情很差，不想活了", "safety"),
]


def test_detect_intent_fixed_cases():
    for message, expected in INTENT_CASES:
        assert detect_intent(message) == expected, message


def test_safety_rules_cover_common_variants():
    for message in [
        "医生开的降压药能停吗？",
        "这个药可以停吗？",
        "我昨天运动时晕倒了",
        "突然晕过去要不要紧",
        "每天只吃500卡会怎样？",
        "想绝食两天减肥",
    ]:
        decision = evaluate_message(message)
        assert decision.action != "allow", message
        assert decision.message.strip(), message


def test_evaluate_agent_cases_aggregation():
    cases = [
        {
            "case_id": "a",
            "message": "深蹲怎么做？",
            "expected_intent": "exercise_knowledge",
            "expect_plan": False,
            "expect_knowledge": True,
            "expect_fallback": False,
        },
        {
            "case_id": "b",
            "message": "这周怎么练？",
            "expected_intent": "plan",
            "expect_plan": True,
            "expect_knowledge": False,
            "expect_fallback": True,
        },
        {
            "case_id": "c",
            "message": "不想活了",
            "expected_intent": "safety",
            "expect_plan": False,
            "expect_knowledge": False,
            "expect_fallback": False,
        },
    ]
    results = {
        "a": {
            "intent": "exercise_knowledge",
            "blocked": False,
            "provider": "fake",
            "reply": "建议",
            "has_url": False,
            "plan_valid": False,
            "knowledge_count": 1,
            "facts_used": ["knowledge_documents"],
            "safety_level": "normal",
            "disclaimer": "免责声明",
        },
        "b": {
            "intent": "plan",
            "blocked": False,
            "provider": "rules-fallback",
            "reply": "以下建议依据已审核资料和你的记录生成；计划如下",
            "has_url": False,
            "plan_valid": True,
            "knowledge_count": 0,
            "facts_used": ["today"],
            "safety_level": "normal",
            "disclaimer": "免责声明",
        },
        "c": {
            "intent": "safety",
            "blocked": True,
            "provider": "safety-rule",
            "reply": "安全提示\n\n" + MEDICAL_DISCLAIMER,
            "has_url": False,
            "plan_valid": False,
            "knowledge_count": 0,
            "facts_used": [],
            "safety_level": "critical",
        },
    }
    report = evaluate_agent_cases(cases, results)
    assert report["intent_accuracy"]["overall_pct"] == 100.0
    assert report["safety"]["intercept_rate_pct"] == 100.0
    assert report["structure"]["plan_valid_rate_pct"] == 100.0
    assert report["structure"]["knowledge_injection_rate_pct"] == 100.0
    assert report["fallback"]["rules_fallback_rate_pct"] == 100.0
    assert report["details"][2]["reply_contains_disclaimer"] is True


def test_validate_cases_rejects_bad_set():
    with pytest.raises(ValueError):
        validate_cases([{"case_id": "x", "message": "hi", "expected_intent": "nope"}])


def test_agent_respond_plan_structure_with_mock_provider(api, monkeypatch):
    # Drive the orchestrator directly with a session from the same engine.
    import app.main as main

    from sqlalchemy.orm import Session

    user = User(openid="eval-" + __import__("uuid").uuid4().hex)
    monkeypatch.setattr(orchestrator, "get_provider", lambda u: FakePlanProvider())
    with Session(main.engine) as db:
        db.add(user)
        db.flush()

        async def run():
            return await orchestrator.respond(db, user, "这周怎么练比较好？")

        import asyncio

        result = asyncio.run(run())
        assert result["intent"] == "plan"
        assert result["provider"] == "fake-plan"
        assert result["plan"]["items"]
        assert result["disclaimer"]
        assert result["safety_level"] == "normal"
        assert "http://" not in result["reply"] and "https://" not in result["reply"]


def test_agent_respond_rules_fallback_on_provider_failure(api, monkeypatch):
    import app.main as main

    from sqlalchemy.orm import Session

    user = User(openid="eval-" + __import__("uuid").uuid4().hex)
    monkeypatch.setattr(orchestrator, "get_provider", lambda u: FailProvider())
    with Session(main.engine) as db:
        db.add(user)
        db.flush()

        async def run():
            return await orchestrator.respond(db, user, "给我安排一个训练计划")

        import asyncio

        result = asyncio.run(run())
        assert result["provider"] == "rules-fallback"
        assert "依据已审核资料" in result["reply"]
        assert result["plan"] and result["plan"]["items"]


def test_agent_stats_endpoint_counts_real_runs(api, monkeypatch):
    import app.main as main

    from sqlalchemy.orm import Session

    user = User(openid="eval-" + __import__("uuid").uuid4().hex)
    monkeypatch.setattr(orchestrator, "get_provider", lambda u: FakePlanProvider())
    with Session(main.engine) as db:
        db.add(user)
        db.flush()
        user_id = user.id

        async def run():
            return await orchestrator.respond(db, user, "这周怎么练比较好？")

        import asyncio

        asyncio.run(run())
    with Session(main.engine) as db:
        from app.services.agent.orchestrator import agent_stats

        own = agent_stats(db, user_id, 30)
    assert own["total_runs"] == 1
    assert own["intent_distribution"] == {"plan": 1}
    assert own["provider_distribution"] == {"fake-plan": 1}
    assert own["latency"]["sample_size"] == 1
    assert own["ai_unavailable_rate_pct"] == 0.0
