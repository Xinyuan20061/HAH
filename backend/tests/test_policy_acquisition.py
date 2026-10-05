"""Production-surface checks for exact proofs and the acquisition journey."""

from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
import json
from itertools import product
from threading import Barrier
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import (
    HarnessPluginInstallation, KnowledgeDocument, PersonalStrategyUnit, PolicyAcquisitionQuestion,
    PolicyAcquisitionDailyUsage, PolicyAcquisitionFence,
    PolicyAcquisitionSession, PolicyAdjudication, PolicyEpisode, PolicyExecutionOpportunity,
    PolicyObservationRef, PolicyDecisionCertificate, PolicyReport,
)
from app.services.policy_learning.acquisition.contracts import AnswerRequest, StartAcquisitionRequest
from app.services.policy_learning.acquisition.oracle import execution_proof
from app.services.policy_learning.acquisition.planner import (
    Branch, Query, SupportPairDomain, choose_next,
)
from app.services.policy_learning.compiler import compile_strategy


def _enable_policy_acquisition(api):
    rows = api.get("/api/v1/harness/installations").json()["installations"]
    row = next(item for item in rows if item["plugin_id"] == "personal_policy")
    config = {
        "goal": "execution_pattern",
        "data_scopes": ["policy.goals.read", "policy.execution.read", "policy.outcomes.read",
                        "health.profile.read", "health.records.read", "health.knowledge.read"],
        "allow_action_proposals": True,
    }
    updated = api.patch(
        f"/api/v1/harness/installations/{row['installation_id']}",
        json={"config_version": row["config_version"], "config": config},
        headers={"Idempotency-Key": "acq-install-config"},
    )
    assert updated.status_code == 200, updated.text
    installation = updated.json()["installation"]
    preview = api.post(
        f"/api/v1/harness/installations/{installation['installation_id']}/preview",
        json={"config_version": installation["config_version"]},
        headers={"Idempotency-Key": "acq-install-preview"},
    )
    assert preview.status_code == 200, preview.text
    resumed = api.post(
        f"/api/v1/harness/installations/{installation['installation_id']}/resume",
        json={"config_version": installation["config_version"]},
        headers={"Idempotency-Key": "acq-install-resume"},
    )
    assert resumed.status_code == 200, resumed.text


def _seed_episode(migrated_engine, user_id):
    with Session(migrated_engine) as db:
        compiled = compile_strategy(
            db, user_id, "session_duration", parameters={"variant": "session_15m", "time_budget": "tight"}
        )
        unit = db.get(PersonalStrategyUnit, compiled["strategy_unit_id"])
        now = utc_now()
        episode_id = uuid4().hex
        episode = PolicyEpisode(
            id=episode_id, user_id=user_id, unit_id=unit.id, decision_id=None,
            learning_epoch="acquisition-test-epoch", status="active", start_at=now,
            end_at=now + timedelta(days=7), version=1, review_revision=0,
            effective_adjudication_revision=None, protocol_snapshot_json=unit.protocol_json,
            execution_json=json.dumps([None] * 7), context_key=unit.context_key,
            baseline_context_key=unit.context_key, followup_context_key=unit.context_key,
            baseline_state_snapshot_hash=unit.state_snapshot_hash,
            followup_state_snapshot_hash=unit.state_snapshot_hash, changed_variables_json="[]",
        )
        db.add(episode)
        for slot in range(7):
            db.add(PolicyExecutionOpportunity(
                id=uuid4().hex, user_id=user_id, episode_id=episode_id,
                scheduled_at=now - timedelta(seconds=2) if slot == 0 else now + timedelta(days=slot),
                slot=slot, frozen_action_json="{}",
            ))
        db.commit()
        return episode_id


def test_execution_proof_matches_completion_enumeration_for_all_seven_day_states():
    for partial in product((True, False, None), repeat=7):
        unknowns = [slot for slot, value in enumerate(partial) if value is None]
        possible = set()
        for fill in product((True, False), repeat=len(unknowns)):
            complete = list(partial)
            for slot, value in zip(unknowns, fill):
                complete[slot] = value
            possible.add(int(sum(complete) / 7 >= 0.7))
        expected = next(iter(possible)) if len(possible) == 1 else None
        assert execution_proof(partial, 0.7).label == expected


def test_answer_contract_does_not_allow_unknown_to_carry_a_fact():
    good = AnswerRequest(
        expected_session_version=1, expected_episode_version=1,
        response="unknown", confirmation=False,
    )
    assert good.execution_value is None
    try:
        AnswerRequest(expected_session_version=1, expected_episode_version=1,
                      response="unknown", confirmation=False, execution_value="explicitly_not_completed")
    except Exception as error:
        assert "NON_ANSWER_CANNOT_CARRY_FACT" in str(error)
    else:
        raise AssertionError("unknown must never become execution evidence")


