"""Ports of the sign-in part: where users, sessions and failed logins are kept, and the hasher."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ..domain.auth import NewUser, Session, User


class UserRepo(Protocol):
    def add(self, new: NewUser) -> User:
        """Store a user. ``ConflictError`` when the username is taken."""
        ...

    def by_id(self, user_id: int) -> User | None: ...

    def by_username(self, username: str) -> User | None: ...

    def all(self) -> list[User]:
        """Every user, ordered by username."""
        ...

    def save(self, user: User) -> None:
        """Replace every field of the stored user that has this id."""
        ...


class SessionRepo(Protocol):
    def add(self, session: Session) -> None: ...

    def by_token_hash(self, token_hash: str) -> Session | None: ...

    def by_id(self, session_id: str) -> Session | None: ...

    def touch(self, session_id: str, at: datetime) -> None:
        """Record that the session was used at ``at``."""
        ...

    def delete(self, session_id: str) -> bool:
        """End one session. False when there was none."""
        ...

    def delete_for_user(self, user_id: int, *, keep: str | None = None) -> int:
        """End every session of a user except ``keep``. Returns how many ended."""
        ...

    def delete_idle_or_old(self, *, idle_before: datetime, created_before: datetime) -> int:
        """Remove sessions last used before ``idle_before`` or begun before ``created_before``."""
        ...

    def all(self) -> list[Session]:
        """Every session, newest first."""
        ...


class LoginAttempts(Protocol):
    def record_failure(self, *, username: str, address: str, at: datetime) -> None: ...

    def failure_times(
        self, *, username: str | None = None, address: str | None = None, since: datetime
    ) -> list[datetime]:
        """Failures after ``since`` for a username or an address, oldest first.

        A failure made exactly at ``since`` is a whole window old and no longer counts.
        """
        ...

    def clear_username(self, username: str) -> None: ...

    def purge_before(self, at: datetime) -> None:
        """Forget failures older than ``at``."""
        ...


@dataclass(frozen=True)
class Verification:
    ok: bool
    upgraded_hash: str | None = None
    """A fresh hash when the stored one uses outdated settings; store it in place of the old."""


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...

    def verify(self, password: str, password_hash: str) -> Verification: ...

    def burn(self, password: str) -> None:
        """Spend the time of one verification on nothing, so an unknown username costs the same."""
        ...
