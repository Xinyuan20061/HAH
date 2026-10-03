"""Model-call budget enforcement (capability plan §13.5).

§13.5 states two caps that the orchestrator previously did not enforce: a simple task
must use ≤2 model calls and a cross-domain task ≤5. Every turn was handed a budget
of 5, so the simple-task cap existed only in a docstring. These tests lock the
derivation and the enforcement.
"""

from __future__ import annotations

from app.harness.collaboration import matched_domains
from app.services.agent.run_stages import TurnBudget


def test_budget_caps_match_the_published_table():
    assert TurnBudget.SIMPLE_TASK_MAX_CALLS == 2
    assert TurnBudget.CROSS_DOMAIN_MAX_CALLS == 5


def test_single_domain_turn_gets_the_simple_cap():
    budget = TurnBudget.for_task(worker_count=1)
    assert budget.max_calls == 2
    assert budget.scope == "simple"
    assert budget.allow() is True
    budget.charge()
    budget.charge()
    assert budget.used == 2
    assert budget.allow() is False, "简单任务不得超过 2 次模型调用"
    assert budget.exhausted() is True


def test_cross_domain_turn_gets_the_cross_domain_cap():
    budget = TurnBudget.for_task(worker_count=3)
    assert budget.max_calls == 5
    assert budget.scope == "cross_domain"
    for _ in range(5):
        budget.charge()
    assert budget.allow() is False, "跨领域任务不得超过 5 次模型调用"


def test_blocked_turn_gets_no_budget_at_all():
    budget = TurnBudget.for_task(blocked=True, worker_count=3)
    assert budget.max_calls == 0
    assert budget.scope == "blocked"
    assert budget.allow() is False
    assert budget.trace()["max_model_calls"] == 0


def test_budget_never_exceeds_the_cross_domain_cap():
    for worker_count in range(0, 8):
        budget = TurnBudget.for_task(worker_count=worker_count)
        assert budget.max_calls <= TurnBudget.CROSS_DOMAIN_MAX_CALLS


def test_domain_breadth_drives_the_budget_from_real_wording():
    """The budget must follow the same evidence the router uses."""
    single = matched_domains("", "深蹲的膝盖应该怎么控制")
    assert len(single) == 1
    assert TurnBudget.for_task(worker_count=len(single)).max_calls == 2

    cross = matched_domains("", "深蹲怎么练，另外晚餐吃什么比较好")
    assert len(cross) > 1
    assert TurnBudget.for_task(worker_count=len(cross)).max_calls == 5


def test_plan_intent_is_single_domain_by_default():
    domains = matched_domains("plan", "帮我排一份这周的训练安排")
    assert domains == ["planner"], domains
    assert TurnBudget.for_task(worker_count=len(domains)).max_calls == 2


def test_budget_trace_is_reported():
    budget = TurnBudget.for_task(worker_count=1)
    trace = budget.trace()
    assert trace["max_model_calls"] == 2
    assert trace["used_model_calls"] == 0
    assert trace["scope"] == "simple"
    assert "elapsed_ms" in trace
