"""Immutable response-model snapshot (spec A3, response_model.py).

A ResponseModelSnapshot is an append-only, versioned description of the
declared/calibrated distribution of user answers used by the planner to order
questions and estimate cost. It NEVER changes the exact oracle, the frozen
contract, hard budgets or safety blocking; it only affects question order and
estimated cost. With no consented event log it stays in ``prior_only`` state
(sample_count=0) and is explicitly not a real-world constant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ResponseModelSnapshot:
    schema_version: str = "response-model-v2"
    kind: str = "declared_prior_or_consented_calibration"
    version: str = "declared-common-response-v2"
    population_scope: str = "session_duration"
    sample_count: int = 0
    probabilities: dict = field(default_factory=lambda: {
        "answered": 0.75, "unknown": 0.10, "declined": 0.05, "no_response": 0.10,
    })
    latency_ms: dict = field(default_factory=lambda: {"p50": 4000, "p95": None})
    calibration_state: str = "prior_only"
    created_at: str = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if self.sample_count < 0:
            raise ValueError("INVALID_SAMPLE_COUNT")
        if self.calibration_state not in {"prior_only", "calibrated", "shrunk_to_prior"}:
            raise ValueError("INVALID_CALIBRATION_STATE")
        total = sum(self.probabilities.values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError("INVALID_RESPONSE_PROBABILITIES")

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "version": self.version,
            "population_scope": self.population_scope,
            "sample_count": self.sample_count,
            "probabilities": dict(self.probabilities),
            "latency_ms": dict(self.latency_ms),
            "calibration_state": self.calibration_state,
            "created_at": self.created_at,
        }


# Product default: declared prior, no consented calibration yet. The 0.75
# answered rate is an engineering initial value, NOT a measured user constant.
DEFAULT_RESPONSE_MODEL = ResponseModelSnapshot()
