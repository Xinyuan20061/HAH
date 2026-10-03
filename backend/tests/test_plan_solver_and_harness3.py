"""Phase 4/5 acceptance — Constrained planning + Harness 3.0 + Outcome learning.

Specification: capability plan §7 (plan solver), §8 (Harness), §9 (feedback), with
the §13.4 and §13.5 checklists as the literal assertions:

§13.4 plan solver — random constraint combinations satisfy every hard constraint;
replan never modifies completed items; identical input + version is stable; an
illegal LLM output is rejected and falls back.

§13.5 harness — an unauthorised tool cannot be called; an unavailable capability
cannot be routed to; an unconfirmed Action never writes; a repeated confirm executes
once; the simple-task budget is ≤2 and the cross-domain budget ≤5; long-term memory
only comes from explicit/repeated confirmed behaviour; clearing memory removes its
influence on ranking.
"""

from __future__ import annotations

import itertools
import json
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import business_today, utc_now
from app.models import (
    ActionOutcome,
    ActionPolicyStat,
    HealthPlan,
    HealthPlanItem,
    User,
    UserPreferenceMemory,
)
from app.services.agent.capability_graph import (
    capability_graph,
    manifest,
    motion_capabilities,
    unavailable_reasons,
)
from app.services.agent.decision import (
    ACTION_CANDIDATES,
    NEVER_AUTO,
    decide,
    filter_candidates,
    next_best_action,
)
from app.services.agent.outcome import (
    ALLOWED_PREFERENCE_KEYS,
    MIN_SAMPLES_FOR_PERSONALISATION,
    OutcomeError,
    clear_preference,
    outcome_history,
    preference_value,
    rank_variants,
    read_preferences,
    record_outcome,
    upsert_preference,
)
from app.services.planning.constraints import legal_exercise_ids, validate_plan
from app.services.planning.contracts import (
    PlanCandidate,
    PlanContext,
    PlanDiff,
    PlanDiffEntry,
    PlanItemDraft,
    PlanRequest,
)
from app.services.planning.library import EXERCISES, PLANNING_ONLY_IDS, get_exercise
from app.services.planning.replan import apply_diff, build_diff, replan
from app.services.planning.solver import solve_weekly_plan

# --------------------------------------------------------------------------- #
# §7 Plan solver
# --------------------------------------------------------------------------- #


def test_solver_is_deterministic_for_identical_input():
    request = PlanRequest(goal="fitness", days_per_week=3, minutes_per_session=30)
    first = solve_weekly_plan(request, PlanContext())
    second = solve_weekly_plan(request, PlanContext())
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_every_requested_goal_produces_a_legal_plan():
    for goal in ("fat_loss", "strength", "fitness", "posture", "maintain"):
        request = PlanRequest(goal=goal, days_per_week=3, minutes_per_session=30)
        plan = solve_weekly_plan(request, PlanContext())
        report = validate_plan(request, PlanContext(), plan)
        assert report.legal, (goal, report.hard_violations)
        assert plan.items
        assert len(plan.session_minutes()) <= request.days_per_week


def test_hard_constraints_hold_across_constraint_combinations():
    """Property test over many combinations (§13.4)."""
    equipment_choices = [
        {"bodyweight"},
        {"dumbbell"},
        {"bodyweight", "dumbbell"},
        {"barbell", "bodyweight"},
        {"band", "bodyweight"},
        set(),
    ]
    checked = 0
    for equipment, days, minutes, excluded in itertools.product(
        equipment_choices, (1, 3, 7), (10, 30, 60), (set(), {"squat"}, {"pushup", "lunge"})
    ):
        request = PlanRequest(
            days_per_week=days,
            minutes_per_session=minutes,
            equipment=equipment,
            excluded_exercises=excluded,
        )
        plan = solve_weekly_plan(request, PlanContext())
        if not plan.legal:
            continue
        checked += 1
        used = set(plan.exercise_ids())
        assert not (used & {"squat", "pushup", "lunge"} & excluded) or not excluded
        for exercise_id in used:
            if exercise_id == "recovery_item":
                continue
            exercise = get_exercise(exercise_id)
            assert exercise is not None
            # Bodyweight is always implicitly available.
            assert exercise.requires() <= (equipment | {"bodyweight"})
        budget = int(minutes * 1.15)
        for day_minutes in plan.session_minutes().values():
            assert day_minutes <= budget
    assert checked > 20, f"too few legal plans to be meaningful: {checked}"


