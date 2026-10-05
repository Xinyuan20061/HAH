"""Reproducible synthetic acquisition-policy replay; not a user study."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.services.policy_learning.acquisition.oracle import execution_proof
from app.services.policy_learning.acquisition.planner import (
    ResponsePrior, SupportPairDomain, choose_next,
)
from baselines import STRATEGIES, select_query


DEFAULT_SEED = 20261005


def canonical_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_fixtures(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def generate_cases(config: dict, seed: int) -> list[dict]:
    rng = random.Random(seed)
    cases = []
    for mechanism, params in config["mechanisms"].items():
        for case_index in range(config["episodes_per_mechanism"]):
            truth = [rng.random() < 0.64 for _ in range(config["window_days"])]
            visible: list[bool | None] = []
            observed_before: list[bool] = []
            for slot, fact in enumerate(truth):
                probability = params["base_missing_probability"]
                if mechanism == "MAR" and observed_before:
                    completed_fraction = sum(observed_before) / len(observed_before)
                    if completed_fraction < 0.5:
                        probability += params["observed_low_execution_adjustment"]
                elif mechanism == "MNAR" and not fact:
                    probability += params["hidden_noncompletion_adjustment"]
                is_missing = rng.random() < min(0.95, probability)
                visible.append(None if is_missing else fact)
                if not is_missing:
                    observed_before.append(fact)
            known_slots = [i for i, value in enumerate(visible) if value is not None]
            revision_slot = rng.choice(known_slots) if known_slots else rng.randrange(7)
            revision_event = rng.choices(["none", "corrected", "deleted"], [0.90, 0.05, 0.05])[0]
            cases.append({
                "case_id": f"{mechanism.lower()}-{case_index:04d}",
                "mechanism": mechanism,
                "truth": truth,
                "initial_visible": visible,
                "due_slots": [i for i, value in enumerate(visible) if value is None],
                "response_uniforms": [rng.random() for _ in range(7)],
                "repair_uniforms": [rng.random() for _ in range(7)],
                "latency_ms": [rng.randint(2500, 5000) for _ in range(7)],
                "random_order": rng.sample(range(7), 7),
                "revision_event": revision_event,
                "revision_slot": revision_slot,
            })
    return cases


def _response(case: dict, slot: int, response_model: dict, *, repair: bool = False) -> str:
    if repair:
        return "answered" if case["repair_uniforms"][slot] < response_model["repair_answered_probability"] else "no_response"
    p_answered = response_model["answered_probability"]
    if case["mechanism"] == "MAR":
        known = [x for x in case["initial_visible"] if x is not None]
        completion_fraction = sum(known) / len(known) if known else 0.5
        p_answered = max(0.25, min(0.90, 0.45 + 0.30 * completion_fraction))
    elif case["mechanism"] == "MNAR" and not case["truth"][slot]:
        p_answered = max(0.20, p_answered - response_model["hidden_noncompletion_answer_adjustment"])
    remaining = 1 - p_answered
    unanswered_total = (response_model["unknown_probability"] + response_model["declined_probability"] +
                        response_model["no_response_probability"])
    u = case["response_uniforms"][slot]
    if u < p_answered:
        return "answered"
    u = (u - p_answered) / max(remaining, 1e-12) * unanswered_total
    if u < response_model["unknown_probability"]:
        return "unknown"
    u -= response_model["unknown_probability"]
    if u < response_model["declined_probability"]:
        return "declined"
    return "no_response"


def simulate(strategy: str, case: dict, config: dict, response_model: dict, *,
             allow_repair: bool = True, refusal_model: bool = True,
             unlimited_time: bool = False) -> dict:
    target = float(config["execution_target"])
    state = list(case["initial_visible"])
    truth = list(case["truth"])
    asked: set[str] = set()
    question_count = answered_count = simulated_ms = repair_questions = 0
    remaining_questions = int(config["max_questions"])
    remaining_ms = 60000 if unlimited_time else int(config["max_time_ms"])
    due = list(case["due_slots"])
    if strategy == "random_same_budget":
        order = {slot: i for i, slot in enumerate(case["random_order"])}
        due.sort(key=lambda slot: order[slot])
    elif strategy == "current_gate_with_fixed_acquisition_adapter":
        due.sort()

    if strategy == "fixed_fill":
        final_state = tuple(False if value is None else value for value in state)
        guessed = int(sum(final_state) / len(final_state) >= target)
        predicted = guessed
    else:
        prior = ResponsePrior(
            answered=1.0 if not refusal_model else response_model["answered_probability"],
            unknown=0.0 if not refusal_model else response_model["unknown_probability"],
            declined=0.0 if not refusal_model else response_model["declined_probability"],
            no_response=0.0 if not refusal_model else response_model["no_response_probability"],
            yes_given_answer=response_model["completion_probability_given_answer"],
        )
        while remaining_questions > 0 and remaining_ms > 0:
            proof = execution_proof(tuple(state), target)
            if proof.label is not None:
                break
            query = select_query(strategy, tuple(state), tuple(due), frozenset(asked),
                                target=target, questions_left=remaining_questions,
                                time_left_ms=remaining_ms, prior=prior)
            if query is None:
                break
            slot = int(query.split(":", 1)[1])
            asked.add(query)
            question_count += 1
            remaining_questions -= 1
            cost = min(case["latency_ms"][slot], remaining_ms)
            simulated_ms += cost
            remaining_ms -= cost
            status = _response(case, slot, response_model)
            if status == "answered":
                state[slot] = truth[slot]
                answered_count += 1
        initial_proof = execution_proof(tuple(state), target)
        had_certificate = initial_proof.label is not None
        certificate_binding = (tuple(state), tuple([1] * len(state))) if had_certificate else None
        revisions = [1] * len(state)
        invalidated_certificates = 0

        # Inject a shared source-revision event after initial acquisition. A current
        # binding check must block the old certificate before it can be consumed.
        revision = case["revision_event"]
        revision_slot = int(case["revision_slot"])
        if revision != "none" and state[revision_slot] is not None:
            revisions[revision_slot] += 1
            if revision == "corrected":
                truth[revision_slot] = not truth[revision_slot]
                state[revision_slot] = truth[revision_slot]
            else:
                state[revision_slot] = None
            current_binding = (tuple(state), tuple(revisions))
            if certificate_binding is not None and current_binding != certificate_binding:
                invalidated_certificates = 1
                certificate_binding = None
            if (revision == "deleted" and state[revision_slot] is None and
                    remaining_questions > 0 and remaining_ms > 0 and
                    strategy != "fixed_fill" and allow_repair):
                repair_questions = 1
                question_count += 1
                remaining_questions -= 1
                cost = min(case["latency_ms"][revision_slot], remaining_ms)
                simulated_ms += cost
                remaining_ms -= cost
                if _response(case, revision_slot, response_model, repair=True) == "answered":
                    state[revision_slot] = truth[revision_slot]
                    answered_count += 1
        final_state = tuple(state)
        predicted = execution_proof(final_state, target).label
        # This harness always checks the certificate binding before consumption.
        stale_certificate_uses = 0

    gold = int(sum(truth) / len(truth) >= target)
    determined = predicted is not None
    correct = determined and predicted == gold
    return {
        "case_id": case["case_id"], "mechanism": case["mechanism"],
        "strategy": strategy, "gold_label": gold, "predicted_label": predicted,
        "resolved": determined, "correct": bool(correct),
        "wrong_determined": bool(determined and not correct),
        "issued_questions": question_count, "answered_questions": answered_count,
        "simulated_time_ms": simulated_ms, "repair_questions": repair_questions,
        "certificate_invalidations": invalidated_certificates if strategy != "fixed_fill" else 0,
        "stale_certificate_uses": stale_certificate_uses if strategy != "fixed_fill" else 0,
        "deferred": not determined,
        "final_unknown_slots": [i for i, value in enumerate(final_state) if value is None],
        "revision_event": case["revision_event"],
    }


def summarize(rows: list[dict]) -> dict:
    count = len(rows)
    resolved = sum(row["resolved"] for row in rows)
    correct = sum(row["correct"] for row in rows)
    total_time = sum(row["simulated_time_ms"] for row in rows)
    return {
        "eligible_episodes": count,
        "resolved_episodes": resolved,
        "valid_resolutions": correct,
        "wrong_determined_labels": sum(row["wrong_determined"] for row in rows),
        "deferred_episodes": count - resolved,
        "issued_questions": sum(row["issued_questions"] for row in rows),
        "answered_questions": sum(row["answered_questions"] for row in rows),
        "total_measured_or_simulated_time_ms": total_time,
        "repair_questions": sum(row["repair_questions"] for row in rows),
        "certificate_invalidations": sum(row["certificate_invalidations"] for row in rows),
        "stale_certificate_uses": sum(row["stale_certificate_uses"] for row in rows),
        "cost_per_valid_resolution": total_time / correct if correct else None,
        "coverage_rate": resolved / count if count else 0.0,
        "accuracy_when_determined": correct / resolved if resolved else None,
        "mean_questions_per_episode": sum(row["issued_questions"] for row in rows) / count if count else 0.0,
    }


def controlled_complementary_pair(response_model: dict) -> dict:
    """A narrow planner-mechanism check, separate from the execution benchmark.

    Both policies receive the same two-question/eight-second budget. The
    one-step arm makes one horizon-1 decision and stops; the look-ahead arm
    values the baseline/follow-up pair, then replans after an answered baseline.
    The answered/answered trajectory is an explicit controlled branch, not an
    estimate of real user response or end-to-end policy superiority.
    """
    prior = ResponsePrior(
        answered=response_model["answered_probability"],
        unknown=response_model["unknown_probability"],
        declined=response_model["declined_probability"],
        no_response=response_model["no_response_probability"],
        yes_given_answer=response_model["completion_probability_given_answer"],
    )
    domain = SupportPairDomain(
        state=(0,), eligible_baseline_slots=(0,),
        eligible_followup_slots=(0,), required_pairs=1,
        cost_ms=4000, defer_penalty=20.0, prior=prior,
    )
    one = choose_next(domain, (0,), remaining_questions=2,
                      remaining_time_ms=8000, depth=1)
    lookahead = choose_next(domain, (0,), remaining_questions=2,
                            remaining_time_ms=8000, depth=2)
    pair_questions = []
    pair_state = (0,)
    remaining_questions = 2
    remaining_time_ms = 8000
    if lookahead.action == "ask":
        pair_questions.append(lookahead.query_key)
        pair_state = (1,)  # controlled answered branch for baseline
        remaining_questions -= 1
        remaining_time_ms -= 4000
        followup = choose_next(domain, pair_state,
                               remaining_questions=remaining_questions,
                               remaining_time_ms=remaining_time_ms, depth=2)
        if followup.action == "ask":
            pair_questions.append(followup.query_key)
            pair_state = (3,)  # controlled answered branch for follow-up
            remaining_questions -= 1
            remaining_time_ms -= 4000
    return {
        "design": "single_decision_horizon_complementarity",
        "budget_questions": 2,
        "budget_time_ms": 8000,
        "response_realization": "answered_then_answered",
        "one_step": {
            "depth": 1, "action": one.action, "query_key": one.query_key,
            "planned_pair_count": domain.pair_count((1,)) if one.action == "ask" else 0,
        },
        "two_step": {
            "depth": 2, "first_action": lookahead.action,
            "question_keys": pair_questions,
            "planned_pair_count": domain.pair_count(pair_state),
            "questions_used": len(pair_questions),
            "time_used_ms": 8000 - remaining_time_ms,
        },
        "interpretation": (
            "The controlled case verifies that a single horizon-1 decision defers a non-complementary half-pair, "
            "while horizon 2 recognizes and completes the baseline/follow-up pair. It is not an end-to-end "
            "comparison: a receding-horizon one-step policy could ask again after the first answer."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "missingness_configs.json")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    config = read_json(args.config)
    response_model = read_json(ROOT / "response_models.json")
    revision_scenarios = read_json(ROOT / "revision_scenarios.json")
    fixtures = load_fixtures(ROOT / "cases.jsonl")
    cases = fixtures + generate_cases(config, args.seed)
    results = {strategy: [simulate(strategy, case, config, response_model)
                          for case in cases] for strategy in STRATEGIES}
    summaries = {strategy: summarize(rows) for strategy, rows in results.items()}
    primary = summaries["two_step_decision_value"]
    budget_curve = []
    for budget in (0, 1, 2, 3):
        budget_config = {**config, "max_questions": budget}
        for strategy in ("random_same_budget", "one_step_decision_value",
                         "two_step_decision_value", "current_gate_with_fixed_acquisition_adapter"):
            rows = [simulate(strategy, case, budget_config, response_model) for case in cases]
            budget_curve.append({"question_budget": budget, "strategy": strategy,
                                 **summarize(rows)})
    ablations = {}
    ablation_specs = {
        "two_step_no_refusal_model": {"strategy": "two_step_decision_value", "refusal_model": False},
        "two_step_no_repair_after_source_deletion": {"strategy": "two_step_decision_value", "allow_repair": False},
        "entropy_objective": {"strategy": "entropy_first"},
        "two_step_without_time_budget": {"strategy": "two_step_decision_value", "unlimited_time": True},
    }
    for label, options in ablation_specs.items():
        strategy = options.pop("strategy")
        ablations[label] = summarize([
            simulate(strategy, case, config, response_model, **options) for case in cases
        ])
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO,
                                check=True, capture_output=True, text=True).stdout.strip()
    except Exception:
        commit = "unavailable"
    dataset_hash = digest(cases)
    config_bundle = {"missingness": config, "response_model": response_model,
                     "revision_scenarios": revision_scenarios}
    report = {
        "report_schema_version": "evidence-acquisition-report-v1",
        "git_commit": commit,
        "oracle_version": "exact-execution-v1",
        "planner_version": "bounded-lookahead-v1",
        "dataset_hash": dataset_hash,
        "config_hash": digest(config_bundle),
        "seed": args.seed,
        "primary_strategy": "two_step_decision_value",
        **primary,
        "strategies": summaries,
        "budget_curve": budget_curve,
        "ablations": ablations,
        "controlled_complementary_pair": controlled_complementary_pair(response_model),
        "missingness_mechanisms": sorted(config["mechanisms"]),
        "response_model_id": response_model["model_id"],
        "revision_scenario_count": len(revision_scenarios["scenarios"]),
        "evidence_level": "synthetic_replay",
        "limitations": [
            "Synthetic 7-slot execution episodes only; no user data and no WeChat device evaluation.",
            "The response prior is declared, not estimated from HealthMate users.",
            "This replay does not establish medical effectiveness or novelty over published methods.",
            "Certificate invalidation is a deterministic harness check, not a production-database concurrency proof.",
            "The bounded two-step policy did not outperform the simple acquisition baselines under this single frozen synthetic configuration; treat this as a negative result, not a novelty claim.",
        ],
    }
    output = args.output or ROOT / "results" / f"seed-{args.seed}"
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                                     allow_nan=False) + "\n", encoding="utf-8")
    with (output / "traces.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for strategy in STRATEGIES:
            for row in results[strategy]:
                stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                        allow_nan=False) + "\n")
    print(json.dumps({"report": str(output / "report.json"),
                      "dataset_hash": dataset_hash,
                      "strategies": summaries}, ensure_ascii=False, indent=2,
                      allow_nan=False))


if __name__ == "__main__":
    main()
