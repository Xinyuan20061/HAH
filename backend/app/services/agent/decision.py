"""Decision Contract + Next Best Action (capability plan §8.2/§8.3/§8.4).

The pipeline the Agent is allowed to follow:

    Health State + Signals → candidate action space → hard filter
      → preference/outcome ranking → Next Best Action + alternatives

What makes this a *contract* rather than a heuristic:

* the candidate set is closed (``ACTION_CANDIDATES``) — the model cannot invent an
  action, and every candidate that can write names an ``action_key`` that must
  exist in the Action Registry;
* a candidate is filtered out by a named reason (capability, hard constraint,
  memory, already active), and those reasons are returned to the caller;
* the response is serialisable and testable, so "why this suggestion" has an answer
  that does not require reading a prompt.

Deterministic scoring only: no model call, and no drift between runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.services.agent.actions import ACTION_REGISTRY
from app.services.agent.capability_graph import capability_graph
from app.services.health_state import HealthStateSnapshot, build_snapshot

ACTION_CANDIDATES: tuple[str, ...] = (
    "plan.apply",
    "plan.replan.apply",
    "goal.adjustment.apply",
    "experiment.start",
    "diet.ai.finalize",
    "review_records",
    "recovery_action",
    "seek_care",
)

# Ranking weights. Named so the breakdown can be shown and reviewed.
WEIGHTS: dict[str, float] = {
    "expected_impact": 1.0,
    "efficacy_from_history": 0.7,
    "urgency": 0.8,
    "plan_fit": 0.6,
    "effort": -0.5,
    "negative_feedback": -1.2,
}

EFFORT_BY_CANDIDATE: dict[str, float] = {
    "plan.apply": 0.5,
    "plan.replan.apply": 0.3,
    "goal.adjustment.apply": 0.4,
    "experiment.start": 0.7,
    "diet.ai.finalize": 0.2,
    "review_records": 0.2,
    "recovery_action": 0.3,
    "seek_care": 0.1,
}

# Candidates that must never be auto-selected: they are informational, or they are
# safety escalations the user must decide on with a human.
NEVER_AUTO: frozenset[str] = frozenset({"seek_care"})


@dataclass
class Candidate:
    id: str
    action_key: str | None
    title: str
    reason: str
    evidence: list[str] = field(default_factory=list)
    requires_user_confirmation: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "action_key": self.action_key,
            "title": self.title,
            "reason": self.reason,
            "evidence": self.evidence,
            "requires_user_confirmation": self.requires_user_confirmation,
        }


@dataclass
class Ranked:
    candidate: Candidate
    score: float
    breakdown: dict[str, float]


@dataclass
class DecisionContract:
    as_of: str
    state_snapshot_hash: str
    capabilities_available: list[str]
    candidates: list[Ranked]
    filtered: list[dict]
    next_best_action: Ranked | None
    alternatives: list[Ranked]
    memory_used: list[dict] = field(default_factory=list)
    memory_ignored: list[dict] = field(default_factory=list)
    budget_note: str = (
        "决策层为纯规则计算，不消耗模型调用；只有最终解释答复走模型。"
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of,
            "state_snapshot_hash": self.state_snapshot_hash,
            "capabilities_available": self.capabilities_available,
            "candidates": [
                {
                    "id": item.candidate.id,
                    "title": item.candidate.title,
                    "action_key": item.candidate.action_key,
                    "score": item.score,
                    "breakdown": item.breakdown,
                    "reason": item.candidate.reason,
                    "evidence": item.candidate.evidence,
                    "requires_user_confirmation": item.candidate.requires_user_confirmation,
                }
                for item in self.candidates
            ],
            "filtered": self.filtered,
            # Capability plan §9.2: the contract must show the memory it actually
            # used, so "the system learned your preference" is inspectable and a
            # cleared preference provably stops appearing here.
            "memory_used": self.memory_used,
            "memory_ignored": self.memory_ignored,
            "next_best_action": (
                {
                    "id": self.next_best_action.candidate.id,
                    "title": self.next_best_action.candidate.title,
                    "action_key": self.next_best_action.candidate.action_key,
                    "score": self.next_best_action.score,
                    "breakdown": self.next_best_action.breakdown,
                    "reason": self.next_best_action.candidate.reason,
                    "evidence": self.next_best_action.candidate.evidence,
                }
                if self.next_best_action
                else None
            ),
            "alternatives": [
                {
                    "id": item.candidate.id,
                    "title": item.candidate.title,
                    "action_key": item.candidate.action_key,
                    "score": item.score,
                    "reason": item.candidate.reason,
                }
                for item in self.alternatives
            ],
            "budget_note": self.budget_note,
            "policy": (
                "候选集合是封闭的；每个能写库的候选都指向已注册 Action，"
                "任何写入都需要用户确认；命中安全规则时只提议求助/记录，不提议加量。"
                "长期记忆只能影响候选排序与档位，不能改变硬约束。"
            ),
        }


def _state(db: Session, user_id: int, window_days: int) -> HealthStateSnapshot:
    # Persisted snapshots may contain source domains the current capability
    # configuration no longer grants; compute a fresh scoped view for decisions.
    from app.harness.plugins import health_state_excluded_sources

    return build_snapshot(
        db, user_id, window_days=window_days, persist=False,
        excluded_sources=health_state_excluded_sources(db, user_id),
    )


def _denied_constraints(state: HealthStateSnapshot) -> set[str]:
    return set(state.block_keys())


def _preference(db: Session, user_id: int, key: str) -> str | None:
    from app.services.agent.outcome import preference_value

    return preference_value(db, user_id, key)


def planning_preferences(db: Session, user_id: int) -> dict[str, Any]:
    """Memory projected onto plan inputs, within its allowed influence.

    Capability plan §9.2/§9.3: memory may tune the *shape* of a plan (variant,
    session length, equipment, training days) and nothing else. A stored value that
    is expired, low-confidence or not a behavioural key is ignored here — which is
    the same rule ``memory_view`` publishes, applied at the point of use.
    """
    from app.harness.plugins import capability_scope_granted
    if not capability_scope_granted(db, user_id, "plan_outcome", "user.preferences.read"):
        return {}
    from app.services.agent.outcome import memory_view

    effective = memory_view(db, user_id)["effective"]
    out: dict[str, Any] = {}
    variant = effective.get("plan_variant")
    if variant in {"gentle", "standard"}:
        out["intensity_preference"] = variant
    minutes = effective.get("session_minutes")
    if isinstance(minutes, str) and minutes.isdigit():
        out["minutes_per_session"] = max(10, min(120, int(minutes)))
    days = effective.get("training_days")
    if isinstance(days, str) and days.isdigit():
        out["days_per_week"] = max(1, min(7, int(days)))
    equipment = effective.get("equipment")
    if isinstance(equipment, str) and equipment.strip():
        out["equipment"] = [item.strip() for item in equipment.split(",") if item.strip()]
    return out


def _policy_lookup(db: Session, user_id: int) -> dict[str, dict]:
    from app.services.agent.outcome import policy_snapshot

    return {
        row["action_family"]: row for row in policy_snapshot(db, user_id)
    }


def build_candidates(
    db: Session,
    user_id: int,
    state: HealthStateSnapshot,
    signals: list[dict],
) -> list[Candidate]:
    """Deterministic candidate set derived from the state, not from a prompt."""
    candidates: list[Candidate] = []
    signal_codes = {item.get("code") for item in signals}
    denied = _denied_constraints(state)

    if "seek_care" not in NEVER_AUTO or True:
        # Safety first: if a rule fired, the only proposals are escalation and
        # recording — never a load increase.
        if {key for key in denied if key.startswith("safety_rule:")}:
            return [
                Candidate(
                    id="seek_care",
                    action_key=None,
                    title="先咨询专业人员并继续记录",
                    reason="近期命中安全规则；不提议任何训练或目标变更",
                    evidence=sorted(k for k in denied if k.startswith("safety_rule:")),
                    requires_user_confirmation=False,
                ),
                Candidate(
                    id="review_records",
                    action_key=None,
                    title="补齐并复核记录",
                    reason="记录能帮助专业人员判断；本条只读不写库",
                    evidence=["health.state.read"],
                    requires_user_confirmation=False,
                ),
            ]

    coverage_blocked = "insufficient_record_coverage" in denied
    if coverage_blocked or "record_gap" in signal_codes:
        candidates.append(
            Candidate(
                id="review_records",
                action_key=None,
                title="先补齐 3 天记录",
                reason="记录覆盖不足，先获得可用数据再谈计划调整",
                evidence=["data_reliability_score", "diet_record_coverage_7d"],
                requires_user_confirmation=False,
            )
        )

    plan_available = "plan.solve" in capability_graph(db, user_id=user_id) and not coverage_blocked
    if plan_available and "plan.solve" in {
        key for key, value in capability_graph(db, user_id=user_id).items() if value.available
    }:
        candidates.append(
            Candidate(
                id="plan.apply",
                action_key="plan.apply",
                title="生成并确认本周计划",
                reason="记录与约束足以求解一份合法计划",
                evidence=["plan_adherence_7d", "sleep_debt_7d", "data_reliability_score"],
            )
        )

    sleep_debt = state.numeric("sleep_debt_7d")
    if (sleep_debt is not None and sleep_debt >= 5) or "sleep_debt" in signal_codes:
        candidates.append(
            Candidate(
                id="recovery_action",
                action_key=None,
                title="安排恢复日并提前入睡",
                reason=f"近 7 日睡眠债 {sleep_debt}h，优先恢复而不是加量",
                evidence=["sleep_debt_7d"],
                requires_user_confirmation=False,
            )
        )

    adherence = state.numeric("plan_adherence_7d")
    if adherence is not None and adherence < 0.5:
        candidates.append(
            Candidate(
                id="plan.replan.apply",
                action_key="plan.replan.apply",
                title="降低计划复杂度并重排",
                reason=f"计划完成率 {round(adherence * 100)}%，先减复杂度",
                evidence=["plan_adherence_7d"],
            )
        )

    if "motion_decline" in signal_codes or state.value("motion_quality_trend") is not None:
        trend = state.value("motion_quality_trend")
        if trend is not None and trend.value is not None and trend.value < 0:
            candidates.append(
                Candidate(
                    id="experiment.start",
                    action_key="experiment.start",
                    title="用两周微实验验证一个变量",
                    reason="同动作同版本评分下降，用单变量实验替代盲目加量",
                    evidence=["motion_quality_trend"],
                )
            )

    if "weight_change" in signal_codes:
        candidates.append(
            Candidate(
                id="goal.adjustment.apply",
                action_key="goal.adjustment.apply",
                title="确认目标调整建议",
                reason="体重变化超过规则阈值，建议按观察值微调目标",
                evidence=["weight_kg_latest"],
            )
        )

    return candidates


def filter_candidates(
    db: Session,
    user_id: int,
    candidates: list[Candidate],
    state: HealthStateSnapshot,
) -> tuple[list[Candidate], list[dict]]:
    """Remove candidates with a named reason; every removal is reported."""
    graph = capability_graph(db, user_id=user_id)
    denied = _denied_constraints(state)
    active = set(state.active_actions)
    allowed: list[Candidate] = []
    filtered: list[dict] = []

    for candidate in candidates:
        if candidate.action_key is not None:
            if candidate.action_key not in ACTION_REGISTRY:
                filtered.append(
                    {
                        "id": candidate.id,
                        "reason": "action_not_registered",
                        "detail": f"{candidate.action_key} 不在 Action Registry 中",
                    }
                )
                continue
            if f"proposal:{candidate.action_key}" in active:
                filtered.append(
                    {
                        "id": candidate.id,
                        "reason": "already_active",
                        "detail": f"{candidate.action_key} 已有待确认提案",
                    }
                )
                continue
            if candidate.action_key == "experiment.start" and any(
                item.startswith("experiment:") for item in active
            ):
                filtered.append(
                    {
                        "id": candidate.id,
                        "reason": "experiment_active",
                        "detail": "已有进行中的微实验，一次只验证一个变量",
                    }
                )
                continue
            if candidate.action_key == "plan.replan.apply" and "experiment_active" in denied:
                filtered.append(
                    {
                        "id": candidate.id,
                        "reason": "constraint_conflict",
                        "detail": "微实验进行中，避免同时改动计划",
                    }
                )
                continue

        if candidate.id == "plan.apply" and "plan.solve" in {
            key for key, value in graph.items() if not value.available
        }:
            filtered.append(
                {
                    "id": candidate.id,
                    "reason": "capability_unavailable",
                    "detail": graph["plan.solve"].reason,
                }
            )
            continue

        if candidate.id == "seek_care":
            filtered.append(
                {
                    "id": candidate.id,
                    "reason": "human_decision",
                    "detail": "求助属于用户与专业人员决定，系统不自动执行也不排序推进",
                }
            )
            continue

        allowed.append(candidate)

    return allowed, filtered


def _score(
    db: Session,
    user_id: int,
    candidate: Candidate,
    state: HealthStateSnapshot,
    signals: list[dict],
) -> Ranked:
    policy = _policy_lookup(db, user_id)
    family = candidate.action_key or candidate.id
    stats = policy.get(family, {})
    preferred_variant = _preference(db, user_id, "plan_variant")

    impact = 0.0
    urgency = 0.0
    plan_fit = 0.0
    evidence = list(candidate.evidence)

    for code in {item.get("code") for item in signals}:
        if code in {"sleep_debt", "motion_decline", "weight_change"}:
            urgency += 0.5
        if code == "record_gap":
            urgency += 0.3

    if candidate.id in {"plan.apply", "plan.replan.apply"}:
        impact = 0.8
        adherence = state.numeric("plan_adherence_7d")
        if adherence is not None and adherence < 0.5:
            impact += 0.3
        if preferred_variant == "gentle":
            plan_fit = 1.0
        else:
            plan_fit = 0.5
    elif candidate.id == "recovery_action":
        impact = 0.9
        urgency += 0.4
    elif candidate.id == "experiment.start":
        impact = 0.6
        plan_fit = 0.7
    elif candidate.id == "goal.adjustment.apply":
        impact = 0.5
        plan_fit = 0.6
    elif candidate.id == "review_records":
        impact = 0.4
        urgency += 0.4

    # Personal history only adjusts the *ordering of already-safe options*.
    efficacy = 0.5
    if stats:
        preference = stats.get("preference_score")
        completion = stats.get("completion_score")
        samples = stats.get("offered") or 0
        if isinstance(preference, (int, float)) and samples >= 3:
            efficacy = float(preference)
        if isinstance(completion, (int, float)):
            efficacy = (efficacy + float(completion)) / 2
    negative = 1.0 if (stats.get("inaccurate") or 0) > (stats.get("helpful") or 0) else 0.0

    effort = EFFORT_BY_CANDIDATE.get(candidate.id, 0.5)
    breakdown = {
        "expected_impact": round(impact, 4),
        "efficacy_from_history": round(efficacy, 4),
        "urgency": round(min(1.0, urgency), 4),
        "plan_fit": round(plan_fit, 4),
        "effort": round(effort, 4),
        "negative_feedback": negative,
    }
    score = sum(WEIGHTS[key] * value for key, value in breakdown.items())

    enriched = Candidate(
        id=candidate.id,
        action_key=candidate.action_key,
        title=candidate.title,
        reason=candidate.reason,
        evidence=evidence,
        requires_user_confirmation=candidate.requires_user_confirmation,
    )
    return Ranked(candidate=enriched, score=round(score, 4), breakdown=breakdown)


def decide(
    db: Session,
    user_id: int,
    *,
    window_days: int = 7,
    signals: list[dict] | None = None,
) -> DecisionContract:
    """Compute the Decision Contract: candidates, ranking, NBA and alternatives."""
    state = _state(db, user_id, window_days)
    signal_list = signals or []
    candidates = build_candidates(db, user_id, state, signal_list)
    allowed, filtered = filter_candidates(db, user_id, candidates, state)
    ranked = [
        _score(db, user_id, candidate, state, signal_list) for candidate in allowed
    ]
    ranked.sort(key=lambda item: (-item.score, item.candidate.id))

    automatic = [item for item in ranked if item.candidate.id not in NEVER_AUTO]
    nba = automatic[0] if automatic else None
    alternatives = [item for item in automatic if item is not nba][:3]

    graph = capability_graph(db, user_id=user_id)
    from app.harness.plugins import capability_scope_granted
    if capability_scope_granted(db, user_id, "plan_outcome", "user.preferences.read"):
        from app.services.agent.outcome import memory_view
        memory = memory_view(db, user_id)
    else:
        memory = {"entries": [], "ignored": []}
    return DecisionContract(
        as_of=state.as_of.isoformat() + "Z",
        state_snapshot_hash=state.snapshot_hash,
        capabilities_available=sorted(
            key for key, value in graph.items() if value.available
        ),
        candidates=ranked,
        filtered=filtered,
        next_best_action=nba,
        alternatives=alternatives,
        memory_used=[
            {
                "key": item["key"],
                "value": item["value"],
                "source": item["source"],
                "evidence_count": item["evidence_count"],
                "may_steer": item["may_steer"],
            }
            for item in memory["entries"]
            if item["influential"]
        ],
        memory_ignored=memory["ignored"],
    )


def next_best_action(db: Session, user_id: int, *, window_days: int = 7) -> dict:
    """NBA plus a plain-language answer to "why this one"."""
    contract = decide(db, user_id, window_days=window_days)
    payload = contract.as_dict()
    nba = payload["next_best_action"]
    if nba is None:
        payload["explanation"] = (
            "当前没有可自动提议的行动：要么缺少记录，要么存在硬约束。"
            + (
                "已列出被过滤的原因。"
                if payload["filtered"]
                else "请先补齐记录或由你主动指定目标。"
            )
        )
    else:
        payload["explanation"] = (
            f"建议先做「{nba['title']}」，依据：{nba['reason']}。"
            f"其余 {len(payload['alternatives'])} 个候选已按同一套权重排序并列出。"
        )
    return payload


__all__ = [
    "ACTION_CANDIDATES",
    "Candidate",
    "DecisionContract",
    "EFFORT_BY_CANDIDATE",
    "NEVER_AUTO",
    "Ranked",
    "WEIGHTS",
    "build_candidates",
    "decide",
    "filter_candidates",
    "next_best_action",
]
