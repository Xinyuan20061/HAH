from datetime import datetime, timezone, timedelta

import pytest

from app.services.policy_learning.algorithm import (
    BetaBelief, Candidate, EpisodeEvidence, Point, Protocol, Scope, adjudicate,
    rank_candidates,
)


def test_gate_missing_execution_is_not_failure():
    scope = Scope(1, "short", "1", "m1", "ctx")
    protocol = Protocol(scope, expected_days=3, minimum_days=2, minimum_coverage=0.6, execution_target=0.8)
    evidence = EpisodeEvidence("e1", 1, protocol, (True, None, False), followup=())
    verdict = adjudicate(evidence)
    assert verdict.execution_label is None
    assert verdict.support_label is None


def test_negative_observation_does_not_credit_support():
    scope = Scope(1, "short", "1", "m1", "ctx")
    protocol = Protocol(scope, expected_days=3, minimum_days=2, minimum_coverage=0.6, execution_target=0.6, direction="increase", target=1.0)
    baseline = tuple(Point(i, 1, f"b{i}", 1, "m1") for i in range(3))
    followup = tuple(Point(i, 1.1, f"f{i}", 1, "m1") for i in range(3))
    verdict = adjudicate(EpisodeEvidence("e1", 1, protocol, (True, True, True), baseline, followup, baseline_context="ctx", followup_context="ctx"))
    assert verdict.support_label == 0
    assert verdict.availability_label == 1


def test_hard_constraint_filters_even_positive_history():
    scope = Scope(1, "safe", "1", "m1", "ctx")
    ranked = rank_candidates({}, [Candidate("blocked", scope, 0.1, safe=False), Candidate("ok", scope, 0.1)], user_id=1, context_key="ctx")
    assert ranked["selected"] == "ok"
    assert ranked["filtered"][0]["reasons"] == ["hard_constraint"]


def test_registry_aggregation_is_not_silently_median_for_fraction():
    scope = Scope(1, "timing", "1", "m1", "ctx")
    protocol = Protocol(scope, expected_days=3, minimum_days=2, minimum_coverage=.6, execution_target=.6, mode="absolute", direction="increase", target=.7, ambiguity_band=.01, aggregation="fraction")
    points = tuple(Point(i, value, f"f{i}", 1, "m1") for i, value in enumerate((.2, .8, .9)))
    verdict = adjudicate(EpisodeEvidence("fraction", 1, protocol, (True, True, True), followup=points, followup_context="ctx"))
    assert verdict.observed_score == pytest.approx((.2 + .8 + .9) / 3)


def test_api_compile_start_report_and_review(api, migrated_engine):
    compiled = api.post("/api/v1/policy/compile", json={"template_id": "session_duration", "parameters": {"variant": "short", "time_budget": "tight"}})
    assert compiled.status_code == 200, compiled.text
    payload = compiled.json()
    started = api.post("/api/v1/policy/episodes/start", json={"strategy_unit_id": payload["strategy_unit_id"], "protocol_hash": payload["protocol_hash"], "version": 1})
    # A fresh account has a hard insufficient-coverage constraint. Compilation is
    # still auditable, but starting requires that missing evidence to be resolved.
    if not payload["compiled"]:
        assert started.status_code == 422
        return
    assert started.status_code == 200, started.text
    episode = started.json()
    version = episode["version"]
    for opportunity in episode["opportunities"]:
        report = api.post(f"/api/v1/policy/episodes/{episode['episode_id']}/reports", json={"episode_version": version, "report_id": f"r-{opportunity['slot']}", "opportunity_id": opportunity["id"], "execution": "completed"})
        assert report.status_code == 200, report.text
        version = report.json()["version"]
    points = []
    for i in range(5):
        points.append({"slot": i, "value": 7, "source_type": "user_confirmed", "source_id": f"baseline-{i}", "source_revision": 1, "observed_at": datetime.now(timezone.utc).isoformat(), "metric_version": "burden-v1", "endpoint": "baseline"})
        points.append({"slot": i, "value": 4, "source_type": "user_confirmed", "source_id": f"followup-{i}", "source_revision": 1, "observed_at": datetime.now(timezone.utc).isoformat(), "metric_version": "burden-v1", "endpoint": "followup"})
    observations = api.post(f"/api/v1/policy/episodes/{episode['episode_id']}/observations", json={"episode_version": version, "points": points})
    assert observations.status_code == 200, observations.text
    version = observations.json()["version"]
    # The production endpoint deliberately refuses an early final adjudication;
    # move the fixture's frozen window into the past to test the real close path.
    from app.models import PolicyEpisode
    from sqlalchemy.orm import Session
    with Session(migrated_engine) as db:
        row = db.get(PolicyEpisode, episode["episode_id"])
        row.end_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
        db.commit()
    finished = api.post(f"/api/v1/policy/episodes/{episode['episode_id']}/finish", json={"episode_version": version})
    assert finished.status_code == 200, finished.text
    assert finished.json()["adjudication"]["conclusion"] == "supports_observed_target"
