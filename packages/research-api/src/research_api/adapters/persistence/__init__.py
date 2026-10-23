"""Persistence adapter: SQLite through SQLAlchemy, with Alembic migrations."""

from .database import Database

__all__ = ["Database"]
