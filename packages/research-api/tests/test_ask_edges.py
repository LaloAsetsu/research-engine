"""Corners of the ask path that the larger tests pass by."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from ask_helpers import ScriptedEngine, engine_over, settle
from helpers import arun
from test_ask_desk import make, q

from research_api.adapters.fake import FakeAskEngine, FakeDocumentReader
from research_api.adapters.fake.answers import render
from research_api.application.documents import DocumentServices
from research_api.application.lanes import GpuLane, Ticket
from research_api.domain.ask import AskRecord, EventKind
from research_api.domain.errors import NotFoundError

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_the_document_use_cases_refuse_what_cannot_be_an_id_or_a_page() -> None:
    documents = DocumentServices(reader=FakeDocumentReader())
    for bad in ("short", "A" * 16, "../etc", ""):
        with pytest.raises(NotFoundError):
            documents.get(bad)
        with pytest.raises(NotFoundError):
            documents.page(bad, 1)
    with pytest.raises(NotFoundError):
        documents.page("a" * 16, 0)


def test_the_search_clamps_the_limit_and_trims_the_query() -> None:
    documents = DocumentServices(reader=FakeDocumentReader())
    assert len(documents.search("  ", 0).items) == 1
    assert len(documents.search("", 10_000).items) == documents.search("", 100).total
    assert documents.search("x" * 1000).total == 0


def test_an_answer_without_citations_has_no_references_list() -> None:
    rendered = render("Nothing is cited here.")
    assert rendered.text == "Nothing is cited here." and rendered.cited == ()


def test_a_record_that_has_never_been_queued_has_no_position_or_phase() -> None:
    r = AskRecord("id", "ana", NOW)
    assert (r.position, r.phase, r.answer, r.failure) == (None, None, None, None)
    r.start()
    assert r.waiting(1) is None
    r.entered("retrieving")
    assert r.phase == "retrieving"
    r.entered("answering")
    assert r.phase == "answering"
    assert [e.kind for e in r.events] == [EventKind.RETRIEVING, EventKind.ANSWERING]


@arun
async def test_a_warm_up_that_is_cancelled_is_not_swallowed() -> None:
    class Hanging:
        async def warm_up(self) -> None:
            await asyncio.sleep(3600)

        def close(self) -> None:
            return None

    engine = engine_over(Hanging)  # type: ignore[arg-type]
    task = asyncio.ensure_future(engine.warm_up())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert engine.health().state == "warming_up"


@arun
async def test_the_fake_engine_can_be_held_by_a_test() -> None:
    hold = asyncio.Event()
    engine = FakeAskEngine(hold=hold)
    task = asyncio.ensure_future(engine.ask(q("grover"), lambda _: None))
    await settle()
    assert not task.done()
    hold.set()
    assert (await task).citations


@arun
async def test_a_lane_started_again_takes_up_the_jobs_that_waited() -> None:
    lane, ran = GpuLane(), []

    async def job() -> None:
        ran.append(1)

    lane._waiting.append(Ticket(0, 1, job, None))  # queued while no worker was alive
    lane.start()
    await settle()
    assert ran == [1]
    await lane.stop()


@arun
async def test_the_desk_stops_cleanly_with_a_warm_up_in_flight() -> None:
    desk, _, _ = make()
    engine = ScriptedEngine("warming_up")

    async def slow() -> None:
        await asyncio.sleep(3600)

    engine.warm_up = slow  # type: ignore[method-assign]
    desk.engine = engine
    await desk.start()
    await settle()
    await desk.stop()
