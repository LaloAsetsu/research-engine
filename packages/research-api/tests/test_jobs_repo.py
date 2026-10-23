"""The job table over a real SQLite file: states move atomically, the queue is first in first out."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from research_api.adapters.persistence import Database
from research_api.domain.jobs import (
    ArmSpec,
    IdMaker,
    Job,
    JobError,
    JobState,
    Progress,
    RunParams,
    ScoreParams,
)
from research_api.domain.sets import SetRef

T0 = datetime(2026, 10, 23, 1, 0, 0, tzinfo=UTC)
ids = IdMaker()


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database.open(tmp_path / "jobs.sqlite3")
    database.upgrade()
    return database


def run_job(owner: str = "ana", state: JobState = JobState.QUEUED, at: datetime = T0) -> Job:
    return Job(
        id=ids.new(at),
        kind="run",
        state=state,
        owner=owner,
        created_at=at,
        queued_at=at,
        params=RunParams(
            SetRef("demo", "v1"), "a" * 32, ArmSpec("harness", "x", {"bm25": "never"}), "m", 2, "n"
        ),
        progress=Progress(0, 8),
        artifact_id="harness-demo-v1-abcd1234",
    )


def score_job(owner: str = "ana") -> Job:
    return Job(
        id=ids.new(T0),
        kind="score",
        state=JobState.QUEUED,
        owner=owner,
        created_at=T0,
        queued_at=T0,
        params=ScoreParams(SetRef("demo", "v1"), ("r1", "r2")),
        progress=Progress(0, 1),
    )


def test_a_job_is_stored_and_read_back_whole(db: Database) -> None:
    job = run_job()
    db.jobs.add(job)
    assert db.jobs.get(job.id) == job
    scoring = score_job()
    db.jobs.add(scoring)
    assert db.jobs.get(scoring.id) == scoring
    assert db.jobs.get("0" * 26) is None


def test_the_catalog_lists_newest_first_and_filters(db: Database) -> None:
    made = [run_job("ana"), run_job("beto"), score_job("ana"), run_job("ana")]
    for job in made:
        db.jobs.add(job)
    assert [j.id for j in db.jobs.list()] == [j.id for j in reversed(made)]
    assert {j.owner for j in db.jobs.list(owner="beto")} == {"beto"}
    assert [j.kind for j in db.jobs.list(kind="score")] == ["score"]
    db.jobs.move(made[0].id, expected={JobState.QUEUED}, to=JobState.CANCELLED)
    assert [j.id for j in db.jobs.list(states=[JobState.CANCELLED])] == [made[0].id]
    assert len(db.jobs.list(limit=2)) == 2
    assert [j.id for j in db.jobs.list(limit=2, offset=3)] == [made[0].id]


def test_the_queue_is_first_in_first_out_by_the_time_it_was_queued(db: Database) -> None:
    first, second, third = (run_job(at=T0 + timedelta(seconds=n)) for n in range(3))
    for job in (third, first, second):
        db.jobs.add(job)
    assert [j.id for j in db.jobs.queued("run")] == [first.id, second.id, third.id]
    # a resume queues a job again at the back
    db.jobs.move(first.id, expected={JobState.QUEUED}, to=JobState.CANCELLED)
    db.jobs.move(
        first.id,
        expected={JobState.CANCELLED},
        to=JobState.QUEUED,
        queued_at=T0 + timedelta(hours=1),
    )
    assert [j.id for j in db.jobs.queued("run")] == [second.id, third.id, first.id]
    assert db.jobs.queued("score") == []


def test_a_move_happens_only_from_an_expected_state(db: Database) -> None:
    job = run_job()
    db.jobs.add(job)
    assert db.jobs.move(job.id, expected={JobState.RUNNING}, to=JobState.DONE) is None
    assert db.jobs.get(job.id) is not None and db.jobs.get(job.id).state is JobState.QUEUED  # type: ignore[union-attr]
    moved = db.jobs.move(
        job.id, expected={JobState.QUEUED, JobState.CANCELLED}, to=JobState.RUNNING, started_at=T0
    )
    assert moved is not None and moved.state is JobState.RUNNING and moved.started_at == T0
    assert db.jobs.move("0" * 26, expected={JobState.QUEUED}, to=JobState.RUNNING) is None


def test_a_move_changes_only_the_columns_it_is_given(db: Database) -> None:
    job = run_job()
    db.jobs.add(job)
    db.jobs.move(
        job.id,
        expected={JobState.QUEUED},
        to=JobState.RUNNING,
        started_at=T0,
        error=JobError("x", "y"),
        progress=Progress(3, 8),
    )
    kept = db.jobs.move(job.id, expected={JobState.RUNNING}, to=JobState.CANCELLING)
    assert kept is not None
    assert (kept.started_at, kept.error, kept.progress) == (T0, JobError("x", "y"), Progress(3, 8))
    cleared = db.jobs.move(
        job.id,
        expected={JobState.CANCELLING},
        to=JobState.CANCELLED,
        error=None,
        started_at=None,
        ended_at=T0,
        artifact_id=None,
    )
    assert cleared is not None
    assert (cleared.error, cleared.started_at, cleared.ended_at, cleared.artifact_id) == (
        None,
        None,
        T0,
        None,
    )


def test_only_one_of_two_racing_moves_wins(db: Database) -> None:
    job = run_job()
    db.jobs.add(job)
    wins: list[Job | None] = []
    barrier = threading.Barrier(8)

    def move() -> None:
        barrier.wait()
        wins.append(db.jobs.move(job.id, expected={JobState.QUEUED}, to=JobState.RUNNING))

    threads = [threading.Thread(target=move) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(w is not None for w in wins) == 1


def test_progress_is_written_on_its_own(db: Database) -> None:
    job = run_job()
    db.jobs.add(job)
    db.jobs.set_progress(job.id, Progress(5, 8))
    assert db.jobs.get(job.id).progress == Progress(5, 8)  # type: ignore[union-attr]


def test_open_jobs_are_counted_by_owner(db: Database) -> None:
    states = [
        JobState.QUEUED,
        JobState.RUNNING,
        JobState.CANCELLING,
        JobState.DONE,
        JobState.FAILED,
    ]
    for state in states:
        db.jobs.add(run_job("ana", state))
    db.jobs.add(run_job("beto"))
    assert db.jobs.open_count("ana") == 3
    assert db.jobs.open_count("beto") == 1
    assert db.jobs.open_count("nobody") == 0


def test_recovery_marks_what_a_stop_left_behind(db: Database) -> None:
    running = run_job(state=JobState.RUNNING)
    cancelling = run_job(state=JobState.CANCELLING)
    queued = run_job()
    done = run_job(state=JobState.DONE)
    for job in (running, cancelling, queued, done):
        db.jobs.add(job)
    now = T0 + timedelta(hours=1)
    changed = db.jobs.recover(now)
    assert sorted((j.id, j.state) for j in changed) == sorted(
        [(running.id, JobState.INTERRUPTED), (cancelling.id, JobState.CANCELLED)]
    )
    got = db.jobs.get(running.id)
    assert got is not None and got.ended_at == now
    assert got.error == JobError("interrupted", "The service stopped while this job ran.")
    assert db.jobs.get(cancelling.id).state is JobState.CANCELLED  # type: ignore[union-attr]
    assert db.jobs.get(queued.id).state is JobState.QUEUED  # type: ignore[union-attr]
    assert db.jobs.get(done.id).state is JobState.DONE  # type: ignore[union-attr]
    assert db.jobs.recover(now) == []  # nothing left to recover


def test_a_long_error_message_is_cut_to_fit(db: Database) -> None:
    job = run_job()
    db.jobs.add(job)
    moved = db.jobs.move(
        job.id, expected={JobState.QUEUED}, to=JobState.RUNNING, error=JobError("x", "m" * 900)
    )
    assert moved is not None and moved.error is not None and len(moved.error.message) == 500


def test_a_state_or_kind_outside_the_list_is_refused_by_the_table(db: Database) -> None:
    import sqlalchemy as sa  # noqa: PLC0415

    with pytest.raises(sa.exc.IntegrityError), db.engine.begin() as connection:
        connection.execute(
            sa.text(
                "insert into jobs (id, kind, state, owner, created_at, queued_at, params, done,"
                " total) values ('x', 'run', 'paused', 'a', '2026-01-01', '2026-01-01', '{}', 0, 0)"
            )
        )
