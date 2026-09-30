import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api.deps import current_user
from app.core.database import get_db
from app.core.config import settings
from app.core.crypto import encrypt_secret, decrypt_secret
from app.models import UserAIConfig
from app.schemas.ai_config import (
    AIConfigIn,
    AIConfigOut,
    AIConnectionTestIn,
    VoiceConnectionTestIn,
)
from app.services.ai.gateway import DeepSeekProvider
from app.harness.voice import OpenAICompatibleVoiceProvider, VoiceUnavailable
from app.core.url_security import validate_ai_base_url

router = APIRouter(prefix="/users/me/ai-config", tags=["ai-config"])


def _hint(secret: str) -> str:
    if not secret:
        return ""
    return f"{secret[:3]}••••{secret[-4:]}" if len(secret) >= 8 else "••••••••"


def _preferences_dict(cfg: UserAIConfig | None) -> dict:
    if not cfg or not getattr(cfg, "voice_preferences_json", None):
        return {}
    try:
        value = json.loads(cfg.voice_preferences_json)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _out(cfg: UserAIConfig | None):
    if cfg:
        key = _stored_key(cfg)
        voice_key = _stored_voice_key(cfg)
        return AIConfigOut(
            enabled=cfg.enabled,
            base_url=cfg.base_url,
            model=cfg.model,
            has_api_key=bool(key),
            api_key_hint=_hint(key),
            source="user",
            voice_enabled=bool(cfg.voice_enabled),
            voice_base_url=cfg.voice_base_url or settings.voice_api_base_url,
            voice_stt_model=cfg.voice_stt_model or settings.voice_stt_model,
            voice_tts_model=cfg.voice_tts_model or settings.voice_tts_model,
            voice_name=cfg.voice_name or settings.voice_tts_voice,
            has_voice_api_key=bool(voice_key),
            voice_api_key_hint=_hint(voice_key),
            system_voice_configured=_system_voice_configured(),
            voice_provider=getattr(cfg, "voice_provider", "off") or "off",
            voice_preferences=_preferences_dict(cfg),
        )
    return AIConfigOut(
        enabled=False,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        has_api_key=False,
        api_key_hint="",
        source="system-default",
        voice_enabled=False,
        voice_base_url=settings.voice_api_base_url,
        voice_stt_model=settings.voice_stt_model,
        voice_tts_model=settings.voice_tts_model,
        voice_name=settings.voice_tts_voice,
        has_voice_api_key=False,
        voice_api_key_hint="",
        system_voice_configured=_system_voice_configured(),
        voice_provider=settings.active_voice_provider,
        voice_preferences={},
    )


def _system_voice_configured() -> bool:
    return bool(settings.voice_api_key.strip() and settings.voice_api_base_url.strip())


def _stored_key(cfg: UserAIConfig | None) -> str:
    try:
        return decrypt_secret(cfg.api_key_encrypted) if cfg else ""
    except ValueError:
        raise HTTPException(
            503, "用户 Key 无法解密，请恢复原加密密钥或重新填写用户 Key"
        ) from None


def _stored_voice_key(cfg: UserAIConfig | None) -> str:
    try:
        return decrypt_secret(cfg.voice_api_key_encrypted or "") if cfg else ""
    except ValueError:
        raise HTTPException(
            503, "用户语音 Key 无法解密，请恢复原加密密钥或重新填写语音 Key"
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
    if body.voice_enabled is not None:
        cfg.voice_enabled = body.voice_enabled
    if body.voice_base_url is not None:
        cfg.voice_base_url = (
            validate_ai_base_url(body.voice_base_url)
            if body.voice_base_url.strip()
            else ""
        )
    if body.voice_stt_model is not None:
        cfg.voice_stt_model = body.voice_stt_model.strip() or settings.voice_stt_model
    if body.voice_tts_model is not None:
        cfg.voice_tts_model = body.voice_tts_model.strip() or settings.voice_tts_model
    if body.voice_name is not None:
        cfg.voice_name = body.voice_name.strip() or settings.voice_tts_voice
    if body.voice_api_key is not None and body.voice_api_key.strip():
        cfg.voice_api_key_encrypted = encrypt_secret(body.voice_api_key.strip())
    # voice_provider switch (spec section 5). "tencent_cloud" uses system-side
    # backend keys only; it never requires (and never accepts) user-provided keys.
    if body.voice_provider is not None:
        cfg.voice_provider = body.voice_provider
    if body.voice_preferences is not None:
        cfg.voice_preferences_json = json.dumps(body.voice_preferences, ensure_ascii=False)
    if cfg.voice_enabled and cfg.voice_provider != "tencent_cloud":
        if not _stored_voice_key(cfg) and not _system_voice_configured():
            raise HTTPException(400, "请填写语音 API Key，或先配置系统语音服务")
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


@router.post("/voice-test")
async def test_voice_config(body: VoiceConnectionTestIn, user=Depends(current_user)):
    cfg = user.ai_config
    key = body.api_key.strip() or _stored_voice_key(cfg) or settings.voice_api_key.strip()
    base_url = (
        body.base_url.strip()
        or (cfg.voice_base_url if cfg else "")
        or settings.voice_api_base_url
    )
    model = (
        body.tts_model.strip()
        or (cfg.voice_tts_model if cfg else "")
        or settings.voice_tts_model
    )
    voice_name = (
        body.voice_name.strip()
        or (cfg.voice_name if cfg else "")
        or settings.voice_tts_voice
    )
    if not key or not base_url:
        raise HTTPException(400, "请先填写语音 API Key 与 Base URL")
    try:
        provider = OpenAICompatibleVoiceProvider(
            key,
            validate_ai_base_url(base_url),
            (cfg.voice_stt_model if cfg else "") or settings.voice_stt_model,
            model,
            voice_name,
        )
        audio = await provider.synthesize("连接成功")
        return {
            "ok": True,
            "tts_model": model,
            "voice_name": voice_name,
            "content_type": audio.content_type,
        }
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except VoiceUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