def test_two_step_planner_recognizes_complementary_evidence():
    class ComplementaryDomain:
        def terminal_loss(self, state):
            return 0.0 if state == 3 else 1.0

        def resolved(self, state):
            return state == 3

        def candidates(self, state, asked):
            keys = {0: ("a", "b"), 1: ("b",), 2: ("a",), 3: ()}[state]
            return tuple(Query(key, 300) for key in keys if key not in asked)

        def branches(self, state, query):
            target = { (0, "a"): 1, (0, "b"): 2, (1, "b"): 3, (2, "a"): 3 }[(state, query.key)]
            return (Branch("observed", 1.0, target),)

    domain = ComplementaryDomain()
    one = choose_next(domain, 0, remaining_questions=2, remaining_time_ms=1000, depth=1)
    two = choose_next(domain, 0, remaining_questions=2, remaining_time_ms=1000, depth=2)
    assert one.action == "defer"
    assert two.action == "ask" and two.query_key == "a"


def test_two_step_support_planner_values_a_complementary_pair_not_one_answer():
    domain = SupportPairDomain(
        state=(0,), eligible_baseline_slots=(0,), eligible_followup_slots=(0,),
        required_pairs=1, cost_ms=4000, defer_penalty=20.0)
    one = choose_next(domain, (0,), remaining_questions=2,
                      remaining_time_ms=8000, depth=1)
    two = choose_next(domain, (0,), remaining_questions=2,
                      remaining_time_ms=8000, depth=2)
    assert one.action == "defer"
    assert two.action == "ask" and two.query_key == "burden_baseline:0"
    continuation = choose_next(domain, (1,), remaining_questions=1,
                               remaining_time_ms=4000, depth=2)
    assert continuation.action == "ask" and continuation.query_key == "burden_followup:0"


def test_planner_pair_value_parameters_are_configurable_and_ablated():
    # With pair value enabled, the two-step planner pays for the baseline;
    # with the defer penalty ablated to zero it has no reason to spend budget.
    domain = SupportPairDomain(
        state=(0,), eligible_baseline_slots=(0,), eligible_followup_slots=(0,),
        required_pairs=1, cost_ms=4000, defer_penalty=20.0)
    enabled = choose_next(domain, (0,), remaining_questions=2,
                          remaining_time_ms=8000, depth=2)
    assert enabled.action == "ask" and enabled.query_key == "burden_baseline:0"
    domain_ablated = SupportPairDomain(
        state=(0,), eligible_baseline_slots=(0,), eligible_followup_slots=(0,),
        required_pairs=1, cost_ms=4000, defer_penalty=0.0)
    ablated = choose_next(domain_ablated, (0,), remaining_questions=2,
                          remaining_time_ms=8000, depth=2)
    assert ablated.action == "defer"


def test_api_consent_question_unknown_pause_and_exact_idempotent_replay(api, migrated_engine):
    _enable_policy_acquisition(api)
    episode_id = _seed_episode(migrated_engine, api.user_id)
    root = f"/api/v1/policy/episodes/{episode_id}/acquisition/sessions"
    start_body = {"expected_episode_version": 1, "consent_to_questions": True,
                  "budget": {"daily_prompt_limit": 2, "episode_prompt_limit": 14,
                             "estimated_daily_seconds": 30}}
    started = api.post(root, json=start_body, headers={"Idempotency-Key": "acq-start-operation"})
    assert started.status_code == 200, started.text
    replay = api.post(root, json=start_body, headers={"Idempotency-Key": "acq-start-operation"})
    assert replay.status_code == 200 and replay.json() == started.json()
    session = started.json()

    next_url = f"/api/v1/policy/acquisition/sessions/{session['session_id']}/next"
    next_body = {"expected_session_version": session["session_version"], "expected_episode_version": 1}
    asked = api.post(next_url, json=next_body, headers={"Idempotency-Key": "acq-next-operation"})
    assert asked.status_code == 200, asked.text
    prompt = asked.json()["decision"]["question"]
    assert prompt and prompt["kind"] == "execution_confirmation"

    answer_url = f"/api/v1/policy/acquisition/sessions/{session['session_id']}/questions/{prompt['question_id']}/answer"
    answer_body = {"expected_session_version": asked.json()["session_version"],
                   "expected_episode_version": 1, "response": "unknown", "confirmation": False,
                   "client_elapsed_ms": 2345}
    unknown = api.post(answer_url, json=answer_body, headers={"Idempotency-Key": "acq-answer-operation"})
    assert unknown.status_code == 200, unknown.text
    assert unknown.json()["endpoints"]["execution"]["unknown_slots"] == list(range(7))
    unknown_replay = api.post(answer_url, json=answer_body, headers={"Idempotency-Key": "acq-answer-operation"})
    assert unknown_replay.status_code == 200 and unknown_replay.json() == unknown.json()
    with Session(migrated_engine) as db:
        assert db.scalar(select(PolicyReport.id).where(PolicyReport.episode_id == episode_id)) is None
        question = db.scalar(select(PolicyAcquisitionQuestion).where(
            PolicyAcquisitionQuestion.session_id == session["session_id"]))
        usage = db.scalar(select(PolicyAcquisitionDailyUsage).where(
            PolicyAcquisitionDailyUsage.user_id == api.user_id))
        assert question.status == "unknown"
        assert usage.measured_ms == 2345

    pause_url = f"/api/v1/policy/acquisition/sessions/{session['session_id']}/pause"
    paused = api.post(pause_url, json={"expected_session_version": unknown.json()["session_version"]},
                      headers={"Idempotency-Key": "acq-pause-operation"})
    assert paused.status_code == 200 and paused.json()["session_status"] == "paused"


