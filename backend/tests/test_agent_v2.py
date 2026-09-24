"""Agent v2 evaluation: sub-agent routing, auditable traces, conversation memory.

The v2 contract adds three capabilities on top of agent-v1:
  1. Coordinator routes each intent to a role-specialised sub-agent
     (planner / coach / nutritionist / general) plus the safety guardian.
  2. Every response carries a `trace` (specialist, routing, evidence reasons,
     guardrail changes) so each suggestion can answer "why this?".
  3. Recent agent interactions are injected as conversation memory so
     "上次的计划" style references resolve without a fresh intent.
These tests use a mock provider; no real DeepSeek requests are made.
"""
import asyncio
import json

from sqlalchemy.orm import Session

from app.services.ai.gateway import AIResult
from app.services.agent import orchestrator
from app.services.agent.orchestrator import route_specialist, detect_intent
from app.services.agent.specialists import (
    SPECIALIST_VERSION,
    build_specialist_instruction,
    build_conversation_memory,
)


def test_route_specialist_maps_intents():
    assert route_specialist("plan", "这周怎么练") == "planner"
    assert route_specialist("exercise_knowledge", "深蹲怎么做") == "coach"
    assert route_specialist("general", "每天吃多少盐") == "nutritionist"
    assert route_specialist("general", "每天喝多少水") == "nutritionist"
    assert route_specialist("general", "久坐腰酸怎么办") == "general"
    assert route_specialist("safety", "不想活了") == "safety_guardian"


def test_specialist_instruction_contains_role_contract():
    for specialist in ["planner", "coach", "nutritionist", "general"]:
        payload = build_specialist_instruction(specialist, {}, [])
        parsed = json.loads(payload)
        assert parsed["specialist"] == specialist
        assert parsed["instruction"]
        assert "output_schema" in parsed


def test_conversation_memory_injects_summaries_only():
    runs = [
        {
            "intent": "plan",
            "provider": "deepseek-system",
            "user_message": "帮我定一周计划",
            "result_json": json.dumps(
                {"reply": "已生成基础计划", "plan": {"title": "本周计划", "items": []}},
                ensure_ascii=False,
            ),
        }
    ]
    memory = build_conversation_memory(runs)
    assert "最近健康助手交互" in memory
    assert "帮我定一周计划" in memory  # summary carries the user message for reference


def test_respond_trace_present_on_normal_path(api, monkeypatch):
    """Normal response carries specialist/trace regardless of provider output."""
    from app.services.ai.gateway import AIResult

    class MockProvider:
        async def chat(self, system, message):
            assert "specialist" in message
            return AIResult(
                json.dumps(
                    {"reply": "建议多喝水。", "facts_used": ["today"], "plan": None},
                    ensure_ascii=False,
                ),
                "deepseek-system",
            )

    monkeypatch.setattr(orchestrator, "get_provider", lambda user: MockProvider())
    response = api.post("/api/v1/agent/respond", json={"message": "每天喝多少水合适？"})
    assert response.status_code == 200
    body = response.json()
    trace = body.get("trace")
    assert trace and trace["specialist_version"] == SPECIALIST_VERSION
    assert trace["specialist"] == "nutritionist"
    assert trace["routing"]
    assert trace["provider"] == "deepseek-system"


def test_respond_trace_present_on_safety_block(api):
    response = api.post("/api/v1/agent/respond", json={"message": "我想用催吐减肥"})
    assert response.status_code == 200
    body = response.json()
    trace = body.get("trace")
    assert trace and trace["specialist"] == "safety_guardian"
    assert trace["routing"] == "输入安全评估拦截"
    assert body["safety_level"] != "normal"


def test_respond_memory_injected_on_second_turn(api, monkeypatch):
    """Second interaction must carry conversation memory of the first."""
    captured = {}

    class MockProvider:
        async def chat(self, system, message):
            captured["message"] = message
            return AIResult(
                json.dumps(
                    {"reply": "好的。", "facts_used": ["today"], "plan": None},
                    ensure_ascii=False,
                ),
                "deepseek-system",
            )

    monkeypatch.setattr(orchestrator, "get_provider", lambda user: MockProvider())
    api.post("/api/v1/agent/respond", json={"message": "帮我定一周计划"})
    api.post("/api/v1/agent/respond", json={"message": "上次的计划练得有点累"})
    assert "最近健康助手交互" in captured["message"]
    assert "帮我定一周计划" in captured["message"]
