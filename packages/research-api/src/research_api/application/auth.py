"""Use cases of accounts and sessions: sign in, sign out, change a password, manage users.

The cookie value is a random 256-bit token; only its sha256 is stored, so a copy of the database
cannot be turned into a session. Nothing here logs a password, a token or a hash.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from ..domain.auth import (
    LANGUAGES,
    ONE_TIME_ALPHABET,
    ONE_TIME_LENGTH,
    Language,
    LoginPolicy,
    NewUser,
    Role,
    Session,
    SessionPolicy,
    User,
    check_password,
    is_valid_username,
    normalize_username,
    validate_display_name,
    validate_username,
)
from ..domain.errors import (
    ConflictError,
    FieldError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
    UnauthenticatedError,
)
from ..ports.auth import LoginAttempts, PasswordHasher, SessionRepo, UserRepo
from ..ports.clock import Clock

audit_log = logging.getLogger("research_api.audit")

# A session's last-use time is written at most this often, so reads do not become writes.
TOUCH_EVERY = timedelta(seconds=60)

WRONG_LOGIN = "Wrong username or password."
NOT_SIGNED_IN = "Sign in to continue."


def audit(event: str, **fields: object) -> None:
    """One line of the audit trail. Only plain facts: never a password, token or hash."""
    audit_log.info(event, extra={"event": event, **fields})


def loggable(name: str) -> str:
    """A typed username for the log. Anything that cannot be a username is not written down: it
    may be a password typed into the wrong box."""
    return name if is_valid_username(name) else "(invalid)"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Authenticated:
    user: User
    session: Session


@dataclass(frozen=True)
class NewSession:
    """A fresh session. ``token`` is the cookie value and exists only here, once."""

    token: str
    session: Session
    user: User


@dataclass(frozen=True)
class SessionInfo:
    id: str
    username: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    current: bool


@dataclass(frozen=True)
class CreatedUser:
    user: User
    password: str
    """The one-time password in clear. Shown once by the command that made it, never stored."""


def _open_session(user: User, now: datetime) -> NewSession:
    token = secrets.token_urlsafe(32)
    session = Session(
        id=secrets.token_hex(16),
        user_id=user.id,
        token_hash=hash_token(token),
        csrf_token=secrets.token_urlsafe(32),
        created_at=now,
        last_seen_at=now,
    )
    return NewSession(token=token, session=session, user=user)


class LoginThrottle:
    """Failed logins counted per username and per address in a window, kept in the database."""

    def __init__(self, *, attempts: LoginAttempts, clock: Clock, policy: LoginPolicy) -> None:
        self._attempts = attempts
        self._clock = clock
        self._policy = policy

    def check(self, username: str, address: str) -> int | None:
        """Seconds to wait when this caller is over a limit, else ``None``."""
        now = self._clock.now()
        since = now - self._policy.window
        self._attempts.purge_before(since)
        by_name = self._policy.retry_after_s(
            self._attempts.failure_times(username=username, since=since),
            self._policy.max_failures,
            now,
        )
        by_address = self._policy.retry_after_s(
            self._attempts.failure_times(address=address, since=since),
            self._policy.max_failures_per_address,
            now,
        )
        waits = [w for w in (by_name, by_address) if w is not None]
        return max(waits) if waits else None

    def fail(self, username: str, address: str) -> None:
        self._attempts.record_failure(username=username, address=address, at=self._clock.now())

    def succeed(self, username: str) -> None:
        self._attempts.clear_username(username)


def _too_many(wait: int) -> RateLimitedError:
    return RateLimitedError("Too many failed attempts. Try again later.", retry_after_s=wait)


class SignIn:
    def __init__(
        self,
        *,
        users: UserRepo,
        sessions: SessionRepo,
        hasher: PasswordHasher,
        clock: Clock,
        throttle: LoginThrottle,
        policy: SessionPolicy,
    ) -> None:
        self._users = users
        self._sessions = sessions
        self._hasher = hasher
        self._clock = clock
        self._throttle = throttle
        self._policy = policy

    def __call__(self, username: str, password: str, address: str) -> NewSession:
        name = normalize_username(username)
        wait = self._throttle.check(name, address)
        if wait is not None:
            audit("login.blocked", username=loggable(name), address=address)
            raise _too_many(wait)
        user = self._users.by_username(name)
        if user is None:
            self._hasher.burn(password)  # an unknown name takes as long as a wrong password
            ok = False
        else:
            verification = self._hasher.verify(password, user.password_hash)
            ok = verification.ok and user.active
            if verification.ok and verification.upgraded_hash is not None:
                user = replace(user, password_hash=verification.upgraded_hash)
                self._users.save(user)
        if not ok or user is None:
            self._throttle.fail(name, address)
            audit("login.failure", username=loggable(name), address=address)
            raise UnauthenticatedError(WRONG_LOGIN)
        self._throttle.succeed(name)
        now = self._clock.now()
        self._sessions.delete_idle_or_old(
            idle_before=now - self._policy.idle, created_before=now - self._policy.absolute
        )
        opened = _open_session(user, now)
        self._sessions.add(opened.session)
        audit(
            "login.success", username=user.username, address=address, session_id=opened.session.id
        )
        return opened


class SignOut:
    def __init__(self, *, sessions: SessionRepo) -> None:
        self._sessions = sessions

    def __call__(self, who: Authenticated) -> None:
        self._sessions.delete(who.session.id)
        audit("logout", username=who.user.username, session_id=who.session.id)


class Authenticate:
    """The user behind a session cookie, or ``UnauthenticatedError``.

    The user is read again on every request, so a disabled account or a changed role takes
    effect at the next request, with no wait for the session to expire.
    """

    def __init__(
        self, *, users: UserRepo, sessions: SessionRepo, clock: Clock, policy: SessionPolicy
    ) -> None:
        self._users = users
        self._sessions = sessions
        self._clock = clock
        self._policy = policy

    def __call__(self, token: str | None) -> Authenticated:
        if not token:
            raise UnauthenticatedError(NOT_SIGNED_IN)
        session = self._sessions.by_token_hash(hash_token(token))
        if session is None:
            raise UnauthenticatedError(NOT_SIGNED_IN)
        now = self._clock.now()
        if not self._policy.is_live(session, now):
            self._sessions.delete(session.id)
            raise UnauthenticatedError(NOT_SIGNED_IN)
        user = self._users.by_id(session.user_id)
        if user is None or not user.active:
            self._sessions.delete(session.id)
            raise UnauthenticatedError(NOT_SIGNED_IN)
        if now - session.last_seen_at >= TOUCH_EVERY:
            self._sessions.touch(session.id, now)
            session = replace(session, last_seen_at=now)
        return Authenticated(user=user, session=session)


class ChangePassword:
    """Replace the caller's password, end every session, and open a new one (no fixation)."""

    def __init__(
        self,
        *,
        users: UserRepo,
        sessions: SessionRepo,
        hasher: PasswordHasher,
        clock: Clock,
        throttle: LoginThrottle,
    ) -> None:
        self._users = users
        self._sessions = sessions
        self._hasher = hasher
        self._clock = clock
        self._throttle = throttle

    def __call__(self, who: Authenticated, current: str, new: str, address: str) -> NewSession:
        user = who.user
        wait = self._throttle.check(user.username, address)
        if wait is not None:
            raise _too_many(wait)
        if not self._hasher.verify(current, user.password_hash).ok:
            self._throttle.fail(user.username, address)
            audit("password.refused", username=user.username, address=address)
            raise ForbiddenError("The current password is wrong.")
        check_password(new, user.username)
        if new == current:
            raise InvalidInputError(
                "The password is not acceptable.",
                (FieldError("/new", "The new password must differ from the current one."),),
            )
        now = self._clock.now()
        changed = replace(
            user,
            password_hash=self._hasher.hash(new),
            must_change_password=False,
            password_changed_at=now,
        )
        self._users.save(changed)
        self._throttle.succeed(user.username)
        self._sessions.delete_for_user(user.id)
        opened = _open_session(changed, now)
        self._sessions.add(opened.session)
        audit("password.changed", username=user.username, session_id=opened.session.id)
        return opened


