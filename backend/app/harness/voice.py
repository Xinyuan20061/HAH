from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.core.url_security import validate_ai_base_url


class VoiceUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class SpeechAudio:
    data: bytes
    content_type: str = "audio/mpeg"


class OpenAICompatibleVoiceProvider:
    """Provider-neutral adapter for OpenAI-compatible STT and TTS endpoints."""

    def __init__(self, api_key: str, base_url: str, stt_model: str, tts_model: str, voice: str):
        self.api_key = api_key
        self.base_url = validate_ai_base_url(base_url)
        self.stt_model = stt_model
        self.tts_model = tts_model
        self.voice = voice

    async def transcribe(self, audio: bytes, filename: str, content_type: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=False, trust_env=False) as client:
                response = await client.post(
                    self.base_url + "/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    data={"model": self.stt_model, "language": "zh"},
                    files={"file": (filename, audio, content_type)},
                )
            response.raise_for_status()
            text = response.json().get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("empty transcription")
            return text.strip()
        except Exception as exc:
            raise VoiceUnavailable("语音识别服务暂时不可用") from exc

    async def synthesize(self, text: str) -> SpeechAudio:
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=False, trust_env=False) as client:
                response = await client.post(
                    self.base_url + "/audio/speech",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.tts_model,
                        "voice": self.voice,
                        "input": text,
                        "response_format": "mp3",
                    },
                )
            response.raise_for_status()
            if not response.content:
                raise ValueError("empty speech")
            return SpeechAudio(response.content)
        except Exception as exc:
            raise VoiceUnavailable("语音播报服务暂时不可用") from exc


def _user_voice_config(user):
    config = getattr(user, "ai_config", None) if user else None
    if not config or not config.voice_enabled or not config.voice_api_key_encrypted:
        return None
    from app.core.crypto import decrypt_secret

    try:
        key = decrypt_secret(config.voice_api_key_encrypted)
    except ValueError as exc:
        raise VoiceUnavailable("用户语音 Key 无法解密，请重新配置") from exc
    if not key or not str(config.voice_base_url or "").strip():
        return None
    return {
        "api_key": key,
        "base_url": config.voice_base_url,
        "stt_model": config.voice_stt_model or settings.voice_stt_model,
        "tts_model": config.voice_tts_model or settings.voice_tts_model,
        "voice": config.voice_name or settings.voice_tts_voice,
    }


def voice_configured(user=None) -> bool:
    if _user_voice_config(user):
        return True
    return bool(settings.voice_api_key.strip() and settings.voice_api_base_url.strip())


def get_voice_provider(user=None):
    user_config = _user_voice_config(user)
    if user_config:
        return OpenAICompatibleVoiceProvider(**user_config)
    if not settings.voice_api_key.strip() or not settings.voice_api_base_url.strip():
        raise VoiceUnavailable("尚未配置语音服务")
    return OpenAICompatibleVoiceProvider(
        settings.voice_api_key,
        settings.voice_api_base_url,
        settings.voice_stt_model,
        settings.voice_tts_model,
        settings.voice_tts_voice,
    )
