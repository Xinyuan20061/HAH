"""Split migrate_head.sql into individual statements for CloudBase execution."""
import json
import re

SRC = r"D:\学习资料\计算机应用大赛\health-assistant\backend\migrate_head.sql"
OUT = r"D:\学习资料\计算机应用大赛\health-assistant\backend\migrate_statements.json"

with open(SRC, "r", encoding="utf-8-sig") as f:
    text = f.read()

# Remove comment lines (alembic -- Running upgrade ...)
lines = []
for line in text.splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith("--"):
        continue
    lines.append(stripped)

# Join into one string, split on ';' at end of statement
joined = "\n".join(lines)
statements = []
for chunk in joined.split(";"):
    chunk = chunk.strip()
    if chunk:
        statements.append(chunk + ";")

print("total statements:", len(statements))
for i, st in enumerate(statements[:5], 1):
    print(i, st[:120].replace("\n", " "))
print("...")
for i, st in enumerate(statements[-3:], len(statements) - 2):
    print(i, st[:120].replace("\n", " "))

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(statements, f, ensure_ascii=False, indent=1)

# Sanity checks
creates = [s for s in statements if s.lstrip().upper().startswith("CREATE TABLE")]
alters = [s for s in statements if s.lstrip().upper().startswith("ALTER TABLE")]
inserts = [s for s in statements if s.lstrip().upper().startswith("INSERT")]
updates = [s for s in statements if s.lstrip().upper().startswith("UPDATE")]
print("CREATE TABLE:", len(creates))
print("ALTER TABLE:", len(alters))
print("INSERT:", len(inserts))
print("UPDATE:", len(updates))
