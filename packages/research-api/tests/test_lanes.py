"""The GPU lane: one job at a time, by priority, with positions."""

from __future__ import annotations

import asyncio
import logging

import pytest
from helpers import arun

from research_api.application.lanes import ASK_PRIORITY, RUN_PRIORITY, GpuLane


class Job:
    """A job the test releases by hand."""

    def __init__(self, name: str, log: list[str]) -> None:
        self.name, self.log = name, log
        self.release = asyncio.Event()
        self.started = asyncio.Event()

    async def __call__(self) -> None:
        self.log.append(f"start {self.name}")
        self.started.set()
        await self.release.wait()
        self.log.append(f"end {self.name}")


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


@arun
async def test_jobs_run_one_at_a_time_in_order() -> None:
    lane, log = GpuLane(), []
    first, second = Job("a", log), Job("b", log)
    lane.submit(first)
    lane.submit(second)
    await settle()
    assert log == ["start a"] and lane.busy and lane.queued == 1
    first.release.set()
    await second.started.wait()
    assert log == ["start a", "end a", "start b"]
    second.release.set()
    await settle()
    assert not lane.busy and lane.queued == 0
    await lane.stop()


@arun
async def test_a_job_of_higher_priority_goes_before_a_waiting_one() -> None:
    lane, log = GpuLane(), []
    running, run_job, ask = Job("running", log), Job("run", log), Job("ask", log)
    lane.submit(running)
    await settle()
    lane.submit(run_job, priority=RUN_PRIORITY)
    lane.submit(ask, priority=ASK_PRIORITY)
    for job in (running, run_job, ask):
        job.release.set()
    await settle()
    assert [line for line in log if line.startswith("start")] == [
        "start running",
        "start ask",
        "start run",
    ]
    await lane.stop()


@arun
async def test_positions_count_the_running_job_and_fall_as_jobs_finish() -> None:
    lane, log = GpuLane(), []
    seen: dict[str, list[int]] = {"a": [], "b": [], "c": []}
    jobs = {name: Job(name, log) for name in seen}
    for name in "abc":
        lane.submit(jobs[name], seen[name].append)
    await settle()
    assert seen == {"a": [0], "b": [1], "c": [2]}
    jobs["a"].release.set()
    await settle()
    assert seen["b"] == [1, 0]  # a is done: b is next (and now running)
    assert seen["c"] == [2, 1]
    for name in "bc":
        jobs[name].release.set()
    await settle()
    await lane.stop()


@arun
async def test_a_waiting_job_can_be_cancelled_and_a_running_one_cannot() -> None:
    lane, log = GpuLane(), []
    running, waiting, last = Job("running", log), Job("waiting", log), Job("last", log)
    positions: list[int] = []
    lane.submit(running)
    ticket = lane.submit(waiting)
    lane.submit(last, positions.append)
    await settle()
    assert positions == [2]
    assert lane.cancel(ticket) is True
    assert positions == [2, 1]
    assert lane.cancel(ticket) is False  # already gone
    running.release.set()
    last.release.set()
    await settle()
    assert "start waiting" not in log and "end last" in log
    await lane.stop()


@arun
async def test_a_job_that_raises_does_not_stop_the_lane(
    caplog: pytest.LogCaptureFixture,
) -> None:
    lane, log = GpuLane(), []

    async def bad() -> None:
        raise RuntimeError("a question: what is T1?")

    good = Job("good", log)
    good.release.set()
    with caplog.at_level(logging.ERROR, logger="research_api.lane"):
        lane.submit(bad)
        lane.submit(good)
        await settle()
    assert "end good" in log
    assert "what is T1" not in caplog.text and "RuntimeError" not in caplog.text
    await lane.stop()


@arun
async def test_stop_is_safe_to_repeat_and_the_lane_can_start_again() -> None:
    lane, log = GpuLane(), []
    await lane.stop()  # never started
    job = Job("a", log)
    job.release.set()
    lane.submit(job)
    await settle()
    await lane.stop()
    await lane.stop()
    second = Job("b", log)
    second.release.set()
    lane.submit(second)
    await settle()
    assert log == ["start a", "end a", "start b", "end b"]
    await lane.stop()


def test_the_lane_moves_to_a_new_event_loop() -> None:
    """A test client without ``with`` gives each request its own loop; the lane follows."""
    lane, log = GpuLane(), []

    async def once(name: str) -> None:
        job = Job(name, log)
        job.release.set()
        lane.submit(job)
        await job.started.wait()
        await settle()

    asyncio.run(once("a"))
    asyncio.run(once("b"))
    assert log == ["start a", "end a", "start b", "end b"]
