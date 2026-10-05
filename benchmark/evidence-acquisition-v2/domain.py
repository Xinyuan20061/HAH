"""Composite two-endpoint domain: exact execution endpoint + paired burden endpoint.

Design (spec work package A2):
- The execution endpoint is a 7-slot completeness proof (reused exact oracle).
- The burden endpoint models baseline/follow-up pairs (SupportPairDomain
  semantics): a pair only counts once BOTH halves are answered. This is the
  complementary structure absent from the v1 execution-only benchmark and is
  the mechanism a horizon-2 policy can exploit: ask baseline first, and the
  answered branch makes the follow-up eligible, so a two-step plan can complete
  a pair where a horizon-1 plan sees only a half-pair.
- unknown/declined/no_response branches leave the composite state unchanged;
  the asked key becomes unaskable.
- terminal loss is a weighted sum of execution deferral penalty (with per-hole
  penalty, matching v1) and burden missing-pair penalty. All weights are
  configurable/freezable/ablatable per spec A3.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Hashable

from app.services.policy_learning.acquisition.oracle import execution_proof


@dataclass(frozen=True)
class Query:
    key: str
    cost_ms: int


@dataclass(frozen=True)
class Branch:
    outcome: str
    probability: float
    state: Hashable


@dataclass(frozen=True)
class ResponsePrior:
    answered: float = 0.75
    unknown: float = 0.10
    declined: float = 0.05
    no_response: float = 0.10
    yes_given_answer: float = 0.50

    def __post_init__(self):
        if not (0 <= self.answered <= 1 and 0 <= self.unknown <= 1
                and 0 <= self.declined <= 1 and 0 <= self.no_response <= 1):
            raise ValueError("INVALID_RESPONSE_PRIOR")


@dataclass
class CompositeDomain:
    """State is (execution_tuple, burden_bits_tuple); burden_bits in 0..3 per slot."""

    execution_target: float
    eligible_execution_slots: tuple[int, ...]
    eligible_burden_slots: tuple[int, ...]
    required_pairs: int
    execution_cost_ms: int = 3000
    burden_cost_ms: int = 4000
    defer_penalty: float = 20.0
    per_hole_penalty: float = 5.0
    burden_missing_penalty: float = 20.0
    prior: ResponsePrior | None = None

    def __post_init__(self):
        if not 0 <= len(self.eligible_execution_slots) + len(self.eligible_burden_slots) <= 28:
            raise ValueError("INVALID_WINDOW_SIZE")
        self._prior = self.prior or ResponsePrior()

    # -- helpers -----------------------------------------------------------

    def _execution(self, state: Hashable):
        return state[0]

    def _burden(self, state: Hashable):
        return state[1]

    def pair_count(self, state: Hashable) -> int:
        return sum(1 for bits in self._burden(state) if bits == 3)

    def exec_label(self, state: Hashable) -> int | None:
        return execution_proof(tuple(self._execution(state)), self.execution_target).label

    # -- domain protocol used by choose_next -------------------------------

    def resolved(self, state: Hashable) -> bool:
        return (self.exec_label(state) is not None
                and self.pair_count(state) >= self.required_pairs)

    def terminal_loss(self, state: Hashable) -> float:
        proof = execution_proof(tuple(self._execution(state)), self.execution_target)
        exec_part = (0.0 if proof.label is not None
                     else self.defer_penalty + self.per_hole_penalty * len(proof.unknown_slots))
        burden_part = (self.burden_missing_penalty
                       * max(0, self.required_pairs - self.pair_count(state)))
        return exec_part + burden_part

    def candidates(self, state: Hashable, asked: frozenset[str]) -> tuple[Query, ...]:
        rows: list[Query] = []
        for slot, value in enumerate(self._execution(state)):
            if (value is None and slot in self.eligible_execution_slots
                    and f"execution:{slot}" not in asked):
                rows.append(Query(f"execution:{slot}", self.execution_cost_ms))
        for slot, bits in enumerate(self._burden(state)):
            if slot not in self.eligible_burden_slots:
                continue
            if (not bits & 1 and f"burden_baseline:{slot}" not in asked):
                rows.append(Query(f"burden_baseline:{slot}", self.burden_cost_ms))
            elif (bits & 1 and not bits & 2 and f"burden_followup:{slot}" not in asked):
                rows.append(Query(f"burden_followup:{slot}", self.burden_cost_ms))
        return tuple(rows)

    def branches(self, state: Hashable, query: Query) -> tuple[Branch, ...]:
        p = self._prior
        kind, raw = query.key.split(":", 1)
        slot = int(raw)
        execution, burden = list(self._execution(state)), list(self._burden(state))
        if kind == "execution":
            if slot not in self.eligible_execution_slots or execution[slot] is not None:
                raise ValueError("INELIGIBLE_QUERY")

            def replace(value: bool):
                out = list(execution)
                out[slot] = value
                return (tuple(out), tuple(burden))

            return (
                Branch("completed", p.answered * p.yes_given_answer, replace(True)),
                Branch("not_completed", p.answered * (1 - p.yes_given_answer), replace(False)),
                Branch("unknown", p.unknown, state),
                Branch("declined", p.declined, state),
                Branch("no_response", p.no_response, state),
            )
        bit = 1 if kind == "burden_baseline" else 2 if kind == "burden_followup" else 0
        if not bit or slot not in self.eligible_burden_slots or burden[slot] & bit:
            raise ValueError("INELIGIBLE_QUERY")
        answered = list(burden)
        answered[slot] |= bit
        return (
            Branch("answered", p.answered, (tuple(execution), tuple(answered))),
            Branch("unknown", p.unknown, state),
            Branch("declined", p.declined, state),
            Branch("no_response", p.no_response, state),
        )
