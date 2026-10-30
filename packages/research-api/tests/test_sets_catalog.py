"""The catalog of question-set versions in SQLite."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from research_api.adapters.persistence import Database
from research_api.domain.errors import ConflictError, NotFoundError
from research_api.domain.sets import SetRef, SetVersion

T0 = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)
MD5_A = "a" * 32
MD5_B = "b" * 32


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database.open(tmp_path / "t.sqlite3")
    database.upgrade()
    yield database
    database.close()


def make(name: str = "demo", version: str = "v1", **changes: object) -> SetVersion:
    base = SetVersion(
        ref=SetRef(name, version),
        status="draft",
        owner="ana",
        md5=MD5_A,
        items=3,
        corpus="c1",
        created_at=T0,
        updated_at=T0,
    )
    return replace(base, **changes)


def test_a_version_round_trips(db: Database) -> None:
    db.sets.add(make())
    assert db.sets.get(SetRef("demo", "v1")) == make()


def test_an_unknown_version_is_none(db: Database) -> None:
    assert db.sets.get(SetRef("demo", "v1")) is None
    assert db.sets.all() == []
    assert db.sets.versions_of("demo") == []


def test_the_same_name_and_version_cannot_be_added_twice(db: Database) -> None:
    db.sets.add(make())
    with pytest.raises(ConflictError, match="demo/v1"):
        db.sets.add(make(owner="beto"))
    assert db.sets.get(SetRef("demo", "v1")) == make()


@pytest.mark.parametrize("other", ["Demo", "DEMO", "dEMo"])
def test_a_name_that_differs_only_in_capitals_cannot_be_added(db: Database, other: str) -> None:
    db.sets.add(make("demo", "v1"))
    with pytest.raises(ConflictError, match="same name"):
        db.sets.add(make(other, "v1"))
    with pytest.raises(ConflictError, match="same name"):
        db.sets.add(make(other, "v2"))  # another version of it is no way in either
    assert [x.ref.header_name for x in db.sets.all()] == ["demo/v1"]
    db.sets.add(make("demo", "v2"))  # the spelling that exists still grows


def test_names_apart_from_capitals_that_exist_are_not_touched_by_the_check(db: Database) -> None:
    db.sets.add(make("demo", "v1"))
    db.sets.add(make("demo-b", "v1"))
    db.sets.add(make("Other", "v1"))
    assert [x.ref.header_name for x in db.sets.all()] == ["Other/v1", "demo/v1", "demo-b/v1"]


def test_versions_come_in_numeric_order_not_text_order(db: Database) -> None:
    for v in ("v10", "v2", "v1"):
        db.sets.add(make(version=v))
    assert [x.ref.version for x in db.sets.versions_of("demo")] == ["v1", "v2", "v10"]


def test_all_is_ordered_by_name_then_version(db: Database) -> None:
    for name, v in (("zed", "v1"), ("abe", "v2"), ("abe", "v1")):
        db.sets.add(make(name, v))
    assert [x.ref.header_name for x in db.sets.all()] == ["abe/v1", "abe/v2", "zed/v1"]


def test_versions_of_one_name_leave_the_others_out(db: Database) -> None:
    db.sets.add(make("a"))
    db.sets.add(make("b"))
    assert [x.ref.name for x in db.sets.versions_of("a")] == ["a"]


def test_save_replaces_the_changing_fields(db: Database) -> None:
    db.sets.add(make())
    frozen_at = T0 + timedelta(hours=1)
    changed = make(
        status="frozen", md5=MD5_B, items=5, corpus="c2", updated_at=frozen_at, frozen_at=frozen_at
    )
    db.sets.save(changed, expected_md5=MD5_A)
    assert db.sets.get(SetRef("demo", "v1")) == changed


def test_save_does_not_change_the_created_time(db: Database) -> None:
    db.sets.add(make())
    db.sets.save(make(md5=MD5_B, created_at=T0 + timedelta(days=9)), expected_md5=MD5_A)
    stored = db.sets.get(SetRef("demo", "v1"))
    assert stored is not None
    assert stored.created_at == T0


def test_save_refuses_a_version_that_moved(db: Database) -> None:
    db.sets.add(make())
    with pytest.raises(ConflictError, match="changed"):
        db.sets.save(make(md5=MD5_B), expected_md5=MD5_B)
    stored = db.sets.get(SetRef("demo", "v1"))
    assert stored is not None
    assert stored.md5 == MD5_A


def test_save_of_a_missing_version_is_not_found(db: Database) -> None:
    with pytest.raises(NotFoundError):
        db.sets.save(make(), expected_md5=MD5_A)


def test_delete_forgets_one_version(db: Database) -> None:
    db.sets.add(make(version="v1"))
    db.sets.add(make(version="v2"))
    db.sets.delete(SetRef("demo", "v1"))
    assert [x.ref.version for x in db.sets.all()] == ["v2"]
    db.sets.delete(SetRef("demo", "v1"))  # again: nothing to do


def test_times_come_back_aware_and_in_utc(db: Database) -> None:
    local = datetime(2026, 3, 4, 0, 0, tzinfo=timezone(timedelta(hours=-6)))
    db.sets.add(make(created_at=local, updated_at=local))
    stored = db.sets.get(SetRef("demo", "v1"))
    assert stored is not None
    assert stored.created_at == local
    assert stored.created_at.utcoffset() == timedelta(0)


def test_a_missing_corpus_and_frozen_time_stay_none(db: Database) -> None:
    db.sets.add(make(corpus=None))
    stored = db.sets.get(SetRef("demo", "v1"))
    assert stored is not None
    assert (stored.corpus, stored.frozen_at) == (None, None)


def test_the_table_refuses_an_unknown_status_or_kind(db: Database) -> None:
    connection = sqlite3.connect(db.engine.url.database)
    row = ("qset", "a", "v1", "x", "o", MD5_A, 1, None, "2026-01-01", "2026-01-01")
    sql = (
        "insert into artifacts (kind, name, version, status, owner, md5, items, corpus,"
        " created_at, updated_at) values (?,?,?,?,?,?,?,?,?,?)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(sql, row)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(sql, ("run", *row[1:3], "draft", *row[4:]))
    connection.close()


def test_parallel_saves_from_the_same_md5_let_exactly_one_through(db: Database) -> None:
    db.sets.add(make())

    def attempt(n: int) -> bool:
        try:
            db.sets.save(make(md5=f"{n:032x}"), expected_md5=MD5_A)
        except ConflictError:
            return False
        return True

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(attempt, range(1, 9)))
    assert results.count(True) == 1


def test_a_frozen_entry_is_never_saved_over(db: Database) -> None:
    db.sets.add(make())
    db.sets.save(make(status="frozen", md5=MD5_B, frozen_at=T0), expected_md5=MD5_A)
    for changed in (make(md5=MD5_A), make(status="frozen", md5="c" * 32, frozen_at=T0)):
        with pytest.raises(ConflictError, match="is frozen"):
            db.sets.save(changed, expected_md5=MD5_B)
    assert db.sets.get(SetRef("demo", "v1")) == make(status="frozen", md5=MD5_B, frozen_at=T0)
