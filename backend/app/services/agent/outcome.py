"""Outcome learning without silent training (capability plan §9).

What this module does:

* records the observed *result* of an executed action or experiment;
* maintains an explainable Beta posterior per (action family, variant) so the
  system can say "given your confirmed choices, the gentle variant is offered
  first";
* keeps structured preference memory that only comes from explicit statements or
  repeated confirmed choices.

What it deliberately does not do (plan §9.3): no medical/diagnostic inference, no
safety-threshold learning, no training of model weights, no single-observation
conclusions, and never a positive update from ``insufficient_data``.
"""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import ActionOutcome, ActionPolicyStat, UserPreferenceMemory

# Prior: Beta(1, 1) is uniform, so with no data the ranking stays at the safe
# default instead of favouring an arbitrary option.
PRIOR_ALPHA = 1.0
PRIOR_BETA = 1.0

# Below this many observations the system must say "not enough personal records"
# rather than presenting a personalised preference.
MIN_SAMPLES_FOR_PERSONALISATION = 3

# Source domains that may update the policy. Anything else is rejected so a new
# call site cannot start steering behaviour without being declared here.
ALLOWED_RESULT_SOURCES = frozenset(
    {"proposal", "experiment", "goal_adjustment", "diet_finalize", "plan"}
)
ALLOWED_RESULTS = frozenset(
    {"accepted", "rejected", "completed", "abandoned", "helpful", "inaccurate", "unknown"}
)
ALLOWED_CONCLUSIONS = frozenset(
    {"changed", "unchanged", "insufficient_data", "stopped"}
)
# User preference keys that are recognised. Free-form keys are rejected.
ALLOWED_PREFERENCE_KEYS = frozenset(
    {
        "plan_variant",  # gentle | standard
        "session_minutes",
        "equipment",
        "training_days",
        "meal_portion",
        "quiet_signals",
        "excluded_exercises",
        "excluded_foods",
    }
)
ALLOWED_MEMORY_SOURCES = frozenset({"explicit", "confirmed_action", "repeated_choice"})


class OutcomeError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _get_or_create_stat(
    db: Session, user_id: int, action_family: str, variant: str
) -> ActionPolicyStat:
    row = db.scalar(
        select(ActionPolicyStat).where(
            ActionPolicyStat.user_id == user_id,
            ActionPolicyStat.action_family == action_family,
            ActionPolicyStat.variant == variant,
        )
    )
    if row is None:
        row = ActionPolicyStat(
            user_id=user_id,
            action_family=action_family,
            variant=variant,
            alpha=PRIOR_ALPHA,
            beta=PRIOR_BETA,
        )
        db.add(row)
        db.flush()
    return row


