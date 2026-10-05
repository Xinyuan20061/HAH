"""Small bounded look-ahead policy for asking about due execution slots."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math
from typing import Hashable, Literal, Protocol

from .oracle import execution_proof

# Versioned planner implementation identifier, reported in planner_meta for
# audit traceability (spec P2). Bump on any behavior-affecting change.
PLANNER_VERSION = "bounded-lookahead-v1"


@dataclass(frozen=True)
class Query:
    key: str
    cost_ms: int


@dataclass(frozen=True)
class Branch:
    outcome: str
    probability: float
    state: Hashable


class PlanningDomain(Protocol):
    def terminal_loss(self, state: Hashable) -> float: ...
    def resolved(self, state: Hashable) -> bool: ...
    def candidates(self, state: Hashable, asked: frozenset[str]) -> tuple[Query, ...]: ...
    def branches(self, state: Hashable, query: Query) -> tuple[Branch, ...]: ...


@dataclass(frozen=True)
class Plan:
    action: Literal["ask", "stop_sufficient", "defer"]
    query_key: str | None
    expected_loss: float
    stop_loss: float
    reason: str
    depth: int


def choose_next(domain: PlanningDomain, state: Hashable, *, remaining_questions: int,
                remaining_time_ms: int, depth: int = 2,
                asked: frozenset[str] = frozenset()) -> Plan:
    if type(remaining_questions) is not int or type(remaining_time_ms) is not int or remaining_questions < 0 or remaining_time_ms < 0 or type(depth) is not int or not 1 <= depth <= 8:
        raise ValueError("INVALID_PLANNING_BUDGET")
    horizon = min(depth, remaining_questions)

    def loss(value: Hashable) -> float:
        result = domain.terminal_loss(value)
        if not math.isfinite(result) or result < 0 or domain.resolved(value) and result != 0:
            raise ValueError("INVALID_TERMINAL_LOSS")
        return result

    def branches(value: Hashable, query: Query) -> tuple[Branch, ...]:
        rows = domain.branches(value, query)
        if (not rows or len(rows) > 6 or
                any(not math.isfinite(row.probability) or row.probability < 0 for row in rows) or
                not math.isclose(sum(row.probability for row in rows), 1, abs_tol=1e-9)):
            raise ValueError("INVALID_RESPONSE_MODEL")
        if len({row.outcome for row in rows}) != len(rows):
            raise ValueError("DUPLICATE_RESPONSE_OUTCOME")
        return rows

    @lru_cache(maxsize=None)
    def value(current: Hashable, asked: frozenset[str], left: int, time_ms: int) -> float:
        best = loss(current)
        if domain.resolved(current) or left <= 0:
            return best
        queries = domain.candidates(current, asked)
        if len(queries) > 12:
            raise ValueError("PLANNING_CANDIDATE_LIMIT_EXCEEDED")
        if len({q.key for q in queries}) != len(queries):
            raise ValueError("DUPLICATE_QUERY_KEY")
        for query in sorted(queries, key=lambda item: item.key):
            if not query.key or type(query.cost_ms) is not int or query.cost_ms <= 0 or query.cost_ms > time_ms or query.key in asked:
                continue
            expected = query.cost_ms / 1000 + sum(
                row.probability * value(row.state, asked | {query.key}, left - 1, time_ms - query.cost_ms)
                for row in branches(current, query)
            )
            best = min(best, expected)
        return best

    stop = loss(state)
    if domain.resolved(state):
        return Plan("stop_sufficient", None, 0.0, 0.0, "endpoint_sufficient", horizon)
    if horizon == 0 or remaining_time_ms == 0:
        return Plan("defer", None, stop, stop, "budget_exhausted", horizon)
    best, selected = stop, None
    candidates = domain.candidates(state, asked)
    if len(candidates) > 12:
        raise ValueError("PLANNING_CANDIDATE_LIMIT_EXCEEDED")
    if len({q.key for q in candidates}) != len(candidates):
        raise ValueError("DUPLICATE_QUERY_KEY")
    for query in sorted(candidates, key=lambda item: item.key):
        if not query.key or type(query.cost_ms) is not int or query.cost_ms <= 0:
            raise ValueError("INVALID_QUERY_COST")
        if query.cost_ms > remaining_time_ms:
            continue
        expected = query.cost_ms / 1000 + sum(
            row.probability * value(row.state, frozenset({query.key}), horizon - 1, remaining_time_ms - query.cost_ms)
            for row in branches(state, query)
        )
        if expected < best - 1e-12:
            best, selected = expected, query.key
    return Plan("ask" if selected else "defer", selected, best, stop,
                "positive_decision_value" if selected else "no_positive_decision_value", horizon)


@dataclass(frozen=True)
class ResponsePrior:
    answered: float = 0.75
    unknown: float = 0.10
    declined: float = 0.05
    no_response: float = 0.10
    yes_given_answer: float = 0.50

    def __post_init__(self):
        values = (self.answered, self.unknown, self.declined, self.no_response, self.yes_given_answer)
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in values) or not math.isclose(sum(values[:4]), 1, abs_tol=1e-9):
            raise ValueError("INVALID_RESPONSE_PRIOR")


class SupportPairDomain:
    """Plan burden questions by whether a comparable baseline/follow-up pair
    can be completed, never by whether the answer supports an outcome.

    State is a tuple per slot: bit 0 means baseline is available, bit 1 means
    follow-up is available. `eligible_slots` contains only slots backed by an
    already-completed opportunity and allowed by the source/window/refusal
    checks performed by the service layer.
    """

    def __init__(self, *, state: tuple[int, ...],
                 eligible_baseline_slots: tuple[int, ...],
                 eligible_followup_slots: tuple[int, ...],
                 required_pairs: int, cost_ms: int = 4000,
                 defer_penalty: float = 20.0,
                 prior: ResponsePrior | None = None):
        if (any(type(value) is not int or value not in range(4) for value in state) or
                any(type(slot) is not int or slot < 0 or slot >= len(state)
                    for slot in (*eligible_baseline_slots, *eligible_followup_slots)) or
                type(required_pairs) is not int or required_pairs < 1):
            raise ValueError("INVALID_SUPPORT_DOMAIN")
        self.initial_state = state
        self.eligible_baseline_slots = frozenset(eligible_baseline_slots)
        self.eligible_followup_slots = frozenset(eligible_followup_slots)
        self.required_pairs = required_pairs
        self.cost_ms = cost_ms
        self.defer_penalty = defer_penalty
        self.prior = prior or ResponsePrior()

    def pair_count(self, state: Hashable) -> int:
        return sum(value == 3 for value in state)

    def resolved(self, state: Hashable) -> bool:
        return self.pair_count(state) >= self.required_pairs

    def terminal_loss(self, state: Hashable) -> float:
        missing = max(0, self.required_pairs - self.pair_count(state))
        return float(missing) * self.defer_penalty

    def candidates(self, state: Hashable, asked: frozenset[str]) -> tuple[Query, ...]:
        rows = []
        for slot in sorted(self.eligible_baseline_slots | self.eligible_followup_slots):
            mask = state[slot]
            # Prefer a baseline when neither side exists. The paired follow-up
            # becomes available only on the answered-baseline branch.
            if (not mask & 1 and slot in self.eligible_baseline_slots and
                    f"burden_baseline:{slot}" not in asked):
                rows.append(Query(f"burden_baseline:{slot}", self.cost_ms))
            elif (mask & 1 and not mask & 2 and slot in self.eligible_followup_slots and
                  f"burden_followup:{slot}" not in asked):
                rows.append(Query(f"burden_followup:{slot}", self.cost_ms))
            elif (mask & 2 and not mask & 1 and slot in self.eligible_baseline_slots and
                  f"burden_baseline:{slot}" not in asked):
                rows.append(Query(f"burden_baseline:{slot}", self.cost_ms))
        return tuple(rows)

    def branches(self, state: Hashable, query: Query) -> tuple[Branch, ...]:
        try:
            kind, raw_slot = query.key.split(":", 1)
            slot = int(raw_slot)
        except (ValueError, TypeError):
            raise ValueError("INELIGIBLE_QUERY") from None
        allowed = (self.eligible_baseline_slots if kind == "burden_baseline"
                   else self.eligible_followup_slots if kind == "burden_followup" else frozenset())
        if slot not in allowed:
            raise ValueError("INELIGIBLE_QUERY")
        bit = 1 if kind == "burden_baseline" else 2
        if state[slot] & bit:
            raise ValueError("INELIGIBLE_QUERY")
        answered = list(state)
        answered[slot] |= bit
        p = self.prior
        return (
            Branch("answered", p.answered, tuple(answered)),
            Branch("unknown", p.unknown, state),
            Branch("declined", p.declined, state),
            Branch("no_response", p.no_response, state),
        )


class ExecutionDomain:
    """Expected-loss policy; its prior affects question order, never the proof."""

    def __init__(self, *, target: float, eligible_slots: tuple[int, ...], cost_ms: int = 3000,
                 defer_penalty: float = 20.0, prior: ResponsePrior | None = None):
        self.target = target
        self.eligible_slots = frozenset(eligible_slots)
        self.cost_ms = cost_ms
        self.defer_penalty = defer_penalty
        self.prior = prior or ResponsePrior()

    def resolved(self, state: Hashable) -> bool:
        return execution_proof(state, self.target).label is not None

    def terminal_loss(self, state: Hashable) -> float:
        return 0.0 if self.resolved(state) else self.defer_penalty

    def candidates(self, state: Hashable, asked: frozenset[str]) -> tuple[Query, ...]:
        return tuple(Query(f"execution:{slot}", self.cost_ms) for slot, item in enumerate(state)
                     if item is None and slot in self.eligible_slots and f"execution:{slot}" not in asked)

    def branches(self, state: Hashable, query: Query) -> tuple[Branch, ...]:
        slot = int(query.key.split(":", 1)[1])
        if slot not in self.eligible_slots or state[slot] is not None:
            raise ValueError("INELIGIBLE_QUERY")
        def replace(value: bool):
            result = list(state)
            result[slot] = value
            return tuple(result)
        p = self.prior
        return (
            Branch("completed", p.answered * p.yes_given_answer, replace(True)),
            Branch("not_completed", p.answered * (1 - p.yes_given_answer), replace(False)),
            Branch("unknown", p.unknown, state),
            Branch("declined", p.declined, state),
            Branch("no_response", p.no_response, state),
        )
