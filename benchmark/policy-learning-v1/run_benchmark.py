"""Deterministic WP5 benchmark for evidence-gated personal policy learning."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.policy_learning.algorithm import (
    BetaBelief, BeliefSet, Candidate, EpisodeEvidence, Point, Protocol, Scope,
    adjudicate, rank_candidates,
)


ROOT = Path(__file__).parent


def _protocol() -> Protocol:
    return Protocol(Scope(1, "demo", "1", "m1", "ctx"), expected_days=5, minimum_days=4, minimum_coverage=.8, execution_target=.8, target=1.0, ambiguity_band=.1)


def _points(delta: float):
    p = _protocol()
    baseline = tuple(Point(i, 5.0, f"b{i}", 1, "m1") for i in range(5))
    followup = tuple(Point(i, 5.0 + delta, f"f{i}", 1, "m1") for i in range(5))
    return p, baseline, followup


def adjudication_cases(*, naive_missing_zero: bool = False):
    p, b, f = _points(1.2)
    cases = {
        "missing_execution": EpisodeEvidence("missing", 1, p, (True, False if naive_missing_zero else None, False, False if naive_missing_zero else None, False if naive_missing_zero else None), b, f, "ctx", "ctx"),
        "insufficient_exposure": EpisodeEvidence("exposure", 1, p, (True, False, False, False, None), b, f, "ctx", "ctx"),
        "supports_target": EpisodeEvidence("support", 1, p, (True, True, True, True, True), b, f, "ctx", "ctx"),
    }
    p2, b2, f2 = _points(.2)
    cases["target_not_supported"] = EpisodeEvidence("negative", 1, p2, (True, True, True, True, True), b2, f2, "ctx", "ctx")
    p3, b3, f3 = _points(1.05)
    cases["ambiguous_target"] = EpisodeEvidence("ambiguous", 1, p3, (True, True, True, True, True), b3, f3, "ctx", "ctx")
    cases["confounded"] = EpisodeEvidence("confounded", 1, p, (True, True, True, True, True), b, f, "ctx", "ctx", confounders=("illness",))
    cases["stopped"] = EpisodeEvidence("stopped", 1, p, (True, True, None, None, None), b, f, "ctx", "ctx", adverse_event=True)
    return cases


def run(ablation: str | None = None):
    expected = {row["id"]: row["expected"] for row in json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))}
    cases = adjudication_cases(naive_missing_zero=ablation == "naive_missing_zero")
    verdicts = {key: adjudicate(value).conclusion for key, value in cases.items()}
    scope = Scope(1, "safe", "1", "m1", "ctx")
    candidates = [Candidate("blocked", scope, .1, safe=ablation == "no_hard_filter"), Candidate("safe", scope, .1)]
    ranking = rank_candidates({}, candidates, user_id=1, context_key="ctx")
    verdicts["hard_constraint"] = ranking["selected"]
    checks = {
        "B0_gate_correctness": verdicts["missing_execution"] == expected["missing_execution"] and verdicts["insufficient_exposure"] == expected["insufficient_exposure"],
        "B1_missing_robustness": verdicts["missing_execution"] != "target_not_supported",
        "B2_outcome_judgement": all(verdicts[key] == expected[key] for key in ("supports_target", "target_not_supported", "ambiguous_target", "confounded", "stopped")),
        "B3_hard_filter": verdicts["hard_constraint"] == ("blocked" if ablation == "no_hard_filter" else "safe"),
        "B4_reproducibility": verdicts == {key: adjudicate(value).conclusion for key, value in cases.items()} | {"hard_constraint": ranking["selected"]},
    }
    return {"benchmark": "policy-learning-v1", "ablation": ablation or "evidence_gated", "checks": checks, "verdicts": verdicts, "pass": all(checks.values()), "ablation_expected_failure": ablation == "naive_missing_zero" and not checks["B0_gate_correctness"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ablation", choices=["naive_missing_zero", "no_hard_filter"])
    args = parser.parse_args()
    print(json.dumps(run(args.ablation), ensure_ascii=False, sort_keys=True))
