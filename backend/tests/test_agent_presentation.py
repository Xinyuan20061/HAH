from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models import HealthAgentRun, HealthPlan, User
from app.services.agent.presentation import (
    NavigationTarget,
    PRESENTATION_VERSION,
    build_plan_preview,
    build_presentation,
    is_plan_result_eligible,
    requested_navigation_target,
)
from app.services.agent.orchestrator import detect_intent, respond


PLAN = {
    "title": "三天基础计划",
    "items": [
        {
            "date_offset": 1,
            "category": "exercise",
            "title": "全身训练",
            "description": "动作质量优先。",
            "target": {"duration_min": 25},
        }
    ],
}


def test_plan_presentation_uses_whitelist_and_never_claims_a_write():
    result = {
        "plan": PLAN,
        "actions": [],
        # A provider-controlled value must never survive reconstruction.
        "presentation": {"navigation": {"target": "https://evil.invalid"}},
    }
    view = build_presentation(
        intent="plan",
        specialist="planner",
        agent_id="xiaojian",
        run_id=41,
        result=result,
    )
    assert view == {
        "version": PRESENTATION_VERSION,
        "actor": "xiaojian",
        "cue": "plan.compose",
        "mood": "focused",
        "navigation": {
            "target": NavigationTarget.PLAN_PREVIEW.value,
            "mode": "after_animation",
            "params": {"run_id": 41},
        },
        "write": {
            "status": "not_applied",
            "automatic": False,
            "confirmation_required": True,
        },
    }


def test_explicit_navigation_covers_non_plan_pages_without_model_chosen_urls():
    cases = {
        "小康，打开记录页": NavigationTarget.RECORDS,
        "带我去饮食记录": NavigationTarget.DIET_RECORDS,
        "切换到七日趋势": NavigationTarget.TRENDS,
        "打开动作分析": NavigationTarget.MOTION_ANALYSIS,
        "去 AI 设置": NavigationTarget.AI_SETTINGS,
        "进入能力中心": NavigationTarget.CAPABILITY_CENTER,
        "回到养生馆": NavigationTarget.HOME,
        "打开小管家": NavigationTarget.STEWARD,
        "打开计划": NavigationTarget.PLAN_HOME,
    }
    for message, target in cases.items():
        assert requested_navigation_target(message) == target
        assert detect_intent(message) == "navigation"
        view = build_presentation(
            intent="navigation", specialist="general", agent_id="xiaokang",
            run_id=15, result={"safety_level": "normal", "plan": None},
            message=message,
        )
        assert view["navigation"] == {"target": target.value, "mode": "after_animation", "params": {}}
        assert view["write"]["automatic"] is False
    assert requested_navigation_target("今天训练计划怎么安排？") is None
    assert detect_intent("今天训练计划怎么安排？") == "plan"
    assert requested_navigation_target("不要打开记录页") is None
    assert requested_navigation_target("过去七天的运动记录怎么样？") is None
    assert requested_navigation_target("制定计划后打开计划页") is None


def test_navigation_is_blocked_by_safety_response():
    view = build_presentation(
        intent="safety", specialist="safety_guardian", agent_id="xiaojian",
        run_id=16, result={"safety_level": "critical", "plan": None},
        message="打开记录页",
    )
    assert view["navigation"]["target"] is None


def test_navigation_short_circuit_runs_without_llm_or_migration_fixture():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            user = User(openid="navigation-smoke")
            db.add(user)
            db.commit()
            turn = respond(db, user, "小康，打开七日趋势", agent_id="xiaokang")
            try:
                turn.send(None)
            except StopIteration as completed:
                response = completed.value
            else:
                raise AssertionError("明确页面导航不应等待模型或外部服务")
            assert response["intent"] == "navigation"
            assert response["provider"] == "navigation-rule"
            assert response["trace"]["model_calls"] == 0
            assert response["presentation"]["navigation"]["target"] == "trends"
    finally:
        engine.dispose()


def test_safety_presentation_has_no_navigation_even_if_result_contains_plan():
    view = build_presentation(
        intent="safety",
        specialist="safety_guardian",
        agent_id="xiaokang",
        run_id=8,
        result={"safety_level": "critical", "plan": PLAN},
    )
    assert view["cue"] == "safety.pause"
    assert view["navigation"] == {"target": None, "mode": "none", "params": {}}
    assert view["write"]["automatic"] is False


def test_plan_without_reviewed_draft_routes_to_capability_setup():
    view = build_presentation(
        intent="plan",
        specialist="planner",
        agent_id="xiaokang",
        run_id=12,
        result={"safety_level": "normal", "plan": None, "actions": []},
    )
    assert view["actor"] == "xiaokang"
    assert view["navigation"] == {
        "target": NavigationTarget.CAPABILITY_SETUP.value,
        "mode": "on_user_action",
        "params": {},
    }
    assert view["write"]["automatic"] is False


