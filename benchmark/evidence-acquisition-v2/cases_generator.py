"""v2 synthetic case generator with user-level split and auditable case types.

Case types (spec A2 auditable case checklist):
  execution_gap                  missing execution reports (v1 baseline parity)
  paired_burden                  execution sufficient but baseline/follow-up pair missing
  window_boundary                some slots not yet due (unaskable)
  refusal_unknown                user declines / unknown at high rate
  mnar                          non-random missingness tied to non-completion
  source_revision               corrected / deleted source record
  knowledge_withdrawal          knowledge source withdrawn -> burden pair re-pending
  certificate_invalidation_repair  revision invalidates old certificate; repair prompt
  duplicate_cross_day           duplicate/retry request semantics across day split

Every case carries: user_id (split unit), split (train/test by user), case_type,
execution truth/visible, burden availability, per-slot response uniforms and
latency (shared across strategies, frozen), revision event, knowledge withdrawal
flag. No personal data; synthetic only.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

CASE_TYPES = (
    "execution_gap", "paired_burden", "window_boundary", "refusal_unknown",
    "mnar", "source_revision", "knowledge_withdrawal",
    "certificate_invalidation_repair", "duplicate_cross_day",
)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    import hashlib
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def generate_cases(config: dict, seed: int) -> list[dict]:
    rng = random.Random(seed)
    window = int(config["window_days"])
    weights = config["case_type_weights"]
    pool = []
    for t, w in weights.items():
        pool.extend([t] * w)
    users = int(config["users"])
    per_user = int(config["episodes_per_user"])
    test_fraction = float(config["test_user_fraction"])
    test_user_count = max(1, int(round(users * test_fraction)))
    test_users = set(rng.sample(range(users), test_user_count))
    cases: list[dict] = []
    seq = 0
    for user_id in range(users):
        split = "test" if user_id in test_users else "train"
        for _ in range(per_user):
            case_type = rng.choice(pool)
            case = build_case(case_type, rng, window, seq, user_id, split)
            cases.append(case)
            seq += 1
    return cases


def build_case(case_type: str, rng: random.Random, window: int,
               seq: int, user_id: int, split: str) -> dict:
    truth = [rng.random() < 0.64 for _ in range(window)]
    # Base missingness per slot (independent), then type-specific structure.
    base_missing = {
        "execution_gap": 0.28, "paired_burden": 0.18, "window_boundary": 0.26,
        "refusal_unknown": 0.30, "mnar": 0.20, "source_revision": 0.28,
        "knowledge_withdrawal": 0.24, "certificate_invalidation_repair": 0.26,
        "duplicate_cross_day": 0.26,
    }[case_type]
    visible: list[bool | None] = []
    for slot, fact in enumerate(truth):
        missing = rng.random() < base_missing
        if case_type == "mnar" and not fact and rng.random() < 0.30:
            missing = True  # non-random: non-completion is hidden more often
        visible.append(None if missing else fact)

    # Burden pairing availability: burden question exists on a subset of slots
    # with an already-completed opportunity.
    burden_available = [False] * window
    if case_type == "paired_burden":
        completed = [i for i, v in enumerate(visible) if v is True]
        for slot in rng.sample(completed, min(2, len(completed))):
            burden_available[slot] = True
    elif case_type in ("knowledge_withdrawal", "certificate_invalidation_repair",
                       "duplicate_cross_day"):
        completed = [i for i, v in enumerate(visible) if v is True]
        if completed:
            burden_available[rng.choice(completed)] = True

    # Window boundary: later slots not yet due (unaskable).
    if case_type == "window_boundary":
        due_until = rng.randint(4, window - 1)
        for slot in range(due_until, window):
            visible[slot] = None

    due_slots = [i for i, v in enumerate(visible) if v is None]
    revision_event = "none"
    revision_slot = rng.randrange(window)
    if case_type in ("source_revision", "certificate_invalidation_repair"):
        known = [i for i, v in enumerate(visible) if v is not None]
        if known:
            revision_slot = rng.choice(known)
            revision_event = rng.choices(["corrected", "deleted"], [0.5, 0.5])[0]
    knowledge_withdrawal = case_type == "knowledge_withdrawal"

    case = {
        "case_id": f"v2-{case_type}-{seq:04d}",
        "user_id": user_id,
        "split": split,
        "case_type": case_type,
        "truth": truth,
        "initial_visible": visible,
        "due_slots": due_slots,
        "burden_available": burden_available,
        "response_uniforms": [rng.random() for _ in range(window)],
        "latency_ms": [rng.randint(2500, 5000) for _ in range(window)],
        "random_order": rng.sample(range(window), window),
        "revision_event": revision_event,
        "revision_slot": revision_slot,
        "knowledge_withdrawal": knowledge_withdrawal,
        "day_split": case_type == "duplicate_cross_day",
    }
    return case
