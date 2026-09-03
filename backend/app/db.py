"""Database session management (SQLAlchemy 2.x, sync engine).

SQLite by default for local/dev; swap AGENTALGO_DATABASE_URL to Postgres in
production — models use only portable column types.
"""
from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import DATABASE_URL

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    # allow use across FastAPI threadpool + asyncio worker threads
    connect_args["check_same_thread"] = False

engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)

if DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record):  # pragma: no cover
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency yielding a request-scoped session."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401  (register mappings)

    Base.metadata.create_all(bind=engine)
    _migrate()


def _migrate() -> None:
    """Additive column migrations for existing dev databases."""
    from sqlalchemy import text

    migrations = [
        "ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0",
        # C10: event sequence integrity at the DB level
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_run_events_run_seq ON run_events (run_id, seq)",
    ]
    with engine.begin() as conn:
        for stmt in migrations:
            try:
                conn.execute(text(stmt))
            except Exception:  # column already exists
                pass
