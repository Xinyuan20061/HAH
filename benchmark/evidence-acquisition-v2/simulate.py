"""v2 simulator: exact execution endpoint + paired burden endpoint.

Replay logic mirrors the frozen v1 runner where it overlaps (per-slot
response uniforms and latency shared across strategies, exact oracle, frozen
budget) and adds: composite domain state, burden pairs, knowledge withdrawal,
certificate invalidation with repair, and day-split duplicate semantics.
No frozen v1 file is modified.
"""

from __future__ import annotations

from typing import Any

from domain import CompositeDomain, ResponsePrior
from app.services.policy_learning.acquisition.oracle import execution_proof
from app.services.policy_learning.acquisition.planner import choose_next


def _response(case: dict, slot: int, model: dict) -> str:
    p_answered = model["answered"]
    if case["case_type"] == "mnar" and not case["truth"][slot]:
        p_answered = max(0.20, p_answered - model.get("hidden_noncompletion_adjustment", 0.0))
    remaining = 1 - p_answered
    unanswered_total = model["unknown"] + model["declined"] + model["no_response"]
    u = case["response_uniforms"][slot]
    if u < p_answered:
        return "answered"
    u = (u - p_answered) / max(remaining, 1e-12) * unanswered_total
    if u < model["unknown"]:
        return "unknown"
    u -= model["unknown"]
    if u < model["declined"]:
        return "declined"
    return "no_response"


def _repair_response(case: dict, slot: int, model: dict) -> str:
    p = model.get("repair_answered", 0.70)
    return "answered" if case["response_uniforms"][slot] < p else "no_response"


def _initial_burden_bits(case: dict) -> list[int]:
    # bit0 = baseline available/answered; burden starts fully unanswered.
    return [0] * len(case["initial_visible"])


def select_query(strategy: str, domain: CompositeDomain, state, asked, *,
                 config: dict, case: dict) -> str | None:
    if domain.resolved(state):
        return None
    candidates = domain.candidates(state, asked)
    if not candidates:
        return None
    target = float(config["execution_target"])
    if strategy in ("one_step_decision_value", "two_step_decision_value"):
        hashable_state = (tuple(state[0]), tuple(state[1]))
        plan = choose_next(domain, hashable_state,
                           remaining_questions=config["max_questions"],
                           remaining_time_ms=config["max_time_ms"],
                           depth=1 if strategy == "one_step_decision_value" else 2,
                           asked=frozenset(asked))
        return plan.query_key
    if strategy == "current_gate":
        # Production gate adapter: fixed order over due execution slots only.
        for slot in sorted(case["due_slots"]):
            if f"execution:{slot}" in asked:
                continue
            for q in candidates:
                if q.key == f"execution:{slot}":
                    return q.key
        return None
    if strategy == "fixed_order":
        for q in sorted(candidates, key=lambda item: item.key):
            if q.cost_ms <= config["max_time_ms"]:
                return q.key
        return None
    if strategy == "random_same_budget":
        order = {slot: i for i, slot in enumerate(case["random_order"])}
        best = None
        for q in candidates:
            slot = int(q.key.split(":", 1)[1])
            if best is None or order[slot] < order[int(best.split(":", 1)[1])]:
                best = q.key
        return best
    if strategy == "entropy_first":
        from baselines_v2 import label_entropy_gain
        return label_entropy_gain(domain, state, candidates, target, asked)
    if strategy == "small_state_exact_reference":
        from baselines_v2 import exact_best
        return exact_best(domain, (tuple(state[0]), tuple(state[1])), candidates, asked,
                          questions_left=config["max_questions"],
                          time_left_ms=config["max_time_ms"])
    raise ValueError(f"unknown strategy: {strategy}")


