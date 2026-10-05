"""Named, deterministic acquisition baselines for synthetic replay only."""

from __future__ import annotations

from itertools import product
import math

from app.services.policy_learning.acquisition.oracle import execution_proof
from app.services.policy_learning.acquisition.planner import (
    ExecutionDomain, ResponsePrior, choose_next,
)


STRATEGIES = (
    "fixed_fill", "random_same_budget", "entropy_first",
    "one_step_decision_value", "two_step_decision_value",
    "current_gate_with_fixed_acquisition_adapter", "small_state_exact_reference",
)


def _entropy(values: list[float]) -> float:
    return -sum(p * math.log(p) for p in values if p > 0)


def _label_entropy(state: tuple[bool | None, ...], target: float) -> float:
    holes = [i for i, value in enumerate(state) if value is None]
    if not holes:
        return 0.0
    counts = [0, 0]
    for completion in product((False, True), repeat=len(holes)):
        full = list(state)
        for slot, value in zip(holes, completion):
            full[slot] = value
        counts[int(sum(full) / len(full) >= target)] += 1
    return _entropy([count / sum(counts) for count in counts])


def _decision_domain(target: float, due_slots: tuple[int, ...], prior: ResponsePrior):
    domain = ExecutionDomain(target=target, eligible_slots=due_slots, prior=prior)
    original_loss = domain.terminal_loss
    domain.terminal_loss = lambda state: (
        0.0 if domain.resolved(state)
        else original_loss(state) + 5.0 * sum(value is None for value in state)
    )
    return domain


def select_query(strategy: str, state: tuple[bool | None, ...], due_slots: tuple[int, ...],
                 asked: frozenset[str], *, target: float, questions_left: int,
                 time_left_ms: int, prior: ResponsePrior) -> str | None:
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy: {strategy}")
    if execution_proof(state, target).label is not None:
        return None
    candidates = [slot for slot in due_slots if state[slot] is None and f"execution:{slot}" not in asked]
    if not candidates or questions_left <= 0 or time_left_ms <= 0:
        return None
    if strategy in {"one_step_decision_value", "two_step_decision_value"}:
        domain = _decision_domain(target, due_slots, prior)
        plan = choose_next(domain, state, remaining_questions=questions_left,
                           remaining_time_ms=time_left_ms,
                           depth=1 if strategy == "one_step_decision_value" else 2)
        return plan.query_key
    if strategy == "small_state_exact_reference":
        domain = _decision_domain(target, due_slots, prior)

        def value(current, used, count_left, millis_left):
            stop = domain.terminal_loss(current)
            if domain.resolved(current) or count_left <= 0:
                return stop
            best = stop
            for query in domain.candidates(current, used):
                if query.cost_ms > millis_left:
                    continue
                expected = query.cost_ms / 1000
                for branch in domain.branches(current, query):
                    expected += branch.probability * value(
                        branch.state, used | {query.key}, count_left - 1,
                        millis_left - query.cost_ms)
                best = min(best, expected)
            return best

        baseline = domain.terminal_loss(state)
        selected, best = None, baseline
        for query in domain.candidates(state, asked):
            if query.cost_ms > time_left_ms:
                continue
            expected = query.cost_ms / 1000
            for branch in domain.branches(state, query):
                expected += branch.probability * value(
                    branch.state, asked | {query.key}, questions_left - 1,
                    time_left_ms - query.cost_ms)
            if expected < best - 1e-12:
                selected, best = query.key, expected
        return selected
    if strategy == "entropy_first":
        before = _label_entropy(state, target)
        selected, best = None, 0.0
        for slot in candidates:
            candidate_cost = 3000
            if candidate_cost > time_left_ms:
                continue
            yes_state, no_state = list(state), list(state)
            yes_state[slot], no_state[slot] = True, False
            gain = before - (prior.answered * 0.5 * (
                _label_entropy(tuple(yes_state), target) + _label_entropy(tuple(no_state), target)
            ) + (1 - prior.answered) * before)
            if gain > best + 1e-12:
                selected, best = f"execution:{slot}", gain
        return selected
    if strategy == "current_gate_with_fixed_acquisition_adapter":
        selected = min(candidates)
        return f"execution:{selected}" if 3000 <= time_left_ms else None
    # The random strategy's ordering is supplied by the runner through due_slots.
    if strategy == "random_same_budget":
        selected = candidates[0]
        return f"execution:{selected}" if 3000 <= time_left_ms else None
    return None
