"""WP0 red tests — HARNESS-01 / §8 (durable Action proposal closure).

Confirmed defect: the Action Registry could only *block* a write and report
``approval_required``. Nothing was persisted, so a user could not confirm the
proposal later, an expired proposal was indistinguishable from a fresh one, and
repeating a confirm could double-write.
"""

from __future__ import annotations

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import create_access_token
from app.core.time import utc_now
from app.harness.contracts import ToolContext
from app.harness.tools import get_tool_registry
from app.models import AgentActionProposal, HealthAgentRun, User


@pytest.fixture
def db(migrated_engine):
    """Read/write session on the migrated test DB.

    It closes (not just commits) at teardown: the API writes through its own
    connection and a session holding an open SQLite read transaction would both
    lock the file and hide rows committed by the API.
    """
    with Session(migrated_engine) as session:
        yield session
        session.close()


def _release(db) -> None:
    """Hand the SQLite file lock back before the API writes.

    The test session is reused across the whole test; closing it releases the
    connection while keeping the Session object usable for later reads.
    """
    db.close()


def _run(db, user_id, result: dict | None = None) -> int:
    """Seed a completed run and return its id (never a detached ORM instance)."""
    run = HealthAgentRun(
        user_id=user_id,
        intent="plan",
        user_message="帮我排个训练计划",
        result_json=json.dumps(
            result
            or {
                "reply": "我整理了一份三天训练安排。",
                "plan": {
                    "title": "三天训练",
                    "items": [
                        {
                            "date_offset": 0,
                            "category": "exercise",
                            "title": "全身基础训练",
                            "description": "深蹲与俯卧撑",
                            "target": {"duration_min": 30},
                        }
                    ],
                },
            },
            ensure_ascii=False,
        ),
        provider="test",
        status="completed",
    )
    db.add(run)
    db.commit()
    return run.id


def _propose(
    db, user, run_id: int, action_key: str, arguments: dict, *, source: str = "agent"
):
    registry = get_tool_registry()
    context = ToolContext(db=db, user=user, agent_id="steward")
    # The orchestrator sets these on the tool context before the model runs; the
    # proposal must therefore carry the originating run id and source.
    context.state["run_id"] = run_id
    context.state["action_source"] = source
    observation = registry.execute(
        action_key,
        context,
        {"arguments": arguments, "user_visible_reason": "用户要求加入计划"},
        confirmed=False,
        step=1,
    )
    assert observation.status == "approval_required", observation.summary
    _release(db)
    return observation.output


def test_action_tool_persists_a_proposal(api, db):
    """§8.3: the registry persists a proposal instead of only blocking."""
    user = db.get(User, api.user_id)
    run_id = _run(db, api.user_id)
    payload = _propose(db, user, run_id, "plan.apply", {"run_id": run_id})
    assert payload["approval_required"] is True
    proposal_id = payload["proposal_id"]
    assert proposal_id.startswith("ap_")

    row = db.scalar(
        select(AgentActionProposal).where(AgentActionProposal.proposal_id == proposal_id)
    )
    # Read every attribute before the session releases its connection.
    snapshot = {
        "user_id": row.user_id,
        "run_id": row.run_id,
        "action_key": row.action_key,
        "status": row.status,
        "payload_hash": row.payload_hash,
        "expires_at": row.expires_at,
        "risk_level": row.risk_level,
        "display_json": row.display_json,
    }
    _release(db)
    assert snapshot["user_id"] == api.user_id
    assert snapshot["run_id"] == run_id
    assert snapshot["action_key"] == "plan.apply"
    assert snapshot["status"] == "pending"
    assert snapshot["payload_hash"]
    assert snapshot["expires_at"] is not None
    assert snapshot["risk_level"] == "low"
    display = json.loads(snapshot["display_json"])
    assert display["action_key"] == "plan.apply"
    assert "payload" not in display, "用户可见摘要不得包含原始执行载荷"


def test_action_tool_rejects_unknown_arguments(api, db):
    """§8.3: arguments are validated by the action-specific schema."""
    user = db.get(User, api.user_id)
    run_id = _run(db, api.user_id)
    registry = get_tool_registry()
    context = ToolContext(db=db, user=user, agent_id="steward")
    observation = registry.execute(
        "plan.apply",
        context,
        {"arguments": {"run_id": "not-an-int", "unexpected": True}},
        confirmed=False,
        step=1,
    )
    assert observation.status == "error", observation.summary
    assert db.scalars(select(AgentActionProposal)).all() == [], "非法载荷不得落库"


