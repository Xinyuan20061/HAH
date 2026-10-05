"""Runnable algorithm reference for the Luna development specification.

This is a documentation asset, not a registered production service. Database
ownership, source resolution, permissions, clocks and transactions are adapter
responsibilities described in the accompanying development document.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
import hashlib
import json
import math
from typing import Hashable, Literal, Protocol


@dataclass(frozen=True)
class ExecutionProof:
    label: int | None
    minimum_completed: int
    lower_completed: int
    upper_completed: int
    unknown_slots: tuple[int, ...]
    witness_slots: tuple[int, ...]
    method: str = "exact_execution_bounds_v1"


def execution_proof(
    values: tuple[bool | None, ...], target: float
) -> ExecutionProof:
    """Exact over all binary completions; no missingness model is required."""
    if not 1 <= len(values) <= 28:
        raise ValueError("INVALID_EXECUTION_WINDOW")
    if isinstance(target, bool) or not math.isfinite(target) or not 0 < target <= 1:
        raise ValueError("INVALID_EXECUTION_TARGET")
    if any(value is not None and type(value) is not bool for value in values):
        raise ValueError("INVALID_EXECUTION_VALUE")
    n = len(values)
    # Match the production function's division/comparison semantics. A bare
    # ceil(target*n) can differ at floating-point boundaries.
    required = next(k for k in range(n + 1) if k / n >= target)
    yes = tuple(i for i, value in enumerate(values) if value is True)
    no = tuple(i for i, value in enumerate(values) if value is False)
    unknown = tuple(i for i, value in enumerate(values) if value is None)
    low, high = len(yes), len(yes) + len(unknown)
    if low / n >= target:
        label, witness = 1, yes[:required]
    elif high / n < target:
        label, witness = 0, no[: n - required + 1]
    else:
        label, witness = None, ()
    return ExecutionProof(label, required, low, high, unknown, witness)


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


def choose_next(
    domain: PlanningDomain,
    state: Hashable,
    *,
    remaining_questions: int,
    remaining_time_ms: int,
    depth: int = 2,
) -> Plan:
    """Finite Bellman lookahead under the explicitly supplied response model.

