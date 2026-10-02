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
# Each router is included exactly once. A duplicate include silently produces
# duplicate OpenAPI operationIds and a second route entry for the same path
# (spec §9.1 API-01); tests/test_api_contract_governance.py guards this.
_routers = [
    auth.router,
    users.router,
    records.router,
    health.router,
    chat.router,
    vision.router,
    ai_config.router,
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
    # The media package owns four routers; include each exactly once.
    media.router,
    media.admin_router,  # /admin/motion-analyses/{id}/diagnostics
    media.worker_preview_router,  # /worker/jobs/{id}/preview-upload-urls
]

_seen: set[int] = set()
for _router in _routers:
    if id(_router) in _seen:  # pragma: no cover - a programming error, not input
        raise RuntimeError(f"API 路由重复注册: {_router.prefix or _router}")
    _seen.add(id(_router))
    api_router.include_router(_router)