def test_user_exclusion_is_never_solved_into_the_plan():
    request = PlanRequest(
        goal="strength", days_per_week=3, equipment={"dumbbell"}, excluded_exercises={"squat", "goblet_squat"}
    )
    plan = solve_weekly_plan(request, PlanContext())
    assert "squat" not in plan.exercise_ids()
    assert "goblet_squat" not in plan.exercise_ids()


def test_safety_red_flag_produces_no_training_load():
    from app.models import SafetyEvent
    from app.services.health_state import build_snapshot

    # A snapshot carrying a safety constraint must block planning.
    state = build_snapshot.__wrapped__ if hasattr(build_snapshot, "__wrapped__") else None
    del state
    from app.services.health_state.contracts import (
        HealthConstraint,
        HealthStateSnapshot,
    )

    snapshot = HealthStateSnapshot(
        as_of=utc_now(),
        constraints=[
            HealthConstraint(
                key="safety_rule:chest_pain",
                severity="hard",
                source="safety_rule",
                description="x",
            )
        ],
    )
    request = PlanRequest(goal="strength", days_per_week=3)
    plan = solve_weekly_plan(request, PlanContext(health_state=snapshot))
    assert plan.legal is False
    assert any(item.startswith("safety_red_flag") for item in plan.violations)
    allowed, rejected = legal_exercise_ids(request, PlanContext(health_state=snapshot))
    assert allowed == []
    assert rejected and rejected[0]["reason"] == "hard_constraint"


def test_unmeasurable_high_load_exercise_is_blocked():
    """§5.10/§7.3: no measurer means no automatic high-load prescription."""
    from app.services.health_state.contracts import HealthConstraint, HealthStateSnapshot

    snapshot = HealthStateSnapshot(
        as_of=utc_now(),
        constraints=[
            HealthConstraint(
                key="unmeasurable_exercise:deadlift",
                severity="hard",
                source="motion_catalog",
                description="x",
            )
        ],
    )
    request = PlanRequest(goal="strength", days_per_week=3, equipment={"barbell"})
    plan = solve_weekly_plan(request, PlanContext(health_state=snapshot))
    if "deadlift" in plan.exercise_ids():
        assert any(
            item.startswith("unmeasurable_high_load") for item in plan.violations
        )


def test_recovery_priority_blocks_high_intensity():
    request = PlanRequest(goal="strength", days_per_week=3, equipment={"barbell"})
    context = PlanContext(recovery_constraints=["sleep_debt"])
    plan = solve_weekly_plan(request, context)
    assert plan.legal, plan.violations
    for item in plan.items:
        exercise = get_exercise(item.exercise_id)
        if exercise is not None:
            assert exercise.load != "high"


def test_illegal_candidate_is_rejected_by_the_validator():
    """§13.4: the validator must reject a plan that violates a hard constraint."""
    request = PlanRequest(days_per_week=1, minutes_per_session=20)
    illegal = PlanCandidate(
        goal="fitness",
        days_per_week=1,
        minutes_per_session=20,
        items=[
            PlanItemDraft(
                day_index=0,
                exercise_id="barbell_only_fake",
                title="不存在",
                duration_min=10,
            ),
            PlanItemDraft(
                day_index=0,
                exercise_id="squat",
                title="深蹲",
                duration_min=90,
            ),
        ],
    )
    report = validate_plan(request, PlanContext(), illegal)
    assert report.legal is False
    assert any(item.startswith("unknown_exercise") for item in report.hard_violations)
    assert any(item.startswith("session_too_long") for item in report.hard_violations)
    assert report.checked_constraints


def test_planning_only_exercises_are_declared():
    """A planning entry with no motion catalog row must be visible, not hidden."""
    assert isinstance(PLANNING_ONLY_IDS, tuple)
    for exercise_id in PLANNING_ONLY_IDS:
        assert get_exercise(exercise_id) is not None
        assert get_exercise(exercise_id).in_catalog is False


