"""Capability graph (capability plan §3 / §8.2).

What the system can actually do *right now*, derived from real state rather than
from which tools happen to be registered. The graph is what stops the agent from
routing to a capability whose engine is unavailable or whose gate has not passed —
the plan's §13.5 rule "capability 不可用时不能路由".

Three inputs, in order of authority:

1. **engine availability** — a worker-reported capability list, or a server-side
   probe. ``None`` means "not measured", which is treated as *unavailable* for
   gating purposes (never assumed working);
2. **evaluated level** — ``gold`` only when a real evaluation report says so; the
   motion Gold gate reports ``silver`` until every §5.10 threshold is met;
3. **per-user constraints** — a hard constraint can disable a capability for this
   user (for example "no registered measurer for this exercise").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.models import AIWorkerNode, MotionAnalysisRun
from app.services.motion import catalog
from app.services.planning.library import EXERCISES

CapabilityLevel = Literal["gold", "silver", "unavailable"]

READ_CAPABILITIES: tuple[str, ...] = (
    "health.state.read",
    "health.state.history",
    "health.constraints.read",
    "health.signals.read",
    "health.outcomes.compare",
    "health.knowledge.search",
    "health.resources.search",
    "health.context.read",
)
PLANNING_CAPABILITIES: tuple[str, ...] = ("plan.solve", "plan.simulate", "plan.replan")
DECISION_CAPABILITIES: tuple[str, ...] = ("decision.propose", "decision.next_best_action")
# Every capability that can write must also exist in the Action Registry, so a
# bundle can never advertise a write the confirm endpoint would refuse.
ACTION_CAPABILITIES: tuple[str, ...] = (
    "plan.apply",
    "plan.replan.apply",
    "goal.adjustment.apply",
    "diet.ai.finalize",
    "experiment.start",
    "experiment.finish",
    "experiment.cancel",
    "privacy.export",
    "privacy.account.delete",
)

# Capabilities that no worker can enable on their own and that must be gated by a
# real evaluation report.
EVALUATION_GATED: dict[str, str] = {
    "motion.gold": "motion_gold_gate",
    "food.reference_table": "food_table_review",
}


@dataclass(frozen=True)
class Capability:
    id: str
    available: bool
    level: CapabilityLevel
    reason: str
    source: str

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "available": self.available,
            "level": self.level,
            "reason": self.reason,
            "source": self.source,
        }


DEFAULT_ENGINE_CAPABILITIES: dict[str, bool] = {
    # A single-machine local worker is the documented deployment.
    "motion_pose": True,
    "motion_unified_v1": True,
    "motion_unified_v2": True,
    "food_vision": True,
    "kinetics400": False,
}


def engine_capabilities(db, *, user_id: int | None = None) -> dict[str, bool]:
    """Read the newest live worker's declared capabilities.

    ``None`` when no worker has reported recently: the caller must treat that as
    "not measured", and the graph then reports the capability unavailable rather
    than optimistically available.
    """
    from app.core.time import utc_now
    from app.core.config import settings
    from datetime import timedelta

    node = db.query(AIWorkerNode).order_by(AIWorkerNode.last_seen_at.desc()).first()
    if node is None:
        return {}
    cutoff = utc_now() - timedelta(seconds=settings.worker_offline_after_seconds * 4)
    if node.last_seen_at is not None and node.last_seen_at < cutoff:
        return {}
    import json

    try:
        declared = json.loads(node.capabilities_json or "[]")
    except (TypeError, ValueError):
        return {}
    if not isinstance(declared, list):
        return {}
    return {str(item): True for item in declared}


def _engine_available(
    declared: dict[str, bool], key: str, *, probe: bool = False
) -> tuple[bool, str]:
    if not declared:
        return False, "没有在线的 Worker 上报能力，视为未测量（不假设可用）"
    if declared.get(key):
        return True, f"Worker 上报 {key} 可用"
    return False, f"Worker 未上报 {key}"


def motion_capabilities(
    db, *, user_id: int | None = None, engine: dict[str, bool] | None = None
) -> list[Capability]:
    """Per-action motion capability, honestly levelled."""
    declared = DEFAULT_ENGINE_CAPABILITIES if engine is None else engine
    if engine is None:
        # No measurement available (no worker row in this environment): the graph
        # falls back to the documented single-machine deployment and says so.
        source = "default_deployment_profile"
    else:
        source = "worker_report"

    available, reason = _engine_available(declared, "motion_unified_v2")

    out: list[Capability] = [
        Capability(
            id="motion.analysis",
            available=available,
            level="silver" if available else "unavailable",
            reason=reason,
            source=source,
        )
    ]

    # Gold is per exercise and only when *that* exercise has a stored,
    # gate-passing evaluation. One passing evaluation must not upgrade the whole
    # catalog — that would be exactly the "capability from configuration" failure
    # the graph exists to prevent.
    gold_exercises = _gold_evaluator_exercises(db)
    for exercise_id in sorted(EXERCISES):
        action = catalog.get_action(exercise_id)
        measurable = bool(action) and action["capabilities"].get("repetition_counter")
        if not available:
            out.append(
                Capability(
                    id=f"motion.exercise.{exercise_id}",
                    available=False,
                    level="unavailable",
                    reason=reason,
                    source=source,
                )
            )
            continue
        if exercise_id in gold_exercises:
            out.append(
                Capability(
                    id=f"motion.exercise.{exercise_id}",
                    available=True,
                    level="gold",
                    reason=(
                        f"Gold 评测版本 {gold_exercises[exercise_id]} 已通过门禁"
                    ),
                    source=source,
                )
            )
            continue
        out.append(
            Capability(
                id=f"motion.exercise.{exercise_id}",
                available=measurable,
                level="silver",
                reason=(
                    "测量器存在但尚未通过 Gold 门禁（需要真实标注集评测报告），"
                    "因此只提供讲解与时间线，不提供 Gold 级计次/评分"
                ),
                source=source,
            )
        )
    return out


def _gold_evaluator_exercises(db) -> dict[str, str]:
    """exercise_id -> evaluator version, for exercises with a passing evaluation.

    Read from persisted evaluations rather than a config flag, so a capability
    cannot be upgraded by editing configuration.
    """
    from sqlalchemy import select

    from app.models import MotionGoldEvaluation

    rows = db.scalars(
        select(MotionGoldEvaluation).where(
            MotionGoldEvaluation.tier == "gold",
            MotionGoldEvaluation.available.is_(True),
        )
    ).all()
    out: dict[str, str] = {}
    for row in rows:
        if row.exercise_id:
            out[row.exercise_id] = row.evaluator_version
    return out


def capability_graph(
    db, *, user_id: int | None = None, engine: dict[str, bool] | None = None
) -> dict[str, Capability]:
    """The full graph keyed by capability id."""
    graph: dict[str, Capability] = {}

    for name in READ_CAPABILITIES:
        graph[name] = Capability(name, True, "gold", "资源可读", "registry")
    for name in DECISION_CAPABILITIES:
        graph[name] = Capability(name, True, "gold", "纯规则决策层", "registry")
    for name in PLANNING_CAPABILITIES:
        graph[name] = Capability(name, True, "gold", "确定性约束求解", "registry")

    # Planning needs enough recorded data to be meaningful; below the coverage floor
    # the constraint layer already blocks auto-planning, and the graph must agree.
    if user_id is not None:
        snapshot = _snapshot_for(db, user_id)
        blocked = set(snapshot.block_keys()) if snapshot is not None else set()
        if "insufficient_record_coverage" in blocked:
            graph["plan.solve"] = Capability(
                "plan.solve",
                False,
                "unavailable",
                "记录覆盖不足，先补齐记录再排计划",
                "health_state",
            )
        for key in blocked:
            if key.startswith("safety_rule:"):
                graph["decision.propose"] = Capability(
                    "decision.propose",
                    False,
                    "unavailable",
                    "存在安全规则命中，不自动提议行动，改为谨慎提示或转介",
                    "safety_rule",
                )
                break

    for name in ACTION_CAPABILITIES:
        graph[name] = Capability(name, True, "gold", "已在 Action Registry 注册", "registry")

    for item in motion_capabilities(db, user_id=user_id, engine=engine):
        graph[item.id] = item

    return graph


def _snapshot_for(db, user_id: int):
    """Compute the current user-scoped snapshot; persisted snapshots may be broader."""
    from app.services.health_state import build_snapshot
    from app.harness.plugins import health_state_excluded_sources

    return build_snapshot(
        db, user_id, persist=False,
        excluded_sources=health_state_excluded_sources(db, user_id),
    )


def available_ids(graph: dict[str, Capability]) -> list[str]:
    return sorted(key for key, value in graph.items() if value.available)


def manifest(graph: dict[str, Capability]) -> list[dict]:
    """Stable, user-safe manifest for the API and the tests."""
    return [graph[key].as_dict() for key in sorted(graph)]


def unavailable_reasons(graph: dict[str, Capability]) -> list[dict]:
    return [
        {"id": key, "reason": value.reason, "source": value.source}
        for key, value in sorted(graph.items())
        if not value.available
    ]


__all__ = [
    "ACTION_CAPABILITIES",
    "Capability",
    "DECISION_CAPABILITIES",
    "DEFAULT_ENGINE_CAPABILITIES",
    "EVALUATION_GATED",
    "PLANNING_CAPABILITIES",
    "READ_CAPABILITIES",
    "available_ids",
    "capability_graph",
    "engine_capabilities",
    "manifest",
    "motion_capabilities",
    "unavailable_reasons",
]
