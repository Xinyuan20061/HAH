"""Rebatch remaining statements (from health_goal_adjustments onward) in small groups."""
import json
import glob

BASE = r"D:\学习资料\计算机应用大赛\health-assistant\backend\scripts"

with open(f"{BASE}\\batch_1.json", "r", encoding="utf-8") as f:
    b1 = json.load(f)

# batch_1 index 32 = health_goal_adjustments (0-based 32)
rest = b1[32:]
for i in range(2, 7):
    with open(f"{BASE}\\batch_{i}.json", "r", encoding="utf-8") as f:
        rest.extend(json.load(f))

print("remaining:", len(rest))

BATCH = 10
groups = [rest[i:i + BATCH] for i in range(0, len(rest), BATCH)]
for idx, g in enumerate(groups, 1):
    out = f"{BASE}\\small_{idx}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(g, f, ensure_ascii=False)
    print(f"small_{idx}: {len(g)} stmts, {sum(len(s) for s in g)} chars")
