"""Long-term structured memory acceptance (capability plan §9.2/§9.3/§9.5).

The plan names four §9.5 tools and states what may **not** be learned (§9.3). This
file locks both, because the failure mode here is silent: if memory quietly never
reaches the reasoning context, or a stored key quietly starts steering behaviour,
nothing errors — the system just stops being the system that was described.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.time import utc_now
from app.harness.contracts import ToolContext
from app.harness.tools import get_tool_registry
from app.models import User
from app.services.agent.decision import decide, planning_preferences
from app.services.agent.outcome import (
    ALLOWED_MEMORY_SOURCES,
    ALLOWED_PREFERENCE_KEYS,
    BEHAVIOURAL_PREFERENCE_KEYS,
    NEVER_LEARNED,
    OutcomeError,
    clear_preference,
    memory_view,
    upsert_preference,
)

# --------------------------------------------------------------------------- #
# §9.5 the four named tools
# --------------------------------------------------------------------------- #


def test_all_four_plan_named_tools_exist():
    """§9.5 names these four explicitly; each must be registered and scoped."""
    registry = get_tool_registry()
    names = set(registry.names())
    assert "outcomes.history.read" in names
    assert "preferences.read" in names
    assert "experiment.result.read" in names
    # The plan's `next_action.rank` is delivered as `decision.next_best_action`.
    assert "decision.next_best_action" in names
    assert "next_action.rank" in names, "排序工具必须以 §9.5 的名字注册"


@pytest.mark.parametrize(
    "tool",
    ["preferences.read", "outcomes.history.read", "experiment.result.read", "next_action.rank"],
)
def test_each_tool_is_granted_to_the_planner(api, tool):
    from app.harness.collaboration import WORKERS

    registry = get_tool_registry()
    assert tool in registry.scoped(WORKERS["planner"].tools).names()


def test_each_tool_is_read_only():
    registry = get_tool_registry()
    for name in (
        "preferences.read",
        "outcomes.history.read",
        "experiment.result.read",
        "next_action.rank",
    ):
        spec = next(
            item for item in registry.manifest() if item["name"] == name
        )
        assert spec["kind"] == "read", f"{name} 必须是只读工具"
        assert spec["proposal_only"] is False


def _ctx(db, user_id):
    return ToolContext(db=db, user=db.get(User, user_id), agent_id="planner")


def test_preferences_tool_reports_provenance_and_boundaries(api, db):
    upsert_preference(
        db, user_id=api.user_id, key="plan_variant", value="gentle", source="explicit"
    )
    observation = get_tool_registry().execute(
        "preferences.read", _ctx(db, api.user_id), {}, step=1
    )
    assert observation.status == "ok", observation.summary
    output = observation.output
    assert output["found"] is True
    assert output["effective"]["plan_variant"] == "gentle"
    entry = next(item for item in output["entries"] if item["key"] == "plan_variant")
    assert entry["source"] == "explicit"
    assert entry["influential"] is True
    assert output["never_learned"] == list(NEVER_LEARNED)
    assert output["policy"]


def test_outcomes_history_tool_reports_results(api, db):
    from app.services.agent.outcome import record_outcome

    record_outcome(
        db,
        user_id=api.user_id,
        action_key="plan.apply",
        result="completed",
        source_id="proposal:h1",
        conclusion="changed",
    )
    observation = get_tool_registry().execute(
        "outcomes.history.read", _ctx(db, api.user_id), {}, step=1
    )
    assert observation.status == "ok", observation.summary
    assert observation.output["found"] is True
    assert observation.output["outcomes"]["items"]


def test_experiment_result_tool_marks_insufficient_data(api, db):
    from app.models import AgentMicroExperiment

    today_iso = utc_now().date().isoformat()
    row = AgentMicroExperiment(
        user_id=api.user_id,
        insight_code="sleep_debt",
        title="无数据实验",
        hypothesis="x",
        primary_metric="sleep_minutes",
        variant="gentle",
        status="completed",
        start_date=today_iso,
        end_date=today_iso,
        baseline_json="{}",
        target_json="{}",
        outcome_json='{"conclusion": "insufficient_data"}',
    )
    db.add(row)
    db.commit()
    experiment_id = row.id

    observation = get_tool_registry().execute(
        "experiment.result.read", _ctx(db, api.user_id), {"experiment_id": experiment_id}, step=1
    )
    assert observation.status == "ok", observation.summary
    item = observation.output["experiments"][0]
    assert item["is_conclusive"] is False
    assert "无法判断" in item["reading_note"]
    assert "不是支持证据" in item["reading_note"]


def test_rank_tool_requires_caller_supplied_options(api, db):
    registry = get_tool_registry()
    missing = registry.execute("next_action.rank", _ctx(db, api.user_id), {}, step=1)
    assert missing.status == "ok"
    assert missing.output["found"] is False
    assert missing.output["reason"] == "missing_arguments"

    ranked = registry.execute(
        "next_action.rank",
        _ctx(db, api.user_id),
        {"action_family": "plan.apply", "variants": ["gentle", "standard"]},
        step=1,
    )
    assert ranked.status == "ok"
    assert ranked.output["found"] is True
    assert ranked.output["personalised"] is False, "样本不足时不得声称已个性化"


# --------------------------------------------------------------------------- #
# §9.2 memory reaches the reasoning context
# --------------------------------------------------------------------------- #


def test_read_context_exposes_memory_and_effective_preferences(api, db):
    from app.services.agent.tools import read_context

    upsert_preference(
        db, user_id=api.user_id, key="plan_variant", value="gentle", source="explicit"
    )
    context = read_context(db, db.get(User, api.user_id))
    assert "memory" in context, "长期记忆必须进入统一读取上下文"
    assert context["preferences"]["plan_variant"] == "gentle"
    assert context["memory"]["policy"]
    assert context["memory"]["never_learned"] == list(NEVER_LEARNED)


def test_decision_contract_reports_the_memory_it_used(api, db):
    upsert_preference(
        db, user_id=api.user_id, key="plan_variant", value="gentle", source="explicit"
    )
    contract = decide(db, api.user_id).as_dict()
    used = {item["key"]: item for item in contract["memory_used"]}
    assert "plan_variant" in used
    assert used["plan_variant"]["value"] == "gentle"
    assert used["plan_variant"]["may_steer"] == "presentation_and_volume"
    assert "硬约束" in contract["policy"]


def test_cleared_memory_disappears_from_the_contract(api, db):
    upsert_preference(
        db, user_id=api.user_id, key="plan_variant", value="gentle", source="explicit"
    )
    assert decide(db, api.user_id).as_dict()["memory_used"]
    clear_preference(db, api.user_id, "plan_variant")
    after = decide(db, api.user_id).as_dict()
    assert after["memory_used"] == [], "清除后不得再影响决策"


def test_expired_memory_is_listed_but_not_influential(api, db):
    row = upsert_preference(
        db, user_id=api.user_id, key="plan_variant", value="gentle", source="explicit"
    )
    row.expires_at = utc_now() - timedelta(minutes=1)
    db.add(row)
    db.commit()
    view = memory_view(db, api.user_id)
    entry = next(item for item in view["entries"] if item["key"] == "plan_variant")
    assert entry["expired"] is True
    assert entry["influential"] is False
    assert view["effective"] == {}
    assert decide(db, api.user_id).as_dict()["memory_used"] == []


def test_low_confidence_memory_cannot_steer(api, db):
    upsert_preference(
        db, user_id=api.user_id, key="plan_variant", value="gentle", source="explicit"
    )
    row = db.scalar(
        select(__import__("app.models", fromlist=["UserPreferenceMemory"]).UserPreferenceMemory)
    )
    row.confidence_level = "low"
    db.add(row)
    db.commit()
    view = memory_view(db, api.user_id)
    entry = next(item for item in view["entries"] if item["key"] == "plan_variant")
    assert entry["influential"] is False


# --------------------------------------------------------------------------- #
# §9.3 what may never be learned / may never steer
# --------------------------------------------------------------------------- #


def test_safety_keys_are_not_memory_keys(api, db):
    """§9.3: a safety threshold has no route into memory at all."""
    from app.models import UserPreferenceMemory

    for forbidden in ("safety_threshold", "medical_diagnosis", "medication"):
        assert forbidden not in ALLOWED_PREFERENCE_KEYS
        with pytest.raises(OutcomeError) as excinfo:
            upsert_preference(
                db, user_id=api.user_id, key=forbidden, value="170", source="explicit"
            )
        assert "PREFERENCE_KEY" in excinfo.value.code or "记忆键" in excinfo.value.message

    # And nothing may slip one in through the API either.
    res = api.put(
        "/api/v1/health/preferences",
        json={"key": "safety_threshold", "value": "170"},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "UNKNOWN_PREFERENCE_KEY"
    db.expire_all()
    assert db.scalars(select(UserPreferenceMemory)).all() == []


def test_only_behavioural_keys_may_steer(api, db):
    """A statement-only key is stored, is honoured as a statement, never steers."""
    upsert_preference(
        db, user_id=api.user_id, key="excluded_exercises", value="squat", source="explicit"
    )
    view = memory_view(db, api.user_id)
    entry = next(item for item in view["entries"] if item["key"] == "excluded_exercises")
    assert entry["may_steer"] == "statement_only"
    assert entry["influential"] is False, "排除项由约束层处理，不由记忆层改变行为"
    assert "excluded_exercises" not in view["effective"]
    assert "excluded_exercises" not in planning_preferences(db, api.user_id)


def test_unrecognised_source_is_reported_as_ignored(api, db):
    from app.models import UserPreferenceMemory

    db.add(
        UserPreferenceMemory(
            user_id=api.user_id,
            key="plan_variant",
            value="gentle",
            source="model_inference",  # not in ALLOWED_MEMORY_SOURCES
            evidence_count=9,
            confidence_level="high",
        )
    )
    db.commit()
    view = memory_view(db, api.user_id)
    assert view["ignored"], "未登记的来源必须被列出并忽略"
    assert view["ignored"][0]["reason"] == "source_not_allowed"
    entry = next(item for item in view["entries"] if item["key"] == "plan_variant")
    assert entry["influential"] is False
    assert view["effective"] == {}


def test_memory_sources_allowlist_is_explicit():
    assert "explicit" in ALLOWED_MEMORY_SOURCES
    assert "repeated_choice" in ALLOWED_MEMORY_SOURCES
    assert "model_inference" not in ALLOWED_MEMORY_SOURCES
    assert "unconfirmed" not in ALLOWED_MEMORY_SOURCES


def test_planning_preferences_stay_within_shape_and_volume(api, db):
    for key, value in (
        ("plan_variant", "gentle"),
        ("session_minutes", "20"),
        ("training_days", "2"),
        ("equipment", "bodyweight,dumbbell"),
        ("excluded_exercises", "squat"),  # statement only
        ("meal_portion", "small"),  # not a plan input
    ):
        upsert_preference(db, user_id=api.user_id, key=key, value=value, source="explicit")
    projected = planning_preferences(db, api.user_id)
    assert projected["intensity_preference"] == "gentle"
    assert projected["minutes_per_session"] == 20
    assert projected["days_per_week"] == 2
    assert set(projected["equipment"]) == {"bodyweight", "dumbbell"}
    assert "excluded_exercises" not in projected
    assert "meal_portion" not in projected


def test_planning_preferences_reject_unsafe_values(api, db):
    upsert_preference(
        db, user_id=api.user_id, key="plan_variant", value="maximum", source="explicit"
    )
    assert "intensity_preference" not in planning_preferences(db, api.user_id)


def test_plan_simulate_applies_memory_only_where_unset(api, db):
    registry = get_tool_registry()
    upsert_preference(
        db, user_id=api.user_id, key="session_minutes", value="20", source="explicit"
    )
    from_memory = registry.execute(
        "plan.simulate", _ctx(db, api.user_id), {"goal": "fitness"}, step=1
    )
    assert from_memory.status == "ok", from_memory.summary
    assert from_memory.output["memory_applied"].get("minutes_per_session") == "20"
    assert from_memory.output["candidate"]["minutes_per_session"] == 20

    explicit = registry.execute(
        "plan.simulate",
        _ctx(db, api.user_id),
        {"goal": "fitness", "minutes_per_session": 45},
        step=1,
    )
    assert "minutes_per_session" not in explicit.output["memory_applied"], (
        "显式参数必须优先于记忆"
    )
    assert explicit.output["candidate"]["minutes_per_session"] == 45


def test_memory_cannot_override_a_hard_constraint(api, db):
    """§9.3: memory tunes shape/volume; a hard constraint still wins."""
    from app.services.agent.action_executors import plan_context_for
    from app.services.planning.contracts import PlanRequest, PlanContext
    from app.services.planning.solver import solve_weekly_plan

    for _ in range(3):
        upsert_preference(
            db,
            user_id=api.user_id,
            key="plan_variant",
            value="standard",
            source="repeated_choice",
        )
    request = PlanRequest(
        goal="strength",
        days_per_week=3,
        equipment={"barbell"},
        excluded_exercises={"squat"},
        intensity_preference="standard",
    )
    context = plan_context_for(db, api.user_id)
    plan = solve_weekly_plan(request, context)
    assert "squat" not in plan.exercise_ids(), "记忆不得覆盖用户的排除项"
    del PlanContext


# --------------------------------------------------------------------------- #
# API surface (§11 Phase 5: user can view and clear personalisation data)
# --------------------------------------------------------------------------- #


def test_preferences_api_view_set_and_clear(api):
    empty = api.get("/api/v1/health/preferences")
    assert empty.status_code == 200, empty.text
    assert empty.json()["entries"] == []
    assert empty.json()["writable_keys"]

    created = api.put(
        "/api/v1/health/preferences", json={"key": "plan_variant", "value": "gentle"}
    )
    assert created.status_code == 200, created.text
    assert created.json()["source"] == "explicit"

    viewed = api.get("/api/v1/health/preferences").json()
    assert viewed["effective"]["plan_variant"] == "gentle"
    entry = next(i for i in viewed["entries"] if i["key"] == "plan_variant")
    assert entry["influential"] is True

    removed = api.delete("/api/v1/health/preferences/plan_variant")
    assert removed.status_code == 200
    assert api.get("/api/v1/health/preferences").json()["effective"] == {}

    missing = api.delete("/api/v1/health/preferences/plan_variant")
    assert missing.status_code == 404


def test_preferences_api_rejects_unknown_keys(api):
    res = api.put(
        "/api/v1/health/preferences", json={"key": "safety_threshold", "value": "170"}
    )
    assert res.status_code == 422
    body = res.json()
    assert body["error"]["code"] == "UNKNOWN_PREFERENCE_KEY"
    assert body["error"]["details"].get("field") == "key"
    assert "plan_variant" in body["error"]["details"]["allowed_values"]


def test_experiments_results_api(api):
    res = api.get("/api/v1/health/experiments/results")
    assert res.status_code == 200, res.text
    assert res.json()["policy"]


def test_decision_api_exposes_memory_used(api):
    api.put("/api/v1/health/preferences", json={"key": "plan_variant", "value": "gentle"})
    body = api.get("/api/v1/agent/decision").json()
    assert "memory_used" in body
    assert "memory_ignored" in body
    assert any(item["key"] == "plan_variant" for item in body["memory_used"])
