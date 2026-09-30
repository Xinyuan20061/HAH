# -*- coding: utf-8 -*-
"""Motion catalog accessor (contract §8).

Single read-side facade over the generated catalog_data.py. F/C/G packages and
the motion-capabilities endpoint import from here; they must not reach into the
generated data module directly. No model / network calls.
"""
from __future__ import annotations

from typing import Any, Optional

from . import catalog_data

SIX_LEGACY_IDS = frozenset(
    {"squat", "pushup", "lunge", "leg_abduction", "arm_abduction", "arm_vw"}
)


def catalog_version() -> str:
    """Return the shared catalog version string."""
    return catalog_data.CATALOG_VERSION


def list_capabilities() -> list[dict[str, Any]]:
    """Return id / name_zh / aliases / capabilities for every action.

    Backs GET /fitness/motion-capabilities (manual selection + capability notes).
    """
    return [
        {
            "id": a["id"],
            "name_zh": a["name_zh"],
            "aliases": list(a["aliases"]),
            "capabilities": dict(a["capabilities"]),
        }
        for a in catalog_data.ACTIONS
    ]


def get_action(canonical_id: str) -> Optional[dict[str, Any]]:
    """Return the full action entry, or None when the id is unknown."""
    for a in catalog_data.ACTIONS:
        if a["id"] == canonical_id:
            return a
    return None


def map_kinetics_label(label: str) -> Optional[str]:
    """Map a raw Kinetics-400 label to a canonical action id.

    Returns None when the catalog has no exact mapping (the raw label is then
    kept as visual reference, per spec §6.2 — never force-mapped to a legacy
    six-class action).
    """
    if not label:
        return None
    return catalog_data.KINETICS_TO_ID.get(label.strip())


def knowledge(canonical_id: str) -> list[str]:
    """Return the knowledge-entry keys registered for an action (hooks for C)."""
    a = get_action(canonical_id)
    return list(a["knowledge_keys"]) if a else []
