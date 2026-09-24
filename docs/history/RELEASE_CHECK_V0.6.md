# v0.6 Release Check

- Python `compileall`: PASS
- pytest: **3 passed**
- Alembic fresh database upgrade: **0005_health_intelligence (head)**
- Legacy v0.5 create_all database adoption test: PASS (`0004` → `0005`)
- Mini Program JavaScript syntax (`node --check`): PASS
- API smoke test: PASS
  - `/health/today`
  - `/health/trends/7d`
  - `/insights/weekly-facts`
  - `/health/goals/dynamic/evaluate`
  - `/agent/respond`
- Agent closed-loop smoke test: PASS
  - generate plan → confirm plan → read current plan
- Release package intentionally excludes runtime SQLite database and caches.
