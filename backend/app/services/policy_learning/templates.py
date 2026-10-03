"""Server-owned, reviewed policy and metric registry."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json

TEMPLATE_REGISTRY = {
    "session_duration": {
        "template_version": "1.0.0", "metric_key": "burden", "metric_version": "burden-v1",
        "changed_variable": "session_minutes", "mode": "delta", "direction": "decrease",
        "target": -1.0, "ambiguity_band": 0.1, "expected_days": 7, "minimum_days": 5,
        "minimum_coverage": 0.7, "execution_target": 0.7, "aggregation": "paired_median",
    },
    "record_complexity": {
        "template_version": "1.0.0", "metric_key": "record_burden", "metric_version": "record-burden-v1",
        "changed_variable": "record_item_count", "mode": "delta", "direction": "decrease",
        "target": -1.0, "ambiguity_band": 0.1, "expected_days": 7, "minimum_days": 5,
        "minimum_coverage": 0.7, "execution_target": 0.7, "aggregation": "paired_median",
    },
    "task_timing": {
        "template_version": "1.0.0", "metric_key": "completion_fraction", "metric_version": "completion-v1",
        "changed_variable": "scheduled_time", "mode": "absolute", "direction": "increase",
        "target": 0.7, "ambiguity_band": 0.05, "expected_days": 7, "minimum_days": 5,
        "minimum_coverage": 0.7, "execution_target": 0.7, "aggregation": "fraction",
    },
}


def get_template(template_id: str, version: str | None = None) -> dict:
    item = TEMPLATE_REGISTRY.get(template_id)
    if item is None or version is not None and item["template_version"] != version:
        raise KeyError("POLICY_TEMPLATE_NOT_FOUND")
    return {"template_id": template_id, **item}


def template_hash(template: dict) -> str:
    return hashlib.sha256(json.dumps(template, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
