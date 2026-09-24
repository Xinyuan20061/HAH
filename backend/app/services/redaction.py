from __future__ import annotations
import re

SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "api-key",
    "authorization",
    "access_token",
    "refresh_token",
    "token",
    "password",
    "secret",
    "secret_key",
    "wechat_app_secret",
    "s3_secret_key",
    "api_key_encrypted",
}


def redact_text(value: str) -> str:
    if not value:
        return ""
    text = str(value)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+", r"\1***REDACTED***", text)
    text = re.sub(r"(?i)(sk-[A-Za-z0-9_\-]{6,})", "sk-***REDACTED***", text)
    text = re.sub(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,&]+", r"\1***REDACTED***", text)
    return text[:5000]


def redact(value):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key = str(k)
            out[key] = "***REDACTED***" if key.lower() in SENSITIVE_KEYS else redact(v)
        return out
    if isinstance(value, list):
        return [redact(x) for x in value]
    if isinstance(value, tuple):
        return tuple(redact(x) for x in value)
    if isinstance(value, str):
        return redact_text(value)
    return value
