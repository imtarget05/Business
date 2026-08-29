#!/usr/bin/env sh
# Production entrypoint: migrate then serve (24/7).
set -e
alembic upgrade head
exec uvicorn apps.api.main:app --host 0.0.0.0 --port 8000 --workers "${UVICORN_WORKERS:-2}"
