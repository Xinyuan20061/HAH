"""v2 auxiliary baselines: label entropy first and small-state exact reference.

Both reuse the composite domain; entropy treats burden candidates as invisible
(it optimises only execution-label entropy, mirroring v1), while the exact
reference evaluates the full finite-state expected loss to budget exhaustion.
"""

from __future__ import annotations

from itertools import product
import math

from app.services.policy_learning.acquisition.oracle import execution_proof


def _label_entropy(state, target: float) -> float:
    holes = [i for i, v in enumerate(state) if v is None]
    if not holes:
        return 0.0
    counts = [0, 0]
    for completion in product((False, True), repeat=len(holes)):
        full = list(state)
        for slot, value in zip(holes, completion):
            full[slot] = value
        counts[int(sum(full) / len(full) >= target)] += 1
    total = counts[0] + counts[1]
    return -sum(p * math.log(p) for p in (counts[0] / total, counts[1] / total) if p > 0)


def label_entropy_gain(domain, state, candidates, target: float, asked) -> str | None:
    before = _label_entropy(tuple(domain._execution(state)), target)
    prior = domain._prior
    best_key, best_gain = None, 0.0
    for q in candidates:
        if not q.key.startswith("execution:"):
            continue  # entropy sees no burden-pair value
        slot = int(q.key.split(":", 1)[1])
        yes_state, no_state = list(domain._execution(state)), list(domain._execution(state))
        yes_state[slot], no_state[slot] = True, False
        gain = before - (prior.answered * 0.5 * (
            _label_entropy(tuple(yes_state), target) + _label_entropy(tuple(no_state), target)
        ) + (1 - prior.answered) * before)
        if gain > best_gain + 1e-12:
            best_key, best_gain = q.key, gain
    return best_key


def exact_best(domain, state, candidates, asked, *, questions_left: int,
               time_left_ms: int) -> str | None:
    from functools import lru_cache

    @lru_cache(maxsize=None)
    def value(current, used: frozenset, left: int, time_ms: int) -> float:
        best = domain.terminal_loss(current)
        if domain.resolved(current) or left <= 0:
            return best
        for query in domain.candidates(current, used):
            if query.cost_ms > time_ms:
                continue
            expected = query.cost_ms / 1000
            for branch in domain.branches(current, query):
                expected += branch.probability * value(
                    branch.state, used | {query.key}, left - 1, time_ms - query.cost_ms)
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
