"""Write migrate statements into batches for initializeSchema calls."""
import json

SRC = r"D:\学习资料\计算机应用大赛\health-assistant\backend\migrate_statements.json"

with open(SRC, "r", encoding="utf-8") as f:
    stmts = json.load(f)

# Skip index 0 (CREATE TABLE alembic_version) — already created.
# Also drop the duplicate INSERT INTO alembic_version (initial row added manually).
rest = [
    s for s in stmts[1:]
    if not s.lstrip().upper().startswith("INSERT INTO ALEMBIC_VERSION")
]
BATCH = 50
batches = [rest[i:i + BATCH] for i in range(0, len(rest), BATCH)]
for idx, batch in enumerate(batches, 1):
    out = rf"D:\学习资料\计算机应用大赛\health-assistant\backend\scripts\batch_{idx}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(batch, f, ensure_ascii=False)
    print(f"batch_{idx}: {len(batch)} statements, {sum(len(s) for s in batch)} chars")
