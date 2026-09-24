from __future__ import annotations
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlparse
import httpx
from .config import settings
from .security import resolve_download_url
from .errors import ProcessingError

REDIRECT_CODES = {301, 302, 303, 307, 308}


def download_media(url: str, original_name: str = "media.bin") -> Path:
    current, _ = resolve_download_url(url)
    suffix = Path(urlparse(current).path).suffix or Path(original_name).suffix or ".bin"
    if len(suffix) > 10:
        suffix = ".bin"
    temp = tempfile.NamedTemporaryFile(
        prefix="healthmate-", suffix=suffix, delete=False
    )
    path = Path(temp.name)
    temp.close()
    limit = settings.max_download_mb * 1024 * 1024
    try:
        # Bypass ambient proxies: they could resolve the hostname again, bypassing IP pinning.
        with httpx.Client(
            timeout=settings.request_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            for redirect in range(settings.max_redirects + 1):
                current, address = resolve_download_url(current)
                original = httpx.URL(current)
                pinned = original.copy_with(host=address)
                headers = {"Host": original.netloc.decode("ascii")}
                with client.stream(
                    "GET",
                    pinned,
                    headers=headers,
                    extensions={"sni_hostname": original.host.encode("ascii")},
                ) as response:
                    if response.status_code in REDIRECT_CODES:
                        location = response.headers.get("location")
                        if not location:
                            raise ProcessingError(
                                "invalid_redirect", "媒体重定向缺少 Location"
                            )
                        if redirect >= settings.max_redirects:
                            raise ProcessingError(
                                "too_many_redirects", "媒体重定向次数过多"
                            )
                        current = urljoin(current, location)
                        continue  # The next iteration validates every target before connecting.
                    response.raise_for_status()
                    declared = response.headers.get("Content-Length")
                    if declared and int(declared) > limit:
                        raise ProcessingError(
                            "media_too_large", "媒体下载大小超过配置上限"
                        )
                    total = 0
                    with path.open("wb") as output:
                        for chunk in response.iter_bytes(64 * 1024):
                            total += len(chunk)
                            if total > limit:
                                raise ProcessingError(
                                    "media_too_large", "媒体下载大小超过配置上限"
                                )
                            output.write(chunk)
                    if not total:
                        raise ProcessingError("invalid_media", "媒体为空")
                    return path
        raise ProcessingError("too_many_redirects", "媒体重定向次数过多")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
