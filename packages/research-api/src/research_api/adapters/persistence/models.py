"""The tables, as SQLAlchemy 2 typed models. Alembic compares its migrations with these."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Named constraints, so a batch migration on SQLite can drop or alter them by name.
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class UtcDateTime(TypeDecorator[dt.datetime]):
    """Time-zone aware in Python, naive UTC in the file. A naive value is a bug and is refused."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: dt.datetime | None, dialect: Any) -> dt.datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise TypeError("a time-zone aware datetime is required")
        return value.astimezone(dt.UTC).replace(tzinfo=None)

    def process_result_value(self, value: dt.datetime | None, dialect: Any) -> dt.datetime | None:
        return None if value is None else value.replace(tzinfo=dt.UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class UserRow(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'member')", name="role"),
        CheckConstraint("language IN ('es', 'en')", name="language"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(32), unique=True)
    display_name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(16))
    password_hash: Mapped[str] = mapped_column(String(255))
    must_change_password: Mapped[bool]
    active: Mapped[bool]
    language: Mapped[str] = mapped_column(String(2))
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)
    password_changed_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)
    last_seen_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)


class LoginAttemptRow(Base):
    """One failed login. Successes are not kept: the log has them."""

    __tablename__ = "login_attempts"
    __table_args__ = (
        Index("ix_login_attempts_username_at", "username", "at"),
        Index("ix_login_attempts_address_at", "address", "at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64))
    address: Mapped[str] = mapped_column(String(64))
    at: Mapped[dt.datetime] = mapped_column(UtcDateTime)


class ArtifactRow(Base):
    """The catalog of the files under ``data_dir``: one row per set version (kind ``qset``).

    The file is the content and its md5 is the identity; this row says who owns it, whether it is
    still a draft, and what the catalog shows without reading the file. ``path`` is relative to
    the data folder.
    """

    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("kind", "name", "version", name="uq_artifacts_kind_name_version"),
        CheckConstraint("kind IN ('qset')", name="kind"),
        CheckConstraint("status IN ('draft', 'frozen')", name="status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(8))
    owner: Mapped[str] = mapped_column(String(32))
    md5: Mapped[str] = mapped_column(String(32))
    items: Mapped[int] = mapped_column(Integer)
    corpus: Mapped[str | None] = mapped_column(String(128))
    path: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)
    updated_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)
    frozen_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)


class JobRow(Base):
    """A benchmark job: a run (GPU lane) or a scoring (CPU lane). The files hold the results.

    ``params`` is the request as JSON text (what ``RunParams`` or ``ScoreParams`` say), so the
    table needs no column for each setting. ``artifact_id`` is the run id or score id of the
    file the job writes.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("kind IN ('run', 'score')", name="kind"),
        CheckConstraint(
            "state IN ('queued', 'running', 'cancelling', 'cancelled', 'done', 'failed',"
            " 'interrupted')",
            name="state",
        ),
        Index("ix_jobs_state_kind_queued_at", "state", "kind", "queued_at"),
        Index("ix_jobs_owner_state", "owner", "state"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    kind: Mapped[str] = mapped_column(String(8))
    state: Mapped[str] = mapped_column(String(12))
    owner: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)
    queued_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)
    started_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    ended_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    params: Mapped[str] = mapped_column(Text)
    done: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)
    artifact_id: Mapped[str | None] = mapped_column(String(128))
    error_kind: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(String(500))
