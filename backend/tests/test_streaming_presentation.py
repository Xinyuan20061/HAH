import json

from app.services.ai.gateway import AIResult
from app.services.agent import orchestrator
from app.core.streaming import display_tokens


def _events(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_display_tokens_preserve_chinese_english_and_whitespace():
    source = "晚饭 use 30g protein.\n继续"
    assert "".join(display_tokens(source)) == source
    assert list(display_tokens("健康")) == ["健", "康"]


def test_agent_stream_waits_for_validated_result_and_keeps_structured_payload(api, monkeypatch):
    class MockProvider:
        async def chat(self, system, message):
            return AIResult(
                json.dumps(
                    {
                        "reply": "建议先从二十分钟快走开始。",
                        "facts_used": ["today"],
                        "plan": None,
                    },
                    ensure_ascii=False,
                ),
                "deepseek-system",
            )

    monkeypatch.setattr(orchestrator, "get_provider", lambda user: MockProvider())
    response = api.post(
        "/api/v1/agent/respond/stream", json={"message": "我今天适合怎么运动？"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")

    events = _events(response)
    assert events[0]["type"] == "meta"
    assert events[-1]["type"] == "done"
    assert events[-1]["provider"] == "deepseek-system"
    assert events[-1]["result"]["run_id"]
    assert events[-1]["result"]["trace"]["specialist"]
    # Real pipeline stages are reported, not a fabricated token stream: the meta
    # event carries the request/run linkage the client shows in the trace view.
    stages = [event for event in events if event["type"] == "stage"]
    assert stages, "阶段事件缺失"
    assert {item["stage"] for item in stages} >= {"router", "decision"}
    assert all(item["label"] for item in stages)
    assert events[0]["run_id"] == events[-1]["result"]["run_id"]
    # The reviewed answer is delivered as one complete event before any display
    # animation; the done event still carries the full structured result.
    answers = [event for event in events if event["type"] == "answer"]
    assert len(answers) == 1 and "二十分钟快走" in answers[0]["reply"]
    assert events[-1]["result"]["reply"] == answers[0]["reply"]
    reply = "".join(event["content"] for event in events if event["type"] == "delta")
    assert reply == answers[0]["reply"]
    assert len([event for event in events if event["type"] == "delta"]) > 3
