from datetime import datetime, timezone, timedelta

import pytest

from app.services.policy_learning.algorithm import (
    BetaBelief, Candidate, EpisodeEvidence, Point, Protocol, Scope, adjudicate,
    rank_candidates,
)


def _enable_personal_policy(api):
    installations = api.get("/api/v1/harness/installations").json()["installations"]
    current = next((row for row in installations if row["plugin_id"] == "personal_policy"), None)
    config = {
        "goal": "execution_pattern",
        "data_scopes": [
            "policy.goals.read", "policy.execution.read", "policy.outcomes.read",
            "health.profile.read", "health.records.read",
        ],
        "allow_action_proposals": True,
    }
    if current is None:
        response = api.post(
            "/api/v1/harness/installations",
            json={"plugin_id": "personal_policy", "config": config},
            headers={"Idempotency-Key": "policy-install-test"},
        )
        assert response.status_code == 200, response.text
        current = response.json()["installation"]
    else:
        response = api.patch(
            f"/api/v1/harness/installations/{current['installation_id']}",
            json={"config_version": current["config_version"], "config": config},
            headers={"Idempotency-Key": "policy-config-test"},
        )
        assert response.status_code == 200, response.text
        current = response.json()["installation"]
    preview = api.post(
        f"/api/v1/harness/installations/{current['installation_id']}/preview",
        json={"config_version": current["config_version"]},
        headers={"Idempotency-Key": "policy-preview-test"},
    )
    assert preview.status_code == 200, preview.text
    resumed = api.post(
        f"/api/v1/harness/installations/{current['installation_id']}/resume",
        json={"config_version": current["config_version"]},
        headers={"Idempotency-Key": "policy-resume-test"},
    )
    assert resumed.status_code == 200, resumed.text


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
    _enable_personal_policy(api)
    compiled = api.post("/api/v1/policy/compile", json={"template_id": "session_duration", "parameters": {"variant": "session_15m", "time_budget": "tight"}})
    assert compiled.status_code == 200, compiled.text
    payload = compiled.json()
    proposal = api.post(f"/api/v1/policy/units/{payload['strategy_unit_id']}/proposal", json={})
    # A fresh account has a hard insufficient-coverage constraint. Compilation is
    # still auditable, but starting requires that missing evidence to be resolved.
    if not payload["compiled"]:
        assert proposal.status_code == 422
        return
    assert proposal.status_code == 200, proposal.text
    confirmed = api.post(
        f"/api/v1/agent/actions/{proposal.json()['proposal_id']}/confirm",
        json={"version": proposal.json()["version"], "confirmation": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    current = api.get("/api/v1/policy/episodes/current")
    assert current.status_code == 200, current.text
    episode = current.json()["episode"]
    from app.models import PolicyExecutionOpportunity
    from sqlalchemy.orm import Session
    with Session(migrated_engine) as db:
        due_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
        for opportunity in db.query(PolicyExecutionOpportunity).filter_by(episode_id=episode["episode_id"]).all():
            opportunity.scheduled_at = due_at
        db.commit()
    version = episode["version"]
    for opportunity in episode["opportunities"]:
        report = api.post(f"/api/v1/policy/episodes/{episode['episode_id']}/reports", json={"episode_version": version, "report_id": f"r-{opportunity['slot']}", "opportunity_id": opportunity["id"], "execution": "completed"})
        assert report.status_code == 200, report.text
        version = report.json()["version"]
    points = []
    episode_start = datetime.fromisoformat(episode["start_at"].replace("Z", "+00:00"))
    baseline_at = (episode_start - timedelta(days=1)).isoformat()
    followup_at = datetime.now(timezone.utc).isoformat()
    for i in range(5):
        points.append({"slot": i, "value": 7, "source_type": "user_confirmed", "source_id": f"baseline-{i}", "source_revision": 1, "observed_at": baseline_at, "metric_version": "burden-v1", "endpoint": "baseline"})
        points.append({"slot": i, "value": 4, "source_type": "user_confirmed", "source_id": f"followup-{i}", "source_revision": 1, "observed_at": followup_at, "metric_version": "burden-v1", "endpoint": "followup"})
    observations = api.post(f"/api/v1/policy/episodes/{episode['episode_id']}/observations", json={"episode_version": version, "points": points})
    assert observations.status_code == 200, observations.text
    version = observations.json()["version"]
    repeated = api.post(f"/api/v1/policy/episodes/{episode['episode_id']}/observations", json={"episode_version": version, "points": points})
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["version"] == version

    # A corrected/deleted source retracts the old reference, but the user can
    # supply a replacement into the same slot instead of losing that slot.
    from app.models import PolicyObservationRef
    from app.services.policy_learning.outbox import invalidate_source
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    with Session(migrated_engine) as db:
        invalidate_source(db, api.user_id, "user_report", "followup-0", deleted=True)
        db.commit()
    replacement = dict(points[1], source_id="followup-0-corrected", value=5)
    replaced = api.post(
        f"/api/v1/policy/episodes/{episode['episode_id']}/observations",
        json={"episode_version": version, "points": [replacement]},
    )
    assert replaced.status_code == 200, replaced.text
    version = replaced.json()["version"]
    with Session(migrated_engine) as db:
        ref = db.scalar(select(PolicyObservationRef).where(
            PolicyObservationRef.episode_id == episode["episode_id"],
            PolicyObservationRef.endpoint == "followup",
            PolicyObservationRef.slot == 0,
        ))
        assert ref and ref.valid and ref.source_id == "followup-0-corrected"

    future_point = dict(
        points[1], slot=6, source_id="future-observation",
        observed_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    )
    future_observation = api.post(
        f"/api/v1/policy/episodes/{episode['episode_id']}/observations",
        json={"episode_version": version, "points": [future_point]},
    )
    assert future_observation.status_code == 422
    assert future_observation.json()["error"]["code"] == "EVIDENCE_SOURCE_IN_FUTURE"

    duplicate_slot = api.post(
        f"/api/v1/policy/episodes/{episode['episode_id']}/observations",
        json={"episode_version": version, "points": [replacement, dict(replacement, source_id="duplicate")]},
    )
    assert duplicate_slot.status_code == 422
    # The production endpoint deliberately refuses an early final adjudication;
    # move the fixture's frozen window into the past to test the real close path.
    from app.models import PolicyEpisode
    with Session(migrated_engine) as db:
        row = db.get(PolicyEpisode, episode["episode_id"])
        row.end_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
        db.commit()
    finished = api.post(f"/api/v1/policy/episodes/{episode['episode_id']}/finish", json={"episode_version": version})
    assert finished.status_code == 200, finished.text
    assert finished.json()["adjudication"]["conclusion"] == "supports_observed_target"
    with Session(migrated_engine) as db:
        invalidate_source(db, api.user_id, "user_report", "followup-0-corrected", deleted=True)
        db.commit()
    history = api.get("/api/v1/policy/history?limit=5")
    assert history.status_code == 200, history.text
    assert history.json()["items"][0]["conclusion_valid"] is False
