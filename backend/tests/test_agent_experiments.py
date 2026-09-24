"""Agent v4 micro-experiment contract and consent/audit tests."""

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import business_today
from app.models import AgentActionAudit, AgentMicroExperiment, HealthCheckIn
from app.services.agent.experiments import build_experiment_proposal


def test_every_proactive_signal_has_two_bounded_experiment_choices():
    for code in ("record_gap", "exercise_stall", "sleep_deficit", "weight_rise", "motion_decline"):
        proposal = build_experiment_proposal(code)
        assert proposal["version"] == "v4.0"
        assert [item["key"] for item in proposal["variants"]] == ["gentle", "standard"]
        assert all(3 <= item["days"] <= 7 for item in proposal["variants"])
        assert proposal["stop_condition"]
        assert "不能证明因果" in proposal["boundary"]


def test_insight_exposes_experiment_proposal_without_silently_starting(api, migrated_engine):
    body = api.get("/api/v1/agent/insights").json()
    insight = next(item for item in body["insights"] if item["code"] == "record_gap")
    assert insight["experiment_proposal"]["primary_metric"] == "recorded_days"
    assert body["active_experiment"] is None
    with Session(migrated_engine) as db:
        assert db.scalar(select(AgentMicroExperiment)) is None


def test_micro_experiment_requires_active_signal_and_explicit_choice(api):
    inactive = api.post(
        "/api/v1/agent/experiments",
        json={"insight_code": "sleep_deficit", "variant": "gentle"},
    )
    assert inactive.status_code == 409
    invalid_variant = api.post(
        "/api/v1/agent/experiments",
        json={"insight_code": "record_gap", "variant": "extreme"},
    )
    assert invalid_variant.status_code == 422


def test_start_progress_and_cancel_are_user_controlled_and_audited(api, migrated_engine):
    started = api.post(
        "/api/v1/agent/experiments",
        json={"insight_code": "record_gap", "variant": "gentle"},
    )
    assert started.status_code == 200, started.text
    experiment = started.json()["experiment"]
    assert experiment["status"] == "active"
    assert experiment["protocol"]["days"] == 3
    assert experiment["baseline"]["sample_size"] == 0
    assert "不证明因果" in experiment["boundary"]

    duplicate = api.post(
        "/api/v1/agent/experiments",
        json={"insight_code": "record_gap", "variant": "standard"},
    )
    assert duplicate.status_code == 409

    listing = api.get("/api/v1/agent/experiments").json()
    assert listing["active"]["id"] == experiment["id"]
    assert listing["active"]["progress"]["value"] == 0

    cancelled = api.post(f"/api/v1/agent/experiments/{experiment['id']}/cancel", json={})
    assert cancelled.status_code == 200
    assert cancelled.json()["experiment"]["status"] == "cancelled"

    with Session(migrated_engine) as db:
        actions = db.scalars(
            select(AgentActionAudit)
            .where(AgentActionAudit.user_id == api.user_id)
            .order_by(AgentActionAudit.id)
        ).all()
        assert [item.action_key for item in actions] == ["experiment.start", "experiment.cancel"]
        assert all(item.status == "executed" for item in actions)


def test_finish_freezes_observed_outcome_without_causal_claim(api, migrated_engine):
    started = api.post(
        "/api/v1/agent/experiments",
        json={"insight_code": "record_gap", "variant": "gentle"},
    ).json()["experiment"]
    early = api.post(f"/api/v1/agent/experiments/{started['id']}/finish", json={})
    assert early.status_code == 409

    today = business_today().isoformat()
    with Session(migrated_engine) as db:
        row = db.get(AgentMicroExperiment, started["id"])
        row.end_date = today
        row.target_json = json.dumps({"mode": "threshold", "value": 1, "unit": "天", "requires_baseline": False})
        db.add(HealthCheckIn(user_id=api.user_id, record_date=today, water_ml=1200, sleep_hours=7))
        db.commit()

    finished = api.post(f"/api/v1/agent/experiments/{started['id']}/finish", json={})
    assert finished.status_code == 200, finished.text
    experiment = finished.json()["experiment"]
    assert experiment["status"] == "completed"
    assert experiment["outcome"]["conclusion"] == "supports_hypothesis"
    assert experiment["outcome"]["target_met"] is True
    assert "不能证明因果" in experiment["outcome"]["attribution"]

    repeated = api.post(f"/api/v1/agent/experiments/{started['id']}/finish", json={})
    assert repeated.status_code == 200
    assert repeated.json()["already_finished"] is True


def test_finish_without_enough_records_returns_insufficient_data(api, migrated_engine):
    started = api.post(
        "/api/v1/agent/experiments",
        json={"insight_code": "record_gap", "variant": "gentle"},
    ).json()["experiment"]

    today = business_today().isoformat()
    with Session(migrated_engine) as db:
        row = db.get(AgentMicroExperiment, started["id"])
        row.end_date = today
        # Deliberately add no health record: the observed sample size stays 0.
        db.commit()

    finished = api.post(f"/api/v1/agent/experiments/{started['id']}/finish", json={})
    assert finished.status_code == 200, finished.text
    experiment = finished.json()["experiment"]
    assert experiment["status"] == "completed"
    assert experiment["outcome"]["conclusion"] == "insufficient_data"
    assert experiment["outcome"]["followup"]["sample_size"] == 0
    assert "记录不足" in experiment["outcome"]["summary"]
    assert experiment["progress"]["target_met"] is False


def test_evaluation_dashboard_counts_only_confirmed_experiments(api, migrated_engine):
    dashboard = api.get("/api/v1/evaluation/dashboard").json()
    assert dashboard["runtime_metrics"]
    started = api.post(
        "/api/v1/agent/experiments",
        json={"insight_code": "record_gap", "variant": "gentle"},
    ).json()["experiment"]
    api.post(f"/api/v1/agent/experiments/{started['id']}/cancel", json={})

    updated = api.get("/api/v1/evaluation/dashboard").json()
    by_key = {item["key"]: item for item in updated["runtime_metrics"]}
    assert by_key["experiments_started"]["value"] == 1
    assert by_key["experiments_cancelled"]["value"] == 1
    assert by_key["experiments_completion_rate"]["value"] == 0.0
    assert updated["experiment_outcomes"] == {"cancelled_by_user": 1}


def test_proposal_history_reflects_past_confirmed_choices(api):
    def history_of(code):
        body = api.get("/api/v1/agent/insights").json()
        item = next(x for x in body["insights"] if x["code"] == code)
        return item["proposal_history"]

    assert history_of("record_gap")["total"] == 0
    assert history_of("record_gap")["preferred"] is None

    api.post("/api/v1/agent/experiments", json={"insight_code": "record_gap", "variant": "gentle"})
    history = history_of("record_gap")
    assert history["counts"] == {"gentle": 1}
    assert history["preferred"] == "gentle"
    assert history["total"] == 1
    assert "不改变安全规则" in history["policy"]

    # A later confirmed choice is counted too; preference is explainable, not a lock.
    started = api.get("/api/v1/agent/experiments").json()["active"]
    api.post(f"/api/v1/agent/experiments/{started['id']}/cancel", json={})
    api.post("/api/v1/agent/experiments", json={"insight_code": "record_gap", "variant": "standard"})
    history = history_of("record_gap")
    assert history["counts"] == {"gentle": 1, "standard": 1}
    assert history["preferred"] in {"gentle", "standard"}
    assert history["total"] == 2
