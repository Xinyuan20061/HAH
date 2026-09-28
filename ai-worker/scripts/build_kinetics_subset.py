# -*- coding: utf-8 -*-
"""Build the same-set comparison subset manifest (6 classes x 2 instances x 2
camera views = 24 clips) from the full REHAB24-6 manifest.

The subset keeps both camera views per instance so view variation is present,
while limiting runtime on CPU. The report must link the full 120-clip baseline
(motion-v1) and label this as a subset comparison.
"""
import json
from collections import defaultdict
from pathlib import Path

MANIFEST = Path(r"D:\学习资料\计算机应用大赛\health-assistant\benchmark\rehab24_action_manifest.jsonl")
OUT = Path(r"D:\学习资料\计算机应用大赛\health-assistant\benchmark\motion_same_set_subset.jsonl")

rows = [json.loads(line) for line in MANIFEST.read_text(encoding="utf-8").splitlines() if line.strip()]
by_exercise = defaultdict(list)
for row in rows:
    by_exercise[row["exercise_type"]].append(row)

subset = []
for exercise in sorted(by_exercise):
    instances = {}
    for row in by_exercise[exercise]:
        instances.setdefault(row["instance_id"], []).append(row)
    chosen = []
    for instance in sorted(instances)[:2]:  # two instances
        chosen.extend(sorted(instances[instance], key=lambda r: r["camera"]))
    subset.extend(chosen)

OUT.write_text(
    "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in subset),
    encoding="utf-8",
)
print(f"subset {len(subset)} clips -> {OUT}")
for exercise in sorted(by_exercise):
    n = sum(1 for row in subset if row["exercise_type"] == exercise)
    print(f"  {exercise}: {n}")
