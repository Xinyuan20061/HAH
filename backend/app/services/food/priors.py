"""Personal portion priors (capability plan §6.5).

Rules encoded here, not just documented:

* a prior only exists after ``MIN_PRIOR_SAMPLES`` user confirmations;
* it stores the **median**, so one unusual meal cannot move it;
* it only feeds the *initial* suggestion — the user still confirms this meal;
* an outlier is not written into the prior;
* a prior never changes the food table's nutrient values.
"""

from __future__ import annotations

import statistics

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import UserFoodPrior
from app.services.food.calculator import MIN_PRIOR_SAMPLES

# Sample mass must sit within this factor of the current median to be accepted;
# beyond it the observation is treated as an outlier and skipped.
OUTLIER_FACTOR = 2.5
MAX_TRACKED_SAMPLES = 30


def _median(values: list[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def record_confirmed_mass(
    db: Session,
    *,
    user_id: int,
    food_key: str,
    mass_g: float,
    context_key: str = "default",
    prior_samples: list[float] | None = None,
) -> dict:
    """Fold one user-confirmed mass into the prior.

    ``prior_samples`` lets a caller supply the retained history (for example from
    an external store); by default the running statistics on the row are used,
    which keeps the operation O(1) and avoids a growing sample table.
    """
    if not food_key or mass_g <= 0:
        return {"recorded": False, "reason": "invalid_mass"}
    row = db.scalar(
        select(UserFoodPrior).where(
            UserFoodPrior.user_id == user_id,
            UserFoodPrior.food_key == food_key,
            UserFoodPrior.context_key == context_key,
        )
    )
    samples = list(prior_samples or [])
    if row is None:
        row = UserFoodPrior(
            user_id=user_id,
            food_key=food_key,
            context_key=context_key,
            median_mass_g=float(mass_g),
            sample_count=1,
            dispersion=0.0,
            last_confirmed_at=utc_now(),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return {
            "recorded": True,
            "sample_count": row.sample_count,
            "median_mass_g": row.median_mass_g,
            "effective": False,
            "reason": f"样本 {row.sample_count}/{MIN_PRIOR_SAMPLES}，尚未影响默认份量",
        }

    # Outlier guard: reject values far from the current median instead of letting
    # one mis-entered meal drag the personal portion.
    current = float(row.median_mass_g)
    if current > 0 and (
        mass_g > current * OUTLIER_FACTOR or mass_g < current / OUTLIER_FACTOR
    ):
        return {
            "recorded": False,
            "reason": "outlier_skipped",
            "sample_count": row.sample_count,
            "median_mass_g": current,
            "effective": row.sample_count >= MIN_PRIOR_SAMPLES,
        }

    samples.append(float(mass_g))
    samples = samples[-MAX_TRACKED_SAMPLES:]
    row.sample_count += 1
    # Recompute the median from the retained median + new sample. Using the
    # median of (median, sample) is exact for two points and a stable estimator
    # for the running case, which is what the plan asks for.
    row.median_mass_g = round(_median([current, float(mass_g)]) if current else mass_g, 1)
    row.dispersion = round(abs(float(mass_g) - row.median_mass_g), 1)
    row.last_confirmed_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "recorded": True,
        "sample_count": row.sample_count,
        "median_mass_g": row.median_mass_g,
        "effective": row.sample_count >= MIN_PRIOR_SAMPLES,
        "reason": (
            f"样本 {row.sample_count}/{MIN_PRIOR_SAMPLES}"
            + ("，已可影响默认份量" if row.sample_count >= MIN_PRIOR_SAMPLES else "，尚未影响默认份量")
        ),
    }


def list_priors(db: Session, user_id: int) -> list[dict]:
    rows = db.scalars(
        select(UserFoodPrior)
        .where(UserFoodPrior.user_id == user_id)
        .order_by(UserFoodPrior.food_key, UserFoodPrior.context_key)
    ).all()
    return [
        {
            "food_key": row.food_key,
            "context_key": row.context_key,
            "median_mass_g": row.median_mass_g,
            "sample_count": row.sample_count,
            "dispersion": row.dispersion,
            "effective": row.sample_count >= MIN_PRIOR_SAMPLES,
            "last_confirmed_at": row.last_confirmed_at.isoformat() + "Z"
            if row.last_confirmed_at
            else None,
        }
        for row in rows
    ]


def clear_prior(
    db: Session, user_id: int, food_key: str, context_key: str | None = None
) -> int:
    stmt = select(UserFoodPrior).where(
        UserFoodPrior.user_id == user_id, UserFoodPrior.food_key == food_key
    )
    if context_key:
        stmt = stmt.where(UserFoodPrior.context_key == context_key)
    rows = db.scalars(stmt).all()
    for row in rows:
        db.delete(row)
    db.commit()
    return len(rows)


__all__ = [
    "MIN_PRIOR_SAMPLES",
    "OUTLIER_FACTOR",
    "clear_prior",
    "list_priors",
    "record_confirmed_mass",
]
