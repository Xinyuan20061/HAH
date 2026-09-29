from __future__ import annotations

import hashlib
import threading
import time
from collections import defaultdict, deque

from fastapi import Request

from app.core.config import settings


class InMemoryRateLimiter:
    """Best-effort per-process limiter for costly public endpoints.

    Cloud deployments with multiple replicas should additionally use a gateway or
    shared Redis limiter. Raw tokens are never stored in bucket keys.
    """

    def __init__(self):
        self._buckets: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    @staticmethod
    def _group(path: str) -> tuple[str, int] | None:
        groups = [
            ("/api/v1/worker/", "worker", settings.rate_limit_worker_per_minute),
            ("/api/v1/auth/", "auth", settings.rate_limit_auth_per_minute),
            ("/api/v1/chat", "ai", settings.rate_limit_ai_per_minute),
            ("/api/v1/agent/", "ai", settings.rate_limit_ai_per_minute),
            ("/api/v1/harness/voice/", "ai", settings.rate_limit_ai_per_minute),
            ("/api/v1/users/me/ai-config/voice-test", "ai", settings.rate_limit_ai_per_minute),
            ("/api/v1/vision/food-jobs", "ai", settings.rate_limit_ai_per_minute),
            ("/api/v1/vision/", "media", settings.rate_limit_media_per_minute),
            ("/api/v1/media/", "media", settings.rate_limit_media_per_minute),
        ]
        for prefix, name, limit in groups:
            if path.startswith(prefix):
                return name, limit
        return None

    @staticmethod
    def _identity(request: Request) -> str:
        credential = (
            request.headers.get("authorization")
            or request.headers.get("x-worker-token")
            or (request.client.host if request.client else "unknown")
        )
        return hashlib.sha256(credential.encode("utf-8")).hexdigest()[:24]

    def check(self, request: Request) -> int | None:
        if not settings.rate_limit_enabled:
            return None
        matched = self._group(request.url.path)
        if not matched:
            return None
        group, limit = matched
        now = time.monotonic()
        window = 60.0
        key = (group, self._identity(request))
        with self._lock:
            bucket = self._buckets[key]
            while bucket and now - bucket[0] >= window:
                bucket.popleft()
            if len(bucket) >= limit:
                return max(1, int(window - (now - bucket[0])))
            bucket.append(now)
        return None


limiter = InMemoryRateLimiter()
