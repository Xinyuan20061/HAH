"""Transactional record-change → state and policy-evidence invalidation hook.

The state layer's ``invalidate()`` exists to make edits *visible*: a feature row
carrying a stale value must be removed the moment its source record changes, or the
next plan/signal is computed from data the user already corrected.

Defining ``invalidate()`` is not enough — it has to be **called**. This module is the
one place record mutation paths call, so the rule cannot be forgotten at a new call
site:

* invalidation shares the source record's transaction, so neither the edit nor its
  stale evidence can commit on its own;
* it is **scoped to the domains that changed**, so editing a meal does not throw away
  the motion-metric features;
* it never writes a source record and never calls a model.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import User

logger = logging.getLogger("healthmate.health_state.invalidation")

# Canonical source domains. Callers pass these, never a feature key, so the mapping
# from "what the user changed" to "what became stale" stays in one place
# (`SOURCE_TO_FEATURES`).
SOURCE_CHECKIN = "checkin"
SOURCE_DIET = "diet_record"
SOURCE_EXERCISE = "exercise_record"
SOURCE_PLAN = "plan"
SOURCE_GOAL = "goal"
SOURCE_MOTION = "motion_analysis"
SOURCE_SAFETY = "safety_event"

KNOWN_SOURCES = frozenset(
    {
        SOURCE_CHECKIN,
        SOURCE_DIET,
        SOURCE_EXERCISE,
        SOURCE_PLAN,
        SOURCE_GOAL,
        SOURCE_MOTION,
        SOURCE_SAFETY,
    }
)

POLICY_SOURCE_TYPES = {
    SOURCE_CHECKIN: "checkin",
    SOURCE_DIET: "diet",
    SOURCE_EXERCISE: "exercise",
    SOURCE_PLAN: "plan_task",
}


def record_changed(
    db: Session,
    user_id: int,
    sources: str | list[str],
    *,
    source_id: str | int | None = None,
    source_revision: int | None = None,
    policy_source_type: str | None = None,
    deleted: bool = False,
    recompute: bool = True,
) -> dict:
    """Invalidate the features derived from ``sources`` after a record change.

    Returns the invalidation result (``affected`` / ``deleted_rows`` / ``recomputed``)
    and stages policy source invalidation in the same transaction. The caller owns
    the commit, so a failed invalidation also rolls back the source mutation.
    """
    requested = [sources] if isinstance(sources, str) else list(sources)
    unknown = [item for item in requested if item not in KNOWN_SOURCES]
    if unknown:
        raise ValueError(f"unknown health-state source(s): {unknown}")

    if source_id is not None and (source_revision is None or source_revision < 1):
        raise ValueError("source_revision must be a positive integer when source_id is set")
    if source_id is not None and len(requested) != 1:
        raise ValueError("source identity is only valid for one source domain")

    # A stale/deleted account must not trigger a derived snapshot write with a
    # dangling user_id. This is a harmless no-op for maintenance callers and
    # keeps the foreign-key boundary intact.
    with db.no_autoflush:
        user_exists = db.get(User, user_id) is not None
    if not user_exists:
        return {
            "unknown": [],
            "affected": [],
            "deleted_rows": 0,
            "recomputed": False,
            "missing_user": True,
        }

    # Serialize the source change and its policy-evidence invalidation against
    # certificate issue/consumption. The caller owns this transaction and must
    # roll it back if any later invalidation step fails.
    from app.services.policy_learning.acquisition.service import _fence

    _fence(db, user_id)

    # Sessions run with autoflush disabled; flush the just-edited source before
    # recomputing its derived features or resolving the source revision.
    db.flush()
    from app.services.health_state import invalidate

    result = invalidate(db, user_id, requested, recompute=recompute, commit=False)
    if source_id is not None:
        source_type = policy_source_type or POLICY_SOURCE_TYPES.get(requested[0])
        if not source_type:
            raise ValueError(f"no policy source mapping for {requested[0]}")
        from app.services.policy_learning.outbox import invalidate_source

        result["policy_invalidation"] = invalidate_source(
            db,
            user_id,
            source_type,
            str(source_id),
            source_revision=source_revision,
            deleted=deleted,
        )
    db.flush()
    return result


def active_actions_changed(db: Session, user_id: int) -> dict:
    """Refresh the snapshot after a proposal/experiment changed the active set.

    ``active_actions`` is part of the snapshot contract (it is what stops a second
    proposal for the same action), so it must be refreshed when it changes — but no
    *feature* is stale, so this only recomputes rather than deleting rows.
    """
    try:
        from app.services.health_state import build_snapshot

        build_snapshot(db, user_id)
        return {"recomputed": True}
    except Exception:  # noqa: BLE001
        logger.exception("snapshot refresh failed for user=%s", user_id)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {"recomputed": False, "error": "refresh_failed"}


__all__ = [
    "KNOWN_SOURCES",
    "POLICY_SOURCE_TYPES",
    "SOURCE_CHECKIN",
    "SOURCE_DIET",
    "SOURCE_EXERCISE",
    "SOURCE_PLAN",
    "SOURCE_GOAL",
    "SOURCE_MOTION",
    "SOURCE_SAFETY",
    "active_actions_changed",
    "record_changed",
]
