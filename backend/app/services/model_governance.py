from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ModelEvaluation, ModelRegistry


def _json(value: str, fallback):
    try:
        parsed = json.loads(value or "")
        return parsed if isinstance(parsed, type(fallback)) else fallback
    except (TypeError, ValueError):
        return fallback


def model_readiness(db: Session) -> dict:
    rows = db.scalars(
        select(ModelRegistry)
        .where(ModelRegistry.active.is_(True))
        .order_by(ModelRegistry.task, ModelRegistry.model_key, ModelRegistry.version)
    ).all()
    evaluation_counts = dict(
        db.execute(
            select(ModelEvaluation.model_registry_id, func.count(ModelEvaluation.id)).group_by(
                ModelEvaluation.model_registry_id
            )
        ).all()
    )
    items = [
        {
            "id": row.id,
            "model_key": row.model_key,
            "version": row.version,
            "task": row.task,
            "implementation_type": row.implementation_type,
            "release_status": row.release_status,
            "training_dataset_keys": _json(row.training_dataset_keys_json, []),
            "label_schema_version": row.label_schema_version,
            "metrics": _json(row.metrics_json, {}),
            "thresholds": _json(row.thresholds_json, {}),
            "claims_scope": row.claims_scope,
            "evaluation_runs": int(evaluation_counts.get(row.id, 0)),
        }
        for row in rows
    ]
    trained = [
        item
        for item in items
        if item["implementation_type"] == "trained"
        and item["release_status"] in {"validated", "production"}
    ]
    return {
        "items": items,
        "trained_model_available": bool(trained),
        "formal_benchmark_available": any(item["evaluation_runs"] > 0 for item in items),
        "current_stage": "engineering_baseline" if not trained else "validated_model",
        "notice": "模型登记不等于效果验证；正式声明必须关联固定测试集、报告哈希和样本量。",
    }
