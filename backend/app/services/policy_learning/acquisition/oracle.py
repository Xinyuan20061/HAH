"""Pure, exact completeness proofs for frozen execution endpoints."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from typing import Any


@dataclass(frozen=True)
class ExecutionProof:
    label: int | None
    minimum_completed: int
    lower_completed: int
    upper_completed: int
    unknown_slots: tuple[int, ...]
    witness_slots: tuple[int, ...]
    method: str = "exact_execution_bounds_v1"


def execution_proof(values: tuple[bool | None, ...], target: float) -> ExecutionProof:
    if not 1 <= len(values) <= 28:
        raise ValueError("INVALID_EXECUTION_WINDOW")
    if isinstance(target, bool) or not isinstance(target, (int, float)) or not math.isfinite(target) or not 0 < target <= 1:
        raise ValueError("INVALID_EXECUTION_TARGET")
    if any(value is not None and type(value) is not bool for value in values):
        raise ValueError("INVALID_EXECUTION_VALUE")
    n = len(values)
    required = next(k for k in range(n + 1) if k / n >= target)
    yes = tuple(i for i, value in enumerate(values) if value is True)
    no = tuple(i for i, value in enumerate(values) if value is False)
    unknown = tuple(i for i, value in enumerate(values) if value is None)
    low, high = len(yes), len(yes) + len(unknown)
    if low / n >= target:
        label, witness = 1, yes[:required]
    elif high / n < target:
        label, witness = 0, no[: n - required + 1]
    else:
        label, witness = None, ()
    return ExecutionProof(label, required, low, high, unknown, witness)


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def proof_dict(proof: ExecutionProof) -> dict[str, Any]:
    return asdict(proof)
