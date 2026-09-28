from app.core.time import utc_now, utc_iso
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.config import settings
from app.core.database import get_db
from app.services.ai_jobs import requeue_expired_jobs
from app.core.diagnostics import diagnostics
from app.models import AIWorkerNode, AIJob

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/diagnostics")
def get_diagnostics(user=Depends(current_user)):
    checks = diagnostics(include_ai=True)
    required = [x for x in checks if x.get("required")]
    return {
        "status": "ok" if all(x["ok"] for x in required) else "degraded",
        "env": settings.env,
        "checks": checks,
    }


@router.get("/ai-worker")
def ai_worker_status(user=Depends(current_user), db: Session = Depends(get_db)):
    requeue_expired_jobs(db)
    threshold = utc_now() - timedelta(seconds=settings.worker_offline_after_seconds)
    nodes = db.scalars(
        select(AIWorkerNode).order_by(AIWorkerNode.last_seen_at.desc()).limit(10)
    ).all()
    queue_depth = (
        db.query(AIJob)
        .filter(AIJob.status == "queued", AIJob.user_id == user.id)
        .count()
    )
    processing = (
        db.query(AIJob)
        .filter(AIJob.status == "processing", AIJob.user_id == user.id)
        .count()
    )
    public_nodes = []
    for node in nodes:
        online = node.last_seen_at >= threshold
        try:
            caps = json.loads(node.capabilities_json or "[]")
        except Exception:
            caps = []
        try:
            metadata = json.loads(node.metadata_json or "{}")
        except Exception:
            metadata = {}
        raw_semantic = metadata.get("semantic_model", {}) if isinstance(metadata, dict) else {}
        semantic_model = {
            "configured": raw_semantic.get("configured") is True,
            "available": raw_semantic.get("available") is True,
            "mode": str(raw_semantic.get("mode") or "rule_fallback")[:40],
            "model_key": str(raw_semantic.get("model_key") or "")[:100],
            "version": str(raw_semantic.get("version") or "")[:40],
        }
        public_nodes.append(
            {
                "worker_id": node.worker_id,
                "name": node.name,
                "version": node.version,
                "gpu_name": node.gpu_name,
                "capabilities": caps,
                "semantic_model": semantic_model,
                "online": online,
                "last_seen_at": utc_iso(node.last_seen_at),
            }
        )
    return {
        "enabled": settings.worker_enabled,
        "online": any(x["online"] for x in public_nodes),
        "motion_online": any(
            x["online"] and "motion_pose" in x["capabilities"] for x in public_nodes
        ),
        "food_online": any(
            x["online"] and "food_vision" in x["capabilities"] for x in public_nodes
        ),
        "kinetics_online": any(
            x["online"] and "kinetics400" in x["capabilities"] for x in public_nodes
        ),
        "queue_depth": queue_depth,
        "processing": processing,
        "nodes": public_nodes,
    }