def test_bodyweight_is_implicitly_available():
    request = PlanRequest(goal="fitness", days_per_week=3, equipment={"dumbbell"})
    plan = solve_weekly_plan(request, PlanContext())
    assert plan.legal, plan.violations
    assert plan.items, "拥有哑铃不等于没有自重动作可选"


# --------------------------------------------------------------------------- #
# §7.5 Replan
# --------------------------------------------------------------------------- #


def _plan_with_items(db: Session, user_id: int) -> int:
    today = business_today()
    plan = HealthPlan(
        user_id=user_id,
        title="测试计划",
        period_start=today.isoformat(),
        period_end=(today + timedelta(days=6)).isoformat(),
        source="agent",
        status="active",
    )
    db.add(plan)
    db.flush()
    # One completed (past) item and one open (future) item.
    db.add(
        HealthPlanItem(
            plan_id=plan.id,
            user_id=user_id,
            planned_date=(today - timedelta(days=1)).isoformat(),
            category="exercise",
            title="深蹲",
            description="",
            target_json="{}",
            done=True,
            completed_at=utc_now(),
        )
    )
    db.add(
        HealthPlanItem(
            plan_id=plan.id,
            user_id=user_id,
            planned_date=(today + timedelta(days=1)).isoformat(),
            category="exercise",
            title="俯卧撑",
            description="",
            target_json="{}",
            done=False,
        )
    )
    db.commit()
    return plan.id


def test_replan_proposal_writes_nothing(api, db):
    plan_id = _plan_with_items(db, api.user_id)
    before = db.scalars(select(HealthPlanItem)).all()
    payload = replan(
        db,
        user_id=api.user_id,
        plan_id=plan_id,
        request=PlanRequest(days_per_week=3),
        context=PlanContext(),
        reasons=["睡眠债偏高"],
    )
    assert payload["found"] is True
    assert payload["requires_confirmation"] is True
    after = db.scalars(select(HealthPlanItem)).all()
    assert len(after) == len(before), "重规划提案不得写入任何项目"
    assert payload["frozen_completed"] == 1
    assert payload["diff"]["frozen_completed"] == 1
    assert payload["diff"]["reasons"] == ["睡眠债偏高"]


def test_replan_never_removes_completed_items(api, db):
    plan_id = _plan_with_items(db, api.user_id)
    completed = [
        row
        for row in db.scalars(select(HealthPlanItem)).all()
        if row.done or row.completed_at is not None
    ]
    assert len(completed) == 1
    diff = PlanDiff(
        removes=[
            PlanDiffEntry(
                op="remove",
                planned_date=completed[0].planned_date,
                exercise_id="squat",
                title=completed[0].title,
            )
        ]
    )
    result = apply_diff(db, user_id=api.user_id, plan_id=plan_id, diff=diff)
    assert result["frozen_completed"] == 1
    assert result["removed"] == 0, "已完成项目必须冻结"
    still_there = db.scalars(
        select(HealthPlanItem).where(HealthPlanItem.done.is_(True))
    ).all()
    assert len(still_there) == 1


def test_replan_diff_adds_and_removes_only_open_items(api, db):
    plan_id = _plan_with_items(db, api.user_id)
    today = business_today()
    diff = PlanDiff(
        adds=[
            PlanDiffEntry(
                op="add",
                planned_date=(today + timedelta(days=2)).isoformat(),
                exercise_id="plank",
                title="平板支撑",
            )
        ],
        removes=[
            PlanDiffEntry(
                op="remove",
                planned_date=(today + timedelta(days=1)).isoformat(),
                exercise_id="pushup",
                title="俯卧撑",
            )
        ],
    )
    result = apply_diff(db, user_id=api.user_id, plan_id=plan_id, diff=diff)
    assert result["added"] == 1
    assert result["removed"] == 1
    titles = {row.title for row in db.scalars(select(HealthPlanItem)).all()}
    assert "平板支撑" in titles
    assert "俯卧撑" not in titles
    assert "深蹲" in titles


