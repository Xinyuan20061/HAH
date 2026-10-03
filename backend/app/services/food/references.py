"""Audited food-reference lookup (capability plan §6.4).

Matching is deliberately conservative:

* exact ``food_key`` wins, then an exact ``name_zh``, then a *reviewed* alias;
* a fuzzy guess is never made — an unmapped label stays unmapped so the calculator
  reports it instead of borrowing another food's numbers.

The table is seeded once (idempotent) from ``seed_data`` so a fresh environment can
run the deterministic path, and a reviewer can correct individual rows later
without any code change.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FoodReference
from app.services.food.seed_data import (
    DEFAULT_COOKING_ADJUSTMENTS,
    SEED_ALIASES,
    SEED_FOODS,
    SEED_SOURCE_ID,
    SEED_SOURCE_NOTE,
)

TABLE_VERSION = "food-table-1.0.0"


def ensure_seed_table(db: Session) -> int:
    """Insert any missing seed rows. Idempotent; never overwrites reviewed rows."""
    existing = set(db.execute(select(FoodReference.food_key)).scalars().all())
    created = 0
    for (
        food_key,
        name_zh,
        group,
        calories,
        protein,
        carbs,
        fat,
        fiber,
        density,
    ) in SEED_FOODS:
        if food_key in existing:
            continue
        import json

        db.add(
            FoodReference(
                food_key=food_key,
                name_zh=name_zh,
                food_group=group,
                calories_per_100g=calories,
                protein_per_100g=protein,
                carbs_per_100g=carbs,
                fat_per_100g=fat,
                fiber_per_100g=fiber,
                density_g_per_ml=density,
                aliases_json=json.dumps(
                    list(SEED_ALIASES.get(food_key, ())), ensure_ascii=False
                ),
                cooking_adjustments_json=json.dumps(
                    DEFAULT_COOKING_ADJUSTMENTS, ensure_ascii=False
                ),
                source_id=SEED_SOURCE_ID,
                source_note=SEED_SOURCE_NOTE,
                reviewed_at=None,
                active=True,
            )
        )
        created += 1
    if created:
        db.commit()
    return created


def table_status(db: Session) -> dict:
    rows = db.scalars(select(FoodReference)).all()
    reviewed = [row for row in rows if row.reviewed_at is not None]
    return {
        "table_version": TABLE_VERSION,
        "entries": len(rows),
        "reviewed_entries": len(reviewed),
        "reviewed": bool(rows) and len(reviewed) == len(rows),
        "source_ids": sorted({row.source_id for row in rows if row.source_id}),
        "policy": (
            "数值必须来自审核食物库；未复核条目标记为 seed_unreviewed，"
            "产品文案只能称「粗略草稿」。"
        ),
    }


def _normalise(value: str) -> str:
    return (
        (value or "")
        .strip()
        .lower()
        .replace("（", "(")
        .replace("）", ")")
        .replace(" ", "")
    )


def match_reference(db: Session, label: str) -> FoodReference | None:
    """Resolve a food label to a reference row, or ``None`` (never a guess)."""
    if not label:
        return None
    wanted = _normalise(label)
    rows = db.scalars(select(FoodReference).where(FoodReference.active.is_(True))).all()

    # 1. Exact food_key.
    for row in rows:
        if _normalise(row.food_key) == wanted:
            return row
    # 2. Exact Chinese name.
    for row in rows:
        if _normalise(row.name_zh) == wanted:
            return row
    # 3. Reviewed alias, exact match only (substring matching produced the
    #    "chicken" -> "chicken liver" class of silent error).
    for row in rows:
        for alias in row.aliases:
            if _normalise(alias) == wanted:
                return row
    return None


def resolve_cooking_adjustment(
    reference: FoodReference, cooking: str
) -> dict | None:
    """Look up the oil/sugar adjustment for a cooking method.

    The row's own map wins; the shared default is the fallback. An unknown method
    returns ``None`` so the caller reports "no adjustment applied" instead of
    silently adding zero oil as if the dish were steamed.
    """
    wanted = _normalise(cooking)
    for key, payload in (reference.cooking_adjustments or {}).items():
        if _normalise(key) == wanted and isinstance(payload, dict):
            return payload
        if isinstance(payload, dict) and _normalise(str(payload.get("label") or "")) == wanted:
            return payload
    for key, payload in DEFAULT_COOKING_ADJUSTMENTS.items():
        if _normalise(key) == wanted or _normalise(payload.get("label", "")) == wanted:
            return payload
    return None


def review_reference(
    db: Session, food_key: str, *, source_id: str, source_note: str
) -> FoodReference | None:
    """Mark one row as reviewed. Requires a real source, not a free-text claim."""
    row = db.scalar(select(FoodReference).where(FoodReference.food_key == food_key))
    if row is None:
        return None
    if not source_id or source_id == SEED_SOURCE_ID:
        raise ValueError("复核必须有真实的来源 id，不能沿用 seed_unreviewed")
    row.source_id = source_id[:80]
    row.source_note = source_note[:300]
    row.reviewed_at = date.today()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
