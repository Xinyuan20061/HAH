"""Health state, signals, constraints and outcomes endpoints (plan §4.4/§9).

Read-only. Every response carries the evidence references and limitations the
state layer computed, so a client can always show *why* a value exists and what is
missing instead of presenting a bare number.

Two routers: ``state_router`` owns ``/health/state/*`` and ``router`` owns the
sibling ``/health/signals`` and ``/health/outcomes`` paths, matching the plan's
published paths exactly.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.database import get_db
from app.core.time import utc_iso
from app.schemas.errors import ApiException
from app.services.agent.proactive import PROACTIVE_CODES, build_proactive_insights
from app.services.agent.tools import read_context
from app.services.health_state import (
    FEATURES,
    FEATURE_KEYS,
    build_snapshot,
    display_titles,
    feature_history,
    features_for_sources,
)

router = APIRouter(prefix="/health", tags=["health-state"])
state_router = APIRouter(prefix="/health/state", tags=["health-state"])

MAX_WINDOW_DAYS = 90


def _window(window_days: int) -> int:
    if window_days < 1 or window_days > MAX_WINDOW_DAYS:
        raise ApiException(
            422,
            "VALIDATION_ERROR",
            f"window_days 必须在 1-{MAX_WINDOW_DAYS} 之间",
            details={"field": "window_days"},
        )
    return window_days


@state_router.get("")
def read_state(
    user=Depends(current_user),
    db: Session = Depends(get_db),
    window_days: int = Query(default=7),
):
    """The current versioned snapshot, computed and persisted on demand."""
    snapshot = build_snapshot(db, user.id, window_days=_window(window_days))
    return {
        **snapshot.to_persistable(),
        "feature_versions": {item.key: item.version for item in FEATURES},
        # Titles are display-only and deliberately kept out of the snapshot so a
        # wording change cannot alter `snapshot_hash` (capability plan §4.4).
        "titles": display_titles(),
        "policy": (
            "每个值都带 evidence_type（observed/derived/model_inferred/user_confirmed）、"
            "confidence_level、observed_days 与 limitations；缺失日不按 0 处理。"
        ),
    }


@state_router.get("/history")
def state_history(
    key: str,
    user=Depends(current_user),
    db: Session = Depends(get_db),
    days: int = Query(default=30),
):
    if key not in FEATURE_KEYS:
        raise ApiException(
            404,
            "FEATURE_NOT_FOUND",
            "没有这个健康状态特征",
            details={"field": "key"},
        )
    if days < 1 or days > 365:
        raise ApiException(422, "VALIDATION_ERROR", "days 必须在 1-365 之间")
    return {
        "key": key,
        "days": days,
        "items": feature_history(db, user.id, key, limit=min(days, 90)),
        "policy": "只返回数值、版本与证据数量，不返回原始媒体或模型私有推理。",
    }


@state_router.get("/features")
def list_features(user=Depends(current_user)):
    return {
        "features": [
            {
                "key": item.key,
                "title": item.title,
                "version": item.version,
                "sources": list(item.sources),
            }
            for item in FEATURES
        ],
        "invalidation_map": {
            source: features_for_sources([source])
            for source in sorted({s for item in FEATURES for s in item.sources})
        },
    }


@state_router.get("/constraints")
def read_constraints(
    user=Depends(current_user),
    db: Session = Depends(get_db),
    window_days: int = Query(default=7),
):
    snapshot = build_snapshot(db, user.id, window_days=_window(window_days))
    return {
        "as_of": utc_iso(snapshot.as_of),
        "hard": [item.model_dump() for item in snapshot.hard_constraints()],
        "soft": [
            item.model_dump()
            for item in snapshot.constraints
            if item.severity == "soft"
        ],
        "blocks_auto_planning": bool(snapshot.hard_constraints()),
    }


@router.get("/signals")
def read_signals(
    user=Depends(current_user),
    db: Session = Depends(get_db),
    status: str = Query(default="active"),
):
    """Active proactive signals, derived from the state layer."""
    if status not in {"active", "all"}:
        raise ApiException(422, "VALIDATION_ERROR", "status 只能是 active 或 all")
    context = read_context(db, user)
    payload = context.get("proactive_insights") or build_proactive_insights(context)
    insights = payload.get("insights", [])
    if status == "active":
        insights = [item for item in insights if item.get("code") in PROACTIVE_CODES]
    return {
        "state_snapshot_hash": (context.get("state") or {}).get("snapshot_hash"),
        "data_quality": payload.get("data_quality", {}),
        "signals": insights,
        "policy": "信号来自确定性事实与数据覆盖，不使用模型自报置信度。",
    }


@router.get("/outcomes")
def read_outcomes(
    user=Depends(current_user),
    db: Session = Depends(get_db),
    days: int = Query(default=30),
):
    """Observed outcomes of executed actions and experiments (plan §9)."""
    from app.services.agent.outcome import outcome_history

    if days < 1 or days > 365:
        raise ApiException(422, "VALIDATION_ERROR", "days 必须在 1-365 之间")
    return outcome_history(db, user.id, days=days)


# --------------------------------------------------------------------------- #
# Long-term structured memory (plan §9.2/§9.5, §11 Phase 5)
# --------------------------------------------------------------------------- #


class PreferenceIn(BaseModel):
    key: str = Field(min_length=1, max_length=40)
    value: str = Field(min_length=1, max_length=120)


@router.get("/preferences")
def read_long_term_memory(user=Depends(current_user), db: Session = Depends(get_db)):
    """The user's personalisation data, and exactly what it may influence.

    Plan §11 Phase 5 requires this to be viewable; ``influential`` distinguishes a
    stored entry from one that can actually change behaviour.
    """
    from app.services.agent.outcome import ALLOWED_PREFERENCE_KEYS, memory_view

    view = memory_view(db, user.id)
    return {**view, "writable_keys": sorted(ALLOWED_PREFERENCE_KEYS)}


@router.put("/preferences")
def set_long_term_memory(
    body: PreferenceIn, user=Depends(current_user), db: Session = Depends(get_db)
):
    """Record an *explicit* user statement. Inferred memory never takes this path."""
    from app.services.agent.outcome import (
        ALLOWED_PREFERENCE_KEYS,
        OutcomeError,
        upsert_preference,
    )

    if body.key not in ALLOWED_PREFERENCE_KEYS:
        raise ApiException(
            422,
            "UNKNOWN_PREFERENCE_KEY",
            "未登记的记忆键",
            details={
                "field": "key",
                "allowed_values": sorted(ALLOWED_PREFERENCE_KEYS),
            },
        )
    try:
        row = upsert_preference(
            db, user_id=user.id, key=body.key, value=body.value, source="explicit"
        )
    except OutcomeError as exc:
        raise ApiException(422, exc.code, exc.message) from exc
    return {
        "ok": True,
        "key": row.key,
        "value": row.value,
        "source": row.source,
        "confidence_level": row.confidence_level,
        "note": "这是你明确表达的记忆；可用同接口删除。",
    }


@router.delete("/preferences/{key}")
def clear_long_term_memory(
    key: str, user=Depends(current_user), db: Session = Depends(get_db)
):
    """Clearing memory must stop it influencing ranking immediately."""
    from app.services.agent.outcome import clear_preference

    if not clear_preference(db, user.id, key):
        raise ApiException(404, "PREFERENCE_NOT_FOUND", "没有这条记忆")
    return {"ok": True, "key": key, "note": "已清除；之后的排序不再使用该记忆。"}


@router.get("/experiments/results")
def experiment_results(
    user=Depends(current_user),
    db: Session = Depends(get_db),
    limit: int = Query(default=10, ge=1, le=50),
):
    """Micro-experiment conclusions, with `insufficient_data` stated as such."""
    from app.services.agent.experiments import list_experiments

    items = list_experiments(db, user.id, limit=limit)
    for item in items:
        outcome = item.get("outcome") or {}
        conclusion = str(outcome.get("conclusion") or "")
        item["is_conclusive"] = conclusion in {
            "supports_hypothesis",
            "not_supported_yet",
        }
        if conclusion == "insufficient_data":
            item["reading_note"] = (
                "记录不足，无法判断该做法是否有效；这不是负面结果，也不是支持证据。"
            )
    return {
        "experiments": items,
        "count": len(items),
        "policy": (
            "一次实验只改一个变量；结论来自真实记录，记录不足时如实说明不判断，"
            "不得把 insufficient_data 表达为有效。"
        ),
    }
