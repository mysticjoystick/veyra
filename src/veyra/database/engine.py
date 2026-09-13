"""SQLAlchemy session factory and database bootstrap.

The ORM models are persistence entities that parallel the clean domain
models in veyra.domain. Kept separate so domain logic never depends on
the database.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, Session

from ..config import Settings


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _configure_sqlite_pragma(dbapi_conn, _record) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def build_engine(settings: Settings) -> Engine:
    if settings.database_url.startswith("sqlite:///"):
        db_path = settings.absolute_database_path
        _ensure_parent(db_path)
        engine = create_engine(f"sqlite:///{db_path}", future=True)
        event.listen(engine, "connect", _configure_sqlite_pragma)
    else:
        engine = create_engine(settings.database_url, future=True)
    return engine


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


# Column-level migrations for schema drift on pre-existing SQLite DBs.
# ``create_all`` only creates missing *tables*, never missing columns, so any
# column added to an ORM model after a DB was first created must be applied
# here with additive ALTER TABLE (safe to re-run).
_COLUMN_MIGRATIONS = {
    "users": [
        ("plan", "VARCHAR(32)", "free"),
        ("subscription_expires_at", "DATETIME", None),
    ],
}


def _current_columns(engine: Engine, table: str) -> set:
    with engine.begin() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {r[1] for r in rows}


def _apply_column_migrations(engine: Engine) -> None:
    """Add missing columns to existing tables (safe, additive, non-destructive)."""
    with engine.begin() as conn:
        for table, cols in _COLUMN_MIGRATIONS.items():
            existing = _current_columns(engine, table)
            for name, col_type, default in cols:
                if name in existing:
                    continue
                exists = conn.execute(
                    text(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type='table' AND name=:t"
                    ),
                    {"t": table},
                ).scalar()
                if not exists:
                    continue
                ddl = f"ALTER TABLE {table} ADD COLUMN {name} {col_type}"
                if default is not None:
                    ddl += f" DEFAULT '{default}'"
                conn.execute(text(ddl))


def init_db(engine: Engine) -> None:
    """Create all tables. Imported lazily to avoid circular imports."""
    from . import models  # noqa: F401
    from . import backtest_models  # noqa: F401
    from .models import Base
    from .backtest_models import BacktestBase

    Base.metadata.create_all(bind=engine)
    BacktestBase.metadata.create_all(bind=engine)
    _apply_column_migrations(engine)
