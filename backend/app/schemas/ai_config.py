from pydantic import BaseModel, Field, field_validator
from urllib.parse import urlparse
import ipaddress


class AIConfigIn(BaseModel):
    enabled: bool = True
    base_url: str = Field(default="https://api.deepseek.com", max_length=500)
    model: str = Field(default="deepseek-chat", min_length=1, max_length=120)
    api_key: str = Field(default="", max_length=500)

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


class AIConfigOut(BaseModel):
    enabled: bool
    provider: str = "deepseek"
    base_url: str
    model: str
    has_api_key: bool
    api_key_hint: str = ""
    source: str = "user"


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
