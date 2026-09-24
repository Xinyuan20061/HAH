import hmac
from fastapi import Header, HTTPException

from app.core.config import settings


def require_worker_token(
    x_worker_token: str | None = Header(default=None, alias="X-Worker-Token"),
):
    configured = settings.worker_token.strip()
    if not configured:
        raise HTTPException(503, "本地 AI Worker 尚未配置")
    supplied = (x_worker_token or "").strip()
    if not supplied or not hmac.compare_digest(configured, supplied):
        raise HTTPException(401, "AI Worker 凭证无效")
    return True