def test_decision_response_carries_evidence_debt_and_planner_meta(api, migrated_engine):
    # Spec P2: incremental response contract on existing endpoints; old clients
    # must still parse (backward-compatible keys remain), new clients get
    # decision.endpoint / confidence_kind / estimated_burden_ms, evidence_debt[]
    # and planner_meta.
    _enable_policy_acquisition(api)
    episode_id = _seed_episode(migrated_engine, api.user_id)
    root = f"/api/v1/policy/episodes/{episode_id}/acquisition/sessions"
    started = api.post(root, json={"expected_episode_version": 1, "consent_to_questions": True},
                       headers={"Idempotency-Key": "acq-debt-start"})
    assert started.status_code == 200, started.text
    session_id = started.json()["session_id"]
    next_result = api.post(
        f"/api/v1/policy/acquisition/sessions/{session_id}/next",
        json={"expected_session_version": started.json()["session_version"], "expected_episode_version": 1},
        headers={"Idempotency-Key": "acq-debt-next"},
    )
    assert next_result.status_code == 200, next_result.text
    payload = next_result.json()
    decision = payload["decision"]
    assert decision["reason_code"] and decision["user_message"] and decision["allowed_actions"]
    assert decision["explanation"] == decision["user_message"]  # new spec field, kept in sync
    assert decision["endpoint"] in {"execution", "burden", "availability"}
    assert decision["confidence_kind"] == "exact_oracle_for_endpoint"
    assert isinstance(decision["estimated_burden_ms"], int) and decision["estimated_burden_ms"] > 0
    assert decision["question"] is not None
    debts = payload["evidence_debt"]
    assert [d["endpoint"] for d in debts] == ["execution", "burden", "availability"]
    for d in debts:
        assert d["state"] and d["reason_code"] and d["action"] and d["expiry_rule"]
    meta = payload["planner_meta"]
    assert meta["version"] == "bounded-lookahead-v1"
    assert meta["response_model_kind"] and meta["model_version"]
    # A stale source must surface as needs_repair on both evidence endpoints.
    with Session(migrated_engine) as db:
        from app.services.policy_learning.acquisition.service import _snapshot, _proof, _decode
        session_row = db.scalar(select(PolicyAcquisitionSession).where(
            PolicyAcquisitionSession.id == session_id))
        episode_row = db.get(PolicyEpisode, episode_id)
        contract = _decode(session_row.contract_json, {})
        snapshot = _snapshot(db, episode_row, contract)
        snapshot["stale_sources"] = [{"observation_ref_id": "r", "reason_code": "source_invalidated"}]
        support = {"state": "needs_evidence", "valid_pair_count": 0, "required_pair_count": 5}
        from app.services.policy_learning.acquisition.service import _evidence_debt
        debt = _evidence_debt(session_row, contract, snapshot, _proof(snapshot, contract), support)
        by_endpoint = {d["endpoint"]: d for d in debt}
        assert by_endpoint["execution"]["state"] == "needs_repair"
        assert by_endpoint["burden"]["state"] == "needs_repair"
        assert by_endpoint["availability"]["action"] == "none"


def test_get_session_does_not_mutate_an_expired_question(api, migrated_engine):
    # A separate session fixture keeps this focused on read-only timeout behavior.
    _enable_policy_acquisition(api)
    episode_id = _seed_episode(migrated_engine, api.user_id)
    root = f"/api/v1/policy/episodes/{episode_id}/acquisition/sessions"
    started = api.post(root, json={"expected_episode_version": 1, "consent_to_questions": True},
                       headers={"Idempotency-Key": "acq-expire-start"})
    assert started.status_code == 200, started.text
    session_id = started.json()["session_id"]
    next_result = api.post(
        f"/api/v1/policy/acquisition/sessions/{session_id}/next",
        json={"expected_session_version": started.json()["session_version"], "expected_episode_version": 1},
        headers={"Idempotency-Key": "acq-expire-next"},
    )
    assert next_result.status_code == 200, next_result.text
    q_id = next_result.json()["decision"]["question"]["question_id"]
    with Session(migrated_engine) as db:
        row = db.get(PolicyAcquisitionQuestion, q_id)
        row.expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
    first_read = api.get(f"/api/v1/policy/acquisition/sessions/{session_id}")
    assert first_read.status_code == 200, first_read.text
    with Session(migrated_engine) as db:
        row = db.get(PolicyAcquisitionQuestion, q_id)
        assert row.status == "issued"
        assert db.get(PolicyAcquisitionSession, session_id).active_question_id == q_id


