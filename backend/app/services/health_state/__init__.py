"""Personal Health State Engine (capability plan §4).

One versioned, recomputable view of the user's health state, built from records
and evidence rather than from a prompt that concatenates dictionaries.

    from app.services.health_state import build_snapshot, invalidate

    snapshot = build_snapshot(db, user.id, window_days=7)
    snapshot.numeric("sleep_debt_7d")
    snapshot.block_keys()          # hard constraints that forbid auto-planning
    invalidate(db, user.id, ["diet_record"])   # after a diet edit
"""

from app.services.health_state.builder import (  # noqa: F401
    DEFAULT_WINDOW_DAYS,
    build_snapshot,
    feature_history,
    frozen_state_hash,
    invalidate,
    latest_snapshot,
    load_snapshot,
    persist_snapshot,
    snapshot_hash,
)
from app.services.health_state.constraints import build_constraints  # noqa: F401
from app.services.health_state.contracts import (  # noqa: F401
    STATE_VERSION,
    EvidenceRef,
    HealthConstraint,
    HealthStateSnapshot,
    StateValue,
    confidence_from_coverage,
)
from app.services.health_state.features import (  # noqa: F401
    FEATURES,
    FEATURES_BY_KEY,
    FEATURE_KEYS,
    FEATURE_TITLES,
    SOURCE_TO_FEATURES,
    FeatureContext,
    FeatureDefinition,
    compute_all,
    display_titles,
    feature_title,
    features_for_sources,
)

__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "STATE_VERSION",
    "EvidenceRef",
    "FEATURES",
    "FEATURES_BY_KEY",
    "FEATURE_KEYS",
    "FEATURE_TITLES",
    "SOURCE_TO_FEATURES",
    "FeatureContext",
    "FeatureDefinition",
    "HealthConstraint",
    "HealthStateSnapshot",
    "StateValue",
    "build_constraints",
    "build_snapshot",
    "compute_all",
    "confidence_from_coverage",
    "display_titles",
    "feature_history",
    "frozen_state_hash",
    "feature_title",
    "features_for_sources",
    "invalidate",
    "latest_snapshot",
    "load_snapshot",
    "persist_snapshot",
    "snapshot_hash",
]
