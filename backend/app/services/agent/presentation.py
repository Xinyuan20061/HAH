"""Deterministic UI instructions for the HealthMate companion surface.

The language model may suggest health content, but it never chooses an
arbitrary client route or claims that a write has happened.  This module turns
the already validated orchestration result into a small, versioned presentation
contract that the mini-program can safely interpret.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping


PRESENTATION_VERSION = "healthmate.presentation.v1"


class NavigationTarget(str, Enum):
    """Every destination the Agent presentation layer may request.

    These are semantic targets rather than paths.  The client owns the target
    to route mapping, so neither a provider response nor stored free text can
    navigate to an arbitrary URL.
    """

    PLAN_PREVIEW = "plan_preview"
    CAPABILITY_SETUP = "capability_setup"
    RECORDS = "records"
    WORKOUT = "workout"
    HEALTH_STATE = "health_state"


_ACTORS = frozenset({"xiaojian", "xiaokang", "steward"})


def is_plan_result_eligible(intent: str, result: Mapping[str, Any]) -> bool:
    """Whether a reviewed result may be exposed as a confirmable plan draft."""

    if not isinstance(result, Mapping):
        return False
    if intent != "plan" or str(result.get("safety_level") or "normal") != "normal":
        return False
    plan = result.get("plan")
    return (
        isinstance(plan, Mapping)
        and isinstance(plan.get("items"), list)
        and bool(plan.get("items"))
    )


def _actor(agent_id: str) -> str:
    return agent_id if agent_id in _ACTORS else "steward"


def _mood(agent_id: str, *, safety_blocked: bool) -> str:
    if safety_blocked:
        return "calm"
    return {
        "xiaojian": "focused",
        "xiaokang": "warm",
        "steward": "calm",
    }.get(agent_id, "calm")


def build_presentation(
    *,
    intent: str,
    specialist: str,
    agent_id: str,
    run_id: int,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a safe UI directive from trusted orchestration state only.

    The function deliberately ignores any ``presentation`` or ``ui_directive``
    value returned by the model.  Navigation is inferred from validated intent,
    the selected specialist and the post-sanitisation result.
    """

    safety_blocked = (
        intent == "safety"
        or str(result.get("safety_level") or "normal") != "normal"
    )
    has_plan = is_plan_result_eligible(intent, result)

    cue = "safety.pause" if safety_blocked else "answer.present"
    target: NavigationTarget | None = None
    mode = "none"
    params: dict[str, int] = {}

    if has_plan and not safety_blocked:
        cue = "plan.compose"
        target = NavigationTarget.PLAN_PREVIEW
        mode = "after_animation"
        params = {"run_id": int(run_id)}
    elif intent == "plan" and not safety_blocked:
        # A plan request without a reviewed structured draft is normally a
        # capability boundary. Expose a deterministic setup action instead of
        # leaving the user with an unexplained text-only answer.
        target = NavigationTarget.CAPABILITY_SETUP
        mode = "on_user_action"
    elif (
        specialist == "coach"
        and intent == "exercise_knowledge"
        and not safety_blocked
    ):
        cue = "workout.guide"
        target = NavigationTarget.WORKOUT
        mode = "on_user_action"

    actions = result.get("actions")
    confirmation_required = has_plan or (isinstance(actions, list) and bool(actions))
    return {
        "version": PRESENTATION_VERSION,
        "actor": _actor(agent_id),
        "cue": cue,
        "mood": _mood(agent_id, safety_blocked=safety_blocked),
        "navigation": {
            "target": target.value if target is not None else None,
            "mode": mode,
            "params": params,
        },
        # This is a statement of observable state, not a suggestion from the
        # model.  Only the existing proposal/confirm endpoint may change it.
        "write": {
            "status": "not_applied",
            "automatic": False,
            "confirmation_required": bool(confirmation_required),
        },
    }


def build_plan_preview(
    run_id: int,
    result: Mapping[str, Any],
    *,
    applied: bool = False,
) -> dict[str, Any] | None:
    """Return the minimal read-only draft needed by a plan preview screen."""

    plan = result.get("plan")
    if not isinstance(plan, Mapping):
        return None
    raw_items = plan.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return None

    items: list[dict[str, Any]] = []
    for raw in raw_items[:10]:
        if not isinstance(raw, Mapping):
            continue
        try:
            date_offset = max(0, min(6, int(raw.get("date_offset", 0))))
        except (TypeError, ValueError):
            date_offset = 0
        title = str(raw.get("title") or "").strip()[:160]
        if not title:
            continue
        target = raw.get("target")
        items.append(
            {
                "date_offset": date_offset,
                "category": str(raw.get("category") or "habit")[:30],
                "title": title,
                "description": str(raw.get("description") or "")[:600],
                "target": dict(target) if isinstance(target, Mapping) else {},
            }
        )
    if not items:
        return None
    return {
        "run_id": int(run_id),
        "status": "applied" if applied else "draft",
        "read_only": True,
        "title": str(plan.get("title") or "本周健康计划")[:160],
        "items": items,
        "write": {
            "status": "applied" if applied else "not_applied",
            "automatic": False,
            "confirmation_required": not applied,
        },
    }
