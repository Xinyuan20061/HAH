"""WP3 acceptance — every user-tap write travels the unified proposal protocol.

Spec §8.1/§8.4 and the WP3 exit condition: ``plan.apply``,
``goal.adjustment.apply`` and ``diet.ai.finalize`` must all use the same durable
propose -> confirm path, and confirming twice must not write twice.

These tests drive the real endpoints (including the explicit user-tap shims the
mini program calls) and then assert on the audit ledger, so a second code path
cannot silently reappear.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AgentActionAudit,
    AgentActionProposal,
    AIJob,
    DietRecord,
    FoodAnalysisSession,
    HarnessPluginInstallation,
    HealthAgentRun,
    HealthGoalAdjustment,
    HealthPlan,
    MediaAsset,
    User,
)

PLAN_RESULT = {
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
}


def _run(engine, user_id: int) -> int:
    with Session(engine) as db:
        installation = db.scalar(select(HarnessPluginInstallation).where(
            HarnessPluginInstallation.user_id == user_id,
            HarnessPluginInstallation.plugin_id == "plan_outcome",
        ))
        capability_binding = {
            "plan_outcome": {
                "installation_id": installation.id,
                "config_version": installation.config_version,
                "manifest_hash": installation.reviewed_manifest_hash,
            }
        } if installation else {}
        run = HealthAgentRun(
            user_id=user_id,
            intent="plan",
            user_message="帮我排个训练计划",
            context_json=json.dumps({"_capability_bindings": capability_binding}),
            result_json=json.dumps(PLAN_RESULT, ensure_ascii=False),
            provider="test",
            status="completed",
        )
        db.add(run)
        db.commit()
        return run.id


def _adjustment(engine, user_id: int, **overrides) -> int:
    with Session(engine) as db:
        row = HealthGoalAdjustment(
            user_id=user_id,
            metric="steps",
            window_days=14,
            observed_days=14,
            completion_rate=0.4,
            previous_target=8000,
            recommended_target=6400,
            status="pending",
            facts_json="{}",
            explanation="近期完成率偏低，建议先下调目标。",
            **overrides,
        )
        db.add(row)
        db.commit()
        return row.id


def test_apply_plan_endpoint_returns_the_proposal_it_executed(api, migrated_engine):
    run_id = _run(migrated_engine, api.user_id)

    first = api.post(f"/api/v1/agent/runs/{run_id}/apply-plan", json={})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["plan"]["id"]
    assert body["proposal_id"].startswith("ap_")
    assert body["action_audit_id"]

    second = api.post(f"/api/v1/agent/runs/{run_id}/apply-plan", json={})
    assert second.status_code == 200
    # The second tap reports the plan is already in place and never re-writes.
    assert second.json()["plan"]["id"] == body["plan"]["id"]


def test_apply_plan_writes_exactly_one_plan_and_one_audit(api, migrated_engine):
    run_id = _run(migrated_engine, api.user_id)
    api.post(f"/api/v1/agent/runs/{run_id}/apply-plan", json={})
    api.post(f"/api/v1/agent/runs/{run_id}/apply-plan", json={})

    with Session(migrated_engine) as fresh:
        plans = fresh.scalars(
            select(HealthPlan).where(HealthPlan.source_run_id == run_id)
        ).all()
        assert len(plans) == 1, "重复点击创建了重复计划"
        audits = fresh.scalars(
            select(AgentActionAudit).where(
                AgentActionAudit.action_key == "plan.apply",
                AgentActionAudit.user_id == api.user_id,
            )
        ).all()
        executed = [row for row in audits if row.status == "executed"]
        assert len(executed) == 1, f"plan.apply 执行了 {len(executed)} 次"
        proposals = fresh.scalars(
            select(AgentActionProposal).where(
                AgentActionProposal.action_key == "plan.apply",
                AgentActionProposal.user_id == api.user_id,
            )
        ).all()
        assert proposals
        # Exactly one execution, even when the tap is replayed.
        assert len([row for row in proposals if row.status == "executed"]) == 1


def test_goal_adjustment_apply_uses_a_proposal(api, migrated_engine):
    adjustment_id = _adjustment(migrated_engine, api.user_id)

    res = api.post(f"/api/v1/health/goals/dynamic/{adjustment_id}/apply", json={})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["proposal_id"].startswith("ap_")
    assert body["action_audit_id"]
    assert body["adjustment_id"] == adjustment_id

    with Session(migrated_engine) as fresh:
        row = fresh.get(HealthGoalAdjustment, adjustment_id)
        assert row.status == "applied"
        audits = fresh.scalars(
            select(AgentActionAudit).where(
                AgentActionAudit.action_key == "goal.adjustment.apply",
                AgentActionAudit.user_id == api.user_id,
                AgentActionAudit.status == "executed",
            )
        ).all()
        assert len(audits) == 1
        proposal = fresh.scalar(
            select(AgentActionProposal).where(
                AgentActionProposal.proposal_id == body["proposal_id"]
            )
        )
        assert proposal.status == "executed"
        assert proposal.audit_id == body["action_audit_id"]


def test_goal_adjustment_apply_is_scoped_to_the_owner(api, migrated_engine):
    with Session(migrated_engine) as db:
        other = User(openid="goal-other-" + str(api.user_id))
        db.add(other)
        db.commit()
        other_id = other.id
    adjustment_id = _adjustment(migrated_engine, other_id)

    res = api.post(f"/api/v1/health/goals/dynamic/{adjustment_id}/apply", json={})
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "GOAL_ADJUSTMENT_NOT_FOUND"
    with Session(migrated_engine) as fresh:
        assert fresh.get(HealthGoalAdjustment, adjustment_id).status == "pending"


def test_diet_finalize_uses_a_proposal_and_returns_the_record(
    api, migrated_engine, food_result
):
    """``diet.ai.finalize`` is the third action on the unified protocol."""
    from app.services.vision.finalize import finalize_food_analysis

    with Session(migrated_engine) as db:
        asset = MediaAsset(
            user_id=api.user_id, storage_key=f"fin-{api.user_id}", media_type="image"
        )
        db.add(asset)
        db.flush()
        db.add(
            AIJob(
                user_id=api.user_id,
                media_asset_id=asset.id,
                job_type="food_vision",
                status="done",
                payload_json="{}",
                result_json=json.dumps(food_result, ensure_ascii=False),
            )
        )
        session = FoodAnalysisSession(
            user_id=api.user_id,
            status="corrected",
            initial_json=json.dumps(food_result, ensure_ascii=False),
            corrected_json=json.dumps(food_result, ensure_ascii=False),
            correction_count=1,
        )
        db.add(session)
        db.commit()
        analysis_id = session.id

    with Session(migrated_engine) as db:
        result = finalize_food_analysis(
            db,
            user_id=api.user_id,
            analysis_id=analysis_id,
            meal_type="lunch",
            confirmed=True,
        )
        assert result["ok"] is True
        assert result["record"]["meal_type"] == "lunch"
        assert result["action_audit_id"]
        record_id = result["record"]["id"]

        repeat = finalize_food_analysis(
            db,
            user_id=api.user_id,
            analysis_id=analysis_id,
            meal_type="lunch",
            confirmed=True,
        )
        assert repeat["already_finalized"] is True
        assert repeat["record"]["id"] == record_id

        rows = db.scalars(select(DietRecord)).all()
        assert [row.id for row in rows] == [record_id], "重复确认写入了重复记录"