def test_support_baseline_and_followup_are_confirmed_as_separate_personal_observations(api, migrated_engine):
    _enable_policy_acquisition(api)
    episode_id = _seed_episode(migrated_engine, api.user_id)
    with Session(migrated_engine) as db:
        episode = db.get(PolicyEpisode, episode_id)
        episode.start_at = utc_now() - timedelta(days=1)
        episode.end_at = utc_now() + timedelta(days=6)
        episode.execution_json = json.dumps([True, True, True, True, True, None, None])
        opportunities = db.scalars(select(PolicyExecutionOpportunity).where(
            PolicyExecutionOpportunity.episode_id == episode_id).order_by(
                PolicyExecutionOpportunity.slot)).all()
        for slot, opportunity in enumerate(opportunities[:5]):
            opportunity.scheduled_at = utc_now() - timedelta(days=1)
            report = PolicyReport(
                id=uuid4().hex, client_report_id=f"seed-{episode_id}-{slot}",
                user_id=api.user_id, episode_id=episode_id, opportunity_id=opportunity.id,
                revision=1, execution="completed", burden=None, confounders_json="[]",
                received_at=utc_now(), source_version=1, payload_hash=(f"{slot}" * 64)[:64],
            )
            db.add(report)
            db.flush()
            opportunity.current_report_id = report.id
        db.commit()

    root = f"/api/v1/policy/episodes/{episode_id}/acquisition/sessions"
    started = api.post(root, json={"expected_episode_version": 1, "consent_to_questions": True},
                       headers={"Idempotency-Key": "burden-start-operation"})
    assert started.status_code == 200, started.text
    session = started.json()
    next_url = f"/api/v1/policy/acquisition/sessions/{session['session_id']}/next"
    first = api.post(next_url, json={"expected_session_version": session["session_version"],
                                     "expected_episode_version": 1},
                     headers={"Idempotency-Key": "burden-baseline-next"})
    assert first.status_code == 200, first.text
    question = first.json()["decision"]["question"]
    assert question["kind"] == "burden_baseline"
    answer_url = f"/api/v1/policy/acquisition/sessions/{session['session_id']}/questions/{question['question_id']}/answer"
    baseline_at = (utc_now() - timedelta(days=2)).isoformat() + "Z"
    baseline = api.post(answer_url, json={
        "expected_session_version": first.json()["session_version"],
        "expected_episode_version": first.json()["episode_version"],
        "response": "answered", "confirmation": True,
        "burden_value": 7.0, "observed_at": baseline_at,
    }, headers={"Idempotency-Key": "burden-baseline-answer"})
    assert baseline.status_code == 200, baseline.text
    assert baseline.json()["endpoints"]["execution"]["label"] == 1
    with Session(migrated_engine) as db:
        baseline_ref = db.scalar(select(PolicyObservationRef).where(
            PolicyObservationRef.episode_id == episode_id,
            PolicyObservationRef.endpoint == "baseline", PolicyObservationRef.slot == 0))
        assert baseline_ref and baseline_ref.source_type == "user_report"
        assert json.loads(baseline_ref.value_json) == 7.0 and baseline_ref.confirmed

    second = api.post(next_url, json={"expected_session_version": baseline.json()["session_version"],
                                      "expected_episode_version": baseline.json()["episode_version"]},
                      headers={"Idempotency-Key": "burden-followup-next"})
    assert second.status_code == 200, second.text
    question2 = second.json()["decision"]["question"]
    assert question2["kind"] == "burden_followup" and question2["question_id"] != question["question_id"]
    answer_url2 = f"/api/v1/policy/acquisition/sessions/{session['session_id']}/questions/{question2['question_id']}/answer"
    followup = api.post(answer_url2, json={
        "expected_session_version": second.json()["session_version"],
        "expected_episode_version": second.json()["episode_version"],
        "response": "answered", "confirmation": True,
        "burden_value": 5.0, "observed_at": utc_now().isoformat() + "Z",
    }, headers={"Idempotency-Key": "burden-followup-answer"})
    assert followup.status_code == 200, followup.text
    assert followup.json()["endpoints"]["support"]["label"] is None
    assert followup.json()["endpoints"]["support"]["valid_pair_count"] == 1
    with Session(migrated_engine) as db:
        rows = db.scalars(select(PolicyObservationRef).where(
            PolicyObservationRef.episode_id == episode_id)).all()
        assert {(row.endpoint, row.slot) for row in rows} == {("baseline", 0), ("followup", 0)}
        reports = db.scalars(select(PolicyReport.id).where(
            PolicyReport.episode_id == episode_id)).all()
        assert len(reports) == 5
        from app.services.policy_learning.outbox import invalidate_source
        invalidated = invalidate_source(db, api.user_id, "user_report",
                                        question2["question_id"], deleted=True)
        assert invalidated["acquisition_certificates_invalidated"] >= 1
        db.commit()
        current_ref = db.scalar(select(PolicyObservationRef).where(
            PolicyObservationRef.episode_id == episode_id,
            PolicyObservationRef.endpoint == "followup", PolicyObservationRef.slot == 0))
        assert current_ref and not current_ref.valid
        certs = db.scalars(select(PolicyDecisionCertificate).where(
            PolicyDecisionCertificate.episode_id == episode_id)).all()
        assert certs and all(row.status != "valid" for row in certs)
    blocked = api.get(f"/api/v1/policy/acquisition/sessions/{session['session_id']}")
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["decision"]["action"] == "needs_repair"
    assert blocked.json()["certificate"]["effective_status"] != "current"


