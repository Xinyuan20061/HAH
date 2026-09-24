from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4
import json
import logging
import re
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from app.core.config import settings
from app.core.database import engine
from app.core.errors import register_exception_handlers
from app.api.v1.router import api_router
from app.core.rate_limit import limiter

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("healthmate.request")
# httpx logs full URLs, including WeChat AppSecret and signed media query strings.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_configuration()
    logger.info("startup configuration=%s", json.dumps(settings.safe_summary()))
    yield
    engine.dispose()


app = FastAPI(title=settings.app_name, version="1.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials="*" not in settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    supplied = request.headers.get(settings.request_id_header, "")
    rid = supplied if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", supplied) else uuid4().hex
    request.state.request_id = rid
    started = time.perf_counter()
    retry_after = limiter.check(request)
    if retry_after is not None:
        response = JSONResponse(
            status_code=429,
            content={"detail": "请求过于频繁，请稍后重试", "request_id": rid},
            headers={"Retry-After": str(retry_after)},
        )
        response.headers[settings.request_id_header] = rid
        return response
    try:
        response = await call_next(request)
    finally:
        logger.info(
            "request id=%s method=%s path=%s ms=%.1f",
            rid,
            request.method,
            request.url.path,
            (time.perf_counter() - started) * 1000,
        )
    response.headers[settings.request_id_header] = rid
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    return response


register_exception_handlers(app)
if settings.use_local_storage and not settings.is_production:
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=settings.upload_dir), name="uploads")
app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/health/live")
def live():
    return {"status": "ok", "service": "healthmate-api"}


@app.get("/health")
@app.get("/health/ready")
def ready():
    """Database connectivity AND migration head are required; laptop/LLM are optional."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from alembic.runtime.migration import MigrationContext

    ok = False
    reason = "database_unavailable"
    try:
        config = Config()
        config.set_main_option(
            "script_location", str(Path(__file__).resolve().parents[1] / "migrations")
        )
        heads = set(ScriptDirectory.from_config(config).get_heads())
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            ok = (
                set(MigrationContext.configure(connection).get_current_heads()) == heads
            )
        reason = "connected_at_head" if ok else "migration_required"
    except Exception:
        pass  # Never expose SQL, connection URL, or driver exception text.
    return JSONResponse(
        status_code=200 if ok else 503,
        content={
            "status": "ok" if ok else "degraded",
            "env": settings.env,
            "database_backend": engine.dialect.name,
            "storage_backend": settings.storage_backend,
            "checks": [
                {"name": "database", "ok": ok, "required": True, "detail": reason}
            ],
        },
    )
