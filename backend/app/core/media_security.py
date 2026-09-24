from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from app.core.config import settings


class UnsafeMediaURL(ValueError):
    pass


def validate_media_source_url(value: str) -> str:
    """Validate a client-supplied temporary media URL before exposing it to an AI worker.

    We intentionally accept only HTTPS in production and reject literal loopback/private
    addresses. The local worker performs a second validation after DNS resolution before
    downloading, which protects against DNS rebinding/SSRF-style mistakes.
    """
    url = (value or "").strip()
    if not url:
        raise UnsafeMediaURL("媒体下载地址不能为空")
    p = urlparse(url)
    try:
        p.port
    except ValueError:
        raise UnsafeMediaURL("媒体下载地址端口无效") from None
    if p.username or p.password or p.fragment:
        raise UnsafeMediaURL("媒体下载地址不允许含凭据或 fragment")
    allowed_schemes = {"https"} if settings.is_production else {"http", "https"}
    if p.scheme.lower() not in allowed_schemes:
        raise UnsafeMediaURL("媒体下载地址协议不受支持")
    host = (p.hostname or "").strip().lower()
    if not host:
        raise UnsafeMediaURL("媒体下载地址缺少主机名")
    if host in {"localhost", "0.0.0.0", "::1"} or host.endswith(".local"):
        if settings.is_production:
            raise UnsafeMediaURL("生产环境不允许内网媒体地址")
        return url
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return url
    if settings.is_production and (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
    ):
        raise UnsafeMediaURL("生产环境不允许内网媒体地址")
    return url