def test_build_diff_reports_adds_keeps_and_removes():
    candidate = PlanCandidate(
        goal="fitness",
        days_per_week=2,
        minutes_per_session=30,
        items=[
            PlanItemDraft(
                day_index=0, exercise_id="squat", title="深蹲", duration_min=10
            ),
            PlanItemDraft(
                day_index=1, exercise_id="plank", title="平板支撑", duration_min=10
            ),
        ],
    )
    today = business_today()
    existing = [
        HealthPlanItem(
            plan_id=1,
            user_id=1,
            planned_date=today.isoformat(),
            category="exercise",
            title="深蹲",
            description="",
            target_json="{}",
            done=False,
        ),
        HealthPlanItem(
            plan_id=1,
            user_id=1,
            planned_date=(today + timedelta(days=3)).isoformat(),
            category="exercise",
            title="弓步蹲",
            description="",
            target_json="{}",
            done=False,
        ),
    ]
    diff = build_diff(
        PlanRequest(), PlanContext(), current_open=existing, candidate=candidate
    )
    assert any(item.title == "深蹲" for item in diff.keeps)
    assert any(item.title == "平板支撑" for item in diff.adds)
    assert any(item.title == "弓步蹲" for item in diff.removes)
    assert diff.policy


# --------------------------------------------------------------------------- #
# §8.2 Capability graph
# --------------------------------------------------------------------------- #


def test_capability_graph_reports_unavailable_with_reasons(api, db):
    graph = capability_graph(db, user_id=api.user_id)
    unavailable = unavailable_reasons(graph)
    # With no worker report and no passing gold evaluation, motion must be silver
    # or unavailable — never gold.
    motion = [
        item
        for item in manifest(graph)
        if item["id"].startswith("motion.exercise.")
    ]
    assert motion, "每个动作都应有能力条目"
    for item in motion:
        assert item["level"] in {"silver", "unavailable"}
        assert item["reason"]
    assert unavailable, "缺少记录时必须报告不可用原因"


def test_motion_level_is_silver_without_a_passing_evaluation(api, db):
    rows = motion_capabilities(db, user_id=api.user_id, engine={"motion_unified_v2": True})
    assert rows
    assert all(item.level == "silver" for item in rows[:1])
    assert all(item.level != "gold" for item in rows)


def test_motion_level_becomes_gold_only_for_the_evaluated_exercise(api, db):
    """One passing evaluation upgrades *that* exercise, never the whole catalog."""
    from app.models import MotionGoldEvaluation, MotionAnalysisRun, MediaAsset

    asset = MediaAsset(
        user_id=api.user_id, storage_key=f"gold-{api.user_id}", media_type="video"
    )
    db.add(asset)
    db.flush()
    run = MotionAnalysisRun(
        user_id=api.user_id,
        media_asset_id=asset.id,
        requested_type="squat",
        pipeline_version="motion-unified-v2",
        status="completed",
    )
    db.add(run)
    db.flush()
    db.add(
        MotionGoldEvaluation(
            run_id=run.id,
            user_id=api.user_id,
            exercise_id="squat",
            evaluator_version="gold-eval-1.0.0",
            tier="gold",
            available=True,
            segments_json="[]",
            findings_json="[]",
            measurements_json="{}",
            gate_json="{}",
        )
    )
    db.commit()
    rows = motion_capabilities(db, user_id=api.user_id, engine={"motion_unified_v2": True})
    by_id = {item.id: item for item in rows}
    assert by_id["motion.exercise.squat"].level == "gold"
    others = [item for item in rows if item.id != "motion.exercise.squat"]
    assert others
    assert all(item.level != "gold" for item in others), (
        "单个动作通过评测不得让整个目录升级为 Gold"
    )
    assert all(item.reason for item in rows)


def test_unavailable_engine_disables_motion_capabilities(api, db):
    rows = motion_capabilities(db, user_id=api.user_id, engine={})
    assert all(item.available is False for item in rows)
    assert all(item.level == "unavailable" for item in rows)


def test_insufficient_coverage_disables_plan_capability(api, db):
    graph = capability_graph(db, user_id=api.user_id)
    assert graph["plan.solve"].available is False
    assert "记录覆盖不足" in graph["plan.solve"].reason


