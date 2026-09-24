from urllib.parse import urlparse
import ipaddress
import socket
from app.core.config import settings


def resolve_ai_host(url: str) -> str:
    parsed = urlparse(url)
    try:
        infos = socket.getaddrinfo(
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except (socket.gaierror, ValueError):
        raise ValueError("AI Base URL DNS 解析失败") from None
    if not infos:
        raise ValueError("AI Base URL DNS 无可用地址")
    # getaddrinfo may list IPv6 first, but most container runtimes have no working
    # IPv6 egress. Prefer global IPv4 addresses; fall back to IPv6 only if needed.
    raw = [info[4][0] for info in infos if info[4][0]]
    ipv4 = [ip for ip in raw if ":" not in ip]
    ipv6 = [ip for ip in raw if ":" in ip]
    chosen = ipv4[0] if ipv4 else (ipv6[0] if ipv6 else "")
    if not chosen:
        raise ValueError("AI Base URL DNS 无可用地址")
    address = ipaddress.ip_address(chosen)
    if settings.is_production and (
        not address.is_global or address.is_multicast or address.is_reserved
    ):
        raise ValueError("生产环境不允许把 API Key 发送到内网/保留地址")
    return str(address)


def validate_ai_base_url(value: str) -> str:
    url = (value or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in (
        {"https"} if settings.is_production else {"http", "https"}
    ):
        raise ValueError("AI Base URL 必须使用 HTTPS（本地开发允许 HTTP）")
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("AI Base URL 不允许缺少主机名或包含凭据/查询/fragment")
    if settings.is_production:
        resolve_ai_host(url)
    return url
