"""ProviderGateway: bounded, idempotent, desensitized calls (spec 8.2/8.3).

- One run may spend at most MAX_CALLS_PER_RUN billable DeepSeek generations.
- Billable calls that time out are NOT blindly retried: the upstream may already
  have succeeded and billed; we prefer reading local state / cache first.
- Every real call writes a desensitized provider_invocations ledger row: never
  the full prompt, image bytes, user key or raw media.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ProviderInvocation

logger = logging.getLogger("healthmate.motion.provider_gateway")

MAX_CALLS_PER_RUN = 4

# V2 reservation lifecycle (spec 9.2): reserve before the external call, flip to
# sent when the request goes out, then a terminal outcome. On restart, rows left
# in `sent` whose upstream result cannot be confirmed become outcome_unknown and
# are NEVER auto-resent (the upstream may already have billed).
RESERVATION_RESERVED = "reserved"
RESERVATION_SENT = "sent"
RESERVATION_SUCCEEDED = "succeeded"
RESERVATION_FAILED = "failed"
RESERVATION_OUTCOME_UNKNOWN = "outcome_unknown"


class ProviderBudgetExceeded(RuntimeError):
    pass


def build_reservation_key(
    *,
    user_id: int,
    evidence_hash: str,
    operation: str,
    model: str,
    prompt_version: str,
    policy_version: str,
    consent_mode: str,
) -> str:
    """Deterministic seven-element unique key (contract section 7)."""
    material = "|".join(
        [
            str(user_id),
            evidence_hash or "",
            operation or "",
            model or "",
            prompt_version or "",
            policy_version or "",
            consent_mode or "",
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:128]


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

    # -- V2 atomic reservation (spec 9.2) -------------------------------------
    # The legacy check_budget/record pair has a count-then-insert race. V2 takes
    # the budget up-front: a unique-key insert blocks a double spend before the
    # external call is ever made.

    def reserve_invocation(
        self,
        *,
        operation: str,
        evidence_hash: str,
        model: str,
        prompt_version: str,
        policy_version: str,
        consent_mode: str,
        request_fingerprint: str,
    ) -> ProviderInvocation:
        """Atomically reserve one billable call.

        Returns the `reserved` row on success. Raises ProviderBudgetExceeded when
        the seven-element key already has a row (another caller won the race, or
        this exact call was already reserved) -- callers MUST read local state /
        cache instead of blindly retrying.
        """
        key = build_reservation_key(
            user_id=self.user_id,
            evidence_hash=evidence_hash,
            operation=operation,
            model=model,
            prompt_version=prompt_version,
            policy_version=policy_version,
            consent_mode=consent_mode,
        )
        row = ProviderInvocation(
            user_id=self.user_id,
            run_id=self.run_id,
            provider=self.provider,
            operation=operation,
            request_fingerprint=request_fingerprint[:128],
            reservation_key=key,
            evidence_hash=(evidence_hash or "")[:128] or None,
            model_name=(model or "")[:80] or None,
            prompt_version=(prompt_version or "")[:40] or None,
            policy_version=(policy_version or "")[:40] or None,
            consent_mode=(consent_mode or "")[:30] or None,
            status=RESERVATION_RESERVED,
            latency_ms=0,
        )
        self.db.add(row)
        try:
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            existing = self.db.scalar(
                select(ProviderInvocation).where(
                    ProviderInvocation.reservation_key == key
                )
            )
            if existing is not None:
                raise ProviderBudgetExceeded(
                    f"{operation} already reserved for this fingerprint"
                ) from None
            raise
        return row

    def mark_sent(self, row: ProviderInvocation) -> None:
        """Flip reserved -> sent when the request leaves for the provider."""
        if row.status != RESERVATION_RESERVED:
            return
        row.status = RESERVATION_SENT
        self.db.flush()

    def mark_outcome(
        self,
        row: ProviderInvocation,
        *,
        outcome: str,
        latency_ms: float = 0,
        tokens: int | None = None,
        provider_request_id: str | None = None,
    ) -> None:
        """sent -> succeeded / failed / outcome_unknown."""
        if outcome not in {
            RESERVATION_SUCCEEDED,
            RESERVATION_FAILED,
            RESERVATION_OUTCOME_UNKNOWN,
        }:
            raise ValueError("invalid reservation outcome")
        row.status = outcome
        row.latency_ms = int(max(0, latency_ms))
        row.token_or_char_count = tokens
        row.provider_request_id = (provider_request_id or "")[:128] or None
        self.db.flush()

    def resolve_orphaned_reservations(self) -> int:
        """On restart, mark rows stuck in `sent` as outcome_unknown.

        The upstream may already have succeeded and billed; we must neither claim
        success nor auto-resent. Polling / timeline expansion / settings page must
        never produce a reservation row (they are read-only).
        """
        rows = self.db.scalars(
            select(ProviderInvocation).where(
                ProviderInvocation.status == RESERVATION_SENT
            )
        )
        n = 0
        for row in rows:
            row.status = RESERVATION_OUTCOME_UNKNOWN
            n += 1
        if n:
            self.db.flush()
        return n


class Timer:
    def __enter__(self) -> "Timer":
        self._start = time.monotonic()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.ms = (time.monotonic() - self._start) * 1000
