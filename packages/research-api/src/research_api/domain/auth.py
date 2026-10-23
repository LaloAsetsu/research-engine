"""Accounts, sessions and the rules around them. Standard library only."""

from __future__ import annotations

import re
import string
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal

from .common_passwords import COMMON_PASSWORDS
from .errors import FieldError, InvalidInputError

Language = Literal["es", "en"]
LANGUAGES: tuple[Language, ...] = ("es", "en")

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024
_USERNAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,31}")
MAX_DISPLAY_NAME = 80


class Role(StrEnum):
    ADMIN = "admin"
    MEMBER = "member"


@dataclass(frozen=True)
class User:
    id: int
    username: str
    display_name: str
    role: Role
    password_hash: str
    must_change_password: bool
    active: bool
    language: Language
    created_at: datetime
    password_changed_at: datetime | None


@dataclass(frozen=True)
class NewUser:
    username: str
    display_name: str
    role: Role
    password_hash: str
    must_change_password: bool
    language: Language
    created_at: datetime


@dataclass(frozen=True)
class Session:
    """One signed-in browser. ``token_hash`` is the sha256 of the cookie value, never the value."""

    id: str
    user_id: int
    token_hash: str
    csrf_token: str
    created_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True)
class SessionPolicy:
    """A session ends after ``idle`` without a request, and ``absolute`` after it began."""

    idle: timedelta
    absolute: timedelta

    def expires_at(self, session: Session) -> datetime:
        return min(session.last_seen_at + self.idle, session.created_at + self.absolute)

    def is_live(self, session: Session, now: datetime) -> bool:
        return now < self.expires_at(session)


@dataclass(frozen=True)
class LoginPolicy:
    """Failed logins allowed in ``window`` per username; the per-address budget is four times it."""

    max_failures: int
    window: timedelta

    @property
    def max_failures_per_address(self) -> int:
        return 4 * self.max_failures

    def retry_after_s(self, failures: list[datetime], limit: int, now: datetime) -> int | None:
        """Seconds until a caller with these failure times (oldest first) may try again.

        ``None`` when the caller is below ``limit``. The block lifts when enough of the oldest
        failures have left the window to bring the count back under the limit.
        """
        if len(failures) < limit:
            return None
        frees_at = failures[len(failures) - limit] + self.window
        wait = (frees_at - now).total_seconds()
        return max(1, int(wait) + (0 if wait == int(wait) else 1))


def normalize_username(raw: str) -> str:
    """The form a username is stored and compared in: trimmed and lowercase."""
    return raw.strip().lower()


def validate_username(raw: str) -> str:
    name = normalize_username(raw)
    if not _USERNAME.fullmatch(name):
        raise InvalidInputError(
            "The username is not valid.",
            (
                FieldError(
                    "/username",
                    "Use 1 to 32 characters: letters, digits, dot, underscore or hyphen, "
                    "starting with a letter or digit.",
                ),
            ),
        )
    return name


def validate_display_name(raw: str) -> str:
    name = " ".join(raw.split())
    if not name or len(name) > MAX_DISPLAY_NAME or not name.isprintable():
        raise InvalidInputError(
            "The display name is not valid.",
            (FieldError("/display_name", f"Use 1 to {MAX_DISPLAY_NAME} printable characters."),),
        )
    return name


def password_problems(password: str, username: str) -> list[str]:
    """Why a password is refused, in plain words. An empty list means it is acceptable.

    The rule is length plus two cheap checks, with no composition rules: at least 12
    characters, not the username, not a common password, not one character repeated.
    """
    problems: list[str] = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        problems.append(f"Use at most {MAX_PASSWORD_LENGTH} characters.")
    folded = password.casefold()
    if folded == normalize_username(username):
        problems.append("The password cannot be the username.")
    if folded in COMMON_PASSWORDS or len(set(password)) == 1:
        problems.append("That password is too common.")
    return problems


def check_password(password: str, username: str, *, path: str = "/new") -> None:
    problems = password_problems(password, username)
    if problems:
        raise InvalidInputError(
            "The password is not acceptable.", tuple(FieldError(path, p) for p in problems)
        )


# Letters and digits that cannot be confused when read aloud or copied by hand.
ONE_TIME_ALPHABET = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lI")
ONE_TIME_LENGTH = 20