def test_reviewed_observation_repair_previews_then_requires_confirmed_new_revision(api, migrated_engine):
    _enable_policy_acquisition(api)
    episode_id = _seed_episode(migrated_engine, api.user_id)
    with Session(migrated_engine) as db:
        episode = db.get(PolicyEpisode, episode_id)
        now = utc_now()
        episode.start_at = now - timedelta(days=9)
        episode.end_at = now - timedelta(days=2)
        episode.status = "reviewed"
        episode.version = 2
        episode.review_revision = 1
        episode.effective_adjudication_revision = 1
        episode.execution_json = json.dumps([True, True, True, True, True, None, None])
        opportunities = db.scalars(select(PolicyExecutionOpportunity).where(
            PolicyExecutionOpportunity.episode_id == episode_id).order_by(
                PolicyExecutionOpportunity.slot)).all()
        for slot, opportunity in enumerate(opportunities[:5]):
            opportunity.scheduled_at = now - timedelta(days=3)
            report = PolicyReport(
                id=uuid4().hex, client_report_id=f"rereview-{episode_id}-{slot}",
                user_id=api.user_id, episode_id=episode_id, opportunity_id=opportunity.id,
                revision=1, execution="completed", burden=None, confounders_json="[]",
                received_at=now, source_version=1, payload_hash=(f"{slot + 1}" * 64)[:64],
            )
            db.add(report)
            db.flush()
            opportunity.current_report_id = report.id
        baseline_at = episode.start_at - timedelta(days=2)
        observation = PolicyObservationRef(
            user_id=api.user_id, episode_id=episode_id, endpoint="baseline", slot=0,
            source_type="user_report", source_id="rereview-baseline", source_revision=1,
            revision=1, value_json="7.0", observed_at=baseline_at,
            metric_version="burden-v1", confirmed=True, valid=True,
        )
        db.add(observation)
        db.flush()
        db.add(PolicyAdjudication(
            id=uuid4().hex, user_id=api.user_id, episode_id=episode_id, revision=1,
            learning_epoch=episode.learning_epoch, execution_label=1, support_label=None,
            availability_label=0, conclusion="insufficient_data", reasons_json="[]",
            evidence_refs_json="[]", source_hash="old-adjudication", algorithm_version="egpl-v1.0.0",
            gate_version="evidence-gate-v1.0.0", valid=True, stale=True,
        ))
        db.commit()

    target_response = api.get(f"/api/v1/policy/episodes/{episode_id}/observation-repair-targets")
    assert target_response.status_code == 200, target_response.text
    target = target_response.json()["items"][0]
    repair_at = (baseline_at - timedelta(days=1)).isoformat() + "Z"
    repaired = api.post(
        f"/api/v1/policy/episodes/{episode_id}/observation-repairs",
        json={"expected_episode_version": 2, "observation_ref_id": target["observation_ref_id"],
              "expected_observation_revision": target["observation_revision"],
              "expected_source_revision": target["source_revision"],
              "expected_observation_hash": target["observation_hash"], "confirmation": True,
              "burden_value": 6.0, "observed_at": repair_at},
        headers={"Idempotency-Key": "rereview-observation-repair"},
    )
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()["requires_rereview"] and repaired.json()["prior_revision_preserved"]

    context = api.get(f"/api/v1/policy/episodes/{episode_id}/rereview-context")
    assert context.status_code == 200, context.text
    body = {"expected_episode_version": context.json()["episode_version"],
            "expected_adjudication_revision": context.json()["expected_adjudication_revision"],
            "expected_evidence_hash": context.json()["expected_evidence_hash"]}
    preview = api.post(f"/api/v1/policy/episodes/{episode_id}/rereview-preview", json=body)
    assert preview.status_code == 200 and preview.json()["requires_user_confirmation"]
    proposal = api.post(f"/api/v1/policy/episodes/{episode_id}/rereview-proposal", json=body,
                        headers={"Idempotency-Key": "rereview-confirm-proposal"})
    assert proposal.status_code == 200, proposal.text
    confirmed = api.post(f"/api/v1/agent/actions/{proposal.json()['proposal_id']}/confirm",
                         json={"version": proposal.json()["version"], "confirmation": True})
    assert confirmed.status_code == 200, confirmed.text
    with Session(migrated_engine) as db:
        episode = db.get(PolicyEpisode, episode_id)
        revisions = db.scalars(select(PolicyAdjudication).where(
            PolicyAdjudication.episode_id == episode_id).order_by(PolicyAdjudication.revision)).all()
        assert episode.review_revision == 2 and episode.effective_adjudication_revision == 2
        assert len(revisions) == 2 and revisions[0].stale and revisions[1].valid


