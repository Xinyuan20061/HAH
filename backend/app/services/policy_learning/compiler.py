from __future__ import annotations

from datetime import timedelta
import hashlib
import json
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import PersonalStrategyUnit
from app.services.health_state import build_snapshot

from .algorithm import Scope, Protocol
from .templates import get_template, template_hash


def _context(parameters: dict, snapshot) -> tuple[dict, str]:
    # Only explicit values become context. Missing values remain unknown.
    context = {
        "time_budget": parameters.get("time_budget", "unknown"),
        "recovery": parameters.get("recovery", "unknown"),
        "schedule": parameters.get("schedule", "unknown"),
    }
    for key, value in context.items():
        if value not in {"tight", "open", "normal", "constrained", "workday", "restday", "unknown"}:
            raise ValueError(f"INVALID_CONTEXT_{key.upper()}")
    material = json.dumps(context, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return context, hashlib.sha256(material.encode()).hexdigest()


def compile_strategy(db: Session, user_id: int, template_id: str, template_version: str | None = None, parameters: dict | None = None, goal_key: str = "make_plan_sustainable") -> dict:
    parameters = parameters or {}
    template = get_template(template_id, template_version)
    snapshot = build_snapshot(db, user_id, window_days=7, persist=False)
    context, context_key = _context(parameters, snapshot)
    scope = Scope(user_id, f"{template_id}:{parameters.get('variant', 'default')}", template["template_version"], template["metric_version"], context_key)
    protocol = Protocol(scope, expected_days=template["expected_days"], minimum_days=template["minimum_days"], minimum_coverage=template["minimum_coverage"], execution_target=template["execution_target"], mode=template["mode"], direction=template["direction"], target=template["target"], ambiguity_band=template["ambiguity_band"], changed_variable=template["changed_variable"], aggregation=template.get("aggregation", "paired_median"))
    missing: list[str] = []
    if not parameters.get("variant"):
        missing.append("parameters.variant")
    if snapshot.hard_constraints():
        missing.append("hard_constraints_review")
    # The registry determines the measurement; do not infer that an unavailable
    # source is usable merely because a model can describe it.
    strategy_id = scope.strategy_id
    protocol_dict = {
        "scope": scope.__dict__, "goal_key": goal_key, "template": template,
        "parameters": parameters, "expected_days": protocol.expected_days,
        "minimum_days": protocol.minimum_days, "minimum_coverage": protocol.minimum_coverage,
        "execution_target": protocol.execution_target, "mode": protocol.mode,
        "direction": protocol.direction, "target": protocol.target,
        "ambiguity_band": protocol.ambiguity_band, "changed_variable": protocol.changed_variable,
        "gate_version": "evidence-gate-v1.0.0",
        "aggregation": protocol.aggregation,
    }
    protocol_hash = hashlib.sha256(json.dumps(protocol_dict, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    unit_id = uuid4().hex
    row = PersonalStrategyUnit(
        id=unit_id, user_id=user_id, template_id=template_id,
        template_version=template["template_version"], strategy_id=strategy_id,
        protocol_version=template["template_version"], metric_version=template["metric_version"],
        context_schema_version="context-v1", context_key=context_key,
        protocol_json=json.dumps(protocol_dict, ensure_ascii=False, sort_keys=True, default=str),
        context_json=json.dumps(context, ensure_ascii=False, sort_keys=True),
        state_snapshot_hash=snapshot.snapshot_hash, protocol_hash=protocol_hash,
        baseline_refs_json="[]", status="needs_information" if missing else "compiled",
    )
    db.add(row)
    db.flush()
    return {"compiled": not missing, "strategy_unit_id": unit_id, "strategy_id": strategy_id, "template_id": template_id, "template_version": template["template_version"], "protocol_version": template["template_version"], "metric_version": template["metric_version"], "context": context, "context_key": context_key, "protocol_hash": protocol_hash, "state_snapshot_hash": snapshot.snapshot_hash, "missing": missing, "hard_constraints": [item.model_dump() for item in snapshot.hard_constraints()], "protocol": protocol_dict, "template_hash": template_hash(template)}
