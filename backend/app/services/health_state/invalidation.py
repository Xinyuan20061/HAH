"""Record-change → state invalidation hook (capability plan §4.5).

The state layer's ``invalidate()`` exists to make edits *visible*: a feature row
carrying a stale value must be removed the moment its source record changes, or the
next plan/signal is computed from data the user already corrected.

Defining ``invalidate()`` is not enough — it has to be **called**. This module is the
one place record mutation paths call, so the rule cannot be forgotten at a new call
site:

* invalidation is **best-effort**: the user's record edit has already been committed,
  and a state-layer failure must never turn a successful edit into an error;
* it is **scoped to the domains that changed**, so editing a meal does not throw away
  the motion-metric features;
* it never writes a record and never calls a model.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

logger = logging.getLogger("healthmate.health_state.invalidation")

# Canonical source domains. Callers pass these, never a feature key, so the mapping
# from "what the user changed" to "what became stale" stays in one place
# (`SOURCE_TO_FEATURES`).
SOURCE_CHECKIN = "checkin"
SOURCE_DIET = "diet_record"
SOURCE_EXERCISE = "exercise_record"
SOURCE_GOAL = "goal"
SOURCE_MOTION = "motion_analysis"
SOURCE_SAFETY = "safety_event"

KNOWN_SOURCES = frozenset(
    {
        SOURCE_CHECKIN,
        SOURCE_DIET,
        SOURCE_EXERCISE,
        SOURCE_GOAL,
        SOURCE_MOTION,
        SOURCE_SAFETY,
    }
)


def record_changed(
    db: Session, user_id: int, sources: str | list[str], *, recompute: bool = True
) -> dict:
    """Invalidate the features derived from ``sources`` after a record change.

    Returns the invalidation result (``affected`` / ``deleted_rows`` / ``recomputed``)
    or an ``{"error": ...}`` dict. Never raises: see the module docstring.
    """
    requested = [sources] if isinstance(sources, str) else list(sources)
    unknown = [item for item in requested if item not in KNOWN_SOURCES]
    if unknown:
        # A typo would silently invalidate nothing, which is exactly the stale-value
        # bug this hook exists to prevent. Surface it in the log instead.
        logger.warning(
            "record_changed called with unknown source(s) %s for user=%s", unknown, user_id
        )
        return {"affected": [], "deleted_rows": 0, "recomputed": False, "unknown": unknown}
    try:
        from app.services.health_state import invalidate

        result = invalidate(db, user_id, requested, recompute=recompute)
        # Policy observations are source-versioned independently from the
        # state feature cache.  The shared mutation hook is the safest place to
        # fan out edits/deletes so no diet, exercise or check-in route can leave
        # a stale learned episode eligible.  This is deliberately best-effort:
        # the record mutation has already committed.
        try:
            from sqlalchemy import select
            from app.models import PolicyObservationRef
            from app.services.policy_learning.outbox import invalidate_source
            refs = db.execute(select(PolicyObservationRef.source_type, PolicyObservationRef.source_id).where(PolicyObservationRef.user_id == user_id, PolicyObservationRef.source_type.in_(requested), PolicyObservationRef.valid.is_(True))).all()
            for source_type, source_id in refs:
                invalidate_source(db, user_id, source_type, str(source_id))
            if refs:
                db.commit()
        except Exception:  # policy invalidation must not break record writes
            logger.exception("policy invalidation failed for user=%s sources=%s", user_id, requested)
            db.rollback()
        logger.info(
            "state invalidated user=%s sources=%s affected=%s deleted=%s",
            user_id,
            requested,
            len(result.get("affected", [])),
            result.get("deleted_rows", 0),
        )
        return result
    except Exception:  # noqa: BLE001 - the record edit is already committed
        logger.exception("state invalidation failed for user=%s sources=%s", user_id, requested)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {
            "affected": [],
            "deleted_rows": 0,
            "recomputed": False,
            "error": "invalidation_failed",
        }


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
    "SOURCE_CHECKIN",
    "SOURCE_DIET",
    "SOURCE_EXERCISE",
    "SOURCE_GOAL",
    "SOURCE_MOTION",
    "SOURCE_SAFETY",
    "active_actions_changed",
    "record_changed",
]
