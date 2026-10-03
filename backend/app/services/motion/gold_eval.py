"""Gold evaluator runner and persistence (capability plan §5.3/§5.11).

This backend-side runner calls the worker's Gold package when it is importable and
persists the **gate result**. Two properties matter:

* the tier is computed by ``evaluate_gold_gate`` against the §5.10 thresholds, not
  asserted here;
* when the evaluator is absent (a backend-only deployment, or a venv without the
  worker package) the run is reported as ``unavailable`` with the reason — it never
  fabricates a Gold tier.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import MotionAnalysisRun, MotionGoldEvaluation

logger = logging.getLogger("healthmate.motion.gold")

GOLD_EVALUATOR_VERSION = "motion-gold-1.0.0"


def gold_package_available() -> tuple[bool, str]:
    """Whether the worker's Gold package can be imported from this process."""
    import importlib.util

    if importlib.util.find_spec("healthmate_worker") is None:
        return False, "worker 包不在当前解释器的导入路径中，无法运行 Gold 评测"
    try:
        importlib.import_module("healthmate_worker.processors.motion_gold")
    except Exception as exc:  # noqa: BLE001 - the reason is the finding
        return False, f"worker Gold 模块导入失败（{type(exc).__name__}）"
    return True, "worker Gold 模块可用"


def evaluate_run_gold(
    db: Session, run: MotionAnalysisRun, *, signal_windows: list | None = None
) -> MotionGoldEvaluation:
    """Produce and persist the Gold evaluation for one run.

    ``signal_windows`` carries the per-frame joint series; without it there is no
    measurement evidence, so the row records ``unavailable`` with a reason rather
    than an empty-but-Gold result.
    """
    available, reason = gold_package_available()
    exercise_id = run.requested_type or ""
    existing = db.scalar(
        select(MotionGoldEvaluation).where(
            MotionGoldEvaluation.run_id == run.id,
            MotionGoldEvaluation.exercise_id == exercise_id,
            MotionGoldEvaluation.evaluator_version == GOLD_EVALUATOR_VERSION,
        )
    )
    row = existing or MotionGoldEvaluation(
        run_id=run.id,
        user_id=run.user_id,
        exercise_id=exercise_id,
        evaluator_version=GOLD_EVALUATOR_VERSION,
        segments_json="[]",
        findings_json="[]",
        measurements_json="{}",
        gate_json="{}",
    )

    if not available:
        row.tier = "unavailable"
        row.available = False
        row.reason_unavailable = reason[:120]
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    from healthmate_worker.processors.motion_gold import (  # type: ignore[import-not-found]
        evaluate_counter,
        evaluate_gold_gate,
        evaluate_measurements,
        evaluate_open_set,
    )

    frames = signal_windows or []
    counter = evaluate_counter(exercise_id, frames)
    findings = evaluate_measurements(exercise_id, frames, counter_result=counter)

    # The gate needs real metrics. Until an evaluation report supplies them, the
    # gate returns silver and lists what is missing — by design.
    metrics = _metrics_from_report(db, exercise_id)
    gate = evaluate_gold_gate(metrics)

    row.tier = gate.tier
    row.available = gate.tier == "gold"
    row.reason_unavailable = (
        "" if row.available else (gate.reasons[0] if gate.reasons else "未通过 Gold 门禁")
    )[:120]
    row.reps = getattr(counter, "reps", None)
    row.hold_seconds = getattr(counter, "hold_seconds", None)
    row.segments_json = json.dumps(
        [item.model_dump(mode="json") for item in getattr(counter, "segments", [])],
        ensure_ascii=False,
        default=str,
    )
    row.findings_json = json.dumps(
        [item.model_dump(mode="json") for item in findings], ensure_ascii=False, default=str
    )
    row.measurements_json = json.dumps(
        getattr(counter, "as_dict", lambda: {})(), ensure_ascii=False, default=str
    )
    row.gate_json = json.dumps(
        gate.model_dump(mode="json"), ensure_ascii=False, default=str
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _metrics_from_report(db: Session, exercise_id: str) -> dict[str, float]:
    """Read §5.10 metrics from a stored evaluation report, if one exists.

    There is deliberately no hard-coded metric table: a metric the system has not
    measured must be *missing*, which makes the gate return silver.
    """
    from app.models import ModelEvaluation, ModelRegistry

    registry = db.scalar(
        select(ModelRegistry).where(
            ModelRegistry.model_key == f"motion_gold_{exercise_id}",
            ModelRegistry.active.is_(True),
        )
    )
    if registry is None:
        return {}
    evaluations = db.scalars(
        select(ModelEvaluation)
        .where(ModelEvaluation.model_registry_id == registry.id)
        .order_by(ModelEvaluation.evaluated_at.desc())
        .limit(1)
    ).all()
    if not evaluations:
        return {}
    try:
        payload = json.loads(evaluations[0].metrics_json or "{}")
    except (TypeError, ValueError):
        return {}
    return {
        key: float(value)
        for key, value in payload.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def gold_status(db: Session, user_id: int | None = None) -> dict[str, Any]:
    stmt = select(MotionGoldEvaluation)
    if user_id is not None:
        stmt = stmt.where(MotionGoldEvaluation.user_id == user_id)
    rows = db.scalars(stmt.order_by(MotionGoldEvaluation.created_at.desc()).limit(50)).all()
    available, reason = gold_package_available()
    gold = [row for row in rows if row.tier == "gold"]
    return {
        "evaluator_version": GOLD_EVALUATOR_VERSION,
        "worker_package": {"available": available, "reason": reason},
        "evaluations": len(rows),
        "gold_exercises": sorted({row.exercise_id for row in gold}),
        "tiers": {
            tier: len([row for row in rows if row.tier == tier])
            for tier in {row.tier for row in rows}
        },
        "policy": (
            "Gold 等级由 evaluate_gold_gate 按 §5.10 阈值计算；未提供指标即视为未通过，"
            "后端不会自行声明 Gold。"
        ),
        "checked_at": utc_now().isoformat() + "Z",
    }


def worker_source_available() -> bool:
    """Whether a worker checkout exists next to the backend (for diagnostics)."""
    return (Path(__file__).resolve().parents[3].parent / "ai-worker").is_dir()
