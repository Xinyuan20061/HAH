"""Executable algorithm reference; this is NOT connected to production services.

See HEALTHMATE_PERSONAL_POLICY_LEARNING_ALGORITHM_SPEC_2026-10-02.md.
Only the Python standard library is used. All example values are synthetic.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from statistics import median
from typing import Literal

ALGORITHM_VERSION = "egpl-reference-1.0.0"
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
    while x < 8.0:
        result -= 1.0 / x
        x += 1.0
    inv = 1.0 / x
    inv2 = inv * inv
    return result + math.log(x) - 0.5 * inv - inv2 * (
        1.0 / 12.0 - inv2 * (1.0 / 120.0 - inv2 * (1.0 / 252.0))
    )


@dataclass(frozen=True)
class BetaBelief:
    alpha: float = 1.0
    beta: float = 1.0

    def __post_init__(self) -> None:
        if finite(self.alpha) < 1 or finite(self.beta) < 1:
            raise ValueError("REFERENCE_REQUIRES_UNIFORM_PRIOR_AND_COUNTS")

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def episodes(self) -> int:
        return int(self.alpha + self.beta - 2)

    def updated(self, label: int | None) -> BetaBelief:
        if label is None:
            return self
        if type(label) is not int or label not in (0, 1):
            raise ValueError("INVALID_ENDPOINT_LABEL")
        return BetaBelief(self.alpha + label, self.beta + 1 - label)

    def information_gain(self) -> float:
        """I(theta; next Bernoulli observation), in nats; not full IDS."""
        a, b = self.alpha, self.beta
        p = self.mean
        h = -p * math.log(p) - (1 - p) * math.log1p(-p)
        expected_negative_entropy = (
            p * (_digamma(a + 1) - _digamma(a + b + 1))
            + (1 - p) * (_digamma(b + 1) - _digamma(a + b + 1))
        )
        return max(0.0, h + expected_negative_entropy)


@dataclass(frozen=True)
class Scope:
    user_id: int
    strategy_id: str
    protocol_version: str
    metric_version: str
    context_key: str

    def __post_init__(self) -> None:
        if self.user_id <= 0 or not all(
            (self.strategy_id, self.protocol_version, self.metric_version, self.context_key)
        ):
            raise ValueError("INVALID_SCOPE")


@dataclass(frozen=True)
class Protocol:
    scope: Scope
    # Values here are ENGINEERING example thresholds, not medical thresholds.
    expected_days: int = 7
    minimum_days: int = 5
    minimum_coverage: float = 0.7
    execution_target: float = 0.7
    mode: Literal["absolute", "delta"] = "delta"
    direction: Literal["increase", "decrease"] = "increase"
    target: float = 1.0
    ambiguity_band: float = 0.1
    changed_variable: str = "session_minutes"

    def __post_init__(self) -> None:
        if not 1 <= self.minimum_days <= self.expected_days <= 28:
            raise ValueError("INVALID_WINDOW")
        for x in (self.minimum_coverage, self.execution_target):
            if not 0 < finite(x) <= 1:
                raise ValueError("INVALID_THRESHOLD")
        if finite(self.ambiguity_band) < 0 or not self.changed_variable:
            raise ValueError("INVALID_PROTOCOL")
        finite(self.target)
        if self.mode not in ("absolute", "delta"):
            raise ValueError("INVALID_MODE")
        if self.direction not in ("increase", "decrease"):
            raise ValueError("INVALID_DIRECTION")


@dataclass(frozen=True)
class Point:
    # slot is a pre-registered comparable occasion: e.g. weekday at the same time.
    slot: int
    value: float
    source_ref: str
    source_revision: int
    metric_version: str
    confirmed: bool = True

    def __post_init__(self) -> None:
        finite(self.value)
        if not self.source_ref or self.source_revision < 1:
            raise ValueError("INVALID_EVIDENCE_REF")


@dataclass(frozen=True)
class Episode:
    episode_id: str
    revision: int
    protocol: Protocol
    # A None opportunity means unknown, never an automatic failure.
    execution: tuple[bool | None, ...]
    baseline: tuple[Point, ...] = ()
    followup: tuple[Point, ...] = ()
    changed_variables: tuple[str, ...] = ("session_minutes",)
    baseline_context: str = ""
    followup_context: str = ""
    confounders: tuple[str, ...] = ()
    adverse_event: bool = False
    window_closed: bool = True

    def __post_init__(self) -> None:
        if not self.episode_id or self.revision < 1:
            raise ValueError("INVALID_EPISODE")
        if len(self.execution) != self.protocol.expected_days:
            raise ValueError("EXPECTED_OPPORTUNITIES_MISMATCH")
        if any(value is not None and type(value) is not bool for value in self.execution):
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
    reasons: tuple[str, ...]
    observed_score: float | None = None


def execution_label(values: tuple[bool | None, ...], target: float) -> int | None:
    """Decide only if all possible assignments of missing values agree."""
    successes = sum(value is True for value in values)
    unknown = sum(value is None for value in values)
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
            raise ValueError("DUPLICATE_DAY_MUST_BE_RESOLVED_BY_ADAPTER")
        if point.source_ref in sources:
            raise ValueError("DUPLICATE_SOURCE_EVIDENCE")
        sources.add(point.source_ref)
        if not point.confirmed or point.metric_version != protocol.scope.metric_version:
            continue
        result[point.slot] = point
    return result


def adjudicate(episode: Episode) -> Adjudication:
    p = episode.protocol
    if not episode.window_closed:
        return Adjudication(
            p.scope, episode.episode_id, episode.revision, None, None, None,
            "pending", ("window_not_closed",),
        )
    if episode.followup_context != p.scope.context_key or episode.changed_variables != (
        p.changed_variable,
    ):
        # These observations no longer describe this strategy/context arm.
        return Adjudication(
            p.scope, episode.episode_id, episode.revision, None, None, None,
            "incomparable", ("actual_strategy_or_context_mismatch",),
        )
    execution = execution_label(episode.execution, p.execution_target)
    if episode.adverse_event:
        return Adjudication(
            p.scope, episode.episode_id, episode.revision, execution, None, None,
            "stopped", ("adverse_event_requires_runtime_stop",),
        )
    if execution != 1:
        return Adjudication(
            p.scope, episode.episode_id, episode.revision, execution, None, None,
            "insufficient_exposure" if execution == 0 else "insufficient_data",
            ("execution_does_not_establish_adequate_exposure",),
        )

    reasons: list[str] = []
    if episode.changed_variables != (p.changed_variable,):
        reasons.append("single_variable_violation")
    if episode.followup_context != p.scope.context_key:
        reasons.append("followup_context_mismatch")
    if p.mode == "delta" and episode.baseline_context != p.scope.context_key:
        reasons.append("baseline_context_mismatch")
    if episode.confounders:
        reasons.append("confounded")
    baseline = _points(episode.baseline, p)
    followup = _points(episode.followup, p)
    required = max(p.minimum_days, math.ceil(p.expected_days * p.minimum_coverage))
    if len(followup) < required:
        reasons.append("insufficient_followup")
    if p.mode == "delta":
        slots = sorted(baseline.keys() & followup.keys())
        if len(slots) < required:
            reasons.append("insufficient_comparable_pairs")
        # A source row cannot be both a baseline and a follow-up observation.
        if {x.source_ref for x in baseline.values()} & {
            x.source_ref for x in followup.values()
        }:
            reasons.append("baseline_followup_overlap")
    else:
        slots = sorted(followup)
    if reasons:
        return Adjudication(
            p.scope, episode.episode_id, episode.revision, execution, None, 0,
            "incomparable" if any(x in reasons for x in (
                "single_variable_violation", "confounded", "baseline_context_mismatch",
                "followup_context_mismatch", "baseline_followup_overlap",
            )) else "insufficient_data", tuple(reasons),
        )

    if p.mode == "delta":
        raw_score = median(followup[s].value - baseline[s].value for s in slots)
    else:
        raw_score = median(followup[s].value for s in slots)
    score = raw_score if p.direction == "increase" else -raw_score
    signed_target = p.target if p.direction == "increase" else -p.target
    if score >= signed_target + p.ambiguity_band:
        support, conclusion = 1, "supports_observed_target"
    elif score < signed_target - p.ambiguity_band:
        support, conclusion = 0, "target_not_supported"
    else:
        support, conclusion = None, "ambiguous"
    return Adjudication(
        p.scope, episode.episode_id, episode.revision, execution, support, 1,
        conclusion, (), float(raw_score),
    )


@dataclass
class BeliefSet:
    execution: BetaBelief = field(default_factory=BetaBelief)
    support: BetaBelief = field(default_factory=BetaBelief)
    availability: BetaBelief = field(default_factory=BetaBelief)


class Ledger:
    """Rebuildable, revision-aware in-memory example, NOT a database adapter."""

    def __init__(self) -> None:
        self._rows: dict[tuple[Scope, str], Adjudication] = {}

    def put(self, item: Adjudication) -> bool:
        key = (item.scope, item.episode_id)
        for old_scope, episode_id in self._rows:
            if (
                episode_id == item.episode_id
                and old_scope.user_id == item.scope.user_id
                and old_scope != item.scope
            ):
                raise ValueError("IMMUTABLE_EPISODE_SCOPE_CHANGED")
        old = self._rows.get(key)
        if old is not None:
            if old.revision > item.revision:
                raise ValueError("STALE_REVISION")
            if old.revision == item.revision:
                if old != item:
                    raise ValueError("REVISION_PAYLOAD_CONFLICT")
                return False
        self._rows[key] = item
        return True

    def remove(self, scope: Scope, episode_id: str) -> bool:
        return self._rows.pop((scope, episode_id), None) is not None

    def beliefs(self, scope: Scope) -> BeliefSet:
        result = BeliefSet()
        for (key_scope, _), item in self._rows.items():
            if key_scope != scope:
                continue
            result.execution = result.execution.updated(item.execution_label)
            result.support = result.support.updated(item.support_label)
            result.availability = result.availability.updated(item.availability_label)
        return result


@dataclass(frozen=True)
class Candidate:
    id: str
    scope: Scope
    effort: float
    relevance: float = 1.0
    safe: bool = True
    capability_available: bool = True
    excluded: bool = False

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("INVALID_CANDIDATE")
        for value in (self.effort, self.relevance):
            if not 0 <= finite(value) <= 1:
                raise ValueError("NORMALISED_VALUE_REQUIRED")


def rank_candidates(
    ledger: Ledger,
    candidates: list[Candidate],
    *,
    user_id: int,
    context_key: str,
    exploration_consented: bool = False,
    exploration_weight: float = 0.15,
    effort_weight: float = 0.35,
    shrinkage_episodes: int = 3,
) -> dict:
    if len({c.id for c in candidates}) != len(candidates):
        raise ValueError("DUPLICATE_CANDIDATE_ID")
    if shrinkage_episodes < 1 or finite(exploration_weight) < 0 or finite(effort_weight) < 0:
        raise ValueError("INVALID_RANKING_CONFIG")
    rows, filtered = [], []
    for c in candidates:
        reasons = []
        if c.scope.user_id != user_id:
            reasons.append("foreign_user")
        if c.scope.context_key != context_key:
            reasons.append("context_mismatch")
        if not c.safe:
            reasons.append("hard_constraint")
        if not c.capability_available:
            reasons.append("capability_unavailable")
        if c.excluded:
            reasons.append("user_excluded")
        if reasons:
            filtered.append({"id": c.id, "reasons": reasons})
            continue
        b = ledger.beliefs(c.scope)
        k = b.support.episodes / (b.support.episodes + shrinkage_episodes)
        supported = (1 - k) * 0.5 + k * b.support.mean
        utility = c.relevance * b.execution.mean * supported
        ig = (
            b.execution.mean * b.availability.mean
            * b.support.information_gain() / math.log(2)
        )
        bonus = exploration_weight * ig if exploration_consented else 0.0
        cost = effort_weight * c.effort
        rows.append({
            "id": c.id,
            "score": utility + bonus - cost,
            "breakdown": {"utility_proxy": utility, "information_bonus": bonus, "cost": cost},
            "personalised": b.execution.episodes >= 3 and b.support.episodes >= 3,
            "eligible_support_episodes": b.support.episodes,
            "model_means": {
                "execution": b.execution.mean, "support": b.support.mean,
                "availability": b.availability.mean,
            },
        })
    rows.sort(key=lambda x: (-x["score"], x["id"]))
    selected = rows[0]["id"] if rows and rows[0]["score"] > 0 else None
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "kind": "propose_action" if selected else "collect_evidence_or_wait",
        "selected": selected, "ranked": rows, "filtered": filtered,
        "requires_user_confirmation": selected is not None,
        "selection_propensity": 1.0 if selected else None,
        "policy_mode": "deterministic_heuristic",
    }


def demo() -> dict:
    scope = Scope(1, "short_session", "1.0.0", "burden-v1", "weekday:tight")
    protocol = Protocol(scope, direction="decrease", target=-1.0)
    baseline = tuple(Point(i, 7.0, f"baseline:{i}", 1, "burden-v1") for i in range(7))
    followup = tuple(Point(i, 4.0, f"followup:{i}", 1, "burden-v1") for i in range(7))
    episode = Episode(
        "synthetic-1", 1, protocol, (True,) * 7, baseline, followup,
        baseline_context=scope.context_key, followup_context=scope.context_key,
    )
    verdict = adjudicate(episode)
    ledger = Ledger()
    ledger.put(verdict)
    recommendation = rank_candidates(
        ledger, [Candidate("short_session", scope, 0.2)],
        user_id=1, context_key=scope.context_key,
    )
    return {
        "synthetic_example": True,
        "verdict": asdict(verdict),
        "beliefs": asdict(ledger.beliefs(scope)),
        "recommendation": recommendation,
        "note": "Observed target support only; no causal or medical-effect claim.",
    }


if __name__ == "__main__":
    print(json.dumps(demo(), ensure_ascii=True, indent=2, allow_nan=False))
