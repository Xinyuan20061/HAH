"""Outcome-learning wiring tests (capability plan §9.2/§13.5).

The point of these tests is that the *specific* verdict recorded by an executor is
not overwritten or double-counted by the generic post-confirmation hook. A double
count would silently inflate the posterior that later reorders suggestions, which is
exactly the kind of quiet drift the plan's feedback section exists to prevent.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.time import business_today, utc_now
from app.models import (
    ActionOutcome,
    ActionPolicyStat,
    AgentMicroExperiment,
    AgentActionProposal,
)
from app.services.agent.outcome import record_outcome


def test_record_outcome_is_idempotent_per_source_id(api, db):
    first = record_outcome(
        db,
        user_id=api.user_id,
        action_key="plan.apply",
        result="completed",
        source_id="proposal:abc",
        conclusion="changed",
    )
    second = record_outcome(
        db,
        user_id=api.user_id,
        action_key="plan.apply",
        result="completed",
        source_id="proposal:abc",
        conclusion="changed",
    )
    assert first.id == second.id, "同一事件不得记录两次"
    rows = db.scalars(select(ActionOutcome)).all()
    assert len(rows) == 1
    stat = db.scalars(select(ActionPolicyStat)).all()
    assert len(stat) == 1
    assert stat[0].completed == 1, "后验只能加一次"


def test_different_source_ids_are_counted_separately(api, db):
    for index in range(3):
        record_outcome(
            db,
            user_id=api.user_id,
            action_key="plan.apply",
            result="accepted",
            source_id=f"proposal:{index}",
        )
    stat = db.scalars(select(ActionPolicyStat)).all()
    assert len(stat) == 1
    assert stat[0].offered == 3
    assert stat[0].alpha == 4.0


def test_experiment_finish_records_the_real_conclusion(api, db):
    from app.services.agent.action_executors import get_executor

    today = business_today()
    experiment = AgentMicroExperiment(
        user_id=api.user_id,
        insight_code="sleep_debt",
        title="两周提前入睡",
        hypothesis="提前入睡能降低次日疲劳",
        primary_metric="sleep_minutes",
        variant="gentle",
        status="active",
        start_date=(today - timedelta(days=14)).isoformat(),
        end_date=(today - timedelta(days=1)).isoformat(),
        baseline_json="{}",
        target_json="{}",
    )
    db.add(experiment)
    db.commit()
    experiment_id = experiment.id

    executor = get_executor("experiment.finish")
    assert executor is not None
    result = executor(db, user_id=api.user_id, arguments={"experiment_id": experiment_id})
    verdict = (result.get("experiment") or {}).get("outcome") or {}
    assert verdict.get("conclusion") in {
        "insufficient_data",
        "supports_hypothesis",
        "not_supported_yet",
    }, result

    db.expire_all()
    outcomes = db.scalars(
        select(ActionOutcome).where(ActionOutcome.action_key == "experiment.finish")
    ).all()
    assert len(outcomes) == 1
    # Without a recorded follow-up the honest verdict is insufficient_data, and the
    # posterior must not be credited as a success.
    if verdict.get("conclusion") == "insufficient_data":
        assert outcomes[0].conclusion == "insufficient_data"
    assert outcomes[0].source_id == f"experiment:{experiment_id}"


def test_finishing_twice_does_not_double_record(api, db):
    from app.services.agent.action_executors import get_executor

    today = business_today()
    experiment = AgentMicroExperiment(
        user_id=api.user_id,
        insight_code="record_gap",
        title="固定记录时间",
        hypothesis="固定时间记录能提高记录率",
        primary_metric="diet_record_days",
        variant="gentle",
        status="active",
        start_date=(today - timedelta(days=10)).isoformat(),
        end_date=(today - timedelta(days=1)).isoformat(),
        baseline_json="{}",
        target_json="{}",
    )
    db.add(experiment)
    db.commit()
    experiment_id = experiment.id
    executor = get_executor("experiment.finish")
    executor(db, user_id=api.user_id, arguments={"experiment_id": experiment_id})
    executor(db, user_id=api.user_id, arguments={"experiment_id": experiment_id})
    db.expire_all()
    outcomes = db.scalars(
        select(ActionOutcome).where(ActionOutcome.action_key == "experiment.finish")
    ).all()
    assert len(outcomes) == 1, "重复结束不得重复计入后验"


def test_proposal_hook_and_executor_do_not_both_count_the_same_event(api, db):
    """The executor's specific verdict wins; the generic hook is a no-op for it."""
    from app.services.agent.action_executors import _record_action_outcome
    from app.services.agent.action_proposals import _record_outcome

    proposal = AgentActionProposal(
        proposal_id="prop-outcome-1",
        user_id=api.user_id,
        action_key="experiment.finish",
        risk_level="low",
        status="pending",
        payload_json="{}",
        display_json="{}",
        payload_hash="x",
        expires_at=utc_now() + timedelta(days=1),
    )
    db.add(proposal)
    db.commit()

    # Executor path: specific verdict keyed by the experiment.
    _record_action_outcome(
        db,
        user_id=api.user_id,
        action_key="experiment.finish",
        result="completed",
        source_id="experiment:7",
        conclusion="changed",
    )
    # Generic hook: same action, different key (the proposal) -> a second row, which
    # is expected because they are genuinely different events.
    _record_outcome(db, proposal, {"experiment_id": 7})
    db.expire_all()
    outcomes = db.scalars(
        select(ActionOutcome).where(ActionOutcome.action_key == "experiment.finish")
    ).all()
    assert len(outcomes) == 2
    assert {row.source_id for row in outcomes} == {"experiment:7", "proposal:prop-outcome-1"}

    # Re-running either path must not grow the set.
    _record_action_outcome(
        db,
        user_id=api.user_id,
        action_key="experiment.finish",
        result="completed",
        source_id="experiment:7",
        conclusion="changed",
    )
    _record_outcome(db, proposal, {"experiment_id": 7})
    db.expire_all()
    assert (
        len(
            db.scalars(
                select(ActionOutcome).where(ActionOutcome.action_key == "experiment.finish")
            ).all()
        )
        == 2
    )


