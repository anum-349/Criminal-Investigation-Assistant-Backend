#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint for the CIA container.
# 1. Optionally runs Alembic migrations (RUN_MIGRATIONS=true)
# 2. Starts uvicorn on 0.0.0.0:8000
# ─────────────────────────────────────────────────────────────────────────────
set -e

if [ "${RUN_MIGRATIONS}" = "true" ]; then
  echo ">>> Running Alembic migrations..."
  alembic upgrade head
  echo ">>> Migrations done."
fi

echo ">>> Starting uvicorn..."
exec uvicorn main:app --host 0.0.0.0 --port 8000