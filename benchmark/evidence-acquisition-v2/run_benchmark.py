"""v2 evidence-acquisition benchmark entrypoint (synthetic replay; not a user study).

Usage:
    backend/.venv/Scripts/python.exe run_benchmark.py --seed 20261005

Outputs (frozen after run):
    results/seed-<seed>/report.json
    results/seed-<seed>/traces.jsonl
    results/seed-<seed>/cases.jsonl          (generated cases, versioned)
    results/seed-<seed>/failure_cases.jsonl  (all failed/safety cases)

Pre-registered (frozen, spec A2): primary arms two_step vs one_step vs
random_same_budget vs entropy_first vs current_gate vs fixed_order vs
small_state_exact_reference; same cases, same per-slot response uniforms and
latency, same budget, failures included in denominators; direction (two-step
cost <= one-step at non-decreasing coverage) and safety gate (0 wrong
determinations, 0 stale certificate uses) fixed before running.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(REPO / "backend"))

from cases_generator import generate_cases
from simulate import simulate

STRATEGIES = (
    "two_step_decision_value", "one_step_decision_value",
    "random_same_budget", "entropy_first",
    "current_gate", "fixed_order", "small_state_exact_reference",
)


def canonical_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def p50_p95(values: list[float]) -> dict:
    if not values:
        return {"p50": None, "p95": None}
    values = sorted(values)
    def q(frac):
        idx = (len(values) - 1) * frac
        lo = int(idx)
        hi = min(lo + 1, len(values) - 1)
        return values[lo] + (values[hi] - values[lo]) * (idx - lo)
    return {"p50": q(0.50), "p95": q(0.95)}


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {}
    both_resolved = sum(r["both_resolved"] for r in rows)
    both_correct = sum(r["both_resolved"] and r["exec_correct"] and r["burden_correct"] for r in rows)
    exec_determined = sum(r["exec_label"] is not None for r in rows)
    exec_correct = sum(r["exec_correct"] for r in rows)
    burden_determined = sum(r["burden_pairs"] >= r["burden_gold"] and r["burden_gold"] == 1 for r in rows) + \
        sum(r["burden_gold"] == 0 for r in rows)
    times = [r["simulated_time_ms"] for r in rows]
    return {
        "episodes": n,
        "users": len({r["user_id"] for r in rows}),
        "both_resolved": both_resolved,
        "both_correct": both_correct,
        "coverage_rate": both_resolved / n,
        "accuracy_when_determined": both_correct / both_resolved if both_resolved else None,
        "exec_determined": exec_determined,
        "exec_correct": exec_correct,
        "burden_determined": burden_determined,
        "burden_correct": sum(r["burden_correct"] for r in rows),
        "wrong_determined": sum(r["wrong_determined"] for r in rows),
        "deferred": sum(r["deferred"] for r in rows),
        "issued_questions": sum(r["issued_questions"] for r in rows),
        "answered_questions": sum(r["answered_questions"] for r in rows),
        "total_time_ms": sum(times),
        "per_episode_time": p50_p95(times),
        "repair_questions": sum(r["repair_questions"] for r in rows),
        "certificate_invalidations": sum(r["certificate_invalidations"] for r in rows),
        "stale_certificate_uses": sum(r["stale_certificate_uses"] for r in rows),
        "duplicate_attempts": sum(r["duplicate_attempts"] for r in rows),
        "primary_cost_questions": (sum(r["issued_questions"] for r in rows) / both_correct
                                   if both_correct else None),
        "primary_cost_time_ms": (sum(r["simulated_time_ms"] for r in rows) / both_correct
                                 if both_correct else None),
    }


def bootstrap_ci(rows: list[dict], key: str, *, n_boot: int = 1200,
                 rng_seed: int = 1) -> dict:
    users = sorted({r["user_id"] for r in rows})
    by_user = {u: [r for r in rows if r["user_id"] == u] for u in users}
    rng = random.Random(rng_seed)
    values = []
    for _ in range(n_boot):
        sample = []
        for _ in range(len(users)):
            u = rng.choice(users)
            sample.append(rng.choice(by_user[u]))
        if key == "coverage_rate":
            values.append(sum(r["both_resolved"] for r in sample) / len(sample))
        elif key == "primary_cost_questions":
            correct = sum(r["both_resolved"] and r["exec_correct"] and r["burden_correct"] for r in sample)
            questions = sum(r["issued_questions"] for r in sample)
            values.append(questions / correct if correct else None)
        elif key == "primary_cost_time_ms":
            correct = sum(r["both_resolved"] and r["exec_correct"] and r["burden_correct"] for r in sample)
            ms = sum(r["simulated_time_ms"] for r in sample)
            values.append(ms / correct if correct else None)
    clean = [v for v in values if v is not None]
    clean.sort()
    if not clean:
        return {"lower": None, "upper": None}
    return {"lower": clean[int(0.025 * len(clean))],
            "upper": clean[int(0.975 * len(clean))]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=20261005)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    config = read_json(ROOT / "configs.json")
    cases = generate_cases(config, args.seed)
    rows_by_strategy = {
        s: [simulate(s, case, config, config["response_models"]) for case in cases]
        for s in STRATEGIES
    }
    summaries = {s: summarize(rows_by_strategy[s]) for s in STRATEGIES}
    test_rows = {s: [r for r in rows if r["split"] == "test"]
                 for s, rows in rows_by_strategy.items()}
    test_summaries = {s: summarize(rows) for s, rows in test_rows.items()}

    cis = {}
    for s in STRATEGIES:
        cis[s] = {
            "coverage_rate": bootstrap_ci(rows_by_strategy[s], "coverage_rate"),
            "primary_cost_questions": bootstrap_ci(rows_by_strategy[s], "primary_cost_questions"),
            "primary_cost_time_ms": bootstrap_ci(rows_by_strategy[s], "primary_cost_time_ms"),
        }

    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO,
                                check=True, capture_output=True, text=True).stdout.strip()
    except Exception:
        commit = "unavailable"
    dataset_hash = digest(cases)
    config_hash = digest(config)

    failure_cases = []
    for s in STRATEGIES:
        for r in rows_by_strategy[s]:
            if (r["wrong_determined"] or r["deferred"]
                    or r["stale_certificate_uses"] or r["duplicate_attempts"]):
                failure_cases.append(r)

    report = {
        "report_schema_version": "evidence-acquisition-report-v2",
        "git_commit": commit,
        "seed": args.seed,
        "dataset_hash": dataset_hash,
        "config_hash": config_hash,
        "response_model_id": config["response_model_id"],
        "calibration_state": "prior_only",
        "sample_count": 0,
        "evidence_level": "synthetic_replay",
        "primary_strategy": "two_step_decision_value",
        "pre_registered": {
            "arms": list(STRATEGIES),
            "direction": "two_step primary cost <= one_step at non-decreasing coverage",
            "safety_gate": "0 wrong determinations and 0 stale certificate uses in both_resolved cases",
            "sharing_rule": "per-user 70/30 split; tuning and final test share no user",
        },
        "strategies": summaries,
        "test_set": test_summaries,
        "bootstrap_ci_95": cis,
        "failure_cases_count": len(failure_cases),
        "case_type_counts": {t: sum(1 for c in cases if c["case_type"] == t) for t in
                             set(c["case_type"] for c in cases)},
        "split_counts": {u: sum(1 for c in cases if c["split"] == u)
                         for u in ("train", "test")},
        "limitations": [
            "Synthetic only; no user data and no real-device evaluation.",
            "Response probabilities are declared per case type (prior_only); not learned from users.",
            "Latency is simulated; P50/P95 are simulated user-reported time proxies, not measured user time.",
            "This replay does not establish medical effectiveness or novelty over published methods.",
            "Cost per correct determined endpoint counts questions and simulated time separately.",
        ],
    }
    output = args.output or ROOT / "results" / f"seed-{args.seed}"
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                                   allow_nan=False) + "\n", encoding="utf-8")
    with (output / "traces.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for s in STRATEGIES:
            for r in rows_by_strategy[s]:
                stream.write(json.dumps(r, ensure_ascii=False, sort_keys=True,
                                        allow_nan=False) + "\n")
    with (output / "cases.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for c in cases:
            stream.write(json.dumps(c, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False) + "\n")
    with (output / "failure_cases.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for r in failure_cases:
            stream.write(json.dumps(r, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False) + "\n")
    print(json.dumps({
        "report": str(output / "report.json"),
        "dataset_hash": dataset_hash,
        "case_counts": report["case_type_counts"],
        "split_counts": report["split_counts"],
        "primary": {s: {k: summaries[s][k] for k in
                        ("both_resolved", "coverage_rate", "accuracy_when_determined",
                         "primary_cost_questions", "primary_cost_time_ms",
                         "wrong_determined", "stale_certificate_uses")} for s in STRATEGIES},
    }, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
