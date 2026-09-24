from __future__ import annotations
from app.core.time import utc_now, utc_iso

import shutil
import time
from datetime import datetime, timedelta

import httpx
from sqlalchemy import text, select

from app.core.database import engine, SessionLocal
from app.core.config import settings
from app.models import AIWorkerNode


def _check(name, fn, required=True):
    t = time.perf_counter()
    try:
        detail = fn()
        return {
            "name": name,
            "ok": True,
            "required": required,
            "latency_ms": round((time.perf_counter() - t) * 1000, 1),
            "detail": detail,
        }
    except Exception:
        return {
            "name": name,
            "ok": False,
            "required": required,
            "latency_ms": round((time.perf_counter() - t) * 1000, 1),
            "detail": "dependency_unavailable",
        }


def _worker_detail():
    if not settings.worker_enabled:
        return "worker token not configured"
    db = SessionLocal()
    try:
        threshold = utc_now() - timedelta(seconds=settings.worker_offline_after_seconds)
        online = db.scalars(
            select(AIWorkerNode).where(AIWorkerNode.last_seen_at >= threshold)
        ).all()
        if not online:
            raise RuntimeError("no local AI worker heartbeat")
        return ", ".join(f"{x.worker_id} ({x.gpu_name or 'CPU'})" for x in online[:5])
    finally:
        db.close()


def diagnostics(include_ai=True):
    checks = []

    def database_check():
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return engine.dialect.name + " connected"

    checks.append(_check("database", database_check, required=True))
    checks.append(_check("local_ai_worker", _worker_detail, required=False))
    checks.append(
        {
            "name": "cloud_media",
            "ok": settings.storage_backend.lower() in {"cloud_ref", "s3", "local"},
            "required": False,
            "latency_ms": 0,
            "detail": settings.storage_backend,
        }
    )
    checks.append(
        {
            "name": "local_ffmpeg",
            "ok": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
            "required": False,
            "latency_ms": 0,
            "detail": "development fallback only",
        }
    )

    def ai_check():
        if not settings.deepseek_api_key:
            return "no server key configured (user key may still be used)"
        with httpx.Client(timeout=settings.diagnostics_timeout_seconds) as c:
            r = c.get(
                settings.deepseek_base_url.rstrip("/") + "/models",
                headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
            )
            if not r.is_success:
                raise RuntimeError(f"DeepSeek HTTP {r.status_code}")
            return f"HTTP {r.status_code}"

    if include_ai:
        checks.append(_check("deepseek", ai_check, required=False))
    return checks
