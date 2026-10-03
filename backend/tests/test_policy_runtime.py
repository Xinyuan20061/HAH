"""Runtime invariants that are easy to miss in pure algorithm tests."""

import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AgentMicroExperiment, PersonalStrategyUnit, PolicyOutbox
from app.services.privacy import export_preview
from app.services.policy_learning.outbox import process_pending_policy_events
from app.services.policy_learning.repository import learning_epoch


def _force_compiled(migrated_engine, user_id, payload):
    with Session(migrated_engine) as db:
        row = db.get(PersonalStrategyUnit, payload["strategy_unit_id"])
        row.status = "compiled"
        db.commit()


def test_active_legacy_experiment_blocks_policy_start(api, migrated_engine):
    compiled = api.post("/api/v1/policy/compile", json={"template_id": "session_duration", "parameters": {"variant": "short"}}).json()
    _force_compiled(migrated_engine, api.user_id, compiled)
    with Session(migrated_engine) as db:
        db.add(AgentMicroExperiment(user_id=api.user_id, decision_id=uuid4().hex, insight_code="x", title="x", primary_metric="x", start_date="2026-10-01", end_date="2026-10-07", status="active"))
        db.commit()
    response = api.post("/api/v1/policy/episodes/start", json={"strategy_unit_id": compiled["strategy_unit_id"], "protocol_hash": compiled["protocol_hash"], "version": 1})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "POLICY_EPISODE_ACTIVE"


def test_outbox_source_invalidation_is_idempotent(api, migrated_engine):
    with Session(migrated_engine) as db:
        event = PolicyOutbox(event_id=uuid4().hex, user_id=api.user_id, event_type="policy.source_invalidated", ref_id="diet_record:1", revision=1, payload=json.dumps({"strategy_ids": [], "context_keys": []}), status="pending")
        db.add(event)
        db.commit()
        first = process_pending_policy_events(db)
        second = process_pending_policy_events(db)
        assert first["processed"] == 1
        assert second["processed"] == 0
        assert db.get(PolicyOutbox, event.event_id).status == "processed"


def test_global_reset_changes_strategy_epoch_and_export_lists_policy(api, migrated_engine):
    with Session(migrated_engine) as db:
        before = learning_epoch(db, api.user_id, "session_duration:short")
    reset = api.post("/api/v1/policy/memory/reset", json={"scope": "all", "version": 1})
    assert reset.status_code == 200
    with Session(migrated_engine) as db:
        after = learning_epoch(db, api.user_id, "session_duration:short")
        assert before != after
        preview = export_preview(db, api.user_id)
        assert "policy_episodes" in preview["counts"]
        assert "policy_decisions" in preview["counts"]
