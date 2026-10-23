"""The SQLite database: the engine, its pragmas, the repositories and the migrations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import URL, Engine, create_engine, event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.pool import ConnectionPoolEntry

from .jobs import SqlJobs
from .models import Base
from .repos import SqlLoginAttempts, SqlSessions, SqlSetCatalog, SqlUsers

MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def _on_connect(dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry) -> None:
    """Every connection: foreign keys on, WAL, a wait instead of an instant 'database is locked'."""
    # The sqlite3 driver ignores PRAGMA foreign_keys inside a transaction, so run them in
    # autocommit (SQLAlchemy docs, SQLite dialect).
    previous = dbapi_connection.autocommit  # type: ignore[attr-defined]
    dbapi_connection.autocommit = True  # type: ignore[attr-defined]
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()
    dbapi_connection.autocommit = previous  # type: ignore[attr-defined]


def make_engine(path: Path) -> Engine:
    # URL.create keeps a ? or # in the path from being read as part of the URL
    engine = create_engine(URL.create("sqlite", database=str(path)))
    event.listen(engine, "connect", _on_connect)
    return engine


def _alembic_config(engine: Engine) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.attributes["engine"] = engine
    return config


class Database:
    """One SQLite file. ``open`` does not create or change tables; the methods below do."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.users = SqlUsers(engine)
        self.sessions = SqlSessions(engine)
        self.attempts = SqlLoginAttempts(engine)
        self.sets = SqlSetCatalog(engine)
        self.jobs = SqlJobs(engine)

    @classmethod
    def open(cls, path: Path) -> Database:
        return cls(make_engine(path))

    def close(self) -> None:
        self.engine.dispose()

    def upgrade(self) -> None:
        """Apply every migration that has not run yet (``research-api db upgrade``)."""
        command.upgrade(_alembic_config(self.engine), "head")

    def create_all(self) -> None:
        """Build the tables straight from the models: fake mode's throwaway database only."""
        Base.metadata.create_all(self.engine)

    def current_revision(self) -> str | None:
        with self.engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()

    def head_revision(self) -> str:
        head = ScriptDirectory.from_config(_alembic_config(self.engine)).get_current_head()
        assert head is not None
        return head

    def is_current(self) -> bool:
        return self.current_revision() == self.head_revision()

    def schema_differences(self) -> list[Any]:
        """What Alembic would change to make the database match the models. Empty when equal."""
        with self.engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            return list(compare_metadata(context, Base.metadata))