def simulate(strategy: str, case: dict, config: dict, response_models: dict,
             *, allow_repair: bool = True) -> dict:
    model = response_models[case["case_type"]]
    target = float(config["execution_target"])
    state = (list(case["initial_visible"]), _initial_burden_bits(case))
    truth = list(case["truth"])
    required_pairs = 1 if any(case["burden_available"]) else 0
    domain = CompositeDomain(
        execution_target=target,
        eligible_execution_slots=tuple(case["due_slots"]),
        eligible_burden_slots=tuple(i for i, ok in enumerate(case["burden_available"]) if ok),
        required_pairs=required_pairs,
        execution_cost_ms=int(config["execution_cost_ms"]),
        burden_cost_ms=int(config["burden_cost_ms"]),
        defer_penalty=float(config["defer_penalty"]),
        per_hole_penalty=float(config["per_hole_penalty"]),
        burden_missing_penalty=float(config["burden_missing_penalty"]),
        prior=ResponsePrior(
            answered=model["answered"], unknown=model["unknown"],
            declined=model["declined"], no_response=model["no_response"],
            yes_given_answer=model["yes_given_answer"],
        ),
    )
    asked: set[str] = set()
    remaining_questions = int(config["max_questions"])
    remaining_ms = int(config["max_time_ms"])
    issued = answered = 0
    simulated_ms = 0
    duplicate_attempts = 0

    while remaining_questions > 0 and remaining_ms > 0:
        if domain.resolved(state):
            break
        query = select_query(strategy, domain, state, frozenset(asked),
                             config=config, case=case)
        if query is None:
            break
        if query in asked:
            duplicate_attempts += 1  # safety signal; planner never does this
            break
        asked.add(query)
        issued += 1
        remaining_questions -= 1
        kind, raw = query.split(":", 1)
        slot = int(raw)
        cost = min(case["latency_ms"][slot], remaining_ms)
        simulated_ms += cost
        remaining_ms -= cost
        status = _response(case, slot, model) if kind == "execution" else _response(case, slot, model)
        if status != "answered":
            continue
        answered += 1
        if kind == "execution":
            state[0][slot] = truth[slot]
        elif kind == "burden_baseline":
            state[1][slot] |= 1
        elif kind == "burden_followup":
            state[1][slot] |= 2

    # ---- post-acquisition events (frozen contract: old binding must die) ----
    exec_label = execution_proof(tuple(state[0]), target).label
    certificate_invalidations = 0
    repair_questions = 0
    stale_certificate_uses = 0
    revision = case["revision_event"]
    revision_slot = int(case["revision_slot"])
    if revision != "none" and state[0][revision_slot] is not None:
        before_binding = (tuple(state[0]), tuple(state[1]))
        if revision == "corrected":
            truth[revision_slot] = not truth[revision_slot]
            state[0][revision_slot] = truth[revision_slot]
        else:
            state[0][revision_slot] = None
        after_binding = (tuple(state[0]), tuple(state[1]))
        if before_binding != after_binding:
            certificate_invalidations = 1
        if (revision == "deleted" and state[0][revision_slot] is None
                and remaining_questions > 0 and remaining_ms > 0 and allow_repair):
            repair_questions = 1
            issued += 1
            remaining_questions -= 1
            cost = min(case["latency_ms"][revision_slot], remaining_ms)
            simulated_ms += cost
            remaining_ms -= cost
            if _repair_response(case, revision_slot, model) == "answered":
                state[0][revision_slot] = truth[revision_slot]
                answered += 1

    # Knowledge withdrawal: completed burden pair becomes pending again.
    if case["knowledge_withdrawal"]:
        for slot in range(len(state[1])):
            if state[1][slot] == 3 and remaining_questions > 0 and remaining_ms > 0:
                state[1][slot] = 0  # pending re-verification
                certificate_invalidations += 1
                repair_questions += 1
                issued += 1
                remaining_questions -= 1
                cost = min(case["latency_ms"][slot], remaining_ms)
                simulated_ms += cost
                remaining_ms -= cost
                if _repair_response(case, slot, model) == "answered":
                    state[1][slot] = 3
                    answered += 1

    exec_label_final = execution_proof(tuple(state[0]), target).label
    burden_final = sum(1 for b in state[1] if b == 3)
    # Gold is evaluated against the FINAL truth (after any source revision);
    # the final label is evaluated against the final state. Both use the same
    # truth epoch, so a corrected record never counts as a wrong determination.
    exec_gold = int(sum(truth) / len(truth) >= target)
    burden_gold = 1 if any(case["burden_available"]) else 0
    both_resolved = exec_label_final is not None and burden_final >= required_pairs
    exec_correct = exec_label_final is not None and exec_label_final == exec_gold
    burden_correct = burden_final >= required_pairs and required_pairs == burden_gold
    wrong = both_resolved and not (exec_correct and burden_correct)
    return {
        "case_id": case["case_id"], "user_id": case["user_id"],
        "split": case["split"], "case_type": case["case_type"],
        "strategy": strategy,
        "exec_gold": exec_gold, "burden_gold": burden_gold,
        "exec_label": exec_label_final, "burden_pairs": burden_final,
        "both_resolved": both_resolved,
        "exec_correct": exec_correct, "burden_correct": burden_correct,
        "wrong_determined": wrong,
        "deferred": not both_resolved,
        "issued_questions": issued, "answered_questions": answered,
        "simulated_time_ms": simulated_ms,
        "repair_questions": repair_questions,
        "certificate_invalidations": certificate_invalidations,
        "stale_certificate_uses": stale_certificate_uses,
        "duplicate_attempts": duplicate_attempts,
        "asked_keys": sorted(asked),
        "revision_event": case["revision_event"],
    }
