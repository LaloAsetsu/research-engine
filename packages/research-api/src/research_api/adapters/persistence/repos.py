"""The repositories: the ports of ``ports/auth.py`` over SQLAlchemy. One short transaction each."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, Engine, delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from ...domain.auth import Language, NewUser, Role, Session, User
from ...domain.errors import ConflictError
from .models import LoginAttemptRow, SessionRow, UserRow


def _user(row: UserRow) -> User:
    return User(
        id=row.id,
        username=row.username,
        display_name=row.display_name,
        role=Role(row.role),
        password_hash=row.password_hash,
        must_change_password=row.must_change_password,
        active=row.active,
        language=cast(Language, row.language),
        created_at=row.created_at,
        password_changed_at=row.password_changed_at,
    )


def _count(result: Any) -> int:
    """Rows a DELETE or UPDATE touched."""
    return cast("CursorResult[Any]", result).rowcount


def _session(row: SessionRow) -> Session:
    return Session(
        id=row.id,
        user_id=row.user_id,
        token_hash=row.token_hash,
        csrf_token=row.csrf_token,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
    )


class SqlUsers:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, new: NewUser) -> User:
        row = UserRow(
            username=new.username,
            display_name=new.display_name,
            role=new.role.value,
            password_hash=new.password_hash,
            must_change_password=new.must_change_password,
            active=True,
            language=new.language,
            created_at=new.created_at,
            password_changed_at=None,
        )
        try:
            with DbSession(self._engine) as db, db.begin():
                db.add(row)
                db.flush()
                db.refresh(row)  # the stored form: UTC times, as every later read gives them
                return _user(row)
        except IntegrityError:
            raise ConflictError("That username is already taken.") from None

    def by_id(self, user_id: int) -> User | None:
        with DbSession(self._engine) as db:
            row = db.get(UserRow, user_id)
            return None if row is None else _user(row)

    def by_username(self, username: str) -> User | None:
        with DbSession(self._engine) as db:
            row = db.scalar(select(UserRow).where(UserRow.username == username))
            return None if row is None else _user(row)

    def all(self) -> list[User]:
        with DbSession(self._engine) as db:
            return [_user(r) for r in db.scalars(select(UserRow).order_by(UserRow.username))]

    def save(self, user: User) -> None:
        with DbSession(self._engine) as db, db.begin():
            db.execute(
                update(UserRow)
                .where(UserRow.id == user.id)
                .values(
                    display_name=user.display_name,
                    role=user.role.value,
                    password_hash=user.password_hash,
                    must_change_password=user.must_change_password,
                    active=user.active,
                    language=user.language,
                    password_changed_at=user.password_changed_at,
                )
            )


class SqlSessions:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, session: Session) -> None:
        with DbSession(self._engine) as db, db.begin():
            db.add(
                SessionRow(
                    id=session.id,
                    user_id=session.user_id,
                    token_hash=session.token_hash,
                    csrf_token=session.csrf_token,
                    created_at=session.created_at,
                    last_seen_at=session.last_seen_at,
                )
            )

    def by_token_hash(self, token_hash: str) -> Session | None:
        with DbSession(self._engine) as db:
            row = db.scalar(select(SessionRow).where(SessionRow.token_hash == token_hash))
            return None if row is None else _session(row)

    def by_id(self, session_id: str) -> Session | None:
        with DbSession(self._engine) as db:
            row = db.get(SessionRow, session_id)
            return None if row is None else _session(row)

    def touch(self, session_id: str, at: datetime) -> None:
        with DbSession(self._engine) as db, db.begin():
            db.execute(
                update(SessionRow).where(SessionRow.id == session_id).values(last_seen_at=at)
            )

    def delete(self, session_id: str) -> bool:
        with DbSession(self._engine) as db, db.begin():
            return _count(db.execute(delete(SessionRow).where(SessionRow.id == session_id))) > 0

    def delete_for_user(self, user_id: int, *, keep: str | None = None) -> int:
        statement = delete(SessionRow).where(SessionRow.user_id == user_id)
        if keep is not None:
            statement = statement.where(SessionRow.id != keep)
        with DbSession(self._engine) as db, db.begin():
            return _count(db.execute(statement))

    def delete_idle_or_old(self, *, idle_before: datetime, created_before: datetime) -> int:
        with DbSession(self._engine) as db, db.begin():
            return _count(
                db.execute(
                    delete(SessionRow).where(
                        (SessionRow.last_seen_at < idle_before)
                        | (SessionRow.created_at < created_before)
                    )
                )
            )

    def all(self) -> list[Session]:
        with DbSession(self._engine) as db:
            rows = db.scalars(select(SessionRow).order_by(SessionRow.created_at.desc()))
            return [_session(r) for r in rows]


class SqlLoginAttempts:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def reserve(
        self,
        *,
        username: str,
        address: str,
        at: datetime,
        since: datetime,
        max_by_username: int,
        max_by_address: int,
    ) -> int | None:
        name, where = username[:64], address[:64]
        # The write lock is taken before the counts are read (BEGIN IMMEDIATE), so two callers
        # cannot both see "four failures" and both record the fifth. SQLAlchemy is told to leave
        # transactions alone for this one connection, because the sqlite driver would otherwise
        # begin them lazily, at the first write.
        with self._engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                by_name = connection.scalar(
                    select(func.count())
                    .select_from(LoginAttemptRow)
                    .where(LoginAttemptRow.at > since, LoginAttemptRow.username == name)
                )
                by_address = connection.scalar(
                    select(func.count())
                    .select_from(LoginAttemptRow)
                    .where(LoginAttemptRow.at > since, LoginAttemptRow.address == where)
                )
                attempt_id: int | None = None
                if (by_name or 0) < max_by_username and (by_address or 0) < max_by_address:
                    key = connection.execute(
                        insert(LoginAttemptRow).values(username=name, address=where, at=at)
                    ).inserted_primary_key
                    assert key is not None  # a plain insert reports the new row's key
                    attempt_id = int(key[0])
                connection.exec_driver_sql("COMMIT")
            except BaseException:
                connection.exec_driver_sql("ROLLBACK")
                raise
        return attempt_id

    def release(self, attempt_id: int) -> None:
        with DbSession(self._engine) as db, db.begin():
            db.execute(delete(LoginAttemptRow).where(LoginAttemptRow.id == attempt_id))

    def failure_times(
        self, *, username: str | None = None, address: str | None = None, since: datetime
    ) -> list[datetime]:
        statement = select(LoginAttemptRow.at).where(LoginAttemptRow.at > since)
        if username is not None:
            statement = statement.where(LoginAttemptRow.username == username[:64])
        if address is not None:
            statement = statement.where(LoginAttemptRow.address == address[:64])
        with DbSession(self._engine) as db:
            return list(db.scalars(statement.order_by(LoginAttemptRow.at, LoginAttemptRow.id)))

    def clear_username(self, username: str) -> None:
        with DbSession(self._engine) as db, db.begin():
            db.execute(delete(LoginAttemptRow).where(LoginAttemptRow.username == username[:64]))

    def purge_before(self, at: datetime) -> None:
        with DbSession(self._engine) as db, db.begin():
            db.execute(delete(LoginAttemptRow).where(LoginAttemptRow.at < at))