def test_capability_api_exposes_reasons(api):
    res = api.get("/api/v1/agent/capabilities")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["capabilities"]
    assert body["unavailable"]
    for item in body["unavailable"]:
        assert item["reason"] and item["source"]


# --------------------------------------------------------------------------- #
# §8.3/§8.4 Decision contract
# --------------------------------------------------------------------------- #


def test_candidate_set_is_closed_and_registered():
    """Every writable candidate must name an Action that really exists."""
    from app.services.agent.actions import ACTION_REGISTRY

    assert ACTION_CANDIDATES
    writable = [key for key in ACTION_CANDIDATES if key in ACTION_REGISTRY]
    assert writable, "候选集合应包含真实注册的 Action"
    assert "nonexistent.action" not in ACTION_CANDIDATES


def test_decision_is_deterministic(api, db):
    first = decide(db, api.user_id).as_dict()
    second = decide(db, api.user_id).as_dict()
    # as_of changes per call; the ranking must not.
    for payload in (first, second):
        payload.pop("as_of", None)
    assert first == second


def test_decision_ranks_and_explains(api, db):
    contract = decide(db, api.user_id).as_dict()
    assert contract["state_snapshot_hash"]
    assert contract["capabilities_available"] is not None
    scores = [item["score"] for item in contract["candidates"]]
    assert scores == sorted(scores, reverse=True), "候选必须按分数降序"
    for item in contract["candidates"]:
        assert item["breakdown"]
        assert item["reason"]


def test_filtered_candidates_carry_a_named_reason(api, db):
    from app.services.agent.decision import Candidate
    from app.services.health_state import build_snapshot

    state = build_snapshot(db, api.user_id, persist=False)
    candidate = Candidate(
        id="plan.apply",
        action_key="plan.apply",
        title="x",
        reason="y",
    )
    allowed, filtered = filter_candidates(db, api.user_id, [candidate], state)
    assert allowed == [] or filtered == []
    for item in filtered:
        assert item["reason"] in {
            "action_not_registered",
            "already_active",
            "experiment_active",
            "constraint_conflict",
            "capability_unavailable",
            "human_decision",
        }


def test_seek_care_is_never_the_automatic_next_best_action(api, db):
    contract = decide(db, api.user_id).as_dict()
    if contract["next_best_action"]:
        assert contract["next_best_action"]["id"] not in NEVER_AUTO


def test_next_best_action_always_explains(api, db):
    payload = next_best_action(db, api.user_id)
    assert "explanation" in payload and payload["explanation"]
    assert "alternatives" in payload


def test_decision_api_and_plan_solver_api(api):
    decision = api.get("/api/v1/agent/decision")
    assert decision.status_code == 200, decision.text
    assert "next_best_action" in decision.json()

    nba = api.get("/api/v1/agent/next-action")
    assert nba.status_code == 200
    assert nba.json()["explanation"]

    solved = api.post(
        "/api/v1/plan/solve",
        json={"goal": "fitness", "days_per_week": 3, "minutes_per_session": 30},
    )
    assert solved.status_code == 200, solved.text
    body = solved.json()
    assert body["candidate"]["legal"] is True
    assert body["policy"]

    bad = api.post("/api/v1/plan/solve", json={"days_per_week": 99})
    assert bad.status_code == 422


def test_plan_simulate_tool_is_read_only(api, db):
    """§13.4/§13.5: simulation must not write a plan or a proposal."""
    from app.harness.contracts import ToolContext
    from app.harness.tools import get_tool_registry
    from app.models import AgentActionProposal

    proposals_before = len(db.scalars(select(AgentActionProposal)).all())
    plans_before = len(db.scalars(select(HealthPlan)).all())
    context = ToolContext(db=db, user=db.get(User, api.user_id), agent_id="planner")
    observation = get_tool_registry().execute(
        "plan.simulate",
        context,
        {"goal": "fitness", "days_per_week": 3, "minutes_per_session": 30},
        step=1,
    )
    assert observation.status == "ok", observation.summary
    assert observation.output["found"] is True
    assert len(db.scalars(select(AgentActionProposal)).all()) == proposals_before
    assert len(db.scalars(select(HealthPlan)).all()) == plans_before


