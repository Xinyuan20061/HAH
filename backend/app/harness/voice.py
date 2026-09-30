from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import session as _session

from app.core.config import settings
from app.core.url_security import validate_ai_base_url


class VoiceUnavailable(RuntimeError):
    """Legacy 503-style error for the OpenAI-compatible path."""


class VoiceGatewayError(Exception):
    """Base class for all local gateway / provider rejections.

    Attributes:
        status_code: HTTP status the API layer should surface.
        code: stable machine-readable error code (never leaks provider internals).
    """

    status_code: int = 503
    code: str = "voice_unavailable"

    def __init__(self, message: str, status_code: int | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code


class VoiceNotConfigured(VoiceGatewayError):
    status_code = 503
    code = "voice_not_configured"


class VoiceRejected(VoiceGatewayError):
    status_code = 400
    code = "voice_rejected"


class VoiceBudgetExceeded(VoiceGatewayError):
    status_code = 429
    code = "voice_budget_exceeded"


@dataclass(frozen=True)
class SpeechAudio:
    data: bytes
    content_type: str = "audio/mpeg"


@dataclass(frozen=True)
class TranscribeResult:
    text: str
    provider_request_id: str = ""


@dataclass(frozen=True)
class TtsSegment:
    audio: bytes
    content_type: str = "audio/mpeg"
    provider_request_id: str = ""


@dataclass(frozen=True)
class SynthesisResult:
    segments: list[TtsSegment]
    partial: bool
    total_chars: int = 0


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------
class VoiceProvider(Protocol):
    """Unified STT/TTS provider contract (spec 7.2)."""

    name: str

    async def transcribe(self, audio: bytes, fmt: str) -> TranscribeResult: ...

    async def synthesize_segment(self, text: str) -> TtsSegment: ...


# ---------------------------------------------------------------------------
# OpenAI-compatible provider (retained, spec says keep existing one)
# ---------------------------------------------------------------------------
class OpenAICompatibleVoiceProvider:
    """Provider-neutral adapter for OpenAI-compatible STT and TTS endpoints."""

    name = "openai_compatible"

    SUPPORTED_FORMATS = {"mp3": "audio/mpeg", "m4a": "audio/mp4", "aac": "audio/aac", "wav": "audio/wav"}

    def __init__(self, api_key: str, base_url: str, stt_model: str, tts_model: str, voice: str):
        self.api_key = api_key
        self.base_url = validate_ai_base_url(base_url)
        self.stt_model = stt_model
        self.tts_model = tts_model
        self.voice = voice

    async def transcribe_raw(self, audio: bytes, filename: str, content_type: str) -> str:
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

    async def synthesize_raw(self, text: str) -> SpeechAudio:
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
            raise VoiceUnavailable("语音播报服务暂时不可用")

    # --- Protocol adapters ---------------------------------------------------
    async def transcribe(self, audio: bytes, fmt: str) -> TranscribeResult:
        content_type = self.SUPPORTED_FORMATS.get(fmt)
        if not content_type:
            raise VoiceRejected("仅支持 mp3、m4a、aac 或 wav", 400, "unsupported_audio")
        text = await self.transcribe_raw(audio, f"voice.{fmt}", content_type)
        return TranscribeResult(text=text, provider_request_id="")

    async def synthesize_segment(self, text: str) -> TtsSegment:
        audio = await self.synthesize_raw(text)
        return TtsSegment(audio=audio.data, content_type=audio.content_type, provider_request_id="")


# ---------------------------------------------------------------------------
# Tencent Cloud provider (SentenceRecognition + TextToVoice, spec 7.2)
# ---------------------------------------------------------------------------
class TencentCloudVoiceProvider:
    """Tencent Cloud ASR/TTS via the official Python SDK 3.0.

    SDK clients are built lazily so the app imports even when the optional SDK
    is not installed; synchronous SDK calls are offloaded to a thread pool so
    the FastAPI event loop is never blocked.
    """

    name = "tencent_cloud"
    SUPPORTED_FORMATS = {"mp3", "m4a", "aac", "wav"}

    def __init__(
        self,
        secret_id: str,
        secret_key: str,
        region: str,
        engine: str,
        voice_type: int,
        asr_client=None,
        tts_client=None,
    ):
        self.engine = engine
        self.voice_type = voice_type
        if asr_client is not None and tts_client is not None:
            # Injected clients (tests / MockTransport-style stubs).
            self._asr = asr_client
            self._tts = tts_client
            return
        # Lazy, optional SDK import.
        from tencentcloud.common import credential  # noqa: WPS433
        from tencentcloud.asr.v20190614 import asr_client as asr_mod  # noqa: WPS433
        from tencentcloud.tts.v20190823 import tts_client as tts_mod  # noqa: WPS433

        cred = credential.Credential(secret_id, secret_key)
        self._asr = asr_mod.AsrClient(cred, region)
        self._tts = tts_mod.TtsClient(cred, region)

    async def transcribe(self, audio: bytes, fmt: str) -> TranscribeResult:
        if fmt not in self.SUPPORTED_FORMATS or not audio:
            raise VoiceRejected("仅支持 mp3、m4a、aac 或 wav", 400, "unsupported_audio")
        from starlette.concurrency import run_in_threadpool
        from tencentcloud.asr.v20190614 import models as asr_models  # noqa: WPS433

        def call() -> TranscribeResult:
            req = asr_models.SentenceRecognitionRequest()
            req.from_json_string(
                json.dumps(
                    {
                        "EngSerViceType": self.engine,
                        "SourceType": 1,
                        "VoiceFormat": fmt,
                        "Data": base64.b64encode(audio).decode("ascii"),
                        "DataLen": len(audio),
                        "SubServiceType": 2,
                    }
                )
            )
            resp = self._asr.SentenceRecognition(req)
            text = (getattr(resp, "Result", "") or "").strip()
            if not text:
                raise VoiceRejected("未能识别语音内容", 400, "empty_transcription")
            return TranscribeResult(text=text, provider_request_id=getattr(resp, "RequestId", "") or "")

        try:
            return await run_in_threadpool(call)
        except VoiceRejected:
            raise
        except Exception as exc:  # SDK / network errors -> degraded, never leak internals
            raise VoiceGatewayError("腾讯云语音识别暂时不可用", 503, "asr_upstream_error") from exc

    async def synthesize_segment(self, text: str) -> TtsSegment:
        if not text.strip():
            raise VoiceRejected("合成文本为空", 400, "empty_tts_text")
        from starlette.concurrency import run_in_threadpool
        from tencentcloud.tts.v20190823 import models as tts_models  # noqa: WPS433

        def call() -> TtsSegment:
            req = tts_models.TextToVoiceRequest()
            req.from_json_string(
                json.dumps(
                    {
                        "Text": text,
                        "SessionId": uuid4().hex,
                        "VoiceType": self.voice_type,
                        "Codec": "mp3",
                    }
                )
            )
            resp = self._tts.TextToVoice(req)
            audio_b64 = getattr(resp, "Audio", "") or ""
            if not audio_b64:
                raise VoiceGatewayError("腾讯云语音合成返回为空", 503, "tts_upstream_error")
            return TtsSegment(
                audio=base64.b64decode(audio_b64, validate=True),
                content_type="audio/mpeg",
                provider_request_id=getattr(resp, "RequestId", "") or "",
            )

        try:
            return await run_in_threadpool(call)
        except (binascii.Error, ValueError) as exc:
            raise VoiceGatewayError("腾讯云语音合成返回数据无效", 503, "tts_upstream_error") from exc
        except VoiceGatewayError:
            raise
        except Exception as exc:
            raise VoiceGatewayError("腾讯云语音合成暂时不可用", 503, "tts_upstream_error") from exc


# ---------------------------------------------------------------------------
# Text splitting: TTS per-segment safe chunking (spec 7.2)
# ---------------------------------------------------------------------------
# Prefer splitting on Chinese sentence/clause punctuation first.
_SPLIT_POINTS = "。！？；!?;\n"
_SECONDARY_SPLIT = "，、, "


def split_tts_text(text: str, max_chars: int | None = None) -> list[str]:
    """Split reply text into <=max_chars segments without silently truncating.

    Splits preferentially on Chinese full/half-width punctuation. Remaining
    overflow beyond max_chars on a single clause is cut on a character boundary
    so the WHOLE original reply is always covered (never silently dropped).
    """
    text = (text or "").strip()
    if not text:
        return []
    limit = max_chars or settings.voice_tts_segment_chars
    # First pass: break into clauses on primary punctuation, keeping delimiters.
    clauses: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in _SPLIT_POINTS:
            clauses.append(buf)
            buf = ""
    if buf:
        clauses.append(buf)

    # Second pass: pack clauses into segments <= limit, splitting long clauses.
    segments: list[str] = []
    current = ""
    for clause in clauses:
        if len(clause) > limit:
            # Flush current, then chunk the oversized clause on secondary punctuation.
            if current:
                segments.append(current)
                current = ""
            segments.extend(_chunk_oversized(clause, limit))
            continue
        if len(current) + len(clause) <= limit:
            current += clause
        else:
            if current:
                segments.append(current)
            current = clause
    if current:
        segments.append(current)
    return [s.strip() for s in segments if s.strip()]


def _chunk_oversized(clause: str, limit: int) -> list[str]:
    out: list[str] = []
    buf = ""
    for ch in clause:
        buf += ch
        if ch in _SECONDARY_SPLIT and len(buf) >= max(limit // 2, 1):
            out.append(buf)
            buf = ""
        elif len(buf) >= limit:
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return out


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------
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


def resolve_voice_provider_name(user) -> str:
    """Resolve the effective voice provider for a user (system default overridable)."""
    cfg = getattr(user, "ai_config", None) if user else None
    choice = getattr(cfg, "voice_provider", None) if cfg else None
    if choice in {"tencent_cloud", "openai_compatible", "off"}:
        if choice == "off":
            return "off"
        # openai_compatible chosen by user but no credentials anywhere -> treated off.
        if choice == "openai_compatible" and not _user_voice_config(user) and not (
            settings.voice_api_key.strip() and settings.voice_api_base_url.strip()
        ):
            return "off"
        return choice
    return settings.active_voice_provider


def voice_configured(user=None) -> bool:
    """Backwards-compatible check used by /harness/manifest."""
    provider = resolve_voice_provider_name(user)
    if provider == "tencent_cloud":
        return settings.tencent_voice_configured
    if provider == "openai_compatible":
        return bool(_user_voice_config(user)) or bool(
            settings.voice_api_key.strip() and settings.voice_api_base_url.strip()
        )
    return False


def tencent_config_fingerprint() -> str:
    """One-way fingerprint that changes when key/region/engine/voice changes.

    Never stores or logs the secret itself.
    """
    material = "|".join(
        [
            "tencent_cloud",
            settings.tencent_region,
            settings.tencent_asr_engine,
            str(settings.tencent_tts_voice_type),
            settings.tencent_secret_id,
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def get_voice_provider(user=None) -> VoiceProvider:
    """Build a provider instance (raises VoiceNotConfigured when missing)."""
    provider = resolve_voice_provider_name(user)
    if provider == "tencent_cloud":
        if not settings.tencent_voice_configured:
            raise VoiceNotConfigured("尚未配置腾讯云语音密钥（TENCENT_SECRET_ID / TENCENT_SECRET_KEY）")
        return TencentCloudVoiceProvider(
            settings.tencent_secret_id,
            settings.tencent_secret_key,
            settings.tencent_region,
            settings.tencent_asr_engine,
            settings.tencent_tts_voice_type,
        )
    if provider == "openai_compatible":
        user_config = _user_voice_config(user)
        if user_config:
            return OpenAICompatibleVoiceProvider(**user_config)
        if not settings.voice_api_key.strip() or not settings.voice_api_base_url.strip():
            raise VoiceNotConfigured("尚未配置语音服务")
        return OpenAICompatibleVoiceProvider(
            settings.voice_api_key,
            settings.voice_api_base_url,
            settings.voice_stt_model,
            settings.voice_tts_model,
            settings.voice_tts_voice,
        )
    raise VoiceNotConfigured("语音服务未开启")


# ---------------------------------------------------------------------------
# Unified voice gateway: local gates + budget + desensitized ledger + trace_id
# ---------------------------------------------------------------------------
def _estimate_audio_seconds(audio: bytes) -> float:
    """Best-effort duration estimate without a decoder.

    The miniprogram records 16kHz mono mp3 at 48 kbit/s (~6000 bytes/s). This is
    a conservative local gate only; Tencent still enforces <=60s server-side.
    """
    bytes_per_second = 48000 / 8.0  # 6000
    return len(audio) / bytes_per_second


def _month_usage(db, user_id: int, provider: str) -> tuple[int, int]:
    from app.models import VoiceUsageDaily

    today = date.today()
    month_start = date(today.year, today.month, 1)
    rows = (
        db.execute(
            select(VoiceUsageDaily).where(
                VoiceUsageDaily.user_id == user_id,
                VoiceUsageDaily.provider == provider,
                VoiceUsageDaily.usage_date >= month_start,
            )
        )
        .scalars()
        .all()
    )
    return sum(r.asr_count for r in rows), sum(r.tts_chars for r in rows)


def _bump_usage(db, user_id: int, provider: str, *, asr: int = 0, tts_chars: int = 0, failure: bool = False):
    from app.models import VoiceUsageDaily

    today = date.today()
    row = (
        db.execute(
            select(VoiceUsageDaily).where(
                VoiceUsageDaily.user_id == user_id,
                VoiceUsageDaily.usage_date == today,
                VoiceUsageDaily.provider == provider,
            )
        )
        .scalar_one_or_none()
    )
    if row is None:
        row = VoiceUsageDaily(
            user_id=user_id, usage_date=today, provider=provider,
            asr_count=0, tts_chars=0, failure_count=0,
        )
        db.add(row)
    row.asr_count += asr
    row.tts_chars += tts_chars
    if failure:
        row.failure_count += 1
    db.commit()


def _record_invocation(
    db,
    *,
    user_id: int | None,
    provider: str,
    operation: str,
    trace_id: str,
    status: str,
    latency_ms: int,
    char_count: int | None,
    provider_request_id: str,
):
    from app.models import ProviderInvocation

    # request_fingerprint is the trace id only; never audio/text/prompt content.
    db.add(
        ProviderInvocation(
            user_id=user_id,
            provider=provider,
            operation=operation,
            request_fingerprint=trace_id[:128],
            status=status,
            latency_ms=latency_ms,
            token_or_char_count=char_count,
            provider_request_id=(provider_request_id or None),
        )
    )
    db.commit()


class VoiceGateway:
    """Orchestrates local rejection gates, budgets, tracing and the ledger.

    Every call is traced, budget-limited, degradable and auditable. Raw audio,
    full prompt text and keys are never persisted.
    """

    def __init__(self, provider: VoiceProvider, user_id: int | None, db):
        self.provider = provider
        self.provider_name = provider.name
        self.user_id = user_id
        self.db = db
        self.trace_id = uuid4().hex

    # -- ASR -----------------------------------------------------------------
    async def transcribe(self, audio: bytes, fmt: str) -> TranscribeResult:
        fmt = (fmt or "").lower().lstrip(".")
        if not audio:
            raise VoiceRejected("语音为空", 400, "empty_audio")
        if fmt not in OpenAICompatibleVoiceProvider.SUPPORTED_FORMATS:
            raise VoiceRejected("不支持的音频格式", 400, "unsupported_audio")
        if len(audio) > settings.voice_max_audio_bytes:
            raise VoiceRejected("语音文件超过大小限制", 413, "audio_too_large")
        if _estimate_audio_seconds(audio) > settings.voice_max_audio_seconds:
            raise VoiceRejected(
                f"语音时长超过 {settings.voice_max_audio_seconds} 秒限制", 413, "audio_too_long"
            )
        asr_used, _ = _month_usage(self.db, self.user_id, self.provider_name)
        if asr_used >= settings.voice_monthly_asr_budget:
            raise VoiceBudgetExceeded("本月语音识别次数已达预算上限")

        started = _now_ms()
        try:
            result = await self.provider.transcribe(audio, fmt)
        except VoiceGatewayError:
            _bump_usage(self.db, self.user_id, self.provider_name, failure=True)
            _record_invocation(
                self.db, user_id=self.user_id, provider=self.provider_name,
                operation="asr", trace_id=self.trace_id, status="failed",
                latency_ms=_now_ms() - started, char_count=None, provider_request_id="",
            )
            raise
        _bump_usage(self.db, self.user_id, self.provider_name, asr=1)
        _record_invocation(
            self.db, user_id=self.user_id, provider=self.provider_name,
            operation="asr", trace_id=self.trace_id, status="success",
            latency_ms=_now_ms() - started, char_count=len(result.text),
            provider_request_id=result.provider_request_id,
        )
        return result

    # -- TTS (segmented) -----------------------------------------------------
    async def synthesize(self, text: str):
        text = (text or "").strip()
        if not text:
            raise VoiceRejected("合成文本为空", 400, "empty_tts_text")
        if len(text) > settings.voice_tts_segment_chars * 50:
            # Hard ceiling on total reply length beyond what segments can cover.
            raise VoiceRejected("回复文本过长", 413, "tts_text_too_long")
        _, tts_used = _month_usage(self.db, self.user_id, self.provider_name)
        if tts_used + len(text) > settings.voice_monthly_tts_chars_budget:
            raise VoiceBudgetExceeded("本月语音合成字符数已达预算上限")

        segments_text = split_tts_text(text, settings.voice_tts_segment_chars)
        out: list[TtsSegment] = []
        started = _now_ms()
        try:
            for seg_text in segments_text:
                out.append(await self.provider.synthesize_segment(seg_text))
        except VoiceGatewayError:
            # Degrade gracefully: return whatever succeeded. Already-synthesized
            # segments are NOT re-synthesized (spec 7.2). Only a total failure
            # (segment 0 itself failed) is raised as an error.
            succeeded_chars = sum(len(s) for s in segments_text[: len(out)])
            _bump_usage(self.db, self.user_id, self.provider_name, tts_chars=succeeded_chars, failure=not out)
            _record_invocation(
                self.db, user_id=self.user_id, provider=self.provider_name,
                operation="tts", trace_id=self.trace_id,
                status="partial" if out else "failed",
                latency_ms=_now_ms() - started, char_count=len(text),
                provider_request_id=out[-1].provider_request_id if out else "",
            )
            if not out:
                raise
            return SynthesisResult(segments=out, partial=True, total_chars=len(text))
        _bump_usage(self.db, self.user_id, self.provider_name, tts_chars=len(text))
        _record_invocation(
            self.db, user_id=self.user_id, provider=self.provider_name,
            operation="tts", trace_id=self.trace_id, status="success",
            latency_ms=_now_ms() - started, char_count=len(text),
            provider_request_id=out[-1].provider_request_id if out else "",
        )
        return SynthesisResult(segments=out, partial=False, total_chars=len(text))


def _now_ms() -> int:
        import time

        return int(time.time() * 1000)


def build_voice_gateway(user, db) -> VoiceGateway:
    provider = get_voice_provider(user)
    return VoiceGateway(provider, getattr(user, "id", None), db)


# ---------------------------------------------------------------------------
# Status + verify-once helpers (no cloud call on status)
# ---------------------------------------------------------------------------
def voice_status(db, user) -> dict:
    provider = resolve_voice_provider_name(user)
    configured = voice_configured(user)
    last_verified = None
    if provider == "tencent_cloud":
        from app.models import ProviderConnectionCheck

        fp = tencent_config_fingerprint()
        rows = (
            db.execute(
                select(ProviderConnectionCheck).where(
                    ProviderConnectionCheck.config_fingerprint == fp,
                    ProviderConnectionCheck.status == "success",
                )
            )
            .scalars()
            .all()
        )
        times = [r.verified_at for r in rows if r.verified_at]
        if times:
            last_verified = max(times).isoformat()
    return {
        "provider": provider,
        "configured": configured,
        "asr_available": configured,
        "tts_available": configured,
        "last_verified_at": last_verified,
        "live_check_required": bool(configured and last_verified is None),
    }


async def run_verify_once(db, user, direction: str, provider_stub=None) -> dict:
    """Manually trigger one minimal live check; cache success to stop re-calling.

    The (config_fingerprint, direction, status) unique constraint on
    provider_connection_checks guarantees that after a successful check for the
    same fingerprint + direction, later calls return the cached row and never
    reach the external service again (spec 7.3).
    """
    from app.models import ProviderConnectionCheck

    if direction not in {"asr", "tts"}:
        raise VoiceRejected("check 必须是 asr 或 tts", 400, "bad_check")
    if not settings.voice_live_verify_enabled:
        raise VoiceNotConfigured(
            "外部连通验证未启用（VOICE_LIVE_VERIFY_ENABLED=false）；"
            "请在后端配置密钥后由管理员手动开启并触发一次验证"
        )
    if not settings.tencent_voice_configured:
        raise VoiceNotConfigured("尚未配置腾讯云语音密钥，无法进行连通验证")

    fp = tencent_config_fingerprint()
    existing = (
        db.execute(
            select(ProviderConnectionCheck).where(
                ProviderConnectionCheck.config_fingerprint == fp,
                ProviderConnectionCheck.direction == direction,
                ProviderConnectionCheck.status == "success",
            )
        )
        .scalar_one_or_none()
    )
    if existing is not None:
        # Cached success -> do NOT call the external service again.
        return {
            "provider": "tencent_cloud",
            "check": direction,
            "called": False,
            "cached": True,
            "verified_at": existing.verified_at.isoformat() if existing.verified_at else None,
            "provider_request_id": existing.provider_request_id,
        }

    # Perform one minimal real (or stubbed) call.
    provider = provider_stub or TencentCloudVoiceProvider(
        settings.tencent_secret_id,
        settings.tencent_secret_key,
        settings.tencent_region,
        settings.tencent_asr_engine,
        settings.tencent_tts_voice_type,
    )
    request_id = ""
    try:
        if direction == "asr":
            result = await provider.transcribe(b"verify-once", "mp3")
            request_id = result.provider_request_id
        else:
            seg = await provider.synthesize_segment("连接测试")
            request_id = seg.provider_request_id
    except VoiceGatewayError:
        # Record failure (does not block future attempts; unique key includes status).
        _safe_record_check(db, fp, direction, "failed", None)
        raise
    _safe_record_check(db, fp, direction, "success", request_id)
    return {
        "provider": "tencent_cloud",
        "check": direction,
        "called": True,
        "cached": False,
        "verified_at": date.today().isoformat(),
        "provider_request_id": request_id,
    }


def _safe_record_check(db, fp: str, direction: str, status: str, request_id):
    from app.models import ProviderConnectionCheck
    from sqlalchemy.exc import IntegrityError

    row = ProviderConnectionCheck(
        config_fingerprint=fp,
        direction=direction,
        status=status,
        verified_at=date.today() if status == "success" else None,
        provider_request_id=request_id,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()