from __future__ import annotations
import ipaddress
import socket
from urllib.parse import urlparse
from .config import settings


class UnsafeDownloadURL(RuntimeError):
    pass


def resolve_download_url(url: str) -> tuple[str, str]:
    url = (url or "").strip()
    parsed = urlparse(url)
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        raise UnsafeDownloadURL("invalid media port") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise UnsafeDownloadURL("invalid media URL")
    if parsed.scheme != "https" and not settings.allow_private_media_hosts:
        raise UnsafeDownloadURL("non-HTTPS media URL rejected")
    try:
        infos = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise UnsafeDownloadURL("media host DNS failed") from None
    if not infos:
        raise UnsafeDownloadURL("media host DNS returned no address")
    addresses = [ipaddress.ip_address(info[4][0]) for info in infos]
    if not settings.allow_private_media_hosts and any(
        not ip.is_global or ip.is_multicast or ip.is_reserved for ip in addresses
    ):
        raise UnsafeDownloadURL("private/reserved media host rejected")
    # Pin the validated IP during connection, keeping original Host/SNI for TLS verification.
    return url, str(addresses[0])


def validate_download_url(url: str) -> str:
    return resolve_download_url(url)[0]