def test_knowledge_binding_is_content_versioned_and_revocation_blocks_use(api, migrated_engine):
    from app.services.policy_learning.acquisition.knowledge_contract import current_knowledge_contract

    with Session(migrated_engine) as db:
        before = current_knowledge_contract(db, "session_duration")
        assert before["available"]
        document = db.scalar(select(KnowledgeDocument).where(
            KnowledgeDocument.source_key == "who-pa-adults-2020"))
        original = document.content
        document.content = original + " 本地测试修订"
        after_content = current_knowledge_contract(db, "session_duration")
        assert after_content["contract_hash"] != before["contract_hash"]
        document.active = False
        revoked = current_knowledge_contract(db, "session_duration")
        assert not revoked["available"]
        assert revoked["contract"]["status"] == "blocked"


def test_certificate_is_blocked_when_bound_knowledge_changes(api, migrated_engine):
    _enable_policy_acquisition(api)
    episode_id = _seed_episode(migrated_engine, api.user_id)
    with Session(migrated_engine) as db:
        db.get(PolicyEpisode, episode_id).execution_json = json.dumps([True] * 5 + [None] * 2)
        db.commit()
    started = api.post(
        f"/api/v1/policy/episodes/{episode_id}/acquisition/sessions",
        json={"expected_episode_version": 1, "consent_to_questions": True},
        headers={"Idempotency-Key": "knowledge-binding-start"},
    )
    assert started.status_code == 200, started.text
    certificate = started.json()["certificate"]
    assert certificate["effective_status"] == "current"
    with Session(migrated_engine) as db:
        document = db.scalar(select(KnowledgeDocument).where(
            KnowledgeDocument.source_key == "who-pa-adults-2020"))
        document.content += " 文档内容修订应阻断旧证书"
        db.commit()
    read = api.get(f"/api/v1/policy/acquisition/certificates/{certificate['certificate_id']}")
    assert read.status_code == 200, read.text
    assert read.json()["effective_status"] == "certificate_binding_changed"
    next_result = api.post(
        f"/api/v1/policy/acquisition/sessions/{started.json()['session_id']}/next",
        json={"expected_session_version": started.json()["session_version"],
              "expected_episode_version": started.json()["episode_version"]},
        headers={"Idempotency-Key": "knowledge-binding-next"},
    )
    assert next_result.status_code == 200, next_result.text
    assert next_result.json()["decision"]["action"] == "needs_repair"
    assert next_result.json()["decision"]["reason_code"] == "knowledge_contract_changed"


def test_repair_reuses_current_certificate_and_records_repair_time(api, migrated_engine):
    _enable_policy_acquisition(api)
    episode_id = _seed_episode(migrated_engine, api.user_id)
    with Session(migrated_engine) as db:
        db.get(PolicyEpisode, episode_id).execution_json = json.dumps([True] * 5 + [None] * 2)
        db.commit()
    started = api.post(
        f"/api/v1/policy/episodes/{episode_id}/acquisition/sessions",
        json={"expected_episode_version": 1, "consent_to_questions": True},
        headers={"Idempotency-Key": "repair-reuse-start"},
    )
    assert started.status_code == 200, started.text
    original_certificate_id = started.json()["certificate"]["certificate_id"]
    repaired = api.post(
        f"/api/v1/policy/acquisition/sessions/{started.json()['session_id']}/repair",
        json={"expected_session_version": started.json()["session_version"],
              "expected_episode_version": started.json()["episode_version"],
              "client_elapsed_ms": 1234},
        headers={"Idempotency-Key": "repair-reuse-command"},
    )
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()["certificate"]["certificate_id"] == original_certificate_id
    with Session(migrated_engine) as db:
        certs = db.scalars(select(PolicyDecisionCertificate).where(
            PolicyDecisionCertificate.session_id == started.json()["session_id"])).all()
        usage = db.scalar(select(PolicyAcquisitionDailyUsage).where(
            PolicyAcquisitionDailyUsage.user_id == api.user_id))
        assert len(certs) == 1
        assert usage and usage.measured_ms == 1234