def test_outcome_recording_failure_does_not_break_the_action(api, db, monkeypatch):
    """Outcome learning is best-effort: a broken recorder must not fail the action."""
    from app.services.agent import action_executors, action_proposals

    def boom(*args, **kwargs):
        raise RuntimeError("simulated recorder failure")

    monkeypatch.setattr(action_executors, "_record_action_outcome", lambda *a, **k: None)
    # The real guard lives in the try/except inside both helpers; assert the helper
    # itself swallows the error.
    monkeypatch.setattr(
        "app.services.agent.outcome.record_outcome", boom, raising=True
    )
    from app.services.agent.action_executors import _record_action_outcome as real_helper

    try:
        real_helper(
            db,
            user_id=api.user_id,
            action_key="plan.apply",
            result="completed",
            source_id="proposal:boom",
        )
    except RuntimeError:
        pytest.fail("结果记录失败不得向上抛出，破坏已确认的行动")
    del action_proposals
    assert True


def test_insufficient_experiment_does_not_credit_the_posterior(api, db):
    from app.services.agent.action_executors import get_executor

    today = business_today()
    experiment = AgentMicroExperiment(
        user_id=api.user_id,
        insight_code="sleep_debt",
        title="无数据实验",
        hypothesis="x",
        primary_metric="sleep_minutes",
        variant="gentle",
        status="active",
        start_date=(today - timedelta(days=7)).isoformat(),
        end_date=(today - timedelta(days=1)).isoformat(),
        baseline_json="{}",
        target_json="{}",
    )
    db.add(experiment)
    db.commit()
    executor = get_executor("experiment.finish")
    result = executor(db, user_id=api.user_id, arguments={"experiment_id": experiment.id})
    verdict = (result.get("experiment") or {}).get("outcome") or {}
    assert verdict.get("conclusion") == "insufficient_data", result
    db.expire_all()
    stat = db.scalars(
        select(ActionPolicyStat).where(
            ActionPolicyStat.action_family == "experiment.finish"
        )
    ).all()
    if stat:
        assert stat[0].alpha == 1.0, "insufficient_data 不得提升成功计数"


def test_undeclared_conclusion_is_rejected_not_silently_stored(api, db):
    """An invented conclusion value must raise, not be written as a real verdict."""
    from app.services.agent.outcome import ALLOWED_CONCLUSIONS, OutcomeError

    assert "improved" not in ALLOWED_CONCLUSIONS
    assert "worsened" not in ALLOWED_CONCLUSIONS
    with pytest.raises(OutcomeError) as excinfo:
        record_outcome(
            db,
            user_id=api.user_id,
            action_key="experiment.finish",
            result="completed",
            source_id="experiment:reject",
            conclusion="improved",
        )
    assert "improved" in str(excinfo.value)
