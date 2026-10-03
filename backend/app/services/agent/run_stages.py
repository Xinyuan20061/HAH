"""Durable agent-run stage ledger and per-turn model budget (spec §8.2/§8.6).

Two responsibilities, both small and side-effect free:

* ``RunRecorder`` persists stage transitions so a crashed, cancelled or retried
  turn is inspectable instead of invisible;
* ``TurnBudget`` counts provider calls and refuses to exceed the per-turn cap,
  degrading safely instead of silently spending more.
"""

from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import HealthAgentRun, HealthAgentRunStage

# Stage keys (spec §8.2): router / worker:<id> / decision / action.
STAGE_ROUTER = "router"
STAGE_DECISION = "decision"
STAGE_ACTION = "action"

STAGE_QUEUED = "queued"
STAGE_RUNNING = "running"
STAGE_COMPLETED = "completed"
STAGE_FAILED = "failed"
STAGE_CANCELLED = "cancelled"
STAGE_SKIPPED = "skipped"


def worker_stage(worker_id: str) -> str:
    return f"worker:{worker_id}"


class RunRecorder:
    """Persist stage rows for one agent run.

    ``start_stage`` opens a row (committing it immediately so polling sees real
    progress), ``finish_stage`` closes it with a status and a desensitized trace.
    """

    def __init__(self, db: Session, run: HealthAgentRun, *, max_attempts: int = 2):
        self.db = db
        self.run = run
        self.max_attempts = max(1, max_attempts)
        self._open: dict[str, HealthAgentRunStage] = {}

    def _attempt_for(self, stage_key: str) -> int:
        existing = self.db.scalar(
            select(HealthAgentRunStage)
            .where(
                HealthAgentRunStage.run_id == self.run.id,
                HealthAgentRunStage.stage_key == stage_key,
            )
            .order_by(HealthAgentRunStage.attempt.desc())
            .limit(1)
        )
        return (existing.attempt + 1) if existing else 1

    def start_stage(self, stage_key: str, *, provider: str = "") -> HealthAgentRunStage:
        row = HealthAgentRunStage(
            run_id=self.run.id,
            stage_key=stage_key,
            status=STAGE_RUNNING,
            attempt=self._attempt_for(stage_key),
            provider=provider[:60],
            started_at=utc_now(),
        )
        self.db.add(row)
        self.db.commit()
        self._open[stage_key] = row
        return row

    def finish_stage(
        self,
        stage_key: str,
        *,
        status: str = STAGE_COMPLETED,
        provider: str = "",
        error_code: str | None = None,
        trace: dict[str, Any] | None = None,
    ) -> HealthAgentRunStage | None:
        row = self._open.pop(stage_key, None)
        if row is None:
            row = HealthAgentRunStage(
                run_id=self.run.id,
                stage_key=stage_key,
                attempt=self._attempt_for(stage_key),
                started_at=utc_now(),
            )
            self.db.add(row)
        row.status = status
        row.finished_at = utc_now()
        if provider:
            row.provider = provider[:60]
        if error_code:
            row.error_code = error_code[:80]
        if trace is not None:
            row.trace_json = json.dumps(trace, ensure_ascii=False, default=str)[:8000]
        self.db.add(row)
        self.db.commit()
        return row

    def fail_open_stages(self, error_code: str) -> None:
        for stage_key in list(self._open):
            self.finish_stage(
                stage_key, status=STAGE_FAILED, error_code=error_code
            )

    def cancel_open_stages(self) -> None:
        for stage_key in list(self._open):
            self.finish_stage(stage_key, status=STAGE_CANCELLED)

    def stage_views(self) -> list[dict[str, Any]]:
        rows = self.db.scalars(
            select(HealthAgentRunStage)
            .where(HealthAgentRunStage.run_id == self.run.id)
            .order_by(HealthAgentRunStage.id)
        ).all()
        out = []
        for row in rows:
            try:
                trace = json.loads(row.trace_json or "{}")
            except (TypeError, ValueError):
                trace = {}
            out.append(
                {
                    "stage_key": row.stage_key,
                    "status": row.status,
                    "attempt": row.attempt,
                    "provider": row.provider,
                    "started_at": row.started_at.isoformat() + "Z"
                    if row.started_at
                    else None,
                    "finished_at": row.finished_at.isoformat() + "Z"
                    if row.finished_at
                    else None,
                    "error_code": row.error_code,
                    "trace": trace,
                }
            )
        return out


class TurnBudget:
    """Per-turn provider-call budget (capability plan §13.5 / spec §8.6).

    Published table: 0 for a safety short circuit, 2 for a single-domain question,
    5 for cross-domain collaboration. Exceeding a cap degrades safely rather than
    calling the provider again.
    """

    SIMPLE_TASK_MAX_CALLS = 2
    CROSS_DOMAIN_MAX_CALLS = 5

    def __init__(self, max_calls: int):
        self.max_calls = max(0, int(max_calls))
        self.started = time.perf_counter()
        self.used = 0

    @classmethod
    def for_task(cls, *, blocked: bool = False, worker_count: int = 1) -> "TurnBudget":
        """Budget derived from the task's real breadth.

        A single-domain turn gets the simple-task cap of 2; a turn that actually
        fans out to more than one domain worker gets the cross-domain cap of 5.
        """
        if blocked:
            return cls(0)
        return cls(cls.SIMPLE_TASK_MAX_CALLS if worker_count <= 1 else cls.CROSS_DOMAIN_MAX_CALLS)

    def allow(self, *, needed: int = 1) -> bool:
        return self.used + needed <= self.max_calls

    def exhausted(self) -> bool:
        return not self.allow()

    def charge(self, calls: int = 1) -> None:
        self.used += max(0, int(calls))

    @property
    def scope(self) -> str:
        if self.max_calls == 0:
            return "blocked"
        return "simple" if self.max_calls <= self.SIMPLE_TASK_MAX_CALLS else "cross_domain"

    @property
    def elapsed_ms(self) -> int:
        return max(0, int((time.perf_counter() - self.started) * 1000))

    def trace(self) -> dict[str, Any]:
        return {
            "max_model_calls": self.max_calls,
            "used_model_calls": self.used,
            "scope": self.scope,
            "elapsed_ms": self.elapsed_ms,
        }
