#!/bin/sh
set -eu
python scripts/preflight.py
case "${RUN_MIGRATIONS_ON_START:-false}" in
  true|True|TRUE|1)
    echo "Explicit RUN_MIGRATIONS_ON_START enabled; running Alembic (single instance only)."
    alembic upgrade head
    ;;
esac
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
