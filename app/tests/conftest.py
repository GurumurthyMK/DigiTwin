"""Shared test bootstrap: single SQLite file, schema + seed created once.

Per-module unlink/create_all caused 'readonly database' failures (one module
unlinked the file while another's pooled connection held the old inode).
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_digitwin.db")
# Rate limiting is behavior-tested in isolation (test_debt_cleanup); the suite
# makes far more auth calls from one IP than any brute-force budget allows.
os.environ["RATE_LIMIT_ENABLED"] = "false"

import pathlib

_DB = pathlib.Path("test_digitwin.db")
if _DB.exists():
    _DB.unlink()

from app.db.base import Base
from app.db.seed import seed_reference_data
from app.db.session import SessionLocal, engine

Base.metadata.create_all(bind=engine)
seed_reference_data()  # lifespan seeding does not run under bare TestClient
SessionLocal().close()
