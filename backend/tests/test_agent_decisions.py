"""Stage-1 decision ledger contract tests (COMPETITIVE_DEVELOPMENT_PLAN §5).

Covers the read model that joins signal -> evidence -> proposal -> confirmed
action -> progress -> review by one decision_id, plus the user-isolation rule
and the §5 evidence shape exposed by /agent/insights.
"""

import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import create_access_token
from app.core.time import business_today
from app.models import AgentMicroExperiment, HealthCheckIn, User


def _other_client(api, migrated_engine):
    import app.main as main

    from fastapi.testclient import TestClient

    with Session(migrated_engine) as db:
        user = User(openid="dec-other-" + uuid.uuid4().hex)
        db.add(user)
        db.commit()
        other_id = user.id
    client = TestClient(main.app)
    client.headers["Authorization"] = "Bearer " + create_access_token(str(other_id))
    client.user_id = other_id
    return client


def test_decision_read_model_joins_signal_evidence_proposal_progress(api, migrated_engine):
    started = api.post(
        "/api/v1/agent/experiments",
        json={"insight_code": "record_gap", "variant": "gentle"},
    ).json()["experiment"]
    decision_id = started["decision_id"]
    assert decision_id.startswith("dec-")

    body = api.get(f"/api/v1/agent/decisions/{decision_id}")
    assert body.status_code == 200, body.text
    decision = body.json()
    assert decision["decision_id"] == decision_id
    assert decision["signal"]["code"] == "record_gap"
    assert decision["signal"]["observed_window"]

    evidence = decision["evidence"]
    assert {"observed_days", "expected_days"} <= set(evidence["data_coverage"])
    assert isinstance(evidence["knowledge_ids"], list)
    assert evidence["limitations"]
    assert evidence["evidence_type"] == "record_observation"

    proposal = decision["proposal"]
    assert proposal["requires_confirmation"] is True
    assert proposal["stop_condition"]
    assert [item["key"] for item in proposal["variants"]] == ["gentle", "standard"]
    assert proposal["chosen_variant"] == "gentle"

    progress = decision["progress"]
    assert progress["status"] == "active"
    assert decision["outcome"] is None
    event_types = [item["event_type"] for item in decision["timeline"]]
    assert "agent_experiment_started" in event_types
    assert "audit:experiment.start" in event_types

    with Session(migrated_engine) as db:
        row = db.get(AgentMicroExperiment, started["id"])
        assert row.decision_id == decision_id


def test_decision_review_supports_hypothesis_after_real_records(api, migrated_engine):
    started = api.post(
        "/api/v1/agent/experiments",
        json={"insight_code": "record_gap", "variant": "gentle"},
    ).json()["experiment"]
    today = business_today().isoformat()
    with Session(migrated_engine) as db:
        row = db.get(AgentMicroExperiment, started["id"])
        row.end_date = today
        row.target_json = json.dumps(
            {"mode": "threshold", "value": 1, "unit": "天", "requires_baseline": False}
        )
        db.add(HealthCheckIn(user_id=api.user_id, record_date=today, water_ml=1200, sleep_hours=7))
        db.commit()

    finished = api.post(f"/api/v1/agent/experiments/{started['id']}/finish", json={})
    assert finished.status_code == 200, finished.text

    decision = api.get(f"/api/v1/agent/decisions/{started['decision_id']}").json()
    assert decision["outcome"]["conclusion"] == "supports_hypothesis"
    assert decision["progress"]["status"] == "completed"
    fact_names = [fact["name"] for fact in decision["evidence"]["facts"]]
    assert any(name.endswith("_observed") for name in fact_names)
    event_types = [item["event_type"] for item in decision["timeline"]]
    assert "agent_experiment_completed" in event_types
    assert "audit:experiment.finish" in event_types


def test_decision_insufficient_data_keeps_limitations_without_positive_claim(api, migrated_engine):
    started = api.post(
        "/api/v1/agent/experiments",
        json={"insight_code": "record_gap", "variant": "gentle"},
    ).json()["experiment"]
    today = business_today().isoformat()
    with Session(migrated_engine) as db:
        row = db.get(AgentMicroExperiment, started["id"])
        row.end_date = today
        # Deliberately no health record: observed sample size stays 0.
        db.commit()

    finished = api.post(f"/api/v1/agent/experiments/{started['id']}/finish", json={})
    assert finished.status_code == 200, finished.text

    decision = api.get(f"/api/v1/agent/decisions/{started['decision_id']}").json()
    assert decision["outcome"]["conclusion"] == "insufficient_data"
    assert decision["progress"]["target_met"] is False
    assert any("记录不足" in limitation for limitation in decision["evidence"]["limitations"])
    assert decision["evidence"]["data_coverage"]["observed_days"] == 0
    assert decision["evidence"]["data_coverage"]["expected_days"] == 3


def test_decision_id_is_user_scoped_and_not_enumerable(api, migrated_engine):
    started = api.post(
        "/api/v1/agent/experiments",
        json={"insight_code": "record_gap", "variant": "gentle"},
    ).json()["experiment"]
    other = _other_client(api, migrated_engine)
    foreign = other.get(f"/api/v1/agent/decisions/{started['decision_id']}")
    assert foreign.status_code == 404
    unknown = other.get("/api/v1/agent/decisions/dec-0000000000000000")
    assert unknown.status_code == 404


def test_insights_expose_contract_evidence_and_action_timeline(api):
    body = api.get("/api/v1/agent/insights").json()
    item = next(x for x in body["insights"] if x["code"] == "record_gap")
    assert item["evidence_contract"]["evidence_type"] == "record_observation"
    assert {"observed_days", "expected_days"} <= set(item["evidence_contract"]["data_coverage"])
    assert item["evidence_contract"]["knowledge_ids"] == []
    assert item["action_timeline"] == []
    assert item["decision_id"] is None

    started = api.post(
        "/api/v1/agent/experiments",
        json={"insight_code": "record_gap", "variant": "gentle"},
    ).json()["experiment"]
    body = api.get("/api/v1/agent/insights").json()
    item = next(x for x in body["insights"] if x["code"] == "record_gap")
    assert item["decision_id"] == started["decision_id"]
    assert [entry["decision_id"] for entry in item["action_timeline"]] == [started["decision_id"]]
    assert item["action_timeline"][0]["status"] == "active"
