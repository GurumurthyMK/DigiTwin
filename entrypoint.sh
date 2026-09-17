#!/bin/sh
# Production entrypoint: migrations first (explicit Alembic, not create_all),
# then serve. Fails closed: the API never boots on an unmigrated database.
set -e
python -m alembic upgrade head
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
