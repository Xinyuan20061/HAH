import hashlib
import base64
import json
import secrets
import time
from datetime import timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.api.deps import current_user
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.core.time import utc_iso, utc_now
from app.models import User, UserIdentity, UserIdentityLinkCode

router = APIRouter(prefix="/auth", tags=["auth"])


def _require_mobile_auth_enabled() -> None:
    if not settings.mobile_auth_enabled:
        raise HTTPException(503, "Android 微信登录与账号关联尚未启用")


class DevLogin(BaseModel):
    nickname: str = Field(default="", max_length=64)


class WechatLogin(BaseModel):
    code: str = Field(min_length=1, max_length=256)


class LinkComplete(BaseModel):
    link_code: str = Field(min_length=32, max_length=32, pattern=r"^[0-9A-Fa-f]{32}$")


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def create_cloudbase_custom_ticket(user_id: int, credentials_json: str, env_id: str) -> str:
    """Sign a short-lived CloudBase custom-login ticket using server-only credentials."""
    try:
        credentials = json.loads(credentials_json)
    except (TypeError, json.JSONDecodeError):
        raise HTTPException(503, "CloudBase 自定义登录凭据配置无效") from None
    if not isinstance(credentials, dict):
        raise HTTPException(503, "CloudBase 自定义登录凭据配置无效")
    credential_env = str(credentials.get("env_id") or "").strip()
    key_id = str(credentials.get("private_key_id") or "").strip()
    private_key = credentials.get("private_key")
    if credential_env != env_id.strip() or not key_id or not isinstance(private_key, str):
        raise HTTPException(503, "CloudBase 自定义登录凭据与当前环境不匹配")
    uid = f"hm_user_{int(user_id)}"
    try:
        signer = serialization.load_pem_private_key(private_key.encode("utf-8"), password=None)
    except (TypeError, ValueError):
        raise HTTPException(503, "CloudBase 自定义登录私钥无法读取") from None
    if not isinstance(signer, rsa.RSAPrivateKey):
        raise HTTPException(503, "CloudBase 自定义登录仅接受 RSA 私钥")

    now_ms = int(time.time() * 1000)
    header = _base64url(b'{"alg":"RS256","typ":"JWT"}')
    payload = _base64url(
        json.dumps(
            {
                "alg": "RS256",
                "env": env_id.strip(),
                "iat": now_ms,
                "exp": now_ms + 10 * 60 * 1000,
                "uid": uid,
                "refresh": 60 * 60 * 1000,
                "expire": now_ms + 7 * 24 * 60 * 60 * 1000,
            },
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = signer.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{key_id}/@@/{header}.{payload}.{_base64url(signature)}"


def _identity_user(
    db: Session,
    *,
    provider: str,
    issuer: str,
    subject: str,
    nickname: str = "",
    union_subject: str | None = None,
    legacy_openid: str | None = None,
) -> User:
    """Find or create a user through a verified, provider-scoped identity."""
    issuer = issuer.strip()[:191]
    subject = subject.strip()[:191]
    if not issuer or not subject:
        raise HTTPException(502, "微信登录未返回有效身份")

    identity = db.scalar(
        select(UserIdentity).where(
            UserIdentity.provider == provider,
            UserIdentity.issuer == issuer,
            UserIdentity.subject == subject,
        )
    )
    if identity:
        user = db.get(User, identity.user_id)
        if not user:
            raise HTTPException(409, "登录身份关联记录异常，请联系支持人员")
        if identity.union_subject and union_subject and identity.union_subject != union_subject:
            raise HTTPException(409, "微信身份信息不一致，请联系支持人员")
        if union_subject and not identity.union_subject:
            identity.union_subject = union_subject[:191]
            db.commit()
        return user

    user = None
    if legacy_openid:
        user = db.scalar(select(User).where(User.openid == legacy_openid))
        if user:
            legacy_identity = db.scalar(
                select(UserIdentity).where(
                    UserIdentity.user_id == user.id,
                    UserIdentity.provider == provider,
                    UserIdentity.issuer == "legacy",
                    UserIdentity.subject == subject,
                )
            )
            if legacy_identity:
                legacy_identity.issuer = issuer
                if union_subject:
                    legacy_identity.union_subject = union_subject[:191]
            else:
                db.add(
                    UserIdentity(
                        user_id=user.id,
                        provider=provider,
                        issuer=issuer,
                        subject=subject,
                        union_subject=union_subject[:191] if union_subject else None,
                    )
                )
    if not user:
        user = User(openid=legacy_openid, nickname=nickname[:64])
        db.add(user)
        db.flush()
        db.add(
            UserIdentity(
                user_id=user.id,
                provider=provider,
                issuer=issuer,
                subject=subject,
                union_subject=union_subject[:191] if union_subject else None,
            )
        )

    try:
        db.commit()
    except IntegrityError:
        # Simultaneous first logins may race on the provider identity constraint.
        db.rollback()
        identity = db.scalar(
            select(UserIdentity).where(
                UserIdentity.provider == provider,
                UserIdentity.issuer == issuer,
                UserIdentity.subject == subject,
            )
        )
        user = db.get(User, identity.user_id) if identity else None
        if not user:
            raise HTTPException(409, "登录身份正在创建，请重试")
    return user


def upsert(db, openid, nickname=""):
    """Compatibility helper for tests and existing mini-program callers."""
    return _identity_user(
        db,
        provider="wechat_miniprogram",
        issuer=settings.wechat_app_id.strip() or "legacy",
        subject=openid,
        nickname=nickname,
        legacy_openid=openid,
    )


def token_response(user):
    return {
        "access_token": create_access_token(str(user.id)),
        "token_type": "bearer",
        "user": {"id": user.id, "nickname": user.nickname},
    }


@router.get("/me")
def read_current_user(user=Depends(current_user), db: Session = Depends(get_db)):
    providers = db.scalars(
        select(UserIdentity.provider)
        .where(UserIdentity.user_id == user.id)
        .distinct()
        .order_by(UserIdentity.provider)
    ).all()
    return {"id": user.id, "nickname": user.nickname, "linked_providers": providers}


@router.post("/dev-login")
def dev_login(body: DevLogin, db: Session = Depends(get_db)):
    if settings.is_production:
        raise HTTPException(404, "Not found")
    user = _identity_user(
        db,
        provider="development",
        issuer="local",
        subject="dev-user",
        nickname=body.nickname,
        legacy_openid="dev-user",
    )
    return token_response(user)


@router.post("/cloud-login")
def cloud_login(request: Request, db: Session = Depends(get_db)):
    """Optional identity-header login for private-only WeChat Cloud Hosting deployments.

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
    issuer = appid or settings.wechat_app_id.strip() or "legacy"
    user = _identity_user(
        db,
        provider="wechat_miniprogram",
        issuer=issuer,
        subject=openid,
        legacy_openid=openid,
    )
    return token_response(user)


@router.post("/wechat")
async def wechat_login(body: WechatLogin, db: Session = Depends(get_db)):
    """Mini-program login. Its jscode2session code remains separate from mobile OAuth."""
    if not settings.wechat_app_id or not settings.wechat_app_secret:
        raise HTTPException(503, "Wechat login is not configured")
    url = "https://api.weixin.qq.com/sns/jscode2session"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.get(
                url,
                params={
                    "appid": settings.wechat_app_id,
                    "secret": settings.wechat_app_secret,
                    "js_code": body.code,
                    "grant_type": "authorization_code",
                },
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, "微信登录服务暂时不可用，请重试")
    if not isinstance(data, dict):
        raise HTTPException(502, "微信登录返回了无法识别的结果")
    if "openid" not in data:
        raise HTTPException(400, "微信登录凭证无效或微信服务暂不可用")
    openid = data.get("openid")
    if not isinstance(openid, str) or not openid.strip():
        raise HTTPException(502, "微信登录未返回有效身份")
    user = _identity_user(
        db,
        provider="wechat_miniprogram",
        issuer=settings.wechat_app_id,
        subject=openid,
        union_subject=data.get("unionid") if isinstance(data.get("unionid"), str) else None,
        legacy_openid=openid,
    )
    return token_response(user)


@router.post("/mobile/wechat")
async def mobile_wechat_login(body: WechatLogin, db: Session = Depends(get_db)):
    """Exchange a native WeChat Open Platform code using server-only credentials."""
    _require_mobile_auth_enabled()
    if not settings.mobile_wechat_configured:
        raise HTTPException(503, "Android 微信登录尚未配置开放平台凭据")
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.get(
                "https://api.weixin.qq.com/sns/oauth2/access_token",
                params={
                    "appid": settings.mobile_wechat_app_id,
                    "secret": settings.mobile_wechat_app_secret,
                    "code": body.code,
                    "grant_type": "authorization_code",
                },
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, "微信登录服务暂时不可用，请重试")

    if not isinstance(data, dict):
        raise HTTPException(502, "微信登录返回了无法识别的结果")
    if data.get("errcode") is not None:
        try:
            code = int(data.get("errcode") or 0)
        except (TypeError, ValueError):
            code = 0
        status = 401 if code in {40029, 40163} else 503
        raise HTTPException(status, "微信登录凭证无效或微信服务暂不可用")
    openid = data.get("openid")
    if not isinstance(openid, str) or not openid.strip():
        raise HTTPException(502, "微信登录未返回有效身份")

    user = _identity_user(
        db,
        provider="wechat_mobile",
        issuer=settings.mobile_wechat_app_id,
        subject=openid,
        union_subject=data.get("unionid") if isinstance(data.get("unionid"), str) else None,
    )
    return token_response(user)


@router.post("/cloudbase/ticket")
def cloudbase_custom_login_ticket(user=Depends(current_user)):
    """Issue a brief CloudBase custom ticket for this authenticated HealthMate user."""
    _require_mobile_auth_enabled()
    if not settings.cloudbase_env_id.strip() or not settings.cloudbase_custom_login_credentials_json.strip():
        raise HTTPException(503, "Android 云媒体登录尚未配置 CloudBase 环境凭据")
    return {
        "ticket": create_cloudbase_custom_ticket(
            user.id,
            settings.cloudbase_custom_login_credentials_json,
            settings.cloudbase_env_id,
        ),
        "expires_in": 600,
        "uid": f"hm_user_{user.id}",
    }


def _has_user_data(db: Session, user_id: int) -> bool:
    """Conservatively detect any row owned by a user before an identity merge."""
    excluded = {"users", "user_identities", "user_identity_link_codes"}
    for table in Base.metadata.tables.values():
        if table.name in excluded:
            continue
        owner_columns = [
            column
            for column in table.columns
            if any(foreign_key.target_fullname == "users.id" for foreign_key in column.foreign_keys)
        ]
        if not owner_columns:
            continue
        owned = db.execute(
            select(1).select_from(table).where(
                or_(*(column == user_id for column in owner_columns))
            ).limit(1)
        ).first()
        if owned:
            return True
    return False


@router.post("/link/start")
def start_identity_link(user=Depends(current_user), db: Session = Depends(get_db)):
    _require_mobile_auth_enabled()
    mini_identity = db.scalar(
        select(UserIdentity.id).where(
            UserIdentity.user_id == user.id,
            UserIdentity.provider == "wechat_miniprogram",
        )
    )
    if not mini_identity:
        raise HTTPException(403, "请先在微信小程序中登录，再发起账号关联")

    now = utc_now()
    db.execute(
        update(UserIdentityLinkCode)
        .where(
            UserIdentityLinkCode.source_user_id == user.id,
            UserIdentityLinkCode.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )
    raw_code = secrets.token_hex(16).upper()
    expires_at = now + timedelta(minutes=10)
    db.add(
        UserIdentityLinkCode(
            code_hash=hashlib.sha256(raw_code.encode("ascii")).hexdigest(),
            source_user_id=user.id,
            expires_at=expires_at,
        )
    )
    db.commit()
    return {"link_code": raw_code, "expires_at": utc_iso(expires_at)}


@router.post("/link/complete")
def complete_identity_link(
    body: LinkComplete,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    _require_mobile_auth_enabled()
    mobile_identity = db.scalar(
        select(UserIdentity.id).where(
            UserIdentity.user_id == user.id,
            UserIdentity.provider == "wechat_mobile",
        )
    )
    if not mobile_identity:
        raise HTTPException(403, "请先使用 Android 微信登录，再完成账号关联")

    normalized = body.link_code.upper()
    code_hash = hashlib.sha256(normalized.encode("ascii")).hexdigest()
    now = utc_now()
    link = db.scalar(
        select(UserIdentityLinkCode)
        .where(UserIdentityLinkCode.code_hash == code_hash)
        .with_for_update()
    )
    if not link or link.consumed_at or link.expires_at <= now:
        raise HTTPException(409, "关联码无效、已使用或已过期，请重新获取")

    target_user = db.get(User, link.source_user_id)
    target_identity = db.scalar(
        select(UserIdentity.id).where(
            UserIdentity.user_id == link.source_user_id,
            UserIdentity.provider == "wechat_miniprogram",
        )
    )
    if not target_user or not target_identity:
        raise HTTPException(409, "原微信账号状态已变化，请重新发起关联")

    if user.id != target_user.id:
        if _has_user_data(db, user.id):
            raise HTTPException(
                409,
                "Android 账号已有健康数据，系统不会自动合并；请先联系支持人员处理。",
            )
        identities = db.scalars(
            select(UserIdentity).where(UserIdentity.user_id == user.id)
        ).all()
        for identity in identities:
            identity.user_id = target_user.id
        db.delete(user)

    consumed = db.execute(
        update(UserIdentityLinkCode)
        .where(
            UserIdentityLinkCode.id == link.id,
            UserIdentityLinkCode.consumed_at.is_(None),
            UserIdentityLinkCode.expires_at > now,
        )
        .values(consumed_at=now)
    )
    if consumed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "关联码已被使用，请重新发起关联")
    db.commit()
    return token_response(target_user)


@router.post("/link/unlink")
def unlink_mobile_identity(user=Depends(current_user), db: Session = Depends(get_db)):
    _require_mobile_auth_enabled()
    identities = db.scalars(
        select(UserIdentity).where(UserIdentity.user_id == user.id)
    ).all()
    mobile_identities = [identity for identity in identities if identity.provider == "wechat_mobile"]
    has_other_login = any(identity.provider != "wechat_mobile" for identity in identities)
    if not mobile_identities:
        raise HTTPException(409, "当前账号没有已关联的 Android 微信身份")
    if not has_other_login:
        raise HTTPException(409, "这是账号唯一的登录方式，不能解绑；请先关联微信小程序账号")

    now = utc_now()
    db.execute(
        update(UserIdentityLinkCode)
        .where(
            UserIdentityLinkCode.source_user_id == user.id,
            UserIdentityLinkCode.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )
    for identity in mobile_identities:
        db.delete(identity)
    db.commit()
    return {"ok": True, "unlinked": True}
