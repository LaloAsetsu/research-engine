from __future__ import annotations

import sqlite3
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import StatementError

from research_api.adapters.files import create_private_file
from research_api.adapters.persistence import Database
from research_api.adapters.persistence.database import _alembic_config
from research_api.adapters.persistence.models import Base, UtcDateTime
from research_api.domain.auth import NewUser, Role, Session
from research_api.domain.errors import ConflictError
from research_api.domain.sets import SetRef

T0 = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database.open(tmp_path / "t.sqlite3")
    database.upgrade()
    yield database
    database.close()


def fail(db: Database, *, username: str, address: str, at: datetime) -> int:
    """Record one failed login, with limits too high to matter. Returns its id."""
    taken = db.attempts.reserve(
        username=username,
        address=address,
        at=at,
        since=at - timedelta(days=1),
        max_by_username=10_000,
        max_by_address=10_000,
    )
    assert taken is not None
    return taken


def new_user(name: str = "ana", **kw: object) -> NewUser:
    values: dict[str, object] = {
        "username": name,
        "display_name": name.title(),
        "role": Role.MEMBER,
        "password_hash": "$argon2id$x",
        "must_change_password": True,
        "language": "es",
        "created_at": T0,
    }
    return NewUser(**{**values, **kw})  # type: ignore[arg-type]


def new_session(user_id: int, sid: str = "s1", **kw: object) -> Session:
    values: dict[str, object] = {
        "id": sid,
        "user_id": user_id,
        "token_hash": "h-" + sid,
        "csrf_token": "c-" + sid,
        "created_at": T0,
        "last_seen_at": T0,
    }
    return Session(**{**values, **kw})  # type: ignore[arg-type]


# --- migrations -----------------------------------------------------------------------------


def test_migrations_build_the_tables_the_models_describe(db: Database) -> None:
    assert db.is_current()
    assert db.schema_differences() == []
    tables = {
        row[0]
        for row in sqlite3.connect(db.engine.url.database).execute(
            "select name from sqlite_master where type='table'"
        )
    }
    assert tables == {"users", "sessions", "login_attempts", "artifacts", "jobs", "alembic_version"}


def test_an_empty_file_is_not_current_until_it_is_upgraded(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "empty.sqlite3")
    assert database.current_revision() is None
    assert not database.is_current()
    database.upgrade()
    assert database.current_revision() == database.head_revision() == "0004"
    database.close()


def test_upgrading_twice_changes_nothing(db: Database) -> None:
    db.users.add(new_user())
    db.upgrade()
    assert [u.username for u in db.users.all()] == ["ana"]


def test_the_migration_can_be_undone_and_redone(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "d.sqlite3")
    database.upgrade()
    command.downgrade(_alembic_config(database.engine), "base")
    with database.engine.connect() as connection:
        left = connection.execute(text("select name from sqlite_master where type='table'")).all()
    assert [r[0] for r in left] == ["alembic_version"]
    database.upgrade()
    assert database.schema_differences() == []
    database.close()


def test_dropping_the_artifact_path_keeps_the_rows_and_can_be_undone(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "p.sqlite3")
    config = _alembic_config(database.engine)
    command.upgrade(config, "0003")
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "insert into artifacts (kind, name, version, status, owner, md5, items, corpus,"
                " path, created_at, updated_at) values ('qset', 'demo', 'v1', 'draft', 'ana',"
                " :md5, 3, 'c', 'sets/demo/v1.qset.jsonl', '2026-01-01', '2026-01-02')"
            ),
            {"md5": "a" * 32},
        )

    database.upgrade()
    assert database.schema_differences() == []
    stored = database.sets.get(SetRef("demo", "v1"))
    assert stored is not None
    assert (stored.owner, stored.md5, stored.items, stored.corpus) == ("ana", "a" * 32, 3, "c")
    with database.engine.connect() as connection:
        columns = {c["name"] for c in inspect(connection).get_columns("artifacts")}
    assert "path" not in columns
    with pytest.raises(Exception, match="CHECK"), database.engine.begin() as connection:
        connection.execute(text("update artifacts set status = 'x'"))
    with pytest.raises(Exception, match="CHECK"), database.engine.begin() as connection:
        connection.execute(text("update artifacts set kind = 'x'"))
    with pytest.raises(Exception, match="UNIQUE"), database.engine.begin() as connection:
        connection.execute(
            text(
                "insert into artifacts (kind, name, version, status, owner, md5, items,"
                " created_at, updated_at) select kind, name, version, status, owner, md5, items,"
                " created_at, updated_at from artifacts"
            )
        )

    command.downgrade(config, "0003")
    assert database.current_revision() == "0003"
    with database.engine.connect() as connection:
        path = {c["name"]: c for c in inspect(connection).get_columns("artifacts")}["path"]
        assert path["nullable"]
        row = connection.execute(text("select name, owner, path from artifacts")).one()
    assert tuple(row) == ("demo", "ana", None)
    database.upgrade()
    assert database.schema_differences() == []
    database.close()