def test_action_proposal_for_unregistered_action_is_blocked(api, db):
    """§8.3: a blocked action never produces a proposal row."""
    user = db.get(User, api.user_id)
    registry = get_tool_registry()
    context = ToolContext(db=db, user=user, agent_id="steward")
    context.state["action_source"] = "agent"
    observation = registry.execute(
        "plan.apply",
        context,
        {"arguments": {"run_id": "not-an-int"}},
        confirmed=False,
        step=1,
    )
    assert observation.status == "error"
    assert db.scalars(select(AgentActionProposal)).all() == []


def test_proposal_confirm_executes_once(api, db):
    """§8.4: confirming executes the real domain service exactly once."""
    user = db.get(User, api.user_id)
    run_id = _run(db, api.user_id)
    proposal_id = _propose(db, user, run_id, "plan.apply", {"run_id": run_id})[
        "proposal_id"
    ]
    first = api.post(
        f"/api/v1/agent/actions/{proposal_id}/confirm",
        json={"version": 1, "confirmation": True, "typed_confirmation": None},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "executed"
    assert body["action_key"] == "plan.apply"
    assert body["audit_id"]
    assert body["result"]["plan"]["id"]

    second = api.post(
        f"/api/v1/agent/actions/{proposal_id}/confirm",
        json={"version": 1, "confirmation": True, "typed_confirmation": None},
    )
    assert second.status_code == 200
    assert second.json()["result"] == body["result"], "重复确认不得重复执行"

    from app.models import HealthPlan

    plans = db.scalars(
        select(HealthPlan).where(HealthPlan.user_id == api.user_id)
    ).all()
    assert len(plans) == 1, "重复确认创建了重复计划"


def test_proposal_cannot_be_confirmed_by_another_user(api, db, migrated_engine):
    """§8.4: a foreign proposal answers 404, never 403."""
    user = db.get(User, api.user_id)
    run_id = _run(db, api.user_id)
    proposal_id = _propose(db, user, run_id, "plan.apply", {"run_id": run_id})[
        "proposal_id"
    ]
    owner_header = api.headers["Authorization"]
    with Session(migrated_engine) as other_db:
        other = User(openid="other-" + uuid4().hex)
        other_db.add(other)
        other_db.commit()
        other_id = other.id
    api.headers["Authorization"] = "Bearer " + create_access_token(str(other_id))
    try:
        assert api.get(f"/api/v1/agent/actions/{proposal_id}").status_code == 404
        assert (
            api.post(
                f"/api/v1/agent/actions/{proposal_id}/confirm",
                json={"version": 1, "confirmation": True},
            ).status_code
            == 404
        )
        assert (
            api.post(f"/api/v1/agent/actions/{proposal_id}/reject", json={"version": 1}).status_code
            == 404
        )
    finally:
        api.headers["Authorization"] = owner_header


def test_proposal_reject_blocks_execution(api, db):
    """§8.4: a rejected proposal is terminal and cannot be confirmed afterwards."""
    user = db.get(User, api.user_id)
    run_id = _run(db, api.user_id)
    proposal_id = _propose(db, user, run_id, "plan.apply", {"run_id": run_id})[
        "proposal_id"
    ]
    rejected = api.post(f"/api/v1/agent/actions/{proposal_id}/reject", json={"version": 1})
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    confirmed = api.post(
        f"/api/v1/agent/actions/{proposal_id}/confirm",
        json={"version": 1, "confirmation": True},
    )
    assert confirmed.status_code == 409
    assert confirmed.json()["error"]["code"] == "ACTION_PROPOSAL_NOT_PENDING"


def test_expired_proposal_returns_410(api, db):
    """§8.4: an expired proposal answers 410 and must be re-proposed."""
    user = db.get(User, api.user_id)
    run_id = _run(db, api.user_id)
    proposal_id = _propose(db, user, run_id, "plan.apply", {"run_id": run_id})[
        "proposal_id"
    ]
    row = db.scalar(
        select(AgentActionProposal).where(AgentActionProposal.proposal_id == proposal_id)
    )
    row.expires_at = utc_now() - timedelta(minutes=1)
    db.add(row)
    db.commit()
    res = api.post(
        f"/api/v1/agent/actions/{proposal_id}/confirm",
        json={"version": 1, "confirmation": True},
    )
    assert res.status_code == 410
    assert res.json()["error"]["code"] == "ACTION_PROPOSAL_EXPIRED"


def test_payload_hash_mismatch_is_detected(api, db):
    """§8.4: a mutated payload answers 409 and must be re-proposed."""
    user = db.get(User, api.user_id)
    run_id = _run(db, api.user_id)
    proposal_id = _propose(db, user, run_id, "plan.apply", {"run_id": run_id})[
        "proposal_id"
    ]
    row = db.scalar(
        select(AgentActionProposal).where(AgentActionProposal.proposal_id == proposal_id)
    )
    row.payload_json = json.dumps({"run_id": run_id, "injected": True})
    db.add(row)
    db.commit()
    _release(db)
    res = api.post(
        f"/api/v1/agent/actions/{proposal_id}/confirm",
        json={"version": 1, "confirmation": True},
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "ACTION_PAYLOAD_HASH_MISMATCH"


def test_privacy_delete_requires_typed_confirmation(api, db):
    """§8.4: ``privacy.account.delete`` cannot run on a bare confirmation=true."""
    user = db.get(User, api.user_id)
    run_id = _run(db, api.user_id)
    proposal_id = _propose(
        db,
        user,
        run_id,
        "privacy.account.delete",
        {"reason": "user_request"},
        source="user",
    )["proposal_id"]
    res = api.post(
        f"/api/v1/agent/actions/{proposal_id}/confirm",
        json={"version": 1, "confirmation": True},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "TYPED_CONFIRMATION_REQUIRED"
    _release(db)
    assert db.get(User, api.user_id) is not None, "缺少二次确认却删除了账户"


def test_experiment_actions_stay_user_source_only(api, db):
    """§8.4: experiment.* keeps ``allowed_sources=("user",)``."""
    user = db.get(User, api.user_id)
    run_id = _run(db, api.user_id)
    registry = get_tool_registry()
    context = ToolContext(db=db, user=user, agent_id="steward")
    context.state["run_id"] = run_id
    context.state["action_source"] = "agent"
    observation = registry.execute(
        "experiment.start",
        context,
        {"arguments": {"insight_code": "sleep_debt"}},
        confirmed=False,
        step=1,
    )
    assert observation.status == "error", observation.status
    assert db.scalars(select(AgentActionProposal)).all() == [], (
        "模型不得为 experiment.* 创建 proposal"
    )


def test_agent_response_exposes_actions_array(api, db, monkeypatch):
    """§8.5: the client renders confirm cards from ``actions[]`` only."""

    async def fake_respond(db_, user_, message, agent_id="steward", channel="text"):
        return {
            "run_id": 501,
            "intent": "plan",
            "provider": "test",
            "reply": "我整理了一份三天训练安排，确认后可加入计划。",
            "plan": None,
            "actions": [
                {
                    "proposal_id": "ap_" + uuid4().hex[:16],
                    "action_key": "plan.apply",
                    "title": "加入本周计划",
                    "risk_level": "low",
                    "summary": "新增 3 项训练安排",
                    "expires_at": utc_now().isoformat() + "Z",
                }
            ],
            "trace": {
                "harness_trace_id": "trace-test",
                "route": {"mode": "single"},
                "tool_calls": [],
                "model_calls": 1,
                "elapsed_ms": 10,
                "budget": {"max_model_calls": 2, "used_model_calls": 1},
            },
        }

    import app.api.v1.agent as agent_api

    monkeypatch.setattr(agent_api, "respond", fake_respond)
    body = api.post("/api/v1/agent/respond", json={"message": "帮我排个计划"}).json()
    assert isinstance(body["actions"], list) and body["actions"]
    action = body["actions"][0]
    assert set(action) >= {
        "proposal_id",
        "action_key",
        "title",
        "risk_level",
        "summary",
        "expires_at",
    }
    assert body["trace"]["budget"]["used_model_calls"] <= body["trace"]["budget"][
        "max_model_calls"
    ]
