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
    health_state,
    agent_decision,
    capabilities,
    food,
    policy,
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
    # Health state engine: ``state_router`` owns /health/state/*, ``router`` owns
    # the sibling /health/signals and /health/outcomes paths (plan §4.4).
    health_state.state_router,
    health_state.router,
    # Decision Contract / capability graph / plan solver (plan §7.4/§8.2).
    agent_decision.router,
    agent_decision.plan_router,
    # Capability honesty surface + Gold tier status (plan §5.11/§13.6).
    capabilities.router,
    # Interactive Food 2.0: questions, deterministic calculation, priors (§6).
    food.router,
    policy.router,
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
