import base64
import json

from app.harness.contracts import ToolContext
from app.harness.collaboration import MultiAgentKernel, WORKERS
from app.harness.kernel import HARNESS_VERSION, ReActKernel
from app.harness.personas import get_persona, list_personas
from app.harness.tools import get_tool_registry
from app.harness.voice import SpeechAudio
from app.services.ai.gateway import AIResult


def test_three_product_agents_share_one_harness_contract():
    agents = {item["id"]: item for item in list_personas()}
    assert set(agents) == {"xiaojian", "xiaokang", "steward"}
    assert agents["xiaojian"]["capabilities"]["voice_input"] is True
    assert agents["xiaokang"]["capabilities"]["voice_output"] is True
    assert agents["steward"]["capabilities"]["text"] is True
    assert agents["steward"]["capabilities"]["voice_output"] is False


def test_registry_exposes_domain_reads_and_confirmation_gated_actions(api, db):
    from app.models import HealthAgentRun, User

    registry = get_tool_registry()
    tools = {item["name"]: item for item in registry.manifest("xiaojian")}
    assert "health.context.read" in tools
    assert "health.knowledge.search" in tools
    assert tools["plan.apply"]["kind"] == "action"
    assert tools["plan.apply"]["requires_confirmation"] is True

    run = HealthAgentRun(
        user_id=api.user_id,
        intent="plan",
        user_message="计划",
        result_json=json.dumps(
            {
                "plan": {
                    "title": "t",
                    "items": [
                        {
                            "date_offset": 0,
                            "category": "exercise",
                            "title": "a",
                            "description": "b",
                            "target": {},
                        }
                    ],
                }
            }
        ),
        provider="test",
        status="completed",
    )
    db.add(run)
    db.commit()
    context = ToolContext(db=db, user=db.get(User, api.user_id), agent_id="xiaojian")
    context.state["run_id"] = run.id
    context.state["action_source"] = "agent"
    observation = registry.execute(
        "plan.apply",
        context,
        {"arguments": {"run_id": run.id}},
    )
    # The write is not executed: it becomes a durable proposal awaiting the user
    # (spec §8.3).
    assert observation.status == "approval_required"
    assert observation.output["proposal_id"].startswith("ap_")

    coach_registry = registry.scoped(WORKERS["coach"].tools)
    assert "health.resources.search" in coach_registry.names()
    assert "plan.apply" not in coach_registry.names()


def test_react_kernel_can_observe_a_tool_then_finish_without_exposing_reasoning():
    class Provider:
        provider_name = "mock"

        def __init__(self):
            self.calls = 0

        async def chat(self, system, message):
            self.calls += 1
            assert "不要输出思维过程" in message
            if self.calls == 1:
                return AIResult(
                    json.dumps(
                        {"action": "tool", "tool": "harness.actions.list", "arguments": {}},
                        ensure_ascii=False,
                    ),
                    "mock",
                )
            assert "harness.actions.list" in message
            return AIResult(
                json.dumps(
                    {
                        "action": "final",
                        "result": {"reply": "先完成今天的一小步。", "facts_used": [], "plan": None},
                    },
                    ensure_ascii=False,
                ),
                "mock",
            )

    import asyncio

    result = asyncio.run(
        ReActKernel(get_tool_registry()).run(
            provider=Provider(),
            persona=get_persona("xiaojian"),
            tool_context=ToolContext(db=None, user=None, agent_id="xiaojian"),
            system="安全约束",
            task_prompt="用户请求：今天练什么",
        )
    )
    assert result.stop_reason == "final"
    assert result.result["reply"] == "先完成今天的一小步。"
    assert [item.tool for item in result.observations] == ["harness.actions.list"]