class ListSessions:
    def __init__(
        self, *, users: UserRepo, sessions: SessionRepo, clock: Clock, policy: SessionPolicy
    ) -> None:
        self._users = users
        self._sessions = sessions
        self._clock = clock
        self._policy = policy

    def __call__(self, who: Authenticated) -> list[SessionInfo]:
        now = self._clock.now()
        names = {u.id: u.username for u in self._users.all()}
        return [
            SessionInfo(
                id=s.id,
                username=names.get(s.user_id, "?"),
                created_at=s.created_at,
                last_seen_at=s.last_seen_at,
                expires_at=self._policy.expires_at(s),
                current=s.id == who.session.id,
            )
            for s in self._sessions.all()
            if self._policy.is_live(s, now)
        ]


class EndSession:
    def __init__(self, *, users: UserRepo, sessions: SessionRepo) -> None:
        self._users = users
        self._sessions = sessions

    def __call__(self, who: Authenticated, session_id: str) -> None:
        target = self._sessions.by_id(session_id)
        if target is None or not self._sessions.delete(session_id):
            raise NotFoundError("No such session.")
        owner = self._users.by_id(target.user_id)
        audit(
            "session.ended",
            username=who.user.username,
            target=owner.username if owner else "?",
            session_id=session_id,
        )


