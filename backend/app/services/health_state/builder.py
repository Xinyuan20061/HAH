"""Snapshot builder + persistence + invalidation (capability plan §4.2/§4.5).

Design rules enforced here:

* **Deterministic** — the same inputs produce the same ``snapshot_hash``, so a
  changed hash means the state genuinely changed;
* **Missing is counted, not zeroed** — ``missingness`` records how many days each
  source contributed;
* **No private reasoning** — only values, evidence references and limitations are
  persisted;
* **Incremental** — a change to one source domain invalidates only the features
  that declare that source (``features_for_sources``).
"""

from __future__ import annotations

import hashlib
import json
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import HealthStateFeature, HealthStateSnapshot
from app.services.health_state.constraints import build_constraints
from app.services.health_state.contracts import (
    STATE_VERSION,
    HealthConstraint,
    EvidenceRef,
    HealthStateSnapshot as SnapshotModel,
    StateValue,
)
from app.services.health_state.features import (
    FEATURES_BY_KEY,
    FeatureContext,
    compute_all,
    features_for_sources,
)

DEFAULT_WINDOW_DAYS = 7


def snapshot_hash(values: dict[str, StateValue], window_days: int) -> str:
    """Stable digest over the ordered feature values.

    Only the *value* and its definition version participate: evidence row ids
    change when a record is re-saved, and that must not look like a state change.
    """
    material = json.dumps(
        {
            "window_days": window_days,
            "values": [
                {
                    "key": key,
                    "value": value.value,
                    "feature_version": value.feature_version,
                    "observed_days": value.observed_days,
                }
                for key, value in sorted(values.items())
            ],
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def frozen_state_hash(snapshot: SnapshotModel) -> str:
    """Hash the feature snapshot plus its safety constraints and data gaps.

    The compact display hash remains value-focused; actions that require a
    confirm-time compare-and-swap use this stronger digest so a changed hard
    constraint cannot hide behind unchanged feature values.
    """
    material = {
        "snapshot_hash": snapshot.snapshot_hash,
        "version": snapshot.version,
        "window_days": snapshot.window_days,
        "constraints": sorted(
            (item.model_dump(mode="json") for item in snapshot.constraints),
            key=lambda item: (item["key"], item["severity"], item["source"]),
        ),
        "missingness": dict(sorted(snapshot.missingness.items())),
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def input_hash_for(definition_sources: tuple[str, ...], days: list[dict]) -> str:
    """Digest of the raw inputs a feature read, used for cheap recompute checks."""
    material = json.dumps(
        {"sources": sorted(definition_sources), "days": days},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _missingness(
    days: list[dict], excluded_sources: frozenset[str] = frozenset()
) -> dict[str, int]:
    total = len(days)
    keys = tuple(
        key
        for key in ("checkin", "diet", "exercise", "plan")
        if key not in excluded_sources
    )
    return {
        key: total - sum(1 for row in days if row.get("observed", {}).get(key))
        for key in keys
    }


def _active_actions(
    db: Session, user_id: int, *, include_proposals: bool = True,
    include_experiments: bool = True,
) -> list[str]:
    """Phenomena that are currently open and must not be duplicated."""
    from app.models import AgentActionProposal, AgentMicroExperiment, HealthGoalAdjustment

    out: list[str] = []
    if include_experiments:
        experiment = db.scalar(
            select(AgentMicroExperiment).where(
                AgentMicroExperiment.user_id == user_id,
                AgentMicroExperiment.status == "active",
            )
        )
        if experiment is not None:
            out.append(f"experiment:{experiment.insight_code}")
    if include_proposals:
        pending = db.scalars(
            select(AgentActionProposal)
            .where(
                AgentActionProposal.user_id == user_id,
                AgentActionProposal.status.in_(("pending", "executing")),
            )
            .limit(10)
        ).all()
        out.extend(f"proposal:{row.action_key}" for row in pending)
        adjustments = db.scalars(
            select(HealthGoalAdjustment)
            .where(
                HealthGoalAdjustment.user_id == user_id,
                HealthGoalAdjustment.status == "pending",
            )
            .limit(10)
        ).all()
        out.extend(f"goal_adjustment:{row.metric}" for row in adjustments)
    return sorted(set(out))


def build_snapshot(
    db: Session,
    user_id: int,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    end: date | None = None,
    persist: bool = True,
    commit: bool = True,
    excluded_sources: set[str] | None = None,
) -> SnapshotModel:
    excluded = frozenset(excluded_sources or ())
    ctx = FeatureContext(
        db=db, user_id=user_id, window_days=window_days, end=end,
        excluded_sources=excluded,
    )
    values = compute_all(ctx)
    constraints = build_constraints(db, user_id, ctx, values, excluded_sources=set(excluded))
    digest = snapshot_hash(values, window_days)
    snapshot = SnapshotModel(
        version=STATE_VERSION,
        as_of=utc_now(),
        window_days=window_days,
        values=values,
        constraints=constraints,
        missingness=_missingness(ctx.days, excluded),
        active_actions=(
            [] if {"plan", "experiment"}.intersection(excluded)
            else _active_actions(
                db, user_id,
                include_proposals="plan_proposals" not in excluded,
                include_experiments="experiment" not in excluded,
            )
        ),
        snapshot_hash=digest,
    )
    if persist:
        persist_snapshot(db, user_id, snapshot, ctx.days, commit=commit)
    return snapshot


def persist_snapshot(
    db: Session,
    user_id: int,
    snapshot: SnapshotModel,
    days: list[dict],
    *,
    commit: bool = True,
) -> HealthStateSnapshot:
    """Upsert feature rows and append the frozen snapshot."""
    existing = {
        (row.feature_key, row.window_days, row.feature_version): row
        for row in db.scalars(
            select(HealthStateFeature).where(
                HealthStateFeature.user_id == user_id,
                HealthStateFeature.window_days == snapshot.window_days,
            )
        ).all()
    }
    for key, value in snapshot.sorted_items():
        definition = FEATURES_BY_KEY.get(key)
        sources = definition.sources if definition else ()
        row = existing.get((key, snapshot.window_days, value.feature_version))
        if row is None:
            row = HealthStateFeature(
                user_id=user_id,
                feature_key=key,
                window_days=snapshot.window_days,
                feature_version=value.feature_version,
            )
            db.add(row)
        row.value_numeric = (
            float(value.value)
            if isinstance(value.value, (int, float)) and not isinstance(value.value, bool)
            else None
        )
        row.value_text = (
            str(value.value)
            if value.value is not None
            and not isinstance(value.value, (int, float))
            else ("" if value.value is None else str(value.value))
        )
        row.unit = value.unit
        row.evidence_type = value.evidence_type
        row.confidence_level = value.confidence_level
        row.observed_days = value.observed_days
        row.input_hash = input_hash_for(sources, days)
        row.evidence_json = json.dumps(
            [item.model_dump(mode="json") for item in value.evidence],
            ensure_ascii=False,
        )
        row.limitations_json = json.dumps(value.limitations, ensure_ascii=False)
        row.valid_from = value.valid_from
        row.valid_until = value.valid_until
    record = HealthStateSnapshot(
        user_id=user_id,
        state_version=snapshot.version,
        as_of=snapshot.as_of,
        window_days=snapshot.window_days,
        snapshot_hash=snapshot.snapshot_hash,
        values_json=json.dumps(
            {
                key: value.model_dump(mode="json")
                for key, value in snapshot.sorted_items()
            },
            ensure_ascii=False,
        ),
        constraints_json=json.dumps(
            [item.model_dump(mode="json") for item in snapshot.constraints],
            ensure_ascii=False,
        ),
        missingness_json=json.dumps(snapshot.missingness, ensure_ascii=False),
        active_actions_json=json.dumps(snapshot.active_actions, ensure_ascii=False),
    )
    db.add(record)
    db.flush()
    if commit:
        db.commit()
        db.refresh(record)
    return record


def latest_snapshot(db: Session, user_id: int) -> HealthStateSnapshot | None:
    return db.scalar(
        select(HealthStateSnapshot)
        .where(HealthStateSnapshot.user_id == user_id)
        .order_by(HealthStateSnapshot.as_of.desc(), HealthStateSnapshot.id.desc())
        .limit(1)
    )


def load_snapshot(row: HealthStateSnapshot) -> SnapshotModel:
    """Rebuild the typed snapshot from its persisted form."""

    def _loads(raw: str, fallback):
        try:
            parsed = json.loads(raw or "")
        except (TypeError, ValueError):
            return fallback
        return parsed if isinstance(parsed, type(fallback)) else fallback

    values = {
        key: StateValue.model_validate(payload)
        for key, payload in (_loads(row.values_json, {}) or {}).items()
    }
    return SnapshotModel(
        version=row.state_version,
        as_of=row.as_of,
        window_days=row.window_days,
        values=values,
        constraints=[
            HealthConstraint.model_validate(payload)
            for payload in (_loads(row.constraints_json, []) or [])
        ],
        missingness=_loads(row.missingness_json, {}) or {},
        active_actions=_loads(row.active_actions_json, []) or [],
        snapshot_hash=row.snapshot_hash,
    )


def invalidate(
    db: Session,
    user_id: int,
    sources: list[str],
    *,
    recompute: bool = True,
    commit: bool = True,
) -> dict:
    """Mark affected features stale and (optionally) recompute them (§4.5).

    Deleting the affected rows is the invalidation: the next read recomputes from
    the records, so a stale value can never be served after an edit. The return
    value lists exactly what was touched, which is what the tests assert on.
    """
    affected = features_for_sources(sources)
    if not affected:
        return {"affected": [], "deleted_rows": 0, "recomputed": False}
    rows = db.scalars(
        select(HealthStateFeature).where(
            HealthStateFeature.user_id == user_id,
            HealthStateFeature.feature_key.in_(affected),
        )
    ).all()
    for row in rows:
        db.delete(row)
    db.flush()
    result = {"affected": affected, "deleted_rows": len(rows), "recomputed": False}
    if recompute:
        build_snapshot(db, user_id, commit=False)
        result["recomputed"] = True
    db.flush()
    if commit:
        db.commit()
    return result


def feature_history(
    db: Session, user_id: int, key: str, *, limit: int = 30
) -> list[dict]:
    """Recent snapshot values for one feature (no raw media, no prompts)."""
    rows = db.scalars(
        select(HealthStateSnapshot)
        .where(HealthStateSnapshot.user_id == user_id)
        .order_by(HealthStateSnapshot.as_of.desc(), HealthStateSnapshot.id.desc())
        .limit(limit)
    ).all()
    out: list[dict] = []
    for row in rows:
        try:
            values = json.loads(row.values_json or "{}")
        except (TypeError, ValueError):
            continue
        payload = values.get(key)
        if payload is None:
            continue
        out.append(
            {
                "as_of": row.as_of.isoformat() + "Z",
                "snapshot_hash": row.snapshot_hash,
                "value": payload.get("value"),
                "unit": payload.get("unit"),
                "confidence_level": payload.get("confidence_level"),
                "observed_days": payload.get("observed_days"),
                "feature_version": payload.get("feature_version"),
                "limitations": payload.get("limitations") or [],
            }
        )
    return out


__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "build_snapshot",
    "feature_history",
    "features_for_sources",
    "invalidate",
    "latest_snapshot",
    "load_snapshot",
    "persist_snapshot",
    "snapshot_hash",
]
