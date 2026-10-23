"""Composition root: builds the use cases and wires the adapters to their ports."""

from __future__ import annotations

import os
import shutil
import tempfile
import weakref
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from importlib.metadata import version
from pathlib import Path

from ..adapters.circuits import CircuitsAdapter
from ..adapters.clock import SystemClock
from ..adapters.fake import FixedClock
from ..adapters.hashing import ArgonPasswordHasher
from ..adapters.persistence import Database
from ..application.auth import (
    Authenticate,
    AuthServices,
    ChangePassword,
    EndSession,
    ListSessions,
    LoginThrottle,
    ManageUsers,
    SignIn,
    SignOut,
)
from ..application.circuits import (
    CheckCircuit,
    ParseCircuit,
    RunCircuit,
    ShowCircuitConventions,
)
from ..application.status import ReportStatus
from ..domain.auth import LoginPolicy, SessionPolicy
from ..domain.status import Mode
from ..ports.auth import PasswordHasher
from ..ports.clock import Clock
from .seed import SeedUser, default_seed_users
from .settings import Settings, SettingsError, is_loopback, load_settings
from .storage import open_current_database

CONFIG_ENV = "RESEARCH_API_CONFIG"
FAKE_ENV = "RESEARCH_API_FAKE"


@dataclass(frozen=True)
class Container:
    mode: Mode
    settings: Settings
    version: str
    clock: Clock
    report_status: ReportStatus
    parse_circuit: ParseCircuit
    run_circuit: RunCircuit
    check_circuit: CheckCircuit
    circuit_conventions: ShowCircuitConventions
    auth: AuthServices
    close: Callable[[], None]
    """Release the database. Idempotent; the HTTP app calls it when it shuts down."""


def build_auth(
    database: Database, settings: Settings, clock: Clock, hasher: PasswordHasher
) -> AuthServices:
    sessions = SessionPolicy(
        idle=timedelta(hours=settings.session_idle_hours),
        absolute=timedelta(days=settings.session_max_days),
    )
    throttle = LoginThrottle(
        attempts=database.attempts,
        clock=clock,
        policy=LoginPolicy(
            max_failures=settings.login_max_failures,
            window=timedelta(minutes=settings.login_window_minutes),
        ),
    )
    return AuthServices(
        sign_in=SignIn(
            users=database.users,
            sessions=database.sessions,
            hasher=hasher,
            clock=clock,
            throttle=throttle,
            policy=sessions,
        ),
        sign_out=SignOut(sessions=database.sessions),
        authenticate=Authenticate(
            users=database.users, sessions=database.sessions, clock=clock, policy=sessions
        ),
        change_password=ChangePassword(
            users=database.users,
            sessions=database.sessions,
            hasher=hasher,
            clock=clock,
            throttle=throttle,
        ),
        list_sessions=ListSessions(
            users=database.users, sessions=database.sessions, clock=clock, policy=sessions
        ),
        end_session=EndSession(users=database.users, sessions=database.sessions),
        users=ManageUsers(
            users=database.users, sessions=database.sessions, hasher=hasher, clock=clock
        ),
    )


def _fake_database() -> tuple[Database, Callable[[], None]]:
    """A throwaway database in a private temporary folder, removed when the process ends."""
    folder = Path(tempfile.mkdtemp(prefix="research-api-fake-"))
    database = Database.open(folder / "fake.sqlite3")
    database.create_all()

    def cleanup() -> None:
        database.close()
        shutil.rmtree(folder, ignore_errors=True)

    return database, cleanup


def build_container(
    settings: Settings,
    *,
    fake: bool = False,
    seed: Sequence[SeedUser] = (),
    cheap_hashing: bool = False,
    clock: Clock | None = None,
) -> Container:
    """Wire the service.

    Fake mode uses in-memory stand-ins and a throwaway database, accepts loopback only, and
    creates the accounts of ``seed``. Live mode opens the database named in the settings and
    refuses to start when its folders are open to other accounts or its tables are out of date.
    ``cheap_hashing`` makes password hashing fast enough for a large test suite, and ``clock``
    replaces the time source (to cross a session or rate-limit window); nothing else changes.
    """
    if fake and not is_loopback(settings.bind_host):
        raise SettingsError("fake mode serves test data and may only bind a loopback address")
    clock = clock or (FixedClock() if fake else SystemClock())
    mode: Mode = "fake" if fake else "live"
    api_version = version("research-api")
    if fake:
        database, cleanup = _fake_database()
    else:
        database = open_current_database(settings)
        cleanup = database.close
    auth = build_auth(database, settings, clock, ArgonPasswordHasher(cheap=cheap_hashing))
    for account in seed:
        auth.users.create(
            account.username,
            account.display_name,
            account.role,
            password=account.password,
            must_change_password=account.must_change_password,
            language=account.language,
        )
    caps = settings.circuit_caps.caps()
    # quantum-sim and docingest's gate lists do no I/O, so fake mode runs the real engine.
    circuits = CircuitsAdapter(timeout_s=caps.timeout_s)
    container = Container(
        mode=mode,
        settings=settings,
        version=api_version,
        clock=clock,
        report_status=ReportStatus(
            clock=clock, mode=mode, version=api_version, started_at=clock.now()
        ),
        parse_circuit=ParseCircuit(engine=circuits),
        run_circuit=RunCircuit(engine=circuits, caps=caps),
        check_circuit=CheckCircuit(engine=circuits, caps=caps),
        circuit_conventions=ShowCircuitConventions(engine=circuits),
        auth=auth,
        close=cleanup,
    )
    # A process that exits without a clean shutdown still removes the fake database's folder.
    weakref.finalize(container, cleanup)
    return container


def container_from_env(environ: Mapping[str, str] | None = None) -> Container:
    """The container for ``uvicorn --factory``: fake mode, or the settings file named in the env."""
    env = os.environ if environ is None else environ
    if env.get(FAKE_ENV) == "1":
        return build_container(Settings.defaults(), fake=True, seed=default_seed_users())
    path = env.get(CONFIG_ENV)
    if not path:
        raise SettingsError(f"set {CONFIG_ENV} to a settings file, or {FAKE_ENV}=1 for fake mode")
    return build_container(load_settings(Path(path)))


@dataclass(frozen=True)
class UserAdmin:
    """What the ``users`` command needs: the account use cases, and a way to let go."""

    users: ManageUsers
    close: Callable[[], None]


def open_user_admin(settings: Settings) -> UserAdmin:
    database = open_current_database(settings)
    auth = build_auth(database, settings, SystemClock(), ArgonPasswordHasher())
    return UserAdmin(users=auth.users, close=database.close)