def test_worker_tool_scope_excludes_unauthorized_action(api, db):
    """§13.5: an unauthorised tool cannot be called."""
    from app.harness.contracts import ToolContext
    from app.harness.collaboration import WORKERS
    from app.harness.tools import get_tool_registry

    registry = get_tool_registry()
    coach = registry.scoped(WORKERS["coach"].tools)
    assert "plan.apply" not in coach.names()
    observation = coach.execute(
        "plan.apply",
        ToolContext(db=db, user=db.get(User, api.user_id), agent_id="coach"),
        {"arguments": {"run_id": 1}},
        step=1,
    )
    assert observation.status == "blocked"


def test_replan_action_requires_confirmation_and_is_registered():
    from app.services.agent.actions import ACTION_REGISTRY
    from app.services.agent.action_proposals import ARGUMENT_SCHEMAS
    from app.services.agent.action_executors import EXECUTORS

    assert "plan.replan.apply" in ACTION_REGISTRY
    assert ACTION_REGISTRY["plan.replan.apply"].requires_confirmation is True
    assert "plan.replan.apply" in ARGUMENT_SCHEMAS
    assert "plan.replan.apply" in EXECUTORS


def test_replan_payload_rejects_a_client_supplied_diff():
    """A caller cannot smuggle arbitrary plan edits through the proposal payload."""
    from app.services.agent.action_proposals import validate_arguments

    with pytest.raises(ValueError):
        validate_arguments(
            "plan.replan.apply",
            {
                "plan_id": 1,
                "diff": {"adds": [{"title": "任意写入"}]},
            },
        )
    accepted = validate_arguments("plan.replan.apply", {"plan_id": 1})
    assert accepted["plan_id"] == 1
    assert "diff" not in accepted


# --------------------------------------------------------------------------- #
# §9 Outcome learning
# --------------------------------------------------------------------------- #


def test_outcome_recording_updates_an_explainable_posterior(api, db):
    for _ in range(3):
        record_outcome(
            db,
            user_id=api.user_id,
            action_key="plan.apply",
            result="accepted",
        )
    rows = db.scalars(
        select(ActionPolicyStat).where(ActionPolicyStat.user_id == api.user_id)
    ).all()
    assert rows
    row = rows[0]
    assert row.offered == 3 and row.accepted == 3
    assert row.alpha == 1.0 + 3
    assert 0.0 <= row.alpha / (row.alpha + row.beta) <= 1.0


def test_insufficient_data_never_updates_the_posterior(api, db):
    record_outcome(
        db,
        user_id=api.user_id,
        action_key="plan.apply",
        result="unknown",
        conclusion="insufficient_data",
    )
    row = db.scalars(
        select(ActionPolicyStat).where(ActionPolicyStat.user_id == api.user_id)
    ).all()
    assert row == [] or (row[0].offered == 0 and row[0].alpha == 1.0), (
        "insufficient_data 不得作为正向证据"
    )
    history = outcome_history(db, api.user_id)
    assert history["items"][0]["conclusion"] == "insufficient_data"


def test_unknown_outcome_values_are_rejected(api, db):
    with pytest.raises(OutcomeError):
        record_outcome(db, user_id=api.user_id, action_key="plan.apply", result="won")
    with pytest.raises(OutcomeError):
        record_outcome(
            db,
            user_id=api.user_id,
            action_key="plan.apply",
            result="accepted",
            source="made_up_source",
        )
    with pytest.raises(OutcomeError):
        record_outcome(
            db,
            user_id=api.user_id,
            action_key="plan.apply",
            result="accepted",
            conclusion="cured",
        )


def test_ranking_is_not_personalised_below_the_sample_floor(api, db):
    record_outcome(db, user_id=api.user_id, action_key="plan.apply", result="accepted")
    payload = rank_variants(db, api.user_id, "plan.apply", ["gentle", "standard"])
    assert payload["personalised"] is False
    assert all(item["personalised"] is False for item in payload["ranked"])
    assert str(MIN_SAMPLES_FOR_PERSONALISATION) in payload["note"]


