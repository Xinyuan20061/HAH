from fastapi import APIRouter
from . import (
    auth,
    users,
    records,
    health,
    chat,
    vision,
    ai_config,
    media,
    insights,
    timeline,
    system,
    agent,
    evaluation,
    privacy,
    safety,
    worker,
    resources,
    fitness,
    knowledge,
    harness,
)

api_router = APIRouter()
for r in [
    auth.router,
    users.router,
    records.router,
    health.router,
    chat.router,
    vision.router,
    ai_config.router,
    media.router,
    insights.router,
    timeline.router,
    system.router,
    agent.router,
    evaluation.router,
    privacy.router,
    safety.router,
    worker.router,
    resources.router,
    fitness.router,
    knowledge.router,
    harness.router,
    media.router,
    media.admin_router,  # F 包追加：/admin/motion-analyses/{id}/diagnostics
    media.worker_preview_router,  # F 包追加：/worker/jobs/{id}/preview-upload-urls
]:
    api_router.include_router(r)
