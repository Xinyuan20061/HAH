import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.core.database import get_db
from app.core.security import create_access_token
from app.core.config import settings
from app.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


class DevLogin(BaseModel):
    nickname: str = Field(default="", max_length=64)


class WechatLogin(BaseModel):
    code: str = Field(min_length=1, max_length=256)


def upsert(db, openid, nickname=""):
    user = db.scalar(select(User).where(User.openid == openid))
    if not user:
        user = User(openid=openid, nickname=nickname)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def token_response(user):
    return {
        "access_token": create_access_token(str(user.id)),
        "token_type": "bearer",
        "user": {"id": user.id, "nickname": user.nickname},
    }


@router.post("/dev-login")
def dev_login(body: DevLogin, db: Session = Depends(get_db)):
    if settings.is_production:
        raise HTTPException(404, "Not found")
    return token_response(upsert(db, "dev-user", body.nickname))


@router.post("/cloud-login")
def cloud_login(request: Request, db: Session = Depends(get_db)):
    """Optional identity-header login for private-only Cloud Run deployments.

    The competition topology exposes HTTPS ingress for the laptop worker, so this route is
    disabled by default. The mini program uses wx.login -> /auth/wechat instead.
    """
    if not settings.cloud_header_login_enabled:
        raise HTTPException(404, "Not found")
    openid = (request.headers.get("x-wx-openid") or "").strip()
    appid = (request.headers.get("x-wx-appid") or "").strip()
    source = (request.headers.get("x-wx-source") or "").strip()
    if not openid or source not in {"wx_client", "wx_devtools"}:
        raise HTTPException(401, "未检测到可信微信云托管用户身份")
    if settings.wechat_app_id and appid and appid != settings.wechat_app_id:
        raise HTTPException(403, "微信 AppID 不匹配")
    return token_response(upsert(db, openid))


@router.post("/wechat")
async def wechat_login(body: WechatLogin, db: Session = Depends(get_db)):
    """Compatibility login for HTTP development or non-CloudRun deployment."""
    if not settings.wechat_app_id or not settings.wechat_app_secret:
        raise HTTPException(503, "Wechat login is not configured")
    url = "https://api.weixin.qq.com/sns/jscode2session"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as c:
            r = await c.get(
                url,
                params={
                    "appid": settings.wechat_app_id,
                    "secret": settings.wechat_app_secret,
                    "js_code": body.code,
                    "grant_type": "authorization_code",
                },
            )
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, "微信登录服务暂时不可用，请重试")
    if "openid" not in data:
        raise HTTPException(400, data.get("errmsg", "Wechat login failed"))
    return token_response(upsert(db, data["openid"]))
