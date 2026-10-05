# Evidence acquisition v1 — synthetic replay

This benchmark compares bounded acquisition strategies on seven-day execution
endpoints. It is a deterministic synthetic replay, not a clinical study, user
study, deployment result, or claim of method novelty.

## Reproduce

From the repository root:

```powershell
.\backend\.venv\Scripts\python.exe benchmark/evidence-acquisition-v1/run_benchmark.py --config benchmark/evidence-acquisition-v1/missingness_configs.json --seed 20261005
```

The command writes `report.json` and per-case `traces.jsonl` under
`benchmark/evidence-acquisition-v1/results/seed-20261005/`. The report embeds
the real local Git revision when available, plus a hash of the generated case
set and the full configuration bundle. The response uniforms and latency for a
case/slot are shared by every strategy.

## Strategies

- `fixed_fill`: treats missing execution values as not completed. It is a
  deliberately unsafe point-imputation baseline and always guesses a label.
- `random_same_budget`: asks a seeded random order of due unknown slots.
- `entropy_first`: asks the slot with the largest expected reduction in binary
  endpoint-label entropy.
- `one_step_decision_value`: invokes the production bounded planner at horizon 1.
- `two_step_decision_value`: invokes it at horizon 2.
- `current_gate_with_fixed_acquisition_adapter`: asks due slots in order until
  the exact proof resolves or the budget is exhausted.
- `small_state_exact_reference`: an independent exhaustive recursive evaluator
  over the same finite domain, response prior, and loss.

MCAR, MAR, and MNAR differ only in the synthetic missingness/response
mechanisms declared in the JSON configuration. The exact execution oracle does
not depend on these probability assumptions; selection performance does.
`revision_scenarios.json` records event contracts. The replay injects a source
correction or deletion into a shared case and checks that an old binding is
blocked before certificate reuse; it does not simulate the production database
or its concurrency model.

## Read the results carefully

`coverage_rate` is the fraction of cases with a determined label. Accuracy is
reported only among determined labels; `wrong_determined_labels` is kept
separate so a strategy cannot look safer by deferring everything. Cost per
valid resolution is null when no correct label is determined. The comparison
is workload-specific, uses a declared response prior, and says nothing about
real user burden. No target improvement is assumed; report whatever the run
produces.

The JSON report also contains a 0–3 prompt-budget curve and ablations for the
refusal prior, repair follow-up, entropy objective, and time budget. The initial
frozen run did not show the two-step planner outperforming the simpler
acquisition baselines; this negative result is retained as such. In the
seed-20261005 replay, all 363 cases were synthetic (dataset hash
`627d16ace3fcd467f424731a43126b831da0ae3e3b3d7b36d7703197a4c10847`). The
two-step and one-step planners were identical: each correctly resolved 234/363
episodes (64.46%), issued 368 prompts, and accumulated 1,363,504 simulated ms.
The random same-budget baseline correctly resolved 246/363 (67.77%) with 366
prompts and 1,347,669 simulated ms; entropy-first resolved 242/363 (66.67%).
All three avoided wrong determined labels in this run; fixed-fill guessed every
case and made 107 wrong determinations. This workload-specific replay does not
show that random selection is generally better.

The report additionally includes `controlled_complementary_pair`, a narrow
mechanism check against the production `SupportPairDomain`: with the same
two-question/eight-second budget, a single horizon-1 decision defers a lone
baseline because it cannot value the later follow-up, while horizon 2 plans a
baseline and then its matching follow-up on the answered branch. This is not an
end-to-end superiority result: a receding-horizon one-step system could ask
again after the first response. Keep this bounded result separate from the
negative seven-day execution benchmark above; neither synthetic result is a
real-user or clinical-effect claim.

The frozen case examples in `cases.jsonl`, generation configuration,
response model, revision scenario declarations, JSON schemas, and trace output
are all part of the audit trail. Change any of them only with a new version or
hash recorded in the report.
