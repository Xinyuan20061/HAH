from __future__ import annotations

import time
from uuid import uuid4
from urllib.parse import urlparse
import httpx
from . import __version__
from .config import settings


class CloudAPIError(RuntimeError):
    def __init__(self, status: int, path: str):
        super().__init__(f"Cloud API HTTP {status} ({path})")
        self.status = status
        self.retryable = status in {408, 429} or status >= 500


class CloudAPI:
    def __init__(self, capabilities: list[str] | None = None):
        self.base = settings.api_base_url.rstrip("/")
        parsed = urlparse(self.base)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("API_BASE_URL 不允许含凭据、查询或 fragment")
        if parsed.scheme != "https" and not (
            settings.allow_private_media_hosts and parsed.scheme == "http"
        ):
            raise ValueError(
                "API_BASE_URL 必须为 HTTPS；本地开发显式设置 ALLOW_PRIVATE_MEDIA_HOSTS=true"
            )
        self.capabilities = (
            capabilities if capabilities is not None else settings.capability_list
        )
        self.client = httpx.Client(
            timeout=settings.request_timeout_seconds,
            headers={"X-Worker-Token": settings.worker_token},
            follow_redirects=False,
            trust_env=False,
        )

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _post(self, path: str, data: dict):
        for attempt in range(settings.api_max_retries + 1):
            try:
                response = self.client.post(self.base + path, json=data)
                if response.is_success:
                    try:
                        return response.json()
                    except ValueError:
                        raise CloudAPIError(502, path) from None
                error = CloudAPIError(response.status_code, path)
                if not error.retryable or attempt >= settings.api_max_retries:
                    raise error
                try:
                    delay = min(
                        5.0,
                        max(
                            0.1, float(response.headers.get("Retry-After", 2**attempt))
                        ),
                    )
                except ValueError:
                    delay = min(5.0, 2**attempt)
            except httpx.TransportError:
                if attempt >= settings.api_max_retries:
                    raise CloudAPIError(503, path) from None
                delay = min(5.0, 2**attempt)
            time.sleep(delay)
        raise CloudAPIError(503, path)

    def heartbeat(self, gpu_name: str = "", metadata: dict | None = None):
        return self._post(
            "/worker/heartbeat",
            {
                "worker_id": settings.worker_id,
                "name": settings.worker_name,
                "version": __version__,
                "gpu_name": gpu_name,
                "capabilities": self.capabilities,
                "metadata": metadata or {},
            },
        )

    def claim(self):
        # Reuse the same ID during network retries, so a lost response cannot claim another job.
        return self._post(
            "/worker/jobs/claim",
            {
                "worker_id": settings.worker_id,
                "capabilities": self.capabilities,
                "request_id": uuid4().hex,
            },
        ).get("job")

    def progress(self, job_id: int, lease_token: str, progress: int, stage: str):
        return self._post(
            f"/worker/jobs/{job_id}/progress",
            {
                "worker_id": settings.worker_id,
                "lease_token": lease_token,
                "progress": progress,
                "stage": stage,
            },
        )

    def complete(self, job_id: int, lease_token: str, result: dict, metrics: dict):
        return self._post(
            f"/worker/jobs/{job_id}/complete",
            {
                "worker_id": settings.worker_id,
                "lease_token": lease_token,
                "result": result,
                "metrics": metrics,
            },
        )

    def fail(
        self,
        job_id: int,
        lease_token: str,
        error_code: str,
        error_message: str,
        retryable: bool,
    ):
        return self._post(
            f"/worker/jobs/{job_id}/fail",
            {
                "worker_id": settings.worker_id,
                "lease_token": lease_token,
                "error_code": error_code,
                "error_message": error_message[:800],
                "retryable": retryable,
            },
        )

    def close(self):
        self.client.close()