def test_a_model_change_without_a_migration_is_noticed(db: Database) -> None:
    with db.engine.begin() as connection:
        connection.execute(text("alter table users add column surprise text"))
    assert db.schema_differences() != []


def test_create_all_builds_the_same_schema_as_the_migrations(tmp_path: Path) -> None:
    fake = Database.open(tmp_path / "f.sqlite3")
    fake.create_all()
    assert fake.schema_differences() == []
    fake.close()


# --- connection settings --------------------------------------------------------------------


def test_every_connection_has_foreign_keys_and_wal_on(db: Database) -> None:
    with db.engine.connect() as connection:
        assert connection.execute(text("pragma foreign_keys")).scalar() == 1
        assert connection.execute(text("pragma journal_mode")).scalar() == "wal"
        assert connection.execute(text("pragma busy_timeout")).scalar() == 5000


def test_a_session_for_a_missing_user_is_refused_by_the_database(db: Database) -> None:
    with pytest.raises(Exception, match="FOREIGN KEY"):
        db.sessions.add(new_session(user_id=999))


def test_deleting_a_user_removes_the_sessions_by_cascade(db: Database) -> None:
    user = db.users.add(new_user())
    db.sessions.add(new_session(user.id))
    with db.engine.begin() as connection:
        connection.execute(text("delete from users where id = :i"), {"i": user.id})
    assert db.sessions.all() == []


def test_the_role_and_language_columns_only_take_known_values(db: Database) -> None:
    with pytest.raises(Exception, match="CHECK"), db.engine.begin() as connection:
        connection.execute(
            text(
                "insert into users (username, display_name, role, password_hash,"
                " must_change_password, active, language, created_at)"
                " values ('x', 'X', 'root', 'h', 1, 1, 'es', '2026-01-01')"
            )
        )
    with pytest.raises(Exception, match="CHECK"), db.engine.begin() as connection:
        connection.execute(
            text(
                "insert into users (username, display_name, role, password_hash,"
                " must_change_password, active, language, created_at)"
                " values ('x', 'X', 'member', 'h', 1, 1, 'fr', '2026-01-01')"
            )
        )


# --- the time column ------------------------------------------------------------------------


def test_times_come_back_aware_and_in_utc(db: Database) -> None:
    local = datetime(2026, 3, 4, 0, 6, 7, tzinfo=timezone(timedelta(hours=-5)))
    user = db.users.add(new_user(created_at=local))
    assert user.created_at == T0
    assert user.created_at.tzinfo is UTC
    assert db.users.by_id(user.id).created_at == T0  # type: ignore[union-attr]


def test_a_naive_time_is_a_bug_and_is_refused(db: Database) -> None:
    with pytest.raises((TypeError, StatementError)):
        db.users.add(new_user(created_at=datetime(2026, 1, 1)))


def test_the_file_holds_naive_utc(db: Database) -> None:
    db.users.add(new_user())
    raw = sqlite3.connect(db.engine.url.database).execute("select created_at from users").fetchone()
    assert raw[0] == "2026-03-04 05:06:07.000000"
    assert UtcDateTime().process_bind_param(None, None) is None
    assert UtcDateTime().process_result_value(None, None) is None


# --- users ----------------------------------------------------------------------------------


def test_a_user_round_trips(db: Database) -> None:
    added = db.users.add(new_user("ana", role=Role.ADMIN, language="en"))
    assert added.id > 0
    assert (added.role, added.language, added.active) == (Role.ADMIN, "en", True)
    assert added.password_changed_at is None
    assert db.users.by_username("ana") == added
    assert db.users.by_id(added.id) == added
    assert db.users.by_username("nobody") is None
    assert db.users.by_id(12345) is None


def test_a_taken_username_is_a_conflict(db: Database) -> None:
    db.users.add(new_user("ana"))
    with pytest.raises(ConflictError):
        db.users.add(new_user("ana"))


def test_users_come_back_ordered_by_username(db: Database) -> None:
    for name in ("zoe", "ana", "marta"):
        db.users.add(new_user(name))
    assert [u.username for u in db.users.all()] == ["ana", "marta", "zoe"]