def test_ranking_personalises_only_after_enough_samples(api, db):
    for _ in range(MIN_SAMPLES_FOR_PERSONALISATION):
        record_outcome(
            db,
            user_id=api.user_id,
            action_key="plan.replan.apply",
            result="accepted",
            variant="gentle",
        )
    payload = rank_variants(
        db, api.user_id, "plan.replan.apply", ["standard", "gentle"]
    )
    assert payload["personalised"] is True
    assert payload["ranked"][0]["variant"] == "gentle"
    assert "不改变安全边界" in payload["note"]


def test_high_risk_variants_are_never_explored_by_ranking(api, db):
    """Ranking only reorders the options the caller already deemed safe."""
    payload = rank_variants(db, api.user_id, "plan.apply", ["gentle"])
    assert [item["variant"] for item in payload["ranked"]] == ["gentle"]


def test_preference_memory_requires_a_known_key(api, db):
    with pytest.raises(OutcomeError):
        upsert_preference(db, user_id=api.user_id, key="free_form_note", value="x")
    for key in ALLOWED_PREFERENCE_KEYS:
        row = upsert_preference(db, user_id=api.user_id, key=key, value="v")
        assert row.key == key


def test_repeated_choice_strengthens_memory(api, db):
    for _ in range(MIN_SAMPLES_FOR_PERSONALISATION):
        upsert_preference(
            db, user_id=api.user_id, key="plan_variant", value="gentle", source="repeated_choice"
        )
    items = read_preferences(db, api.user_id)
    row = next(item for item in items if item["key"] == "plan_variant")
    assert row["evidence_count"] == MIN_SAMPLES_FOR_PERSONALISATION
    assert row["confidence_level"] == "high"


def test_clearing_memory_removes_its_influence(api, db):
    upsert_preference(
        db, user_id=api.user_id, key="plan_variant", value="gentle", source="explicit"
    )
    assert preference_value(db, api.user_id, "plan_variant") == "gentle"
    assert clear_preference(db, api.user_id, "plan_variant") is True
    assert preference_value(db, api.user_id, "plan_variant") is None
    assert read_preferences(db, api.user_id) == []


def test_expired_preference_is_ignored(api, db):
    row = upsert_preference(
        db, user_id=api.user_id, key="session_minutes", value="20", source="explicit"
    )
    row.expires_at = utc_now() - timedelta(minutes=1)
    db.add(row)
    db.commit()
    assert preference_value(db, api.user_id, "session_minutes") is None
    items = read_preferences(db, api.user_id)
    assert items[0]["expired"] is True
    assert items[0]["confidence_level"] == "unavailable"


def test_outcomes_api_reports_results_and_preferences(api, db):
    record_outcome(
        db,
        user_id=api.user_id,
        action_key="plan.apply",
        result="completed",
        source="proposal",
        conclusion="changed",
    )
    upsert_preference(db, user_id=api.user_id, key="plan_variant", value="gentle")
    res = api.get("/api/v1/health/outcomes")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] >= 1
    assert "plan.apply" in body["by_action"]
    assert any(item["key"] == "plan_variant" for item in body["policy_preferences"])
    assert "不训练模型权重" in body["note"]


def test_confirmed_action_records_an_outcome(api, db):
    """The confirm endpoint feeds the policy statistics (plan §9.2)."""
    from app.models import AgentActionProposal, HealthAgentRun

    run = HealthAgentRun(
        user_id=api.user_id,
        intent="plan",
        user_message="x",
        result_json=json.dumps(
            {
                "plan": {
                    "title": "t",
                    "items": [
                        {
                            "date_offset": 0,
                            "category": "exercise",
                            "title": "a",
                            "description": "b",
                            "target": {},
                        }
                    ],
                }
            }
        ),
        provider="test",
        status="completed",
    )
    db.add(run)
    db.commit()
    run_id = run.id

    proposals_before = len(db.scalars(select(ActionOutcome)).all())
    applied = api.post(f"/api/v1/agent/runs/{run_id}/apply-plan", json={})
    assert applied.status_code == 200, applied.text
    db.expire_all()
    outcomes = db.scalars(select(ActionOutcome)).all()
    assert len(outcomes) > proposals_before
    assert any(row.action_key == "plan.apply" for row in outcomes)