def test_multi_agent_kernel_routes_workers_and_decision_agent_arbitrates():
    class Provider:
        provider_name = "mock-multi"

        async def chat(self, system, message):
            if '"stage": "router"' in message:
                return AIResult(
                    json.dumps(
                        {
                            "action": "route",
                            "mode": "collaborative",
                            "workers": [
                                {"id": "coach", "task": "评估训练负荷"},
                                {"id": "recovery", "task": "评估恢复状态"},
                            ],
                            "reason": "训练与恢复需要共同判断",
                        },
                        ensure_ascii=False,
                    ),
                    "mock-multi",
                )
            if '"stage": "worker"' in message:
                role = "recovery" if '"specialist": "recovery"' in message else "coach"
                return AIResult(
                    json.dumps(
                        {
                            "action": "final",
                            "result": {
                                "reply": f"{role} 候选意见",
                                "facts_used": ["today"],
                                "plan": None,
                            },
                        },
                        ensure_ascii=False,
                    ),
                    "mock-multi",
                )
            assert '"stage": "decision"' in message
            assert "coach 候选意见" in message and "recovery 候选意见" in message
            return AIResult(
                json.dumps(
                    {
                        "action": "final",
                        "result": {
                            "reply": "今天降低训练量并优先恢复。",
                            "facts_used": ["today"],
                            "plan": None,
                        },
                    },
                    ensure_ascii=False,
                ),
                "mock-multi",
            )

    import asyncio

    result = asyncio.run(
        MultiAgentKernel(get_tool_registry()).run(
            provider=Provider(),
            persona=get_persona("xiaokang"),
            tool_context=ToolContext(db=None, user=None, agent_id="xiaokang"),
            system="安全约束",
            task_prompt="用户请求：今天很累但想训练",
            fallback_worker="coach",
        )
    )
    assert result.route.mode == "collaborative"
    assert [item.worker_id for item in result.reports] == ["coach", "recovery"]
    assert result.result["reply"] == "今天降低训练量并优先恢复。"
    assert result.trace_dict()["decision"]["stop_reason"] == "final"


def test_harness_manifest_and_voice_gateway_contract(api, monkeypatch):
    manifest = api.get("/api/v1/harness/manifest")
    assert manifest.status_code == 200
    body = manifest.json()
    assert body["version"] == HARNESS_VERSION
    assert body["architecture"] == "router-workers-decision"
    assert {item["id"] for item in body["workers"]} >= {"coach", "nutritionist", "planner"}
    assert body["policies"]["write_actions_require_confirmation"] is True
    assert body["policies"]["worker_tools_least_privilege"] is True

    from app.harness import voice as voice_gateway
    from app.harness.voice import TranscribeResult, TtsSegment, VoiceGateway

    class FakeVoice:
        name = "tencent_cloud"

        async def transcribe(self, audio, fmt):
            assert audio == b"voice-bytes"
            assert fmt == "mp3"
            return TranscribeResult(text="今天练十五分钟", provider_request_id="r1")

        async def synthesize_segment(self, text):
            assert text == "现在开始"
            return TtsSegment(audio=b"mp3-bytes", content_type="audio/mpeg", provider_request_id="r2")

    def fake_gateway(user, db):
        return VoiceGateway(FakeVoice(), getattr(user, "id", None), db)

    monkeypatch.setattr(voice_gateway, "build_voice_gateway", fake_gateway)
    transcription = api.post(
        "/api/v1/harness/voice/transcribe",
        json={
            "agent_id": "xiaojian",
            "format": "mp3",
            "audio_base64": base64.b64encode(b"voice-bytes").decode("ascii"),
        },
    )
    assert transcription.status_code == 200
    assert transcription.json()["text"] == "今天练十五分钟"

    speech = api.post(
        "/api/v1/harness/voice/synthesize",
        json={"agent_id": "xiaojian", "text": "现在开始"},
    )
    assert speech.status_code == 200
    # New segmented contract, with legacy single-segment compatibility field.
    assert speech.json()["segments"][0]["audio_base64"]
    assert base64.b64decode(speech.json()["audio_base64"]) == b"mp3-bytes"

    blocked = api.post(
        "/api/v1/harness/voice/synthesize",
        json={"agent_id": "steward", "text": "计划"},
    )
    assert blocked.status_code == 400


def test_agent_response_selects_persona_without_breaking_default(api, monkeypatch):
    from app.services.agent import orchestrator

    class Provider:
        provider_name = "mock"

        async def chat(self, system, message):
            assert "你是小康" in system
            return AIResult(
                json.dumps(
                    {"reply": "今晚早点休息，先把节奏慢下来。", "facts_used": ["today"], "plan": None},
                    ensure_ascii=False,
                ),
                "mock",
            )

    monkeypatch.setattr(orchestrator, "get_provider", lambda user: Provider())
    response = api.post(
        "/api/v1/agent/respond",
        json={"message": "今天有点累", "agent_id": "xiaokang", "channel": "text"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["agent"]["id"] == "xiaokang"
    assert body["trace"]["harness_version"] == HARNESS_VERSION
    assert body["trace"]["loop"] == "router-workers-decision"
    assert body["trace"]["multi_agent"]["route"]["workers"]
    assert body["trace"]["tool_calls"]
