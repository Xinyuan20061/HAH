"""Interactive Food 2.0 (capability plan §6).

Flow: visual draft → largest-uncertainty questions (≤2) → deterministic
calculation from the audited table → personal portion prior → editable draft.

The design rule that matters: a vision model maps visible items to ``food_key``
candidates, and *this package* computes the nutrients. A model's free-text number
never becomes the stored value.
"""

from app.services.food.calculator import (  # noqa: F401
    MIN_PRIOR_SAMPLES,
    FoodCalculation,
    ItemCalculation,
    NutrientTotal,
    calculate,
    personal_prior_mass,
)
from app.services.food.clarifications import (  # noqa: F401
    MAX_QUESTIONS,
    ClarificationOption,
    ClarificationPlan,
    ClarificationQuestion,
    persist_questions,
    propose_questions,
)
from app.services.food.priors import (  # noqa: F401
    clear_prior,
    list_priors,
    record_confirmed_mass,
)
from app.services.food.references import (  # noqa: F401
    TABLE_VERSION,
    ensure_seed_table,
    match_reference,
    resolve_cooking_adjustment,
    review_reference,
    table_status,
)

__all__ = [
    "MAX_QUESTIONS",
    "MIN_PRIOR_SAMPLES",
    "TABLE_VERSION",
    "ClarificationOption",
    "ClarificationPlan",
    "ClarificationQuestion",
    "FoodCalculation",
    "ItemCalculation",
    "NutrientTotal",
    "calculate",
    "clear_prior",
    "ensure_seed_table",
    "list_priors",
    "match_reference",
    "persist_questions",
    "personal_prior_mass",
    "propose_questions",
    "record_confirmed_mass",
    "resolve_cooking_adjustment",
    "review_reference",
    "table_status",
]