def test_save_replaces_the_changeable_fields_and_not_the_name(db: Database) -> None:
    user = db.users.add(new_user("ana"))
    changed = replace(
        user,
        display_name="Ana B",
        role=Role.ADMIN,
        password_hash="$new",
        must_change_password=False,
        active=False,
        language="en",
        password_changed_at=T0,
        username="renamed",
    )
    db.users.save(changed)
    stored = db.users.by_id(user.id)
    assert stored == replace(changed, username="ana")


# --- sessions -------------------------------------------------------------------------------


def test_a_session_is_found_by_its_token_hash_and_by_id(db: Database) -> None:
    user = db.users.add(new_user())
    session = new_session(user.id)
    db.sessions.add(session)
    assert db.sessions.by_token_hash("h-s1") == session
    assert db.sessions.by_id("s1") == session
    assert db.sessions.by_token_hash("nope") is None
    assert db.sessions.by_id("nope") is None


def test_touch_moves_the_last_seen_time(db: Database) -> None:
    user = db.users.add(new_user())
    db.sessions.add(new_session(user.id))
    later = T0 + timedelta(minutes=5)
    db.sessions.touch("s1", later)
    stored = db.sessions.by_id("s1")
    assert stored is not None
    assert (stored.created_at, stored.last_seen_at) == (T0, later)


def test_delete_says_whether_it_ended_anything(db: Database) -> None:
    user = db.users.add(new_user())
    db.sessions.add(new_session(user.id))
    assert db.sessions.delete("s1") is True
    assert db.sessions.delete("s1") is False


def test_delete_for_user_can_keep_one_session(db: Database) -> None:
    ana = db.users.add(new_user("ana"))
    beto = db.users.add(new_user("beto"))
    for sid in ("a1", "a2", "a3"):
        db.sessions.add(new_session(ana.id, sid))
    db.sessions.add(new_session(beto.id, "b1"))
    assert db.sessions.delete_for_user(ana.id, keep="a2") == 2
    assert sorted(s.id for s in db.sessions.all()) == ["a2", "b1"]
    assert db.sessions.delete_for_user(ana.id) == 1
    assert [s.id for s in db.sessions.all()] == ["b1"]


def test_expired_sessions_are_removed_by_idle_time_or_age(db: Database) -> None:
    user = db.users.add(new_user())
    db.sessions.add(new_session(user.id, "fresh", created_at=T0, last_seen_at=T0))
    db.sessions.add(
        new_session(user.id, "idle", created_at=T0, last_seen_at=T0 - timedelta(days=1))
    )
    db.sessions.add(new_session(user.id, "old", created_at=T0 - timedelta(days=9), last_seen_at=T0))
    removed = db.sessions.delete_idle_or_old(
        idle_before=T0 - timedelta(hours=12), created_before=T0 - timedelta(days=7)
    )
    assert removed == 2
    assert [s.id for s in db.sessions.all()] == ["fresh"]


def test_sessions_list_newest_first(db: Database) -> None:
    user = db.users.add(new_user())
    db.sessions.add(new_session(user.id, "old", created_at=T0 - timedelta(hours=1)))
    db.sessions.add(new_session(user.id, "new", created_at=T0))
    assert [s.id for s in db.sessions.all()] == ["new", "old"]


def test_a_token_hash_is_unique(db: Database) -> None:
    user = db.users.add(new_user())
    db.sessions.add(new_session(user.id, "s1"))
    with pytest.raises(Exception, match="UNIQUE"):
        db.sessions.add(new_session(user.id, "s2", token_hash="h-s1"))


# --- failed logins --------------------------------------------------------------------------


def test_failures_are_counted_per_username_and_per_address(db: Database) -> None:
    attempts = db.attempts
    fail(db, username="ana", address="10.0.0.1", at=T0)
    fail(db, username="ana", address="10.0.0.2", at=T0 + timedelta(seconds=1))
    fail(db, username="beto", address="10.0.0.1", at=T0 + timedelta(seconds=2))
    before = T0 - timedelta(seconds=1)
    assert len(attempts.failure_times(username="ana", since=before)) == 2
    assert len(attempts.failure_times(address="10.0.0.1", since=before)) == 2
    assert attempts.failure_times(username="ana", since=T0) == [T0 + timedelta(seconds=1)]
    assert attempts.failure_times(username="nobody", since=before) == []