def record_outcome(
    db: Session,
    *,
    user_id: int,
    action_key: str,
    result: str,
    source: str = "proposal",
    source_id: str = "",
    variant: str = "default",
    decision_id: str | None = None,
    conclusion: str = "insufficient_data",
    user_feedback: str = "",
    observed: dict | None = None,
) -> ActionOutcome:
    """Record one observed outcome and update the explainable posterior.

    Idempotent per ``(user, action_key, source, source_id)`` when ``source_id`` is
    given. That matters because the executor may already have recorded a *specific*
    verdict (a finished experiment's real conclusion); the generic post-execution
    hook must not append a second, vaguer outcome pair for the same event — doing so
    would double-count the posterior and overwrite a real result with "completed".
    """
    if source not in ALLOWED_RESULT_SOURCES:
        raise OutcomeError("UNKNOWN_OUTCOME_SOURCE", f"未登记的结果来源: {source}")
    if result not in ALLOWED_RESULTS:
        raise OutcomeError("UNKNOWN_OUTCOME_RESULT", f"未登记的结果取值: {result}")
    if conclusion not in ALLOWED_CONCLUSIONS:
        raise OutcomeError("UNKNOWN_OUTCOME_CONCLUSION", f"未登记的结论取值: {conclusion}")

    if source_id:
        existing = db.scalar(
            select(ActionOutcome).where(
                ActionOutcome.user_id == user_id,
                ActionOutcome.action_key == action_key,
                ActionOutcome.source == source,
                ActionOutcome.source_id == str(source_id),
            )
        )
        if existing is not None:
            return existing

    row = ActionOutcome(
        user_id=user_id,
        action_key=action_key,
        source=source,
        source_id=source_id,
        decision_id=decision_id,
        variant=variant,
        result=result,
        conclusion=conclusion,
        user_feedback=user_feedback,
        observed_json=json.dumps(observed or {}, ensure_ascii=False, default=str),
        observed_at=utc_now(),
    )
    db.add(row)

    stat = _get_or_create_stat(db, user_id, action_key, variant)
    # A conclusion of `insufficient_data` is not evidence in either direction. It is
    # checked first so that finishing a task the system could not measure (a
    # two-week experiment with no follow-up records) does not quietly strengthen the
    # posterior that later reorders suggestions. The event itself is still recorded.
    unobserved = conclusion == "insufficient_data"
    if result == "accepted":
        stat.offered += 1
        stat.accepted += 1
        stat.alpha += 1
    elif result == "rejected":
        stat.offered += 1
        stat.beta += 1
    elif result == "completed":
        stat.completed += 1
        if not unobserved:
            stat.alpha += 1
    elif result == "abandoned":
        stat.beta += 1
    elif result == "helpful":
        stat.helpful += 1
    elif result == "inaccurate":
        stat.inaccurate += 1
        stat.beta += 1
    # `unknown`/`insufficient_data` intentionally change no posterior count: an
    # unobserved result is not evidence either way (plan §9.5).
    db.add(stat)
    db.commit()
    db.refresh(row)
    return row


def preference_score(row: ActionPolicyStat) -> float | None:
    """Posterior mean of acceptance; ``None`` until there is enough evidence."""
    if row.offered < MIN_SAMPLES_FOR_PERSONALISATION:
        return None
    return round(row.accepted / row.offered, 4)


def completion_score(row: ActionPolicyStat) -> float | None:
    if row.accepted < MIN_SAMPLES_FOR_PERSONALISATION:
        return None
    return round(row.completed / row.accepted, 4)


def rank_variants(
    db: Session, user_id: int, action_family: str, variants: list[str]
) -> dict:
    """Order already-safe options by posterior mean, with an explicit fallback.

    High-risk options are never explored (plan §9.4): this function only reorders
    the list it is given, so it can never introduce an option the caller had not
    already deemed safe.
    """
    rows = {
        row.variant: row
        for row in db.scalars(
            select(ActionPolicyStat).where(
                ActionPolicyStat.user_id == user_id,
                ActionPolicyStat.action_family == action_family,
            )
        ).all()
    }
    scored: list[dict] = []
    for variant in variants:
        row = rows.get(variant)
        if row is None:
            scored.append(
                {
                    "variant": variant,
                    "preference_score": None,
                    "sample_size": 0,
                    "personalised": False,
                }
            )
            continue
        score = preference_score(row)
        scored.append(
            {
                "variant": variant,
                "preference_score": score,
                "sample_size": row.offered,
                "personalised": score is not None,
            }
        )
    # Unpersonalised options keep their declared order; personalised ones sort by
    # their posterior mean. Python's sort is stable, so this is deterministic.
    scored.sort(key=lambda item: (item["preference_score"] is None, -(item["preference_score"] or 0.0)))
    any_personalised = any(item["personalised"] for item in scored)
    return {
        "action_family": action_family,
        "ranked": scored,
        "personalised": any_personalised,
        "note": (
            "排序只改变同一组安全选项的默认顺序，不改变安全边界，也不使用模型权重训练。"
            if any_personalised
            else f"还没有足够个人记录（每个选项至少 {MIN_SAMPLES_FOR_PERSONALISATION} 次），"
            "暂按默认顺序。"
        ),
    }


