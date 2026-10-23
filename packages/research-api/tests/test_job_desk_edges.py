"""The corners of the job desk: races, failures that are not a question's, and a desk that was
never started."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from bench_helpers import (
    ANA_ACTOR,
    TOTAL,
    ScriptedArms,
    container_with,
    every_pair,
    finished,
    pairs,
    request,
    run_file,
    seed_stopped_run,
    until,
)
from bench_helpers import (
    DEMO_REF as DEMO,
)
from helpers import arun

from research_api.application.jobs import JobDesk, ScoreRequest
from research_api.bootstrap import Container
from research_api.domain.errors import ConflictError, NotFoundError, RateLimitedError
from research_api.domain.jobs import (
    MAX_OPEN_PER_MEMBER,
    ArmSpec,
    Job,
    JobState,
    Progress,
    RunParams,
    ScoreOptions,
    ScoreParams,
)


@pytest.fixture
def arms() -> ScriptedArms:
    return ScriptedArms()


@pytest.fixture
def container(arms: ScriptedArms) -> Container:
    box = container_with(arms, bench_recheck_s=0.02)
    yield box
    box.close()


@pytest.fixture
def desk(container: Container) -> JobDesk:
    return container.bench.jobs


# --- a run that fails for a reason that is not a question's --------------------------------


@arun
async def test_a_failure_of_the_run_itself_is_a_failed_job_with_an_error_id_and_no_message(
    desk: JobDesk, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(item: Any) -> bytes:
        raise RuntimeError("a path and a question: /secret/place What symbol")

    desk._formats.item_line = broken  # type: ignore[method-assign]
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    failed = await finished(desk, view)
    assert failed.job.state is JobState.FAILED and failed.job.error is not None
    assert failed.job.error.kind == "internal"
    assert failed.job.error.message.startswith("The run failed. Error id ")
    assert "secret" not in failed.job.error.message
    assert "secret" not in caplog.text and "What symbol" not in caplog.text
    assert "failed: internal" in desk.read_log(view.job.id).text


@arun
async def test_a_log_that_cannot_be_written_does_not_stop_a_run(
    desk: JobDesk, caplog: pytest.LogCaptureFixture
) -> None:
    def refuse(job_id: str, text: str) -> None:
        raise OSError("disk full")

    desk._files.append_log = refuse  # type: ignore[method-assign]
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    done = await finished(desk, view)
    assert done.job.state is JobState.DONE and done.job.progress.done == TOTAL
    assert any(getattr(r, "event", "") == "job.log_failed" for r in caplog.records)


@arun
async def test_a_job_cancelled_before_the_scheduler_took_it_is_left_alone(
    desk: JobDesk, arms: ScriptedArms
) -> None:
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    await finished(desk, view)
    gone = desk._repo.move(view.job.id, expected={JobState.DONE}, to=JobState.CANCELLED)
    assert gone is not None
    calls = len(arms.calls)
    await desk._run_job(gone)
    assert len(arms.calls) == calls and desk.get(view.job.id).job.state is JobState.CANCELLED


@arun
async def test_the_run_scheduler_survives_a_failure_of_its_own(
    desk: JobDesk, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("research_api.application.jobs.SCHEDULER_RETRY_S", 0.01)
    await desk.start()
    real = desk._repo.queued
    calls = {"n": 0}

    def flaky(kind: str):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("the table is locked")
        return real(kind)

    desk._repo.queued = flaky  # type: ignore[method-assign]
    desk._poke()
    await until(lambda: calls["n"] >= 1)
    await asyncio.sleep(0.05)
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    done = await finished(desk, view)
    assert done.job.state is JobState.DONE
    assert any(getattr(r, "event", "") == "job.scheduler_error" for r in caplog.records)


# --- cancel and resume that race with something --------------------------------------------


@arun
async def test_a_cancel_that_loses_a_race_says_what_the_job_became(
    desk: JobDesk, arms: ScriptedArms
) -> None:
    gate = arms.hold("DP-001")
    first, second = await desk.start_runs(ANA_ACTOR, request("bare", "harness"))
    await until(lambda: arms.calls)
    real = desk._repo.move
    state = {"first": True}

    def lose_once(job_id: str, **kw: Any):  # type: ignore[no-untyped-def]
        if state["first"]:
            state["first"] = False
            real(job_id, expected={JobState.QUEUED}, to=JobState.CANCELLED)  # someone else won
            return None
        return real(job_id, **kw)

    desk._repo.move = lose_once  # type: ignore[method-assign]
    assert desk.cancel(ANA_ACTOR, second.job.id).job.state is JobState.CANCELLED
    gate.set()
    await finished(desk, first)


@arun
async def test_a_cancel_that_loses_a_race_to_a_finish_is_a_conflict(
    desk: JobDesk, arms: ScriptedArms
) -> None:
    gate = arms.hold("DP-001")
    first, second = await desk.start_runs(ANA_ACTOR, request("bare", "harness"))
    await until(lambda: arms.calls)
    real = desk._repo.move

    def lose_to_a_finish(job_id: str, **kw: Any):  # type: ignore[no-untyped-def]
        real(job_id, expected={JobState.QUEUED}, to=JobState.RUNNING)
        real(job_id, expected={JobState.RUNNING}, to=JobState.DONE)

    desk._repo.move = lose_to_a_finish  # type: ignore[method-assign]
    with pytest.raises(ConflictError, match="now done"):
        desk.cancel(ANA_ACTOR, second.job.id)
    gate.set()
    await finished(desk, first)


@arun
async def test_a_resume_that_loses_a_race_is_a_conflict(desk: JobDesk) -> None:
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    await finished(desk, view)
    desk._repo.move(view.job.id, expected={JobState.DONE}, to=JobState.CANCELLED)
    desk._repo.move = lambda *a, **k: None  # type: ignore[method-assign]
    with pytest.raises(ConflictError, match="changed while"):
        desk.resume(ANA_ACTOR, view.job.id)


@arun
async def test_a_desk_that_was_never_started_can_still_cancel(container: Container) -> None:
    desk = container.bench.jobs
    seed = seed_stopped_run(container, JobState.QUEUED, 0)[1]
    assert desk._wake is None
    assert desk.cancel(ANA_ACTOR, seed).job.state is JobState.CANCELLED


@arun
async def test_stopping_twice_is_fine(desk: JobDesk) -> None:
    await desk.start()
    await desk.stop()
    await desk.stop()
    await desk.start()
    await desk.stop()


# --- recovery that meets a damaged file ------------------------------------------------------


@arun
async def test_a_stopped_run_whose_file_is_damaged_is_still_marked_interrupted(
    arms: ScriptedArms, caplog: pytest.LogCaptureFixture
) -> None:
    container = container_with(arms)
    try:
        desk, job_id = seed_stopped_run(container, JobState.RUNNING, 2)
        run_id = desk.get(job_id).job.artifact_id or ""
        desk._files.replace_run(run_id, b"this is not a run\n")
        await desk.start()
        assert desk.get(job_id).job.state is JobState.INTERRUPTED
        assert any(getattr(r, "event", "") == "job.mark_failed" for r in caplog.records)
    finally:
        container.close()


@arun
async def test_a_stopped_run_whose_file_vanished_is_still_marked_interrupted(
    arms: ScriptedArms,
) -> None:
    container = container_with(arms)
    try:
        desk, job_id = seed_stopped_run(container, JobState.RUNNING, 1)
        run_id = desk.get(job_id).job.artifact_id or ""
        (desk._files.root / "runs" / f"{run_id}.qa-run.jsonl").unlink()
        await desk.start()
        assert desk.get(job_id).job.state is JobState.INTERRUPTED
        # and a resume starts it again from the first question, with a new file
        desk.resume(ANA_ACTOR, job_id)
        done = await finished(desk, desk.get(job_id))
        assert done.job.state is JobState.DONE
        assert pairs(run_file(desk, done)[1]) == every_pair(1)
    finally:
        container.close()


@arun
async def test_the_header_is_left_alone_when_the_file_goes_away_while_it_is_rewritten(
    desk: JobDesk,
) -> None:
    desk._files.read_run = lambda run_id: None  # type: ignore[method-assign]
    desk._rewrite_header("ghost-run", {"notes": ""}, ended=None, interleaved=0, partial=None)
    desk._mark_partial(
        Job(
            "4" * 26,
            "run",
            JobState.INTERRUPTED,
            "ana",
            desk._clock.now(),
            desk._clock.now(),
            RunParams(DEMO, "a" * 32, ArmSpec("bare"), "m", 1),
            Progress(0, 1),
            artifact_id="ghost-run",
        ),
        "Stopped",
    )


# --- a scoring that goes wrong ---------------------------------------------------------------


@arun
async def test_a_scorer_that_raises_makes_a_failed_scoring_with_an_error_id(
    desk: JobDesk, caplog: pytest.LogCaptureFixture
) -> None:
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    await finished(desk, view)

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("internal detail /private/path")

    desk._scorer.score = boom  # type: ignore[method-assign]
    score = await desk.start_score(ANA_ACTOR, ScoreRequest(DEMO, (view.job.artifact_id or "",)))
    failed = await finished(desk, score)
    assert failed.job.state is JobState.FAILED and failed.job.error is not None
    assert failed.job.error.kind == "internal" and "private" not in failed.job.error.message
    assert "private" not in caplog.text
    assert desk.status().scoring is False


@arun
async def test_a_run_that_disappears_before_its_scoring_runs_fails_the_scoring_with_a_reason(
    desk: JobDesk,
) -> None:
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    await finished(desk, view)
    run_id = view.job.artifact_id or ""
    release = asyncio.Event()

    async def hold() -> None:
        await release.wait()

    desk._cpu.submit(hold)
    score = await desk.start_score(ANA_ACTOR, ScoreRequest(DEMO, (run_id,)))
    (desk._files.root / "runs" / f"{run_id}.qa-run.jsonl").unlink()
    release.set()
    failed = await finished(desk, score)
    assert failed.job.state is JobState.FAILED and failed.job.error is not None
    assert failed.job.error.kind == "not-found" and run_id in failed.job.error.message


@arun
async def test_what_the_scorer_warns_about_goes_to_the_log_of_the_scoring(
    desk: JobDesk, arms: ScriptedArms
) -> None:
    gate = arms.hold("DP-003")
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    await until(lambda: ("bare", "DP-003", 1) in arms.calls)
    desk.cancel(ANA_ACTOR, view.job.id)
    gate.set()
    await finished(desk, view)  # cancelled with 4 of 6 answers
    score = await desk.start_score(ANA_ACTOR, ScoreRequest(DEMO, (view.job.artifact_id or "",)))
    done = await finished(desk, score)
    assert done.job.state is JobState.DONE
    assert "warning: run " in desk.read_log(score.job.id).text
    assert "have no answer" in desk.read_log(score.job.id).text


@arun
async def test_a_member_with_many_jobs_cannot_start_a_scoring_either(
    desk: JobDesk, arms: ScriptedArms
) -> None:
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    await finished(desk, view)
    arms.hold("DP-001")
    for index in range(MAX_OPEN_PER_MEMBER):
        await desk.start_runs(
            ANA_ACTOR, type(request("bare"))(DEMO, (ArmSpec("bare", f"v{index}"),), ("fake-model",))
        )
    with pytest.raises(RateLimitedError):
        await desk.start_score(ANA_ACTOR, ScoreRequest(DEMO, (view.job.artifact_id or "",)))


@arun
async def test_a_scoring_found_running_at_start_up_is_interrupted_and_can_be_resumed(
    arms: ScriptedArms,
) -> None:
    container = container_with(arms)
    try:
        desk = container.bench.jobs
        (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
        await finished(desk, view)
        now = desk._clock.now()
        job_id = "5" * 26
        desk._repo.add(
            Job(
                job_id,
                "score",
                JobState.RUNNING,
                "ana",
                now,
                now,
                ScoreParams(DEMO, (view.job.artifact_id or "",), ScoreOptions()),
                Progress(0, 1),
                artifact_id=job_id,
            )
        )
        desk._recovered = False
        await desk.stop()
        await desk.start()
        assert desk.get(job_id).job.state is JobState.INTERRUPTED
        desk.resume(ANA_ACTOR, job_id)
        assert (await finished(desk, desk.get(job_id))).job.state is JobState.DONE
    finally:
        container.close()


@arun
async def test_an_unknown_set_in_a_resume_is_not_found(desk: JobDesk) -> None:
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    await finished(desk, view)
    desk._repo.add(
        view.job.with_changes(
            id="6" * 26,
            state=JobState.CANCELLED,
            params=RunParams(DEMO.__class__("gone", "v1"), "a" * 32, ArmSpec("bare"), "m", 1),
            artifact_id="bare-gone-v1-66666666",
        )
    )
    with pytest.raises(NotFoundError):
        desk.resume(ANA_ACTOR, "6" * 26)
    assert TOTAL == 6