def test_failure_times_come_back_oldest_first(db: Database) -> None:
    for seconds in (5, 1, 3):
        fail(db, username="ana", address="a", at=T0 + timedelta(seconds=seconds))
    assert db.attempts.failure_times(username="ana", since=T0) == [
        T0 + timedelta(seconds=s) for s in (1, 3, 5)
    ]
    assert db.attempts.failure_times(username="ana", since=T0 + timedelta(seconds=1)) == [
        T0 + timedelta(seconds=s) for s in (3, 5)
    ]


def test_clearing_a_username_leaves_other_usernames_from_the_same_address(db: Database) -> None:
    before = T0 - timedelta(seconds=1)
    fail(db, username="ana", address="10.0.0.1", at=T0)
    fail(db, username="beto", address="10.0.0.1", at=T0)
    db.attempts.clear_username("ana")
    assert db.attempts.failure_times(username="ana", since=before) == []
    assert len(db.attempts.failure_times(address="10.0.0.1", since=before)) == 1


def test_purge_forgets_only_old_failures(db: Database) -> None:
    fail(db, username="a", address="x", at=T0 - timedelta(hours=1))
    fail(db, username="a", address="x", at=T0)
    db.attempts.purge_before(T0 - timedelta(minutes=1))
    assert db.attempts.failure_times(username="a", since=T0 - timedelta(days=1)) == [T0]


def test_a_very_long_name_is_cut_to_the_column_and_still_counts(db: Database) -> None:
    long = "x" * 500
    fail(db, username=long, address="a", at=T0)
    assert len(db.attempts.failure_times(username=long, since=T0 - timedelta(seconds=1))) == 1


def reserve(db: Database, username: str, address: str, *, name_max: int = 5, addr_max: int = 20):
    return db.attempts.reserve(
        username=username,
        address=address,
        at=T0,
        since=T0 - timedelta(minutes=15),
        max_by_username=name_max,
        max_by_address=addr_max,
    )


def test_a_reservation_is_refused_at_the_username_limit(db: Database) -> None:
    taken = [reserve(db, "ana", f"10.0.0.{i}") for i in range(7)]
    assert [t is not None for t in taken] == [True] * 5 + [False] * 2
    assert len(db.attempts.failure_times(username="ana", since=T0 - timedelta(hours=1))) == 5


def test_a_reservation_is_refused_at_the_address_limit(db: Database) -> None:
    taken = [reserve(db, f"user{i}", "10.0.0.1", addr_max=3) for i in range(5)]
    assert [t is not None for t in taken] == [True] * 3 + [False] * 2


def test_a_released_reservation_frees_its_place(db: Database) -> None:
    taken = [reserve(db, "ana", "a", name_max=2) for _ in range(2)]
    assert reserve(db, "ana", "a", name_max=2) is None
    assert taken[0] is not None
    db.attempts.release(taken[0])
    assert reserve(db, "ana", "a", name_max=2) is not None


def test_a_failed_reservation_leaves_nothing_behind(db: Database) -> None:
    naive = datetime(2026, 3, 4)  # the column refuses a time without a zone
    with pytest.raises(StatementError):
        db.attempts.reserve(
            username="ana",
            address="a",
            at=naive,
            since=T0,
            max_by_username=5,
            max_by_address=20,
        )
    assert db.attempts.failure_times(username="ana", since=T0 - timedelta(days=1)) == []
    assert reserve(db, "ana", "a") is not None  # the connection is not stuck in a transaction


def test_parallel_reservations_never_pass_the_limit(db: Database) -> None:
    barrier = threading.Barrier(24)

    def one(i: int) -> int | None:
        barrier.wait()
        return reserve(db, "ana", f"10.0.0.{i}")

    with ThreadPoolExecutor(24) as pool:
        taken = list(pool.map(one, range(24)))
    assert sum(t is not None for t in taken) == 5
    assert len(db.attempts.failure_times(username="ana", since=T0 - timedelta(hours=1))) == 5


def test_the_models_know_every_table() -> None:
    assert set(Base.metadata.tables) == {"users", "sessions", "login_attempts", "artifacts", "jobs"}


def test_the_database_file_made_by_the_service_is_private(tmp_path: Path) -> None:
    path = tmp_path / "p.sqlite3"
    create_private_file(path)
    database = Database.open(path)
    database.upgrade()
    database.users.add(new_user())
    database.close()
    for name in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        if name.exists():
            assert stat.S_IMODE(name.stat().st_mode) == 0o600, name


def test_a_path_with_url_characters_still_opens(tmp_path: Path) -> None:
    odd = tmp_path / "a b?c#d"
    odd.mkdir()
    database = Database.open(odd / "x.sqlite3")
    database.upgrade()
    assert database.is_current()
    database.close()
    assert (odd / "x.sqlite3").is_file()
