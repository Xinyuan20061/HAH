from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api.deps import current_user
from app.core.database import get_db
from app.core.config import settings
from app.core.crypto import encrypt_secret, decrypt_secret
from app.models import UserAIConfig
from app.schemas.ai_config import AIConfigIn, AIConfigOut, AIConnectionTestIn
from app.services.ai.gateway import DeepSeekProvider
from app.core.url_security import validate_ai_base_url

router = APIRouter(prefix="/users/me/ai-config", tags=["ai-config"])


def _hint(secret: str) -> str:
    if not secret:
        return ""
    return f"{secret[:3]}••••{secret[-4:]}" if len(secret) >= 8 else "••••••••"


def _out(cfg: UserAIConfig | None):
    if cfg:
        key = _stored_key(cfg)
        return AIConfigOut(
            enabled=cfg.enabled,
            base_url=cfg.base_url,
            model=cfg.model,
            has_api_key=bool(key),
            api_key_hint=_hint(key),
            source="user",
        )
    return AIConfigOut(
        enabled=False,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        has_api_key=False,
        api_key_hint="",
        source="system-default",
    )


def _stored_key(cfg: UserAIConfig | None) -> str:
    try:
        return decrypt_secret(cfg.api_key_encrypted) if cfg else ""
    except ValueError:
        raise HTTPException(
            503, "用户 Key 无法解密，请恢复原加密密钥或重新填写用户 Key"
        ) from None


@router.get("", response_model=AIConfigOut)
def get_config(user=Depends(current_user)):
    return _out(user.ai_config)


@router.put("", response_model=AIConfigOut)
def save_config(
    body: AIConfigIn, user=Depends(current_user), db: Session = Depends(get_db)
):
    cfg = user.ai_config or UserAIConfig(user_id=user.id)
    cfg.enabled = body.enabled
    try:
        cfg.base_url = validate_ai_base_url(body.base_url)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    cfg.model = body.model.strip()
    if body.api_key.strip():
        cfg.api_key_encrypted = encrypt_secret(body.api_key.strip())
    db.add(cfg)
    db.commit()
    db.refresh(cfg)
    return _out(cfg)


@router.delete("")
def delete_config(user=Depends(current_user), db: Session = Depends(get_db)):
    if user.ai_config:
        db.delete(user.ai_config)
        db.commit()
    return {"ok": True}


@router.post("/test")
async def test_config(body: AIConnectionTestIn, user=Depends(current_user)):
    cfg = user.ai_config
    key = body.api_key.strip() or _stored_key(cfg)
    try:
        base_url = validate_ai_base_url(
            body.base_url.strip().rstrip("/")
            or (cfg.base_url if cfg else settings.deepseek_base_url)
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    model = body.model.strip() or (cfg.model if cfg else settings.deepseek_model)
    if not key:
        raise HTTPException(400, "请先填写 DeepSeek API Key")
    try:
        result = await DeepSeekProvider(
            api_key=key, base_url=base_url, model=model
        ).chat("你是连接测试助手，只回复 OK。", "连接测试，只回复 OK")
        return {
            "ok": True,
            "provider": result.provider,
            "model": model,
            "message": result.text[:80],
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            503,
            "连接失败：请检查 Base URL、模型名、API Key 权限或网络状态。服务端不会回显密钥或上游原始错误。",
        )
