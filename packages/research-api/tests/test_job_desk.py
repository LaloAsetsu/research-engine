"""The job desk: one GPU job at a time, asks between questions, cancel, resume and recovery."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from ask_helpers import ScriptedEngine
from bench_helpers import (
    ANA_ACTOR,
    BETO_ACTOR,
    DEMO_IDS,
    DEMO_REF,
    TOTAL,
    ScriptedArms,
    container_with,
    every_pair,
    finished,
    live_container,
    pairs,
    request,
    run_file,
    seed_stopped_run,
    state_of,
    until,
)
from helpers import MovableClock, arun
from qa_bench.formats import QA_RUN, check_against_set, load_bytes

from research_api.application.jobs import JobDesk, RunRequest, ScoreRequest
from research_api.bootstrap import Container
from research_api.domain.ask import AskAnswer, AskQuestion, Phase
from research_api.domain.errors import (
    ConflictError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
    UnavailableError,
    UpstreamError,
)
from research_api.domain.jobs import ArmSpec, JobError, JobState, Progress, RunParams, ScoreOptions
from research_api.domain.sets import SetRef


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


# --- a run from start to end ----------------------------------------------------------------


@arun
async def test_a_run_goes_from_queued_to_done_and_writes_a_file_that_qa_bench_accepts(
    desk: JobDesk, container: Container, arms: ScriptedArms
) -> None:
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare", notes="my notes"))
    job = view.job
    assert (job.state, view.position, job.progress) == (JobState.QUEUED, 0, Progress(0, TOTAL))
    assert job.params == RunParams(
        DEMO_REF, job.params.set_md5, ArmSpec("bare"), "fake-model", 1, "my notes"
    )  # type: ignore[union-attr]
    done = await finished(desk, view)
    assert done.job.state is JobState.DONE and done.job.progress == Progress(TOTAL, TOTAL)
    assert done.job.started_at and done.job.ended_at and done.position is None
    header, items = run_file(desk, view)
    assert pairs(items) == every_pair(1) and header["run_id"] == job.artifact_id
    assert header["ended"] and header["repeats"] == 1 and header["concurrency"] == 1
    assert header["notes"] == "my notes Interleaved asks: 0."
    assert (
        header["model"]["id"] == "fake-model" and header["model"]["endpoint"] == "fake-model (demo)"
    )
    assert header["set"]["name"] == "demo-papers/v1"
    # the file is a valid qa-run/1 file of the frozen set, read with every rule of the format
    data = desk._files.read_run(job.artifact_id or "")
    loaded = load_bytes(data or b"", QA_RUN)
    assert loaded.ok and loaded.file is not None
    _, set_bytes = container.sets.manage.export(DEMO_REF)
    qset = load_bytes(set_bytes).file
    assert qset is not None and check_against_set(loaded.file, qset) == []
    assert [c[2] for c in arms.calls] == [1] * TOTAL


@arun
async def test_progress_follows_each_answer_and_the_log_tells_it(
    desk: JobDesk, arms: ScriptedArms
) -> None:
    gate = arms.hold("DP-002")
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    await until(lambda: desk.get(view.job.id).job.progress.done == 2)
    now = desk.get(view.job.id).job
    assert now.state is JobState.RUNNING and now.progress == Progress(2, TOTAL)
    log = desk.read_log(view.job.id).text
    assert "2/6 DP-001-es repeat 1: answered" in log and "3/6" not in log
    gate.set()
    done = await finished(desk, view)
    assert done.job.progress.done == TOTAL
    text = desk.read_log(view.job.id).text
    assert text.startswith("20") and "queued by ana" in text and "done: 6 answers" in text
    assert text.count("answered") == TOTAL


@arun
async def test_the_log_never_holds_a_question_or_an_answer(desk: JobDesk) -> None:
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    await finished(desk, view)
    text = desk.read_log(view.job.id).text
    assert "What symbol" not in text and "answer to" not in text


@arun
async def test_repeats_make_every_pair_once_in_repeat_order(desk: JobDesk) -> None:
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare", repeats=3))
    done = await finished(desk, view)
    assert done.job.progress == Progress(3 * TOTAL, 3 * TOTAL)
    assert pairs(run_file(desk, view)[1]) == every_pair(3)


@arun
async def test_one_job_for_each_arm_and_model(desk: JobDesk) -> None:
    views = await desk.start_runs(
        ANA_ACTOR, RunRequest(DEMO_REF, (ArmSpec("bare"), ArmSpec("harness")), ("fake-model",), 1)
    )
    assert [v.job.params.arm.name for v in views] == ["bare", "harness"]  # type: ignore[union-attr]
    assert [v.position for v in views] == [0, 1]
    assert views[0].job.id < views[1].job.id
    assert len({v.job.artifact_id for v in views}) == 2
    for view in views:
        await finished(desk, view)


# --- the GPU lane: one run at a time, asks in between ---------------------------------------


@arun
async def test_two_runs_never_share_the_gpu(desk: JobDesk, arms: ScriptedArms) -> None:
    gate = arms.hold("DP-001-es")
    first, second = await desk.start_runs(ANA_ACTOR, request("bare", "harness"))
    await until(lambda: ("bare", "DP-001-es", 1) in arms.calls)
    assert state_of(desk, first.job.id) is JobState.RUNNING
    waiting = desk.get(second.job.id)
    assert waiting.job.state is JobState.QUEUED and waiting.position == 1
    assert not any(c[0] == "harness" for c in arms.calls)
    gate.set()
    await finished(desk, second)
    assert arms.max_active == 1
    order = [c[0] for c in arms.calls]
    assert (
        order == ["bare"] * TOTAL + ["harness"] * TOTAL
    )  # the first ended before the second began
    assert state_of(desk, first.job.id) is JobState.DONE


class OrderedArms(ScriptedArms):
    """Records, in one list, the questions of the run and the asks, in the order they ran."""

    def __init__(self, order: list[str]) -> None:
        super().__init__()
        self.order = order

    async def answer(self, call):  # type: ignore[no-untyped-def]
        self.order.append(f"run {call.item['id']}")
        return await super().answer(call)


class OrderedEngine(ScriptedEngine):
    def __init__(self, order: list[str]) -> None:
        super().__init__()
        self.order = order

    async def ask(self, question: AskQuestion, on_phase: Callable[[Phase], None]) -> AskAnswer:
        self.order.append("ask")
        return await super().ask(question, on_phase)


@arun
async def test_an_ask_goes_before_the_next_question_and_the_run_notes_it() -> None:
    order: list[str] = []
    arms, engine = OrderedArms(order), OrderedEngine(order)
    container = container_with(arms, ask_engine=engine)
    desk = container.bench.jobs
    try:
        gate = arms.hold("DP-001-es")
        (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
        await until(lambda: "run DP-001-es" in order)  # the second question is in flight
        container.asks.submit("beto", AskQuestion("an urgent question"))
        container.asks.submit("beto", AskQuestion("and another"))
        gate.set()
        await finished(desk, view)
        assert order[:5] == ["run DP-001", "run DP-001-es", "ask", "ask", "run DP-002"]
        header, _ = run_file(desk, view)
        assert header["notes"] == "Interleaved asks: 2."
        assert "2 asks shared the GPU" in desk.read_log(view.job.id).text
    finally:
        container.close()


# --- cancel ---------------------------------------------------------------------------------


@arun
async def test_a_queued_job_is_cancelled_at_once_and_never_runs(
    desk: JobDesk, arms: ScriptedArms
) -> None:
    gate = arms.hold("DP-001")
    first, second = await desk.start_runs(ANA_ACTOR, request("bare", "harness"))
    await until(lambda: arms.calls)
    cancelled = desk.cancel(ANA_ACTOR, second.job.id)
    assert cancelled.job.state is JobState.CANCELLED and cancelled.job.ended_at is not None
    gate.set()
    await finished(desk, first)
    assert desk._files.read_run(second.job.artifact_id or "") is None
    assert not any(c[0] == "harness" for c in arms.calls)
    assert desk.get(second.job.id).job.state is JobState.CANCELLED


@arun
async def test_a_running_job_stops_between_questions_and_the_partial_run_is_marked(
    desk: JobDesk, arms: ScriptedArms
) -> None:
    gate = arms.hold("DP-002")
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    await until(lambda: ("bare", "DP-002", 1) in arms.calls)
    asked = desk.cancel(ANA_ACTOR, view.job.id)
    assert asked.job.state is JobState.CANCELLING
    assert desk.cancel(ANA_ACTOR, view.job.id).job.state is JobState.CANCELLING  # asking twice
    assert len(arms.calls) == 3  # the question in flight is not cut
    gate.set()
    done = await finished(desk, view)
    assert done.job.state is JobState.CANCELLED and done.job.progress == Progress(3, TOTAL)
    assert len(arms.calls) == 3  # and no question was asked after it
    header, items = run_file(desk, view)
    assert pairs(items) == every_pair(1)[:3]  # the answer in flight was written whole
    assert "ended" not in header
    assert "Partial: Cancelled at" in header["notes"] and "with 3 of 6 answers" in header["notes"]
    assert "cancelled with 3 of 6 answers" in desk.read_log(view.job.id).text


@arun
async def test_a_cancel_that_comes_during_the_last_question_still_finishes_the_run(
    desk: JobDesk, arms: ScriptedArms
) -> None:
    gate = arms.hold("DP-005")
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    await until(lambda: ("bare", "DP-005", 1) in arms.calls)
    desk.cancel(ANA_ACTOR, view.job.id)
    gate.set()
    done = await finished(desk, view)
    assert done.job.state is JobState.DONE and done.job.progress == Progress(TOTAL, TOTAL)
    assert run_file(desk, view)[0]["ended"]


@arun
async def test_cancel_and_resume_are_for_the_owner_or_an_admin(
    desk: JobDesk, arms: ScriptedArms
) -> None:
    gate = arms.hold("DP-001")
    (view,) = await desk.start_runs(BETO_ACTOR, request("bare"))
    await until(lambda: arms.calls)
    with pytest.raises(ForbiddenError):
        desk.cancel(type(BETO_ACTOR)("dani"), view.job.id)
    assert desk.cancel(ANA_ACTOR, view.job.id).job.state is JobState.CANCELLING  # an admin may
    gate.set()
    await finished(desk, view)
    with pytest.raises(ForbiddenError):
        desk.resume(type(BETO_ACTOR)("dani"), view.job.id)
    assert desk.resume(BETO_ACTOR, view.job.id).job.state is JobState.QUEUED
    await finished(desk, view)


@arun
async def test_jobs_that_cannot_be_cancelled_or_resumed(desk: JobDesk) -> None:
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    await finished(desk, view)
    with pytest.raises(ConflictError, match="done cannot be cancelled"):
        desk.cancel(ANA_ACTOR, view.job.id)
    with pytest.raises(ConflictError, match="done cannot be resumed"):
        desk.resume(ANA_ACTOR, view.job.id)
    for bad in ("nope", "0" * 26):
        with pytest.raises(NotFoundError):
            desk.get(bad)
        with pytest.raises(NotFoundError):
            desk.cancel(ANA_ACTOR, bad)


# --- resume ---------------------------------------------------------------------------------


@arun
async def test_a_resumed_run_ends_with_every_question_and_repeat_exactly_once(
    desk: JobDesk, arms: ScriptedArms
) -> None:
    gate = arms.hold("DP-002", repeat=2)  # the third question of the second repeat
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare", repeats=2))
    await until(lambda: ("bare", "DP-002", 2) in arms.calls)
    desk.cancel(ANA_ACTOR, view.job.id)
    gate.set()
    stopped = await finished(desk, view)
    assert stopped.job.state is JobState.CANCELLED and stopped.job.progress.done == TOTAL + 3
    first_calls = list(arms.calls)
    resumed = desk.resume(ANA_ACTOR, view.job.id)
    assert resumed.job.state is JobState.QUEUED and resumed.job.ended_at is None
    assert resumed.job.error is None and resumed.job.started_at == stopped.job.started_at
    done = await finished(desk, view)
    assert done.job.state is JobState.DONE and done.job.progress == Progress(2 * TOTAL, 2 * TOTAL)
    header, items = run_file(desk, view)
    assert pairs(items) == every_pair(2)  # each pair once, in order
    assert [c[1:] for c in arms.calls[len(first_calls) :]] == every_pair(2)[TOTAL + 3 :]
    assert header["ended"] and "Partial" not in header["notes"]
    assert state_of(desk, view.job.id) is JobState.DONE
    assert "resume asked by ana" in desk.read_log(view.job.id).text


@arun
async def test_a_resume_goes_to_the_back_of_the_queue(desk: JobDesk, arms: ScriptedArms) -> None:
    gate = arms.hold("DP-001")
    first, second = await desk.start_runs(ANA_ACTOR, request("bare", "harness"))
    await until(lambda: arms.calls)
    desk.cancel(ANA_ACTOR, first.job.id)
    gate.set()
    await finished(desk, first)
    await finished(desk, second)  # the second ran while the first was cancelled
    resumed = desk.resume(ANA_ACTOR, first.job.id)
    assert resumed.position in (0, None)
    await finished(desk, first)


@arun
async def test_a_resume_refuses_a_set_that_is_not_the_file_the_run_started_on(
    desk: JobDesk, arms: ScriptedArms, container: Container
) -> None:
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    await finished(desk, view)
    cancelled = desk._repo.move(view.job.id, expected={JobState.DONE}, to=JobState.CANCELLED)
    assert cancelled is not None
    assert isinstance(cancelled.params, RunParams)
    other = ArmSpec("bare")
    changed = RunParams(cancelled.params.set, "f" * 32, other, "fake-model", 1)
    desk._repo.add(cancelled.with_changes(id="1" * 26, state=JobState.CANCELLED, params=changed))
    with pytest.raises(ConflictError, match="no longer the file"):
        desk.resume(ANA_ACTOR, "1" * 26)


# --- a model server that goes away, and a question that fails -------------------------------


@arun
async def test_a_model_server_that_goes_away_interrupts_the_run_and_a_resume_finishes_it(
    desk: JobDesk, arms: ScriptedArms
) -> None:
    arms.errors[("DP-003", 1)] = UnavailableError("The model server is down.")
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    stopped = await finished(desk, view)
    assert stopped.job.state is JobState.INTERRUPTED
    assert stopped.job.error == JobError("unavailable", "The model server is down.")
    assert stopped.job.progress.done == 3 and stopped.job.ended_at is not None
    header, items = run_file(desk, view)
    assert pairs(items) == every_pair(1)[:3] and "Partial: Interrupted at" in header["notes"]
    del arms.errors[("DP-003", 1)]
    desk.resume(ANA_ACTOR, view.job.id)
    done = await finished(desk, view)
    assert done.job.state is JobState.DONE and done.job.error is None
    assert pairs(run_file(desk, view)[1]) == every_pair(1)


@arun
async def test_a_question_the_model_refuses_is_recorded_and_the_run_goes_on(
    desk: JobDesk, arms: ScriptedArms
) -> None:
    arms.errors[("DP-002", 1)] = UpstreamError("The model server refused the request.")
    arms.errors[("DP-004", 1)] = TimeoutError()
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    done = await finished(desk, view)
    assert done.job.state is JobState.DONE and done.job.progress == Progress(TOTAL, TOTAL)
    _, items = run_file(desk, view)
    by_id = {i["id"]: i for i in items}
    assert by_id["DP-002"]["answer"] is None
    assert by_id["DP-002"]["error"] == {
        "kind": "other",
        "message": "The model server refused the request.",
    }
    assert by_id["DP-004"]["error"]["kind"] == "timeout"
    assert by_id["DP-001"]["answer"] and "error" not in by_id["DP-001"]
    assert "DP-002 repeat 1: error other" in desk.read_log(view.job.id).text


@arun
async def test_a_surprise_in_one_question_is_recorded_without_its_message(
    desk: JobDesk, arms: ScriptedArms, caplog: pytest.LogCaptureFixture
) -> None:
    arms.errors[("DP-001", 1)] = RuntimeError("the question was What symbol does ...")
    with caplog.at_level(logging.INFO, logger="research_api"):
        (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
        await finished(desk, view)
    item = run_file(desk, view)[1][0]
    assert item["error"]["kind"] == "other" and item["error"]["message"].startswith(
        "Unexpected error "
    )
    assert "What symbol" not in caplog.text and "What symbol" not in str(item)
    assert any(getattr(r, "kind", None) == "RuntimeError" for r in caplog.records)


# --- waiting for a model server -------------------------------------------------------------


@arun
async def test_a_run_waits_in_the_queue_while_its_model_server_does_not_answer(
    desk: JobDesk, arms: ScriptedArms
) -> None:
    arms.waiting["bare"] = "The model server of fake-model does not answer."
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    await until(lambda: desk.get(view.job.id).waiting_for is not None)
    waiting = desk.get(view.job.id)
    assert waiting.job.state is JobState.QUEUED and waiting.position == 0
    assert waiting.waiting_for == "The model server of fake-model does not answer."
    assert arms.calls == []
    assert desk.read_log(view.job.id).text.count("waiting:") == 1  # said once, not every check
    arms.waiting["bare"] = None
    done = await finished(desk, view)
    assert done.job.state is JobState.DONE and done.waiting_for is None


@arun
async def test_a_job_that_waits_can_be_cancelled(desk: JobDesk, arms: ScriptedArms) -> None:
    arms.waiting["bare"] = "down"
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    await until(lambda: desk.get(view.job.id).waiting_for)
    assert desk.cancel(ANA_ACTOR, view.job.id).job.state is JobState.CANCELLED
    await asyncio.sleep(0.1)
    assert arms.calls == [] and state_of(desk, view.job.id) is JobState.CANCELLED


@arun
async def test_a_check_that_raises_is_a_reason_to_wait_not_the_end_of_the_scheduler(
    desk: JobDesk, arms: ScriptedArms
) -> None:
    broken = {"on": True}

    async def waiting_for(arm: str, model: str) -> str | None:
        if broken["on"]:
            raise RuntimeError("probe blew up")
        return None

    arms.waiting_for = waiting_for  # type: ignore[method-assign]
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    await until(lambda: desk.get(view.job.id).waiting_for is not None)
    assert "cannot be checked" in (desk.get(view.job.id).waiting_for or "")
    broken["on"] = False
    assert (await finished(desk, view)).job.state is JobState.DONE


# --- what is refused when a run is asked for ------------------------------------------------


@arun
async def test_a_draft_cannot_be_run(desk: JobDesk) -> None:
    with pytest.raises(ConflictError, match="draft"):
        await desk.start_runs(
            ANA_ACTOR, RunRequest(SetRef("demo-papers", "v2"), (ArmSpec("bare"),), ("fake-model",))
        )


@arun
async def test_an_unknown_set_is_not_found(desk: JobDesk) -> None:
    with pytest.raises(NotFoundError):
        await desk.start_runs(
            ANA_ACTOR, RunRequest(SetRef("nothing", "v1"), (ArmSpec("bare"),), ("fake-model",))
        )


@arun
async def test_an_unknown_or_unavailable_model_is_refused_before_any_job_is_made(
    desk: JobDesk,
) -> None:
    with pytest.raises(InvalidInputError) as caught:
        await desk.start_runs(ANA_ACTOR, request("bare", models=("fake-model", "nobody")))
    assert caught.value.errors[0].path == "/models/1"
    with pytest.raises(UnavailableError, match="does not answer"):
        await desk.start_runs(ANA_ACTOR, request("bare", models=("fake-offline",)))
    assert desk.list() == []


@arun
async def test_an_arm_the_service_cannot_run_is_refused(desk: JobDesk, arms: ScriptedArms) -> None:
    arms.refused["oracle"] = "No oracle yet."
    with pytest.raises(UnavailableError, match="No oracle yet"):
        await desk.start_runs(ANA_ACTOR, request("bare", "oracle"))
    assert desk.list() == []


@arun
@pytest.mark.parametrize(
    ("kw", "path"),
    [
        ({"repeats": 0}, "/repeats"),
        ({"repeats": 11}, "/repeats"),
        ({"models": ()}, "/models"),
        ({"models": ("fake-model", "fake-model")}, "/models"),
        ({"models": ("a", "b", "c", "d", "e")}, "/models"),
        ({"notes": "n" * 1001}, "/notes"),
    ],
)
async def test_a_bad_request_is_refused_with_its_place(
    desk: JobDesk, kw: dict[str, Any], path: str
) -> None:
    with pytest.raises(InvalidInputError) as caught:
        await desk.start_runs(ANA_ACTOR, request("bare", **kw))
    assert caught.value.errors[0].path == path


@arun
async def test_the_arms_of_a_request_are_one_to_three_and_not_repeated(desk: JobDesk) -> None:
    with pytest.raises(InvalidInputError) as caught:
        await desk.start_runs(ANA_ACTOR, RunRequest(DEMO_REF, (), ("fake-model",)))
    assert caught.value.errors[0].path == "/arms"
    with pytest.raises(InvalidInputError, match="request is not valid"):
        await desk.start_runs(ANA_ACTOR, request("bare", "bare"))
    harness = ArmSpec("harness", None, {"bm25": "never"})
    with pytest.raises(InvalidInputError):
        await desk.start_runs(ANA_ACTOR, RunRequest(DEMO_REF, (harness, harness), ("fake-model",)))
    four = RunRequest(DEMO_REF, tuple(ArmSpec("bare", f"v{i}") for i in range(4)), ("fake-model",))
    with pytest.raises(InvalidInputError):
        await desk.start_runs(ANA_ACTOR, four)
    same_arm_two_variants = RunRequest(
        DEMO_REF, (ArmSpec("bare", "a"), ArmSpec("bare", "b")), ("fake-model",)
    )
    views = await desk.start_runs(ANA_ACTOR, same_arm_two_variants)
    assert len(views) == 2


@arun
async def test_a_member_cannot_pile_up_jobs(desk: JobDesk, arms: ScriptedArms) -> None:
    arms.hold("DP-001")
    await desk.start_runs(ANA_ACTOR, RunRequest(DEMO_REF, (ArmSpec("bare"),), ("fake-model",)))
    for index in range(11):
        await desk.start_runs(
            ANA_ACTOR, RunRequest(DEMO_REF, (ArmSpec("bare", f"v{index}"),), ("fake-model",))
        )
    with pytest.raises(RateLimitedError) as caught:
        await desk.start_runs(ANA_ACTOR, request("harness"))
    assert caught.value.retry_after_s > 0
    await desk.start_runs(BETO_ACTOR, request("bare"))  # somebody else is not held back
    assert len(desk.list(owner="ana")) == 12


# --- recovery -------------------------------------------------------------------------------


@arun
async def test_a_run_found_running_at_start_up_is_interrupted_and_a_resume_finishes_it(
    arms: ScriptedArms,
) -> None:
    container = container_with(arms)
    try:
        desk, job_id = seed_stopped_run(container, JobState.RUNNING, 2, half_line=True)
        await desk.start()
        found = desk.get(job_id)
        assert found.job.state is JobState.INTERRUPTED and found.job.ended_at is not None
        assert found.job.error == JobError("interrupted", "The service stopped while this job ran.")
        assert "found at start-up and now interrupted" in desk.read_log(job_id).text
        run_id = found.job.artifact_id or ""
        header, items = run_file(desk, found)
        assert pairs(items) == every_pair(1)[:2]
        assert "Partial: Stopped at" in header["notes"] and "with 2 of 6 answers" in header["notes"]
        desk.resume(ANA_ACTOR, job_id)
        done = await finished(desk, found)
        assert done.job.state is JobState.DONE
        _, items = run_file(desk, done)
        assert pairs(items) == every_pair(1)  # the half line was cut, nothing twice
        assert [i["answer"] for i in items[:2]] == ["old DP-001", "old DP-001-es"]
        assert [c[1] for c in arms.calls] == DEMO_IDS[2:]  # only what was missing was asked
        data = desk._files.read_run(run_id)
        assert load_bytes(data or b"", QA_RUN).ok
    finally:
        container.close()


@arun
async def test_a_run_found_cancelling_at_start_up_is_cancelled(arms: ScriptedArms) -> None:
    container = container_with(arms)
    try:
        desk, job_id = seed_stopped_run(container, JobState.CANCELLING, 3)
        await desk.start()
        assert state_of(desk, job_id) is JobState.CANCELLED
        header, _ = run_file(desk, desk.get(job_id))
        assert "Partial" in header["notes"]
    finally:
        container.close()


@arun
async def test_a_queued_job_found_at_start_up_still_runs(arms: ScriptedArms) -> None:
    container = container_with(arms)
    try:
        desk, job_id = seed_stopped_run(container, JobState.QUEUED, 0)
        await desk.start()
        done = await finished(desk, desk.get(job_id))
        assert done.job.state is JobState.DONE and arms.calls
    finally:
        container.close()


@arun
async def test_recovery_happens_once_per_desk(arms: ScriptedArms) -> None:
    container = container_with(arms)
    try:
        desk, job_id = seed_stopped_run(container, JobState.RUNNING, 1)
        await desk.start()
        await desk.stop()
        # something started running after the first recovery; a second start must not touch it
        desk._repo.move(job_id, expected={JobState.INTERRUPTED}, to=JobState.QUEUED)
        desk._repo.move(job_id, expected={JobState.QUEUED}, to=JobState.RUNNING)
        await desk.start()
        assert state_of(desk, job_id) is JobState.RUNNING
        await desk.stop()
    finally:
        container.close()


@arun
async def test_stopping_the_desk_interrupts_the_run_in_flight_and_keeps_its_file(
    desk: JobDesk, arms: ScriptedArms
) -> None:
    gate = arms.hold("DP-002")
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    await until(lambda: ("bare", "DP-002", 1) in arms.calls)
    await desk.stop()
    gate.set()
    stopped = desk.get(view.job.id)
    assert stopped.job.state is JobState.INTERRUPTED and stopped.job.progress.done == 2
    header, items = run_file(desk, stopped)
    assert pairs(items) == every_pair(1)[:2] and "Partial: Stopped at" in header["notes"]
    await desk.start()  # started again in the same process: the run can be resumed
    desk.resume(ANA_ACTOR, view.job.id)
    assert (await finished(desk, stopped)).job.state is JobState.DONE
    assert pairs(run_file(desk, stopped)[1]) == every_pair(1)


@arun
async def test_a_stop_with_a_cancel_pending_leaves_the_job_cancelled(
    desk: JobDesk, arms: ScriptedArms
) -> None:
    arms.hold("DP-002")
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    await until(lambda: ("bare", "DP-002", 1) in arms.calls)
    desk.cancel(ANA_ACTOR, view.job.id)
    await desk.stop()
    assert state_of(desk, view.job.id) is JobState.CANCELLED


@arun
async def test_a_run_whose_set_changed_fails_instead_of_mixing_two_files(
    desk: JobDesk, container: Container
) -> None:
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    await finished(desk, view)
    params = view.job.params
    assert isinstance(params, RunParams)
    desk._repo.add(
        view.job.with_changes(
            id="2" * 26,
            state=JobState.QUEUED,
            params=RunParams(params.set, "e" * 32, params.arm, params.model, 1),
            artifact_id="bare-demo-papers-v1-22222222",
        )
    )
    desk._poke()
    await until(lambda: desk.get("2" * 26).job.settled)
    failed = desk.get("2" * 26).job
    assert failed.state is JobState.FAILED and failed.error is not None
    assert failed.error.kind == "conflict" and "no longer the file" in failed.error.message


# --- reading --------------------------------------------------------------------------------


@arun
async def test_jobs_are_listed_newest_first_with_filters_and_positions(
    desk: JobDesk, arms: ScriptedArms
) -> None:
    gate = arms.hold("DP-001")
    a, b, c = await desk.start_runs(ANA_ACTOR, request("bare", "harness", "oracle"))
    await until(lambda: arms.calls)
    listed = desk.list()
    assert [v.job.id for v in listed] == [c.job.id, b.job.id, a.job.id]
    assert [v.position for v in listed] == [2, 1, None]
    assert [v.job.id for v in desk.list(states=[JobState.QUEUED])] == [c.job.id, b.job.id]
    assert [v.job.id for v in desk.list(owner="beto")] == []
    assert desk.list(kind="score") == [] and len(desk.list(limit=1)) == 1
    assert len(desk.list(limit=1000)) == 3 and len(desk.list(offset=2)) == 1
    status = desk.status()
    assert (status.running_job, status.queued_runs, status.scoring) == (a.job.id, 2, False)
    gate.set()
    for view in (a, b, c):
        await finished(desk, view)
    assert desk.status().running_job is None and desk.status().queued_runs == 0


@arun
async def test_the_log_is_read_from_an_offset(desk: JobDesk) -> None:
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    await finished(desk, view)
    whole = desk.read_log(view.job.id)
    part = desk.read_log(view.job.id, 0, 60)
    assert part.next_offset < whole.size
    rest = desk.read_log(view.job.id, part.next_offset)
    assert part.text + rest.text == whole.text
    with pytest.raises(NotFoundError):
        desk.read_log("0" * 26)
    never_logged = desk._repo.get(view.job.id)
    assert never_logged is not None


# --- a scoring ------------------------------------------------------------------------------


@arun
async def test_a_scoring_runs_on_the_cpu_lane_while_a_run_holds_the_gpu(
    desk: JobDesk, arms: ScriptedArms
) -> None:
    first, second = await desk.start_runs(ANA_ACTOR, request("bare", "harness"))
    await finished(desk, first)
    await finished(desk, second)
    gate = arms.hold("DP-001")
    (third,) = await desk.start_runs(ANA_ACTOR, request("oracle"))
    await until(lambda: ("oracle", "DP-001", 1) in arms.calls)
    runs = (first.job.artifact_id or "", second.job.artifact_id or "")
    score = await desk.start_score(
        ANA_ACTOR, ScoreRequest(DEMO_REF, runs, ScoreOptions(pairs=((runs[0], runs[1]),)))
    )
    done = await finished(desk, score)
    assert done.job.state is JobState.DONE  # while the oracle run still holds the GPU lane
    assert state_of(desk, third.job.id) is JobState.RUNNING
    assert done.job.progress == Progress(1, 1) and done.job.artifact_id == score.job.id
    gate.set()
    await finished(desk, third)


@arun
async def test_scoring_refuses_what_cannot_be_scored(desk: JobDesk, arms: ScriptedArms) -> None:
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    await finished(desk, view)
    run = view.job.artifact_id or ""

    async def refused(request_: ScoreRequest) -> InvalidInputError:
        with pytest.raises(InvalidInputError) as caught:
            await desk.start_score(ANA_ACTOR, request_)
        return caught.value

    assert (await refused(ScoreRequest(DEMO_REF, ()))).errors[0].path == "/runs"
    assert (await refused(ScoreRequest(DEMO_REF, (run, run)))).errors[0].path == "/runs"
    assert (await refused(ScoreRequest(DEMO_REF, ("a/b",)))).errors[0].path == "/runs/0"
    assert (await refused(ScoreRequest(DEMO_REF, ("ghost-run-1",)))).errors[0].path == "/runs/0"
    other = SetRef("demo-papers", "v2")
    assert (await refused(ScoreRequest(other, (run,)))).errors[0].path == "/runs/0"
    options = ScoreOptions(pairs=((run, "ghost"),))
    assert (await refused(ScoreRequest(DEMO_REF, (run,), options))).errors[0].path == "/pairs/0"
    options = ScoreOptions(pairs=((run, run),))
    assert (await refused(ScoreRequest(DEMO_REF, (run,), options))).errors[0].path == "/pairs/0"
    options = ScoreOptions(noise=(run, "ghost"))
    assert (await refused(ScoreRequest(DEMO_REF, (run,), options))).errors[0].path == "/noise"
    options = ScoreOptions(noise=(run, run))
    assert (await refused(ScoreRequest(DEMO_REF, (run,), options))).errors[0].path == "/noise"
    with pytest.raises(NotFoundError):
        await desk.start_score(ANA_ACTOR, ScoreRequest(SetRef("nothing", "v1"), (run,)))


@arun
async def test_a_run_that_has_not_ended_cannot_be_scored(desk: JobDesk, arms: ScriptedArms) -> None:
    gate = arms.hold("DP-002")
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    await until(lambda: ("bare", "DP-002", 1) in arms.calls)
    with pytest.raises(ConflictError, match="has not ended"):
        await desk.start_score(ANA_ACTOR, ScoreRequest(DEMO_REF, (view.job.artifact_id or "",)))
    gate.set()
    await finished(desk, view)


@arun
async def test_a_scoring_that_fails_says_why(desk: JobDesk, arms: ScriptedArms) -> None:
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    await finished(desk, view)
    run = view.job.artifact_id or ""
    # a pair names a run twice in other words: qa-bench refuses it with its own words
    score = await desk.start_score(
        ANA_ACTOR, ScoreRequest(DEMO_REF, (run,), ScoreOptions(engine="dice"))
    )
    failed = await finished(desk, score)
    assert failed.job.state is JobState.FAILED and failed.job.error is not None
    assert failed.job.error.kind == "invalid-input" and "/engine" not in failed.job.error.message
    assert "engine" in failed.job.error.message
    assert "failed: invalid-input" in desk.read_log(score.job.id).text


@arun
async def test_a_scoring_cannot_be_cancelled_once_it_runs_but_can_while_it_waits(
    desk: JobDesk,
) -> None:
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    await finished(desk, view)
    run = view.job.artifact_id or ""
    release = asyncio.Event()

    async def slow() -> None:
        await release.wait()

    desk._cpu.submit(slow)
    queued = await desk.start_score(ANA_ACTOR, ScoreRequest(DEMO_REF, (run,)))
    assert desk.get(queued.job.id).job.state is JobState.QUEUED
    assert desk.cancel(ANA_ACTOR, queued.job.id).job.state is JobState.CANCELLED
    release.set()
    await asyncio.sleep(0.1)
    assert desk._files.read_score(queued.job.id) is None
    again = desk._repo.move(queued.job.id, expected={JobState.CANCELLED}, to=JobState.RUNNING)
    assert again is not None
    with pytest.raises(ConflictError, match="cannot be stopped"):
        desk.cancel(ANA_ACTOR, queued.job.id)


@arun
async def test_a_queued_scoring_found_at_start_up_still_runs() -> None:
    container = container_with(ScriptedArms())
    try:
        desk = container.bench.jobs
        (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
        await finished(desk, view)
        run = view.job.artifact_id or ""
        await desk.stop()
        from research_api.domain.jobs import Job, ScoreParams  # noqa: PLC0415

        now = desk._clock.now()
        job_id = "3" * 26
        desk._repo.add(
            Job(
                job_id,
                "score",
                JobState.QUEUED,
                "ana",
                now,
                now,
                ScoreParams(DEMO_REF, (run,)),
                Progress(0, 1),
                artifact_id=job_id,
            )
        )
        fresh = container.bench.jobs
        fresh._recovered = False
        await fresh.start()
        done = await finished(fresh, fresh.get(job_id))
        assert done.job.state is JobState.DONE
        assert fresh._files.read_score(job_id)
    finally:
        container.close()


@arun
async def test_a_resumed_scoring_runs_again(desk: JobDesk) -> None:
    (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
    await finished(desk, view)
    run = view.job.artifact_id or ""
    score = await desk.start_score(ANA_ACTOR, ScoreRequest(DEMO_REF, (run,)))
    await finished(desk, score)
    desk._repo.move(score.job.id, expected={JobState.DONE}, to=JobState.INTERRUPTED)
    resumed = desk.resume(ANA_ACTOR, score.job.id)
    assert resumed.job.state is JobState.QUEUED
    assert (await finished(desk, score)).job.state is JobState.DONE


@arun
async def test_a_member_must_name_the_clock_the_desk_uses_for_its_times(
    tmp_path: Path,
) -> None:
    clock = MovableClock()
    container = container_with(ScriptedArms(), clock=clock)
    try:
        desk = container.bench.jobs
        (view,) = await desk.start_runs(ANA_ACTOR, request("bare"))
        assert view.job.created_at == clock.at
        done = await finished(desk, view)
        assert done.job.ended_at == clock.at
        assert desk.read_log(view.job.id).text.startswith("2026-01-01 00:00:00Z queued by ana")
    finally:
        container.close()


def test_the_live_container_has_the_same_desk(tmp_path: Path) -> None:
    container = live_container(tmp_path, ScriptedArms())
    try:
        assert isinstance(container.bench.jobs, JobDesk)
        assert container.bench.jobs.get is not None
    finally:
        container.close()