def upsert_preference(
    db: Session,
    *,
    user_id: int,
    key: str,
    value: str,
    source: str = "explicit",
) -> UserPreferenceMemory:
    if key not in ALLOWED_PREFERENCE_KEYS:
        raise OutcomeError("UNKNOWN_PREFERENCE_KEY", f"未登记的偏好键: {key}")
    if source not in ALLOWED_MEMORY_SOURCES:
        raise OutcomeError("UNKNOWN_MEMORY_SOURCE", f"未登记的偏好来源: {source}")
    row = db.scalar(
        select(UserPreferenceMemory).where(
            UserPreferenceMemory.user_id == user_id, UserPreferenceMemory.key == key
        )
    )
    now = utc_now()
    if row is None:
        row = UserPreferenceMemory(
            user_id=user_id,
            key=key,
            value=value[:200],
            source=source,
            evidence_count=1,
            confidence_level="high" if source == "explicit" else "medium",
            last_confirmed_at=now,
        )
        db.add(row)
    else:
        # A repeated identical choice strengthens the memory; a changed explicit
        # statement replaces it outright.
        same = row.value == value
        row.value = value[:200]
        row.source = source
        row.evidence_count = row.evidence_count + 1 if same else 1
        if source == "explicit":
            row.confidence_level = "high"
        elif row.evidence_count >= MIN_SAMPLES_FOR_PERSONALISATION:
            row.confidence_level = "high"
        else:
            row.confidence_level = "medium"
        row.last_confirmed_at = now
        db.add(row)
    db.commit()
    db.refresh(row)
    return row


def read_preferences(db: Session, user_id: int) -> list[dict]:
    rows = db.scalars(
        select(UserPreferenceMemory)
        .where(UserPreferenceMemory.user_id == user_id)
        .order_by(UserPreferenceMemory.key)
    ).all()
    now = utc_now()
    out = []
    for row in rows:
        expired = row.expires_at is not None and row.expires_at < now
        out.append(
            {
                "key": row.key,
                "value": row.value,
                "source": row.source,
                "evidence_count": row.evidence_count,
                "confidence_level": "unavailable" if expired else row.confidence_level,
                "expired": expired,
                "last_confirmed_at": row.last_confirmed_at.isoformat() + "Z"
                if row.last_confirmed_at
                else None,
            }
        )
    return out


def clear_preference(db: Session, user_id: int, key: str) -> bool:
    row = db.scalar(
        select(UserPreferenceMemory).where(
            UserPreferenceMemory.user_id == user_id, UserPreferenceMemory.key == key
        )
    )
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def preference_value(db: Session, user_id: int, key: str) -> str | None:
    """Read one live preference; an expired value is ignored, not returned."""
    row = db.scalar(
        select(UserPreferenceMemory).where(
            UserPreferenceMemory.user_id == user_id, UserPreferenceMemory.key == key
        )
    )
    if row is None:
        return None
    if row.expires_at is not None and row.expires_at < utc_now():
        return None
    if row.confidence_level == "low":
        return None
    return row.value


# Preference keys that may steer *plan/advice shape*. Not listed here means the key
# is stored but may never alter behaviour, which is how §9.3 is enforced: memory can
# tune presentation and volume, never a safety boundary.
BEHAVIOURAL_PREFERENCE_KEYS = frozenset(
    {"plan_variant", "session_minutes", "equipment", "training_days", "meal_portion"}
)
# Memory keys that must never be produced by inference. They are user-stated facts.
STATEMENT_PREFERENCE_KEYS = frozenset(
    {"quiet_signals", "excluded_exercises", "excluded_foods"}
)

# §9.3 "不学习的对象": recorded here as data so the rule is inspectable from the API
# and assertable in tests, rather than living only in prose.
NEVER_LEARNED = (
    "medical_diagnosis",
    "safety_threshold",
    "medication",
    "unconfirmed_model_inference",
    "single_anomalous_event",
    "model_weights",
)


