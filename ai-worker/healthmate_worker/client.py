from __future__ import annotations

import time
from uuid import uuid4
from urllib.parse import urlparse
import httpx
from . import __version__
from .config import settings


class CloudAPIError(RuntimeError):
    """Raised on a non-2xx cloud response.

    Carries only the SAFE fields the server returns (code / field_path /
    request_id). The raw response body is never stored nor stringified, so
    secrets or payloads cannot leak into logs.
    """

    def __init__(
        self,
        status: int,
        path: str,
        *,
        code: str | None = None,
        field_path: str | None = None,
        request_id: str | None = None,
        retryable: bool | None = None,
    ):
        super().__init__(f"Cloud API HTTP {status} ({path})")
        self.status = status
        self.code = code
        self.field_path = field_path
        self.request_id = request_id
        # Permanent client-side / payload errors must never be retried: resending
        # the same invalid receipt would just 422 again once the lease expires.
        if retryable is not None:
            self.retryable = retryable
        elif status in {422, 413, 409}:
            self.retryable = False
        else:
            self.retryable = status in {408, 429} or status >= 500

    @property
    def is_lease_conflict(self) -> bool:
        """409: the lease was lost / taken by another worker. Handle separately
        from payload errors (do not re-attempt this job)."""
        return self.status == 409

    @property
    def is_permanent_payload_error(self) -> bool:
        """422 schema invalid / 413 too large: the same payload will always fail."""
        return self.status in {422, 413}


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

    def _error_from_response(self, response: httpx.Response, path: str) -> CloudAPIError:
        """Extract safe diagnostic fields from a non-2xx body.

        The body itself is never stored on the exception, so a malformed or
        secret-bearing payload cannot leak into worker logs.
        """
        code = field_path = request_id = None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            if isinstance(payload.get("code"), str):
                code = payload["code"]
            if isinstance(payload.get("request_id"), str):
                request_id = payload["request_id"]
            details = payload.get("details")
            if isinstance(details, dict) and isinstance(details.get("field_path"), str):
                field_path = details["field_path"]
        return CloudAPIError(
            response.status_code,
            path,
            code=code,
            field_path=field_path,
            request_id=request_id,
        )

    def _post(self, path: str, data: dict):
        for attempt in range(settings.api_max_retries + 1):
            try:
                response = self.client.post(self.base + path, json=data)
                if response.is_success:
                    try:
                        return response.json()
                    except ValueError:
                        raise CloudAPIError(502, path) from None
                error = self._error_from_response(response, path)
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

    def request_preview_upload_urls(self, job_id: int, *, frame_ids: list[str], asset_prefix: str):
        """Ask the backend for signed per-frame upload URLs (worker side, byte chain).

        Returns ``{"uploads": [{frame_id, asset_id, upload_url}, ...]}``. The
        backend binds each URL to user_id/run_id/asset_prefix/exp; the worker
        cannot pick an arbitrary external URL.
        """
        return self._post(
            f"/worker/jobs/{job_id}/preview-upload-urls",
            {
                "worker_id": settings.worker_id,
                "job_id": job_id,
                "frame_ids": list(frame_ids),
                "asset_prefix": asset_prefix,
            },
        )

    def put_preview(self, upload_url: str, body: bytes) -> None:
        """PUT a rendered JPEG to a backend-minted signed upload URL.

        ``upload_url`` is the path+query returned by request_preview_upload_urls;
        it already carries the signature. Uses the same worker-token client as
        POST /complete. Raises CloudAPIError on non-2xx.
        """
        for attempt in range(settings.api_max_retries + 1):
            try:
                response = self.client.put(
                    self.base + upload_url,
                    content=body,
                    headers={"Content-Type": "image/jpeg"},
                )
                if response.is_success:
                    return
                error = self._error_from_response(response, upload_url)
                if not error.retryable or attempt >= settings.api_max_retries:
                    raise error
                time.sleep(min(5.0, 2 ** attempt))
            except httpx.TransportError:
                if attempt >= settings.api_max_retries:
                    raise CloudAPIError(503, upload_url) from None
                time.sleep(min(5.0, 2 ** attempt))
        raise CloudAPIError(503, upload_url)

    def close(self):
        self.client.close()