class ManageUsers:
    """Accounts, for the ``research-api users`` command and the fake-mode seed. Not over HTTP."""

    def __init__(
        self,
        *,
        users: UserRepo,
        sessions: SessionRepo,
        hasher: PasswordHasher,
        clock: Clock,
    ) -> None:
        self._users = users
        self._sessions = sessions
        self._hasher = hasher
        self._clock = clock

    def create(
        self,
        username: str,
        display_name: str,
        role: Role = Role.MEMBER,
        *,
        password: str | None = None,
        must_change_password: bool = True,
        language: Language = "es",
    ) -> CreatedUser:
        """Make an account. Without ``password`` a random one-time password is made and returned."""
        name = validate_username(username)
        shown = validate_display_name(display_name)
        if language not in LANGUAGES:
            raise InvalidInputError(
                "The language is not valid.", (FieldError("/language", "Use es or en."),)
            )
        secret = password if password is not None else one_time_password()
        check_password(secret, name, path="/password")
        user = self._users.add(
            NewUser(
                username=name,
                display_name=shown,
                role=role,
                password_hash=self._hasher.hash(secret),
                must_change_password=must_change_password,
                language=language,
                created_at=self._clock.now(),
            )
        )
        audit("user.created", username=name, role=role.value, actor="cli")
        return CreatedUser(user=user, password=secret)

    def all(self) -> list[User]:
        return self._users.all()

    def disable(self, username: str) -> User:
        user = self._find(username)
        self._keep_one_admin(user)
        self._sessions.delete_for_user(user.id)
        return self._set(replace(user, active=False), "user.disabled")

    def enable(self, username: str) -> User:
        return self._set(replace(self._find(username), active=True), "user.enabled")

    def reset(self, username: str) -> CreatedUser:
        """A new one-time password; the old one stops working and every session ends."""
        user = self._find(username)
        secret = one_time_password()
        self._users.save(
            replace(user, password_hash=self._hasher.hash(secret), must_change_password=True)
        )
        self._sessions.delete_for_user(user.id)
        audit("user.reset", username=user.username, actor="cli")
        return CreatedUser(user=user, password=secret)

    def set_role(self, username: str, role: Role) -> User:
        user = self._find(username)
        if role is not Role.ADMIN:
            self._keep_one_admin(user)
        return self._set(replace(user, role=role), "user.role", role=role.value)

    def _find(self, username: str) -> User:
        user = self._users.by_username(normalize_username(username))
        if user is None:
            raise NotFoundError(f"No user named {normalize_username(username)!r}.")
        return user

    def _keep_one_admin(self, user: User) -> None:
        """Refuse to take the last active admin away: nobody could be made admin afterwards."""
        if user.role is not Role.ADMIN or not user.active:
            return
        others = [u for u in self._users.all() if u.role is Role.ADMIN and u.active]
        if len(others) <= 1:
            raise ConflictError("This is the only active admin; make another one first.")

    def _set(self, user: User, event: str, **fields: object) -> User:
        self._users.save(user)
        audit(event, username=user.username, actor="cli", **fields)
        return user


def one_time_password() -> str:
    return "".join(secrets.choice(ONE_TIME_ALPHABET) for _ in range(ONE_TIME_LENGTH))


@dataclass(frozen=True)
class AuthServices:
    """Everything the HTTP layer and the commands need from this part, built once."""

    sign_in: SignIn
    sign_out: SignOut
    authenticate: Authenticate
    change_password: ChangePassword
    list_sessions: ListSessions
    end_session: EndSession
    users: ManageUsers