def test_expired_question_maintenance_is_bounded_and_does_not_refund(api, migrated_engine):
    _enable_policy_acquisition(api)
    episode_id = _seed_episode(migrated_engine, api.user_id)
    started = api.post(
        f"/api/v1/policy/episodes/{episode_id}/acquisition/sessions",
        json={"expected_episode_version": 1, "consent_to_questions": True},
        headers={"Idempotency-Key": "maintenance-start"},
    )
    assert started.status_code == 200, started.text
    session = started.json()
    asked = api.post(
        f"/api/v1/policy/acquisition/sessions/{session['session_id']}/next",
        json={"expected_session_version": session["session_version"],
              "expected_episode_version": 1},
        headers={"Idempotency-Key": "maintenance-next"},
    )
    assert asked.status_code == 200, asked.text
    question_id = asked.json()["decision"]["question"]["question_id"]
    with Session(migrated_engine) as db:
        db.get(PolicyAcquisitionQuestion, question_id).expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
    swept = api.post("/api/v1/worker/maintenance/policy-acquisition?limit=5",
                     headers=api.worker_headers)
    assert swept.status_code == 200, swept.text
    assert swept.json()["timed_out"] == 1 and swept.json()["limit"] == 5
    with Session(migrated_engine) as db:
        question = db.get(PolicyAcquisitionQuestion, question_id)
        acq = db.get(PolicyAcquisitionSession, session["session_id"])
        usage = db.scalar(select(PolicyAcquisitionDailyUsage).where(
            PolicyAcquisitionDailyUsage.user_id == api.user_id))
        assert question.status == "timed_out"
        assert json.loads(question.answer_json)["response"] == "no_response"
        assert acq.active_question_id is None
        assert usage.prompt_count == 1


def test_maintenance_compensates_a_missed_certificate_invalidation(api, migrated_engine):
    _enable_policy_acquisition(api)
    episode_id = _seed_episode(migrated_engine, api.user_id)
    with Session(migrated_engine) as db:
        db.get(PolicyEpisode, episode_id).execution_json = json.dumps([True] * 5 + [None] * 2)
        db.commit()
    started = api.post(
        f"/api/v1/policy/episodes/{episode_id}/acquisition/sessions",
        json={"expected_episode_version": 1, "consent_to_questions": True},
        headers={"Idempotency-Key": "maintenance-certificate-start"},
    )
    assert started.status_code == 200, started.text
    cert_id = started.json()["certificate"]["certificate_id"]
    with Session(migrated_engine) as db:
        certificate = db.get(PolicyDecisionCertificate, cert_id)
        certificate.expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
    swept = api.post("/api/v1/worker/maintenance/policy-acquisition?limit=5",
                     headers=api.worker_headers)
    assert swept.status_code == 200, swept.text
    assert swept.json()["certificates_staled"] == 1
    with Session(migrated_engine) as db:
        certificate = db.get(PolicyDecisionCertificate, cert_id)
        session = db.scalar(select(PolicyAcquisitionSession).where(
            PolicyAcquisitionSession.user_id == api.user_id,
            PolicyAcquisitionSession.episode_id == episode_id))
        assert certificate.status == "stale"
        assert session.decision_state == "needs_repair"


def test_concurrent_next_uses_independent_sessions_and_charges_one_prompt(api, migrated_engine):
    from app.services.policy_learning.acquisition.service import AcquisitionError, issue_next_question

    _enable_policy_acquisition(api)
    episode_id = _seed_episode(migrated_engine, api.user_id)
    started = api.post(
        f"/api/v1/policy/episodes/{episode_id}/acquisition/sessions",
        json={"expected_episode_version": 1, "consent_to_questions": True},
        headers={"Idempotency-Key": "concurrent-next-start"},
    )
    assert started.status_code == 200, started.text
    session_id = started.json()["session_id"]
    barrier = Barrier(2)

    def request_next(key):
        with Session(migrated_engine) as db:
            barrier.wait(timeout=5)
            try:
                result = issue_next_question(
                    db, user_id=api.user_id, session_id=session_id,
                    expected_session_version=started.json()["session_version"],
                    expected_episode_version=1, idempotency_key=key)
                db.commit()
                return "ok", result
            except AcquisitionError as exc:
                db.rollback()
                return exc.code, None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(request_next, ("concurrent-next-a", "concurrent-next-b")))
    successes = [result for status, result in results if status == "ok"]
    assert len(successes) == 1, results
    assert successes[0]["decision"]["question"]
    assert all(status in {"ok", "ACQUISITION_VERSION_CONFLICT", "ACQUISITION_WRITE_BUSY"}
               for status, _ in results)
    with Session(migrated_engine) as db:
        questions = db.scalars(select(PolicyAcquisitionQuestion).where(
            PolicyAcquisitionQuestion.session_id == session_id,
            PolicyAcquisitionQuestion.status == "issued")).all()
        usage = db.scalar(select(PolicyAcquisitionDailyUsage).where(
            PolicyAcquisitionDailyUsage.user_id == api.user_id))
    assert len(questions) == 1
    assert usage.prompt_count == 1
    assert usage.estimated_ms == questions[0].estimated_cost_ms