Loss units are seconds plus a declared seconds-equivalent deferral penalty.
They are NOT clinical harm. The caller supplies real budget eligibility and
conditional response probabilities; this function does not learn them.
"""
    if remaining_questions < 0 or remaining_time_ms < 0 or not 1 <= depth <= 8:
        raise ValueError("INVALID_PLANNING_BUDGET")
    horizon = min(depth, remaining_questions)

    def stop_loss(current: Hashable) -> float:
        value = domain.terminal_loss(current)
        if not math.isfinite(value) or value < 0:
            raise ValueError("INVALID_TERMINAL_LOSS")
        if domain.resolved(current) and value != 0:
            raise ValueError("RESOLVED_STATE_MUST_HAVE_ZERO_LOSS")
        return value

    def outcomes(current: Hashable, query: Query) -> tuple[Branch, ...]:
        rows = domain.branches(current, query)
        if not rows or any(
            not math.isfinite(row.probability) or row.probability < 0
            for row in rows
        ) or not math.isclose(sum(row.probability for row in rows), 1.0, abs_tol=1e-9):
            raise ValueError("INVALID_RESPONSE_MODEL")
        if len({row.outcome for row in rows}) != len(rows):
            raise ValueError("DUPLICATE_RESPONSE_OUTCOME")
        return rows

    @lru_cache(maxsize=None)
    def value(current: Hashable, asked: frozenset[str], left: int, time_ms: int) -> float:
        result = stop_loss(current)
        if domain.resolved(current) or left <= 0:
            return result
        queries = domain.candidates(current, asked)
        if len({query.key for query in queries}) != len(queries):
            raise ValueError("DUPLICATE_QUERY_KEY")
        for query in sorted(queries, key=lambda q: q.key):
            if not query.key or type(query.cost_ms) is not int or query.cost_ms <= 0:
                raise ValueError("INVALID_QUERY_COST")
            if query.key in asked or query.cost_ms > time_ms:
                continue
            future = sum(
                row.probability * value(
                    row.state, asked | {query.key}, left - 1, time_ms - query.cost_ms
                )
                for row in outcomes(current, query)
            )
            result = min(result, query.cost_ms / 1000.0 + future)
        return result

    initial_loss = stop_loss(state)
    if domain.resolved(state):
        return Plan("stop_sufficient", None, 0.0, 0.0, "endpoint_sufficient", horizon)
    if horizon == 0 or remaining_time_ms == 0:
        return Plan("defer", None, initial_loss, initial_loss, "budget_exhausted", horizon)
    best_loss, best_query = initial_loss, None
    queries = domain.candidates(state, frozenset())
    if len({query.key for query in queries}) != len(queries):
        raise ValueError("DUPLICATE_QUERY_KEY")
    for query in sorted(queries, key=lambda q: q.key):
        if not query.key or type(query.cost_ms) is not int or query.cost_ms <= 0:
            raise ValueError("INVALID_QUERY_COST")
        if query.cost_ms > remaining_time_ms:
            continue
        candidate_loss = query.cost_ms / 1000.0 + sum(
            row.probability * value(
                row.state, frozenset({query.key}), horizon - 1,
                remaining_time_ms - query.cost_ms,
            )
            for row in outcomes(state, query)
        )
        if candidate_loss < best_loss - 1e-12:
            best_loss, best_query = candidate_loss, query.key
    return Plan(
        "ask" if best_query else "defer", best_query, best_loss, initial_loss,
        "positive_decision_value" if best_query else "no_positive_decision_value",
        horizon,
    )


@dataclass(frozen=True)
class ResponsePrior:
    """Illustrative cold-start prior; MUST be labelled and sensitivity-tested."""

    answered: float = 0.75
    unknown: float = 0.10
    declined: float = 0.05
    no_response: float = 0.10
    yes_given_answer: float = 0.50

    def __post_init__(self):
        probabilities = (self.answered, self.unknown, self.declined, self.no_response)
        if any(not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities):
            raise ValueError("INVALID_RESPONSE_PRIOR")
        if not math.isclose(sum(probabilities), 1.0, abs_tol=1e-9):
            raise ValueError("INVALID_RESPONSE_PRIOR")
        if not math.isfinite(self.yes_given_answer) or not 0 <= self.yes_given_answer <= 1:
            raise ValueError("INVALID_ANSWER_PRIOR")


class ExecutionDomain:
    def __init__(
        self, *, target: float, eligible_slots: tuple[int, ...],
        cost_ms: int = 3000, defer_penalty: float = 20.0,
        prior: ResponsePrior = ResponsePrior(),
    ):
        self.target, self.eligible_slots = target, frozenset(eligible_slots)
        self.cost_ms, self.defer_penalty, self.prior = cost_ms, defer_penalty, prior

    def resolved(self, state: Hashable) -> bool:
        return execution_proof(state, self.target).label is not None

    def terminal_loss(self, state: Hashable) -> float:
        return 0.0 if self.resolved(state) else self.defer_penalty

    def candidates(self, state: Hashable, asked: frozenset[str]) -> tuple[Query, ...]:
        return tuple(
            Query(f"execution:{i}", self.cost_ms)
            for i, value in enumerate(state)
            if value is None and i in self.eligible_slots and f"execution:{i}" not in asked
        )

    def branches(self, state: Hashable, query: Query) -> tuple[Branch, ...]:
        slot = int(query.key.split(":")[1])
        if slot not in self.eligible_slots or state[slot] is not None:
            raise ValueError("INELIGIBLE_QUERY")
        def changed(value: bool) -> tuple:
            rows = list(state)
            rows[slot] = value
            return tuple(rows)
        p = self.prior
        return (
            Branch("completed", p.answered * p.yes_given_answer, changed(True)),
            Branch("not_completed", p.answered * (1 - p.yes_given_answer), changed(False)),
            Branch("unknown", p.unknown, state),
            Branch("declined", p.declined, state),
            Branch("no_response", p.no_response, state),
        )


@dataclass(frozen=True)
class CertificateBinding:
    user_id: int
    episode_id: str
    episode_version: int
    protocol_hash: str
    metric_version: str
    context_key: str
    learning_epoch: str
    rule_version: str
    capability_snapshot_hash: str
    evidence_snapshot_hash: str
    source_generation_hash: str
    knowledge_contract_hash: str
    window_closed: bool


@dataclass(frozen=True)
class Certificate:
    binding: CertificateBinding
    proof: ExecutionProof
    expires_at_epoch_seconds: int
    purpose: Literal["execution_progress", "execution_endpoint"]
    body_hash: str


def _certificate_hash(binding, proof, expiry, purpose) -> str:
    material = {
        "binding": asdict(binding), "proof": asdict(proof),
        "expires_at_epoch_seconds": expiry, "purpose": purpose,
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def issue_execution_certificate(
    binding: CertificateBinding, proof: ExecutionProof, *, expires_at_epoch_seconds: int,
) -> Certificate:
    if binding.user_id <= 0 or binding.episode_version < 1 or proof.label is None:
        raise ValueError("CERTIFICATE_REQUIRES_VALID_BINDING_AND_PROOF")
    if expires_at_epoch_seconds <= 0:
        raise ValueError("INVALID_CERTIFICATE_EXPIRY")
    purpose = "execution_endpoint" if binding.window_closed else "execution_progress"
    digest = _certificate_hash(binding, proof, expires_at_epoch_seconds, purpose)
    return Certificate(binding, proof, expires_at_epoch_seconds, purpose, digest)


def verify_execution_certificate(
    certificate: Certificate, current: CertificateBinding, *,
    now_epoch_seconds: int, revoked: bool = False,
    sources_current: bool = True, authorization_valid: bool = True,
    pending_source_rebuild: bool = False,
) -> tuple[bool, str]:
    if revoked:
        return False, "certificate_revoked"
    if not authorization_valid:
        return False, "authorization_changed"
    if pending_source_rebuild or not sources_current:
        return False, "source_not_current"
    if now_epoch_seconds >= certificate.expires_at_epoch_seconds:
        return False, "certificate_expired"
    expected_hash = _certificate_hash(
        certificate.binding, certificate.proof,
        certificate.expires_at_epoch_seconds, certificate.purpose,
    )
    if expected_hash != certificate.body_hash:
        return False, "certificate_body_changed"
    if certificate.binding != current:
        return False, "certificate_binding_changed"
    if certificate.purpose == "execution_endpoint" and not current.window_closed:
        return False, "window_not_closed"
    return True, "current"
