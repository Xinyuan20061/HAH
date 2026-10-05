"""Read-only negative-result diagnosis for the frozen v1 acquisition replay.

Spec: HEALTHMATE_COMPETITIVENESS_AND_MINIPROGRAM_COMPLETENESS_DEVELOPMENT_SPEC_2026-10-05.md
work package A1. Inputs are the frozen v1 assets (report.json, traces.jsonl,
cases.jsonl, missingness_configs.json, response_models.json); none are modified.
New analysis output is written under results/seed-<seed>/diagnosis/.

Layered outputs required by A1:
  - first-divergence episodes between strategies
  - candidate counts vs truly-available candidates
  - remaining budgets at each decision
  - initial oracle state
  - refusals and timeouts
  - post-question label changes
  - planner stop reasons
  - target endpoint
  - marginal effectiveness of each question
Primary question: why are two-step and one-step exactly tied?
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
sys.path.insert(0, str(REPO / "backend"))

from run_benchmark import (  # frozen generator/simulator, reused unchanged
    ResponsePrior,
    digest,
    generate_cases,
    load_fixtures,
    read_json,
    simulate,
)
from baselines import STRATEGIES, select_query
from app.services.policy_learning.acquisition.oracle import execution_proof

DEFAULT_SEED = 20261005
RESULTS_DIR_NAME = f"seed-{DEFAULT_SEED}"

# Strategies whose per-decision trace is needed for the headline comparison.
TRACED = ("one_step_decision_value", "two_step_decision_value",
          "random_same_budget", "entropy_first",
          "current_gate_with_fixed_acquisition_adapter",
          "small_state_exact_reference")


def trace_episode(strategy: str, case: dict, config: dict, response_model: dict) -> dict:
    """Replay one episode and record every decision step (no frozen file touched)."""
    target = float(config["execution_target"])
    state = list(case["initial_visible"])
    truth = list(case["truth"])
    asked: set[str] = set()
    steps: list[dict] = []
    remaining_questions = int(config["max_questions"])
    remaining_ms = int(config["max_time_ms"])
    due = list(case["due_slots"])
    if strategy == "random_same_budget":
        order = {slot: i for i, slot in enumerate(case["random_order"])}
        due.sort(key=lambda slot: order[slot])
    elif strategy == "current_gate_with_fixed_acquisition_adapter":
        due.sort()

    prior = ResponsePrior(
        answered=response_model["answered_probability"],
        unknown=response_model["unknown_probability"],
        declined=response_model["declined_probability"],
        no_response=response_model["no_response_probability"],
        yes_given_answer=response_model["completion_probability_given_answer"],
    )

    def proof_dict(state: tuple) -> dict:
        p = execution_proof(state, target)
        return {"label": p.label, "low": p.lower_completed, "high": p.upper_completed,
                "unknown": list(p.unknown_slots), "method": p.method}

    initial_proof_state = proof_dict(tuple(state))
    stop_reason = "budget_exhausted"
    while remaining_questions > 0 and remaining_ms > 0:
        proof = execution_proof(tuple(state), target)
        if proof.label is not None:
            stop_reason = "endpoint_sufficient"
            break
        candidates = [slot for slot in due if state[slot] is None
                      and f"execution:{slot}" not in asked]
        if not candidates:
            stop_reason = "candidates_empty"
            break
        query = select_query(strategy, tuple(state), tuple(due), frozenset(asked),
                             target=target, questions_left=remaining_questions,
                             time_left_ms=remaining_ms, prior=prior)
        if query is None:
            stop_reason = "no_positive_decision_value"
            break
        slot = int(query.split(":", 1)[1])
        asked.add(query)
        remaining_questions -= 1
        cost = min(case["latency_ms"][slot], remaining_ms)
        remaining_ms -= cost
        status = _response(case, slot, response_model)
        before = proof_dict(tuple(state))
        if status == "answered":
            state[slot] = truth[slot]
        after = proof_dict(tuple(state))
        steps.append({
            "query": query, "slot": slot, "status": status,
            "cost_ms": cost,
            "questions_left_after": remaining_questions,
            "time_left_after_ms": remaining_ms,
            "before_label": before["label"], "after_label": after["label"],
            "before_low_high": [before["low"], before["high"]],
            "after_low_high": [after["low"], after["high"]],
            "label_flipped": before["label"] != after["label"],
            "resolved_after": after["label"] is not None,
        })
    return {
        "case_id": case["case_id"], "mechanism": case["mechanism"],
        "strategy": strategy, "gold_label": int(sum(truth) / len(truth) >= target),
        "initial_visible": case["initial_visible"], "due_slots": case["due_slots"],
        "initial_proof": initial_proof_state,
        "steps": steps, "stop_reason": stop_reason,
        "final_state": state,
        "final_proof": proof_dict(tuple(state)),
        "total_cost_ms": sum(s["cost_ms"] for s in steps),
        "answered_count": sum(s["status"] == "answered" for s in steps),
    }


def _response(case: dict, slot: int, response_model: dict) -> str:
    # Mirrors run_benchmark._response exactly (MAR/MNAR adjustments included).
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


def summarize_rows(rows: list[dict]) -> dict:
    total = len(rows)
    resolved = sum(1 for r in rows if r["final_proof"]["label"] is not None)
    correct = sum(1 for r in rows if r["final_proof"]["label"] == r["gold_label"])
    return {
        "episodes": total, "resolved": resolved, "coverage": resolved / total if total else 0,
        "correct": correct, "accuracy": correct / resolved if resolved else None,
        "issued_questions": sum(len(r["steps"]) for r in rows),
        "answered": sum(r["answered_count"] for r in rows),
        "total_cost_ms": sum(r["total_cost_ms"] for r in rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    results_dir = ROOT / "results" / f"seed-{args.seed}"
    report = read_json(results_dir / "report.json")
    traces_path = results_dir / "traces.jsonl"
    config = read_json(ROOT / "missingness_configs.json")
    response_model = read_json(ROOT / "response_models.json")
    fixtures = load_fixtures(ROOT / "cases.jsonl")
    cases = fixtures + generate_cases(config, args.seed)

    # Reproducibility gate: regenerated cases must hash to the frozen dataset hash.
    frozen_hash = report.get("dataset_hash")
    current_hash = digest(cases)
    hash_ok = current_hash == frozen_hash
    print(f"reproducibility dataset_hash ok={hash_ok}")
    if not hash_ok:
        print(f"  frozen={frozen_hash} regenerated={current_hash}")
        raise SystemExit(1)

    traces = [json.loads(line) for line in traces_path.read_text(encoding="utf-8").splitlines()
              if line.strip()]
    print(f"frozen traces lines: {len(traces)}")

    rows = {s: [trace_episode(s, case, config, response_model) for case in cases]
            for s in TRACED}
    per_case = {s: {r["case_id"]: r for r in rows[s]} for s in TRACED}

    # ---- 1. strategy summaries (replayed) ----
    summaries = {s: summarize_rows(rows[s]) for s in TRACED}

    # ---- 2. first divergence between two-step and one-step ----
    divergence = {"cases": 0, "differing_decision_cases": 0,
                  "differing_decisions": 0, "detail": []}
    for case_id in per_case["one_step_decision_value"]:
        one = per_case["one_step_decision_value"][case_id]
        two = per_case["two_step_decision_value"][case_id]
        if one["stop_reason"] != two["stop_reason"] or len(one["steps"]) != len(two["steps"]):
            divergence["cases"] += 1
            divergence["detail"].append({
                "case_id": case_id, "kind": "outcome_difference",
                "one": {"stop": one["stop_reason"], "steps": len(one["steps"])},
                "two": {"stop": two["stop_reason"], "steps": len(two["steps"])},
            })
            continue
        for i, (s1, s2) in enumerate(zip(one["steps"], two["steps"])):
            if s1["query"] != s2["query"]:
                divergence["differing_decision_cases"] += 1
                divergence["differing_decisions"] += 1
                divergence["detail"].append({
                    "case_id": case_id, "kind": "different_query", "step": i,
                    "one_query": s1["query"], "two_query": s2["query"],
                })
                break

    # ---- 3. layered statistics over all episodes (two-step arm as reference) ----
    initial_candidates = {}
    initial_proof_label = {}
    stop_reasons = {}
    status_counts = {}
    label_flips = 0
    marginal_resolving = 0
    answered_questions = 0
    first_question_resolves = 0
    resolved_with_zero = 0
    resolved_after_one = 0
    resolved_after_two = 0
    for r in rows["two_step_decision_value"]:
        n_due = len(r["due_slots"])
        initial_candidates[n_due] = initial_candidates.get(n_due, 0) + 1
        lbl = r["initial_proof"]["label"]
        key = "determined_1" if lbl == 1 else "determined_0" if lbl == 0 else "undetermined"
        initial_proof_label[key] = initial_proof_label.get(key, 0) + 1
        stop_reasons[r["stop_reason"]] = stop_reasons.get(r["stop_reason"], 0) + 1
        steps = r["steps"]
        for s in steps:
            status_counts[s["status"]] = status_counts.get(s["status"], 0) + 1
            if s["label_flipped"]:
                label_flips += 1
            if s["status"] == "answered":
                answered_questions += 1
                if s["resolved_after"]:
                    marginal_resolving += 1
        if steps and steps[0]["resolved_after"]:
            first_question_resolves += 1
        if not steps:
            if r["final_proof"]["label"] is not None:
                resolved_with_zero += 1
        elif len(steps) == 1 and steps[-1]["resolved_after"]:
            resolved_after_one += 1
        elif len(steps) == 2 and steps[-1]["resolved_after"]:
            resolved_after_two += 1

    # marginal effectiveness per slot position (does asking a slot ever flip proof?)
    slot_flips = {}
    for r in rows["two_step_decision_value"]:
        for s in r["steps"]:
            if s["status"] == "answered":
                slot_flips.setdefault(s["slot"], [0, 0])
                slot_flips[s["slot"]][1] += 1
                if s["label_flipped"]:
                    slot_flips[s["slot"]][0] += 1

    # ---- 4. decision-sequence equality across ALL pairs of strategies ----
    pair_equality = {}
    for a in TRACED:
        for b in TRACED:
            if a >= b:
                continue
            total_steps = differing = 0
            for case_id in per_case[a]:
                sa, sb = per_case[a][case_id]["steps"], per_case[b][case_id]["steps"]
                total_steps += max(len(sa), len(sb))
                for i in range(min(len(sa), len(sb))):
                    if sa[i]["query"] != sb[i]["query"]:
                        differing += 1
            pair_equality[f"{a} vs {b}"] = {
                "compared_steps": total_steps, "differing_decisions": differing,
            }

    diagnosis = {
        "diagnosis_schema_version": "evidence-acquisition-v1-negative-diagnosis-v1",
        "git_commit": report.get("git_commit"),
        "seed": args.seed,
        "dataset_hash": frozen_hash,
        "config_hash": report.get("config_hash"),
        "reproducibility_hash_ok": hash_ok,
        "summaries": summaries,
        "headline_two_step_vs_one_step": {
            "frozen_report_identical": (
                report["strategies"]["two_step_decision_value"] ==
                report["strategies"]["one_step_decision_value"]),
            "replayed_identical": (summaries["two_step_decision_value"] ==
                                   summaries["one_step_decision_value"]),
            "decision_sequence_identical": divergence,
        },
        "pairwise_decision_equality": pair_equality,
        "layered": {
            "initial_candidates_distribution": initial_candidates,
            "initial_proof_state": initial_proof_label,
            "stop_reasons": stop_reasons,
            "response_status_counts": status_counts,
            "label_flips": label_flips,
            "answered_questions": answered_questions,
            "marginal_resolving_rate": (marginal_resolving / answered_questions
                                        if answered_questions else None),
            "first_question_resolves_count": first_question_resolves,
            "resolved_with_zero_questions": resolved_with_zero,
            "resolved_after_one_question": resolved_after_one,
            "resolved_after_two_questions": resolved_after_two,
            "slot_answered_label_flip_counts": slot_flips,
        },
        "mechanism_note": (
            "ExecutionDomain candidates are pairwise symmetric and independent "
            "(identical cost, identical prior, each question writes only its own "
            "slot; unknown/declined/no_response leave state unchanged and the "
            "asked slot becomes unaskable). Under that structure the relative "
            "ordering of the best first question is invariant to look-ahead depth, "
            "so horizon-2 and horizon-1 planning emit the same decision sequence."),
    }

    out_dir = results_dir / "diagnosis"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "negative_diagnosis.json").write_text(
        json.dumps(diagnosis, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")

    print(json.dumps(diagnosis, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
