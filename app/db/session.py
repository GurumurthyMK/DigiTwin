"""Engine/session factory. SQLite for local dev; Postgres-ready via DATABASE_URL."""

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, future=True)

if engine.url.drivername.startswith("sqlite"):
    # Debt cleanup: without this pragma SQLite silently ignores FK violations
    # and DB-level cascades; integrity was ORM-only. No-op on Postgres.
    @event.listens_for(engine, "connect")
    def _sqlite_fk_on(dbapi_conn, _connection_record) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, class_=Session, future=True
)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