def test_plan_preview_eligibility_rejects_wrong_intent_and_safety_state():
    assert is_plan_result_eligible("plan", {"plan": PLAN}) is True
    assert is_plan_result_eligible("general", {"plan": PLAN}) is False
    assert is_plan_result_eligible("plan", {"plan": PLAN, "safety_level": "critical"}) is False
    assert is_plan_result_eligible("plan", []) is False


def test_plan_preview_is_read_only_and_re_sanitized():
    preview = build_plan_preview(
        9,
        {
            "plan": {
                "title": "一周计划" + "x" * 300,
                "items": [
                    {
                        "date_offset": 999,
                        "category": "exercise",
                        "title": "快走",
                        "description": "轻松完成",
                        "target": {"duration_min": 20},
                    }
                ],
            }
        },
    )
    assert preview is not None
    assert preview["run_id"] == 9
    assert preview["status"] == "draft"
    assert preview["read_only"] is True
    assert preview["items"][0]["date_offset"] == 6
    assert len(preview["title"]) == 160
    assert preview["write"]["confirmation_required"] is True
    assert preview["write"]["automatic"] is False


def test_applied_plan_preview_reports_observable_write_state():
    preview = build_plan_preview(9, {"plan": PLAN}, applied=True)
    assert preview is not None
    assert preview["status"] == "applied"
    assert preview["write"] == {
        "status": "applied",
        "automatic": False,
        "confirmation_required": False,
    }


def test_respond_and_stream_done_include_deterministic_presentation(api):
    # The safety short-circuit is deterministic and does not need a model or
    # personal data capability, making this an honest endpoint contract test.
    response = api.post(
        "/api/v1/agent/respond",
        json={"message": "我想用催吐减肥", "agent_id": "xiaokang"},
    )
    assert response.status_code == 200, response.text
    presentation = response.json()["presentation"]
    assert presentation["actor"] == "xiaokang"
    assert presentation["cue"] == "safety.pause"
    assert presentation["navigation"]["target"] is None

    streamed = api.post(
        "/api/v1/agent/respond/stream",
        json={"message": "我想用催吐减肥", "agent_id": "xiaokang"},
    )
    assert streamed.status_code == 200, streamed.text
    events = [json.loads(line) for line in streamed.text.splitlines() if line]
    done = next(item for item in events if item["type"] == "done")
    assert done["result"]["presentation"]["cue"] == "safety.pause"
    assert done["result"]["presentation"]["write"]["automatic"] is False


def test_xiaokang_routes_to_a_non_plan_page_without_model_call(api):
    response = api.post(
        "/api/v1/agent/respond",
        json={"message": "小康，打开七日趋势", "agent_id": "xiaokang"},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["intent"] == "navigation"
    assert result["provider"] == "navigation-rule"
    assert result["trace"]["model_calls"] == 0
    assert result["presentation"]["navigation"] == {
        "target": NavigationTarget.TRENDS.value,
        "mode": "after_animation",
        "params": {},
    }


def test_run_detail_exposes_owned_read_only_plan_preview(api, migrated_engine):
    with Session(migrated_engine) as db:
        own = HealthAgentRun(
            user_id=api.user_id,
            intent="plan",
            user_message="帮我排计划",
            result_json=json.dumps({"reply": "这是草案。", "plan": PLAN}, ensure_ascii=False),
            provider="test",
            status="completed",
        )
        foreign_user = User(openid="presentation-foreign-user")
        db.add_all([own, foreign_user])
        db.flush()
        foreign = HealthAgentRun(
            user_id=foreign_user.id,
            intent="plan",
            user_message="我的计划",
            result_json=json.dumps({"reply": "私有草案", "plan": PLAN}, ensure_ascii=False),
            provider="test",
            status="completed",
        )
        db.add(foreign)
        db.commit()
        own_id = own.id
        foreign_id = foreign.id

    response = api.get(f"/api/v1/agent/runs/{own_id}")
    assert response.status_code == 200, response.text
    preview = response.json()["plan_preview"]
    assert preview["run_id"] == own_id
    assert preview["read_only"] is True
    assert preview["write"]["status"] == "not_applied"

    denied = api.get(f"/api/v1/agent/runs/{foreign_id}")
    assert denied.status_code == 404


def test_run_detail_does_not_offer_confirmation_after_plan_was_applied(api, migrated_engine):
    with Session(migrated_engine) as db:
        run = HealthAgentRun(
            user_id=api.user_id,
            intent="plan",
            user_message="帮我排计划",
            result_json=json.dumps({"reply": "这是草案。", "plan": PLAN}, ensure_ascii=False),
            provider="test",
            status="completed",
        )
        db.add(run)
        db.flush()
        db.add(
            HealthPlan(
                user_id=api.user_id,
                title=PLAN["title"],
                period_start="2026-10-04",
                period_end="2026-10-10",
                source="agent",
                source_run_id=run.id,
                status="active",
            )
        )
        db.commit()
        run_id = run.id

    response = api.get(f"/api/v1/agent/runs/{run_id}")
    assert response.status_code == 200, response.text
    preview = response.json()["plan_preview"]
    assert preview["write"]["status"] == "applied"
    assert preview["write"]["confirmation_required"] is False
