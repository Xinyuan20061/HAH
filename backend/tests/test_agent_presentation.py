from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models import HealthAgentRun, HealthPlan, User
from app.services.agent.presentation import (
    NavigationTarget,
    PRESENTATION_VERSION,
    build_plan_preview,
    build_presentation,
    is_plan_result_eligible,
)


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