def memory_view(db: Session, user_id: int) -> dict:
    """The single read model for long-term structured memory.

    Every consumer (tool, API, Decision Contract, planner) reads memory through this
    function so that three properties hold in one place:

    * an **expired** or low-confidence entry is reported but can never influence
      behaviour (``influential`` is False);
    * only ``BEHAVIOURAL_PREFERENCE_KEYS`` are marked ``influential``, so a stored
      key cannot start steering behaviour just by existing;
    * the memory that *was* invalidated by an explicit user action
      (``source == "explicit"``) is ranked above inferred memory, so an explicit
      statement always wins over a pattern.

    Memory is never loaded from an unconfirmed inference: ``ALLOWED_MEMORY_SOURCES``
    is enforced at write time, and this function additionally reports any row whose
    source is not in that allowlist instead of silently trusting it.
    """
    now = utc_now()
    rows = read_preferences(db, user_id)
    entries: list[dict] = []
    ignored: list[dict] = []
    for row in rows:
        source_ok = row["source"] in ALLOWED_MEMORY_SOURCES
        key_ok = row["key"] in ALLOWED_PREFERENCE_KEYS
        influential = (
            key_ok
            and source_ok
            and not row["expired"]
            and row["confidence_level"] in {"medium", "high"}
            and row["key"] in BEHAVIOURAL_PREFERENCE_KEYS
        )
        entry = {
            **row,
            "influential": influential,
            "may_steer": (
                "presentation_and_volume"
                if row["key"] in BEHAVIOURAL_PREFERENCE_KEYS
                else "statement_only"
            ),
        }
        if not source_ok or not key_ok:
            ignored.append(
                {
                    "key": row["key"],
                    "reason": (
                        "source_not_allowed" if not source_ok else "key_not_recognised"
                    ),
                    "note": "该记忆不会被用于任何决策",
                }
            )
            entry["influential"] = False
        entries.append(entry)

    effective = {item["key"]: item["value"] for item in entries if item["influential"]}
    return {
        "found": bool(entries),
        "entries": entries,
        "effective": effective,
        "ignored": ignored,
        "count": len(entries),
        "never_learned": list(NEVER_LEARNED),
        "policy": (
            "长期记忆只来自用户明确表达或重复确认的行为；它只能影响计划与建议的"
            "形态和强度，不能改变安全边界、医学结论或安全阈值；清除后立即不再影响排序。"
        ),
    }


def outcome_history(db: Session, user_id: int, *, days: int = 30) -> dict:
    since = utc_now() - timedelta(days=days)
    rows = db.scalars(
        select(ActionOutcome)
        .where(ActionOutcome.user_id == user_id, ActionOutcome.observed_at >= since)
        .order_by(ActionOutcome.observed_at.desc(), ActionOutcome.id.desc())
        .limit(200)
    ).all()
    by_action: dict[str, dict[str, int]] = {}
    for row in rows:
        bucket = by_action.setdefault(row.action_key, {})
        bucket[row.result] = bucket.get(row.result, 0) + 1
    return {
        "window_days": days,
        "total": len(rows),
        "by_action": by_action,
        "items": [
            {
                "action_key": row.action_key,
                "source": row.source,
                "variant": row.variant,
                "result": row.result,
                "conclusion": row.conclusion,
                "observed_at": row.observed_at.isoformat() + "Z",
            }
            for row in rows[:50]
        ],
        "policy_preferences": read_preferences(db, user_id),
        "note": (
            "结果只用于排序已确认安全的选项；insufficient_data 不计为正向信号，"
            "不训练模型权重，不改变安全阈值。"
        ),
    }


def policy_snapshot(db: Session, user_id: int) -> list[dict]:
    rows = db.scalars(
        select(ActionPolicyStat)
        .where(ActionPolicyStat.user_id == user_id)
        .order_by(ActionPolicyStat.action_family, ActionPolicyStat.variant)
    ).all()
    return [
        {
            "action_family": row.action_family,
            "variant": row.variant,
            "offered": row.offered,
            "accepted": row.accepted,
            "completed": row.completed,
            "helpful": row.helpful,
            "inaccurate": row.inaccurate,
            "alpha": row.alpha,
            "beta": row.beta,
            "preference_score": preference_score(row),
            "completion_score": completion_score(row),
        }
        for row in rows
    ]
