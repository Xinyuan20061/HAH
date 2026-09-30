from pydantic import BaseModel, Field, field_validator
from urllib.parse import urlparse
import ipaddress
import json


class AIConfigIn(BaseModel):
    enabled: bool = True
    base_url: str = Field(default="https://api.deepseek.com", max_length=500)
    model: str = Field(default="deepseek-chat", min_length=1, max_length=120)
    api_key: str = Field(default="", max_length=500)
    voice_enabled: bool | None = None
    voice_base_url: str | None = Field(default=None, max_length=500)
    voice_stt_model: str | None = Field(default=None, max_length=120)
    voice_tts_model: str | None = Field(default=None, max_length=120)
    voice_name: str | None = Field(default=None, max_length=80)
    voice_api_key: str | None = Field(default=None, max_length=500)
    # voice_provider: "off" | "openai_compatible" | "tencent_cloud" (spec section 5).
    voice_provider: str | None = Field(default=None, max_length=30)
    # Voice preferences (e.g. tencent voice_type id) as a JSON object. Never holds keys.
    voice_preferences: dict | None = None

    @field_validator("voice_provider")
    @classmethod
    def validate_voice_provider(cls, value: str | None):
        if value is None:
            return value
        if value not in {"off", "openai_compatible", "tencent_cloud"}:
            raise ValueError("voice_provider 必须是 off、openai_compatible 或 tencent_cloud")
        return value

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str):
        value = value.strip().rstrip("/")
        u = urlparse(value)
        if u.scheme != "https" or not u.hostname:
            raise ValueError("Base URL 必须是有效的 HTTPS 地址")
        host = u.hostname.lower()
        if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
            raise ValueError("Base URL 不允许指向本机或局域网地址")
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise ValueError("Base URL 不允许指向私有网络地址")
        except ValueError as exc:
            if "不允许" in str(exc):
                raise
        return value

    @field_validator("voice_base_url")
    @classmethod
    def validate_voice_base_url(cls, value: str | None):
        if value is None or not value.strip():
            return value
        return cls.validate_base_url(value)


class AIConfigOut(BaseModel):
    enabled: bool
    provider: str = "deepseek"
    base_url: str
    model: str
    has_api_key: bool
    api_key_hint: str = ""
    source: str = "user"
    voice_enabled: bool = False
    voice_base_url: str = ""
    voice_stt_model: str = "whisper-1"
    voice_tts_model: str = "tts-1"
    voice_name: str = "alloy"
    has_voice_api_key: bool = False
    voice_api_key_hint: str = ""
    system_voice_configured: bool = False
    voice_provider: str = "off"
    voice_preferences: dict = {}


class VoiceConnectionTestIn(BaseModel):
    base_url: str = Field(default="", max_length=500)
    tts_model: str = Field(default="", max_length=120)
    voice_name: str = Field(default="", max_length=80)
    api_key: str = Field(default="", max_length=500)

    @field_validator("base_url")
    @classmethod
    def validate_optional_base_url(cls, value: str):
        if not value.strip():
            return ""
        return AIConfigIn.validate_base_url(value)


class AIConnectionTestIn(BaseModel):
    base_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=120)
    api_key: str = Field(default="", max_length=500)

    @field_validator("base_url")
    @classmethod
    def validate_optional_base_url(cls, value: str):
        if not value.strip():
            return ""
        return AIConfigIn.validate_base_url(value)
