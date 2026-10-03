"""Pure, deterministic policy-learning functions.

No SQLAlchemy, model calls, wall clock or random selection belongs here.  This
module is deliberately small enough to replay from a frozen evidence bundle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from statistics import median
from typing import Literal

ALGORITHM_VERSION = "egpl-v1.0.0"
GATE_VERSION = "evidence-gate-v1.0.0"
Endpoint = Literal["execution", "support", "availability"]


def finite(value: float) -> float:
    if isinstance(value, bool) or not math.isfinite(value):
        raise ValueError("NON_FINITE_NUMBER")
    return float(value)


def _digamma(x: float) -> float:
    x = finite(x)
    if x <= 0:
        raise ValueError("INVALID_BETA_PARAMETER")
    result = 0.0
    while x < 8:
        result -= 1 / x
        x += 1
    inv = 1 / x
    inv2 = inv * inv
    return result + math.log(x) - 0.5 * inv - inv2 * (
        1 / 12 - inv2 * (1 / 120 - inv2 * (1 / 252))
    )


@dataclass(frozen=True)
class BetaBelief:
    alpha: float = 1.0
    beta: float = 1.0

    def __post_init__(self):
        if finite(self.alpha) < 1 or finite(self.beta) < 1:
            raise ValueError("INVALID_BETA_PRIOR")

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def episodes(self) -> int:
        return int(self.alpha + self.beta - 2)

    def update(self, label: int | None) -> "BetaBelief":
        if label is None:
            return self
        if type(label) is not int or label not in (0, 1):
            raise ValueError("INVALID_ENDPOINT_LABEL")
        return BetaBelief(self.alpha + label, self.beta + 1 - label)

    def information_gain(self) -> float:
        p = self.mean
        h = -p * math.log(p) - (1 - p) * math.log1p(-p)
        expected_negative_entropy = (
            p * (_digamma(self.alpha + 1) - _digamma(self.alpha + self.beta + 1))
            + (1 - p) * (_digamma(self.beta + 1) - _digamma(self.alpha + self.beta + 1))
        )
        return max(0.0, h + expected_negative_entropy)


@dataclass(frozen=True)
class Scope:
    user_id: int
    strategy_id: str
    protocol_version: str
    metric_version: str
    context_key: str

    def __post_init__(self):
        if self.user_id <= 0 or not all((self.strategy_id, self.protocol_version, self.metric_version, self.context_key)):
            raise ValueError("INVALID_SCOPE")


@dataclass(frozen=True)
class Protocol:
    scope: Scope
    expected_days: int = 7
    minimum_days: int = 5
    minimum_coverage: float = 0.7
    execution_target: float = 0.7
    mode: Literal["absolute", "delta"] = "delta"
    direction: Literal["increase", "decrease"] = "increase"
    target: float = 1.0
    ambiguity_band: float = 0.1
    changed_variable: str = "session_minutes"
    aggregation: Literal["median", "paired_median", "mean", "sum", "fraction", "count"] = "paired_median"

    def __post_init__(self):
        if not 1 <= self.minimum_days <= self.expected_days <= 28:
            raise ValueError("INVALID_WINDOW")
        if not 0 < finite(self.minimum_coverage) <= 1 or not 0 < finite(self.execution_target) <= 1:
            raise ValueError("INVALID_THRESHOLD")
        if finite(self.target) != self.target or finite(self.ambiguity_band) < 0 or not self.changed_variable:
            raise ValueError("INVALID_PROTOCOL")
        if self.aggregation not in {"median", "paired_median", "mean", "sum", "fraction", "count"}:
            raise ValueError("INVALID_AGGREGATION")


@dataclass(frozen=True)
class Point:
    slot: int
    value: float
    source_ref: str
    source_revision: int
    metric_version: str
    confirmed: bool = True

    def __post_init__(self):
        finite(self.value)
        if not self.source_ref or self.source_revision < 1:
            raise ValueError("INVALID_EVIDENCE_REF")


@dataclass(frozen=True)
class EpisodeEvidence:
    episode_id: str
    revision: int
    protocol: Protocol
    execution: tuple[bool | None, ...]
    baseline: tuple[Point, ...] = ()
    followup: tuple[Point, ...] = ()
    baseline_context: str = ""
    followup_context: str = ""
    changed_variables: tuple[str, ...] = ("session_minutes",)
    confounders: tuple[str, ...] = ()
    adverse_event: bool = False
    window_closed: bool = True

    def __post_init__(self):
        if not self.episode_id or self.revision < 1:
            raise ValueError("INVALID_EPISODE")
        if len(self.execution) != self.protocol.expected_days:
            raise ValueError("EXPECTED_OPPORTUNITIES_MISMATCH")
        if any(x is not None and type(x) is not bool for x in self.execution):
            raise ValueError("INVALID_EXECUTION_OBSERVATION")


@dataclass(frozen=True)
class Adjudication:
    scope: Scope
    episode_id: str
    revision: int
    execution_label: int | None
    support_label: int | None
    availability_label: int | None
    conclusion: str
    reasons: tuple[str, ...] = ()
    observed_score: float | None = None


def execution_label(values: tuple[bool | None, ...], target: float) -> int | None:
    if not values:
        return None
    successes = sum(x is True for x in values)
    unknown = sum(x is None for x in values)
    low = successes / len(values)
    high = (successes + unknown) / len(values)
    if low >= target:
        return 1
    if high < target:
        return 0
    return None


def _points(points: tuple[Point, ...], protocol: Protocol) -> dict[int, Point]:
    result: dict[int, Point] = {}
    sources: set[str] = set()
    for point in points:
        if not 0 <= point.slot < protocol.expected_days:
            raise ValueError("OUT_OF_WINDOW_EVIDENCE")
        if point.slot in result:
            raise ValueError("DUPLICATE_COMPARISON_SLOT")
        if point.source_ref in sources:
            raise ValueError("DUPLICATE_SOURCE_EVIDENCE")
        sources.add(point.source_ref)
        if point.confirmed and point.metric_version == protocol.scope.metric_version:
            result[point.slot] = point
    return result


def adjudicate(evidence: EpisodeEvidence) -> Adjudication:
    p = evidence.protocol
    if not evidence.window_closed:
        return Adjudication(p.scope, evidence.episode_id, evidence.revision, None, None, None, "pending", ("window_not_closed",))
    if evidence.followup_context != p.scope.context_key or evidence.changed_variables != (p.changed_variable,):
        return Adjudication(p.scope, evidence.episode_id, evidence.revision, None, None, None, "incomparable", ("actual_strategy_or_context_mismatch",))
    f = execution_label(evidence.execution, p.execution_target)
    if evidence.adverse_event:
        return Adjudication(p.scope, evidence.episode_id, evidence.revision, f, None, None, "stopped", ("adverse_event_requires_runtime_stop",))
    if f != 1:
        return Adjudication(p.scope, evidence.episode_id, evidence.revision, f, None, None, "insufficient_exposure" if f == 0 else "insufficient_data", ("execution_does_not_establish_adequate_exposure",))

    baseline, followup = _points(evidence.baseline, p), _points(evidence.followup, p)
    required = max(p.minimum_days, math.ceil(p.expected_days * p.minimum_coverage))
    reasons: list[str] = []
    if p.mode == "delta" and evidence.baseline_context != p.scope.context_key:
        reasons.append("baseline_context_mismatch")
    if evidence.confounders:
        reasons.append("confounded")
    if len(followup) < required:
        reasons.append("insufficient_followup")
    slots = sorted(followup if p.mode == "absolute" else baseline.keys() & followup.keys())
    if len(slots) < required:
        reasons.append("insufficient_comparable_pairs" if p.mode == "delta" else "insufficient_followup")
    if p.mode == "delta" and {x.source_ref for x in baseline.values()} & {x.source_ref for x in followup.values()}:
        reasons.append("baseline_followup_overlap")
    if reasons:
        incomparable = any(x in reasons for x in ("baseline_context_mismatch", "confounded", "baseline_followup_overlap"))
        return Adjudication(p.scope, evidence.episode_id, evidence.revision, f, None, 0, "incomparable" if incomparable else "insufficient_data", tuple(reasons))

    if p.mode == "delta":
        deltas = [followup[s].value - baseline[s].value for s in slots]
        raw = median(deltas) if p.aggregation in {"paired_median", "median"} else sum(deltas) if p.aggregation == "sum" else sum(deltas) / len(deltas)
    else:
        values = [followup[s].value for s in slots]
        raw = median(values) if p.aggregation in {"paired_median", "median"} else sum(values) if p.aggregation in {"sum", "count"} else sum(values) / len(values)
    score = raw if p.direction == "increase" else -raw
    target = p.target if p.direction == "increase" else -p.target
    if score >= target + p.ambiguity_band:
        support, conclusion = 1, "supports_observed_target"
    elif score < target - p.ambiguity_band:
        support, conclusion = 0, "target_not_supported"
    else:
        support, conclusion = None, "ambiguous"
    return Adjudication(p.scope, evidence.episode_id, evidence.revision, 1, support, 1, conclusion, (), float(raw))


@dataclass(frozen=True)
class Candidate:
    id: str
    scope: Scope
    effort: float
    relevance: float = 1.0
    safe: bool = True
    capability_available: bool = True
    excluded: bool = False


@dataclass
class BeliefSet:
    execution: BetaBelief = field(default_factory=BetaBelief)
    support: BetaBelief = field(default_factory=BetaBelief)
    availability: BetaBelief = field(default_factory=BetaBelief)


def rank_candidates(beliefs: dict[Scope, BeliefSet], candidates: list[Candidate], *, user_id: int, context_key: str, exploration_consented: bool = False, exploration_weight: float = 0.15, effort_weight: float = 0.35, shrinkage_episodes: int = 3) -> dict:
    if len({c.id for c in candidates}) != len(candidates):
        raise ValueError("DUPLICATE_CANDIDATE_ID")
    rows, filtered = [], []
    for candidate in candidates:
        reasons = []
        if candidate.scope.user_id != user_id: reasons.append("foreign_user")
        if candidate.scope.context_key != context_key: reasons.append("context_mismatch")
        if not candidate.safe: reasons.append("hard_constraint")
        if not candidate.capability_available: reasons.append("capability_unavailable")
        if candidate.excluded: reasons.append("user_excluded")
        if reasons:
            filtered.append({"id": candidate.id, "reasons": reasons})
            continue
        b = beliefs.get(candidate.scope, BeliefSet())
        k = b.support.episodes / (b.support.episodes + max(1, shrinkage_episodes))
        shrunk = (1 - k) * 0.5 + k * b.support.mean
        utility = candidate.relevance * b.execution.mean * shrunk
        information = b.execution.mean * b.availability.mean * b.support.information_gain() / math.log(2)
        bonus = exploration_weight * information if exploration_consented else 0.0
        cost = effort_weight * candidate.effort
        rows.append({"id": candidate.id, "score": utility + bonus - cost, "breakdown": {"utility_proxy": utility, "information_bonus": bonus, "cost": cost}, "personalised": b.execution.episodes >= 3 and b.support.episodes >= 3, "eligible_support_episodes": b.support.episodes, "model_means": {"execution": b.execution.mean, "support": b.support.mean, "availability": b.availability.mean}})
    rows.sort(key=lambda row: (-row["score"], row["id"]))
    selected = rows[0]["id"] if rows and rows[0]["score"] > 0 else None
    return {"algorithm_version": ALGORITHM_VERSION, "kind": "propose_action" if selected else "collect_evidence_or_wait", "selected": selected, "ranked": rows, "filtered": filtered, "requires_user_confirmation": selected is not None, "selection_propensity": 1.0 if selected else None, "policy_mode": "deterministic_heuristic"}
