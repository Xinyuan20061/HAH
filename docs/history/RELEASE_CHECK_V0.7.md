# HealthMate v0.7 Release Check

- [x] Python compileall
- [x] Mini Program JavaScript syntax check
- [x] pytest: 7 passed
- [x] Alembic 0005 -> 0006 migration test
- [x] Agent safety intercept smoke test
- [x] Agent plan -> Action Registry -> persistence smoke test
- [x] Privacy policy / preview / ZIP export smoke test
- [x] Account hard-delete smoke test; old token becomes invalid
- [x] Food correction -> finalize -> DietRecord traceability smoke test
- [x] Evaluation dashboard returns missing metrics as null / 暂无样本 rather than fake zero

## Production gates

- `SECRET_KEY` must be >= 32 chars or API refuses to boot in production.
- Prefer separate `CREDENTIALS_ENCRYPTION_KEY` >= 32 chars.
- Use MySQL + Redis + S3/MinIO in production.
- Use HTTPS for public API and user-configured AI Base URL.
- Run `alembic upgrade head` before API/worker startup.