def test_harness_episode_preview_without_session_is_read_only(api, migrated_engine):
    from app.harness.contracts import ToolContext
    from app.harness.policy_tools import (
        TOOL_POLICY_ACQUISITION_PREVIEW, TOOL_POLICY_KNOWLEDGE,
    )
    from app.harness.tools import get_tool_registry
    from app.models import User

    _enable_policy_acquisition(api)
    episode_id = _seed_episode(migrated_engine, api.user_id)
    with Session(migrated_engine) as db:
        user = db.get(User, api.user_id)
        observation = get_tool_registry().execute(
            TOOL_POLICY_ACQUISITION_PREVIEW,
            ToolContext(db=db, user=user, agent_id="planner"),
            {"episode_id": episode_id},
        )
        assert observation.status == "ok"
        assert observation.output["found"] is True
        assert observation.output["preview_only"] is True
        assert observation.output["decision"]
        assert db.scalar(select(PolicyAcquisitionSession).where(
            PolicyAcquisitionSession.user_id == api.user_id)) is None
        assert db.scalar(select(PolicyAcquisitionQuestion).where(
            PolicyAcquisitionQuestion.user_id == api.user_id)) is None
        assert db.scalar(select(PolicyDecisionCertificate).where(
            PolicyDecisionCertificate.user_id == api.user_id)) is None
        knowledge = get_tool_registry().execute(
            TOOL_POLICY_KNOWLEDGE,
            ToolContext(db=db, user=user, agent_id="planner"),
            {"query": ""},
        )
        assert knowledge.status == "ok"
        assert knowledge.output["retrieval_status"] == "no_reviewed_match"
        assert knowledge.output["conflict_status"] == "no_sources_to_compare"
        assert "不补写依据" in knowledge.output["conflict_notice"]
        reviewed_source = db.scalar(select(KnowledgeDocument).where(
            KnowledgeDocument.source_key == "who-pa-adults-2020"))
        matched = get_tool_registry().execute(
            TOOL_POLICY_KNOWLEDGE,
            ToolContext(db=db, user=user, agent_id="planner"),
            {"query": reviewed_source.title},
        )
        assert matched.status == "ok"
        assert matched.output["retrieval_status"] == "matched"
        assert matched.output["conflict_status"] == "not_assessed"
        assert "未经过语义冲突裁决" in matched.output["conflict_notice"]


def test_fence_flushes_before_pending_source_edits(migrated_engine, api):
    from sqlalchemy import text
    from app.models import User
    from app.services.policy_learning.acquisition.service import _fence

    with Session(migrated_engine) as db:
        user = db.get(User, api.user_id)
        original = db.scalar(text("SELECT nickname FROM users WHERE id=:id"), {"id": api.user_id})
        user.nickname = "pending-source-change"
        fence = _fence(db, api.user_id)
        persisted = db.scalar(text("SELECT nickname FROM users WHERE id=:id"), {"id": api.user_id})
        assert fence.generation >= 1
        assert persisted == original
        db.flush()
        assert db.scalar(text("SELECT nickname FROM users WHERE id=:id"), {"id": api.user_id}) == "pending-source-change"
        db.rollback()


def test_acquisition_tables_are_exported_and_deleted_with_account(api, migrated_engine):
    import io
    import zipfile
    from app.services.privacy import TABLES, build_export_zip, delete_account_data

    _enable_policy_acquisition(api)
    episode_id = _seed_episode(migrated_engine, api.user_id)
    root = f"/api/v1/policy/episodes/{episode_id}/acquisition/sessions"
    started = api.post(root, json={
        "expected_episode_version": 1,
        "consent_to_questions": True,
        "budget": {"daily_prompt_limit": 1, "episode_prompt_limit": 7,
                   "estimated_daily_seconds": 15},
    }, headers={"Idempotency-Key": "privacy-acquisition-start"})
    assert started.status_code == 200, started.text
    with Session(migrated_engine) as db:
        raw = build_export_zip(db, api.user_id)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            exported = json.loads(archive.read("healthmate-export.json"))["data"]
        acquisition_tables = [name for name, model in TABLES
                              if model.__tablename__.startswith(("policy_acquisition_",
                                                                 "policy_decision_certificate",
                                                                 "policy_certificate_dependency",
                                                                 "policy_evidence_revision"))]
        assert "policy_acquisition_sessions" in acquisition_tables
        assert len(exported["policy_acquisition_sessions"]) == 1
        assert exported["policy_acquisition_commands"]
        assert exported["policy_acquisition_events"]
        assert delete_account_data(db, api.user_id)["deleted_non_cloud_media_objects"] == 0
        for name, model in TABLES:
            if hasattr(model, "user_id"):
                assert db.scalar(select(model).where(model.user_id == api.user_id)) is None, name
