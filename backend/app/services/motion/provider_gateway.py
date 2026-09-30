"""ProviderGateway: bounded, idempotent, desensitized calls (spec 8.2/8.3).

- One run may spend at most MAX_CALLS_PER_RUN billable DeepSeek generations.
- Billable calls that time out are NOT blindly retried: the upstream may already
  have succeeded and billed; we prefer reading local state / cache first.
- Every real call writes a desensitized provider_invocations ledger row: never
  the full prompt, image bytes, user key or raw media.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy.orm import Session

from app.models import ProviderInvocation

logger = logging.getLogger("healthmate.motion.provider_gateway")

MAX_CALLS_PER_RUN = 4


class ProviderBudgetExceeded(RuntimeError):
    pass


class ProviderGateway:
    def __init__(self, db: Session, *, user_id: int, run_id: int | None, provider: str = "deepseek"):
        self.db = db
        self.user_id = user_id
        self.run_id = run_id
        self.provider = provider

    def _count(self, operation: str) -> int:
        return int(
            self.db.query(ProviderInvocation)
            .filter(
                ProviderInvocation.run_id == self.run_id,
                ProviderInvocation.operation == operation,
            )
            .count()
        )

    def check_budget(self, operation: str) -> None:
        if self.run_id is not None and self._count(operation) >= 1:
            # One visual review + one summary per run by design; repeat attempts
            # must come from cache, never from a blind retry.
            raise ProviderBudgetExceeded(f"{operation} already attempted for this run")

    def record(
        self,
        *,
        operation: str,
        request_fingerprint: str,
        status: str,
        latency_ms: float,
        tokens: int | None = None,
        provider_request_id: str | None = None,
    ) -> None:
        self.db.add(
            ProviderInvocation(
                user_id=self.user_id,
                run_id=self.run_id,
                provider=self.provider,
                operation=operation,
                request_fingerprint=request_fingerprint[:128],
                status=status,
                latency_ms=int(max(0, latency_ms)),
                token_or_char_count=tokens,
                provider_request_id=(provider_request_id or "")[:128] or None,
            )
        )
        self.db.flush()


class Timer:
    def __enter__(self) -> "Timer":
        self._start = time.monotonic()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.ms = (time.monotonic() - self._start) * 1000
