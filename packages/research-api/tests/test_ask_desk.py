"""The desk: asks wait their turn in the lane, are followed by events, and are forgotten."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import pytest
from ask_helpers import ScriptedEngine, answer_for, settle
from helpers import MovableClock, arun

from research_api.application.ask import AskDesk
from research_api.domain.ask import AskOptions, AskQuestion, AskState
from research_api.domain.errors import (
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
    UnavailableError,
    UpstreamError,
)


def q(text: str, **kwargs: object) -> AskQuestion:
    return AskQuestion(text, **kwargs)  # type: ignore[arg-type]


async def collect(desk: AskDesk, owner: str, ask_id: str, after: int | None = None) -> list[tuple]:
    return [(e.kind.value, e.position) async for e in desk.events(owner, ask_id, after)]


def make(**kwargs: object) -> tuple[AskDesk, ScriptedEngine, MovableClock]:
    engine, clock = ScriptedEngine(), MovableClock()
    return AskDesk(engine=engine, clock=clock, **kwargs), engine, clock  # type: ignore[arg-type]


@arun
async def test_an_ask_goes_through_every_event_to_its_answer() -> None:
    desk, _, _ = make()
    await desk.start()
    done = desk.submit("ana", q("one"))
    assert done.position == 0
    assert await collect(desk, "ana", done.ask_id) == [
        ("queued", 0),
        ("retrieving", None),
        ("answering", None),
        ("answer", None),
    ]
    record = desk.get("ana", done.ask_id)
    assert record.state is AskState.DONE and record.answer is not None
    assert record.answer.answer == "Answer to: one"
    await desk.stop()


@arun
async def test_asks_run_one_at_a_time_and_say_how_many_are_ahead() -> None:
    desk, engine, _ = make()
    gate = engine.hold("first")
    await desk.start()
    first = desk.submit("ana", q("first"))
    second = desk.submit("beto", q("second"))
    third = desk.submit("dani", q("third"))
    assert (first.position, second.position, third.position) == (0, 1, 2)
    await settle()
    assert engine.asked == ["first"]
    assert desk.status().queued == 2 and desk.status().busy
    gate.set()
    events = await collect(desk, "dani", third.ask_id)
    assert events == [
        ("queued", 2),
        ("queued", 1),
        ("queued", 0),
        ("retrieving", None),
        ("answering", None),
        ("answer", None),
    ]
    assert engine.asked == ["first", "second", "third"]
    await desk.stop()


@arun
async def test_a_member_may_have_only_a_few_asks_open() -> None:
    desk, engine, _ = make(per_user=2)
    engine.hold("a")
    await desk.start()
    desk.submit("ana", q("a"))
    held = desk.submit("ana", q("b"))
    with pytest.raises(RateLimitedError) as error:
        desk.submit("ana", q("c"))
    assert error.value.retry_after_s > 0
    desk.submit("beto", q("d"))  # someone else is not held back
    desk.cancel("ana", held.ask_id)
    desk.submit("ana", q("e"))  # a place is free again
    await desk.stop()


@arun
async def test_the_whole_queue_has_a_limit() -> None:
    desk, engine, _ = make(capacity=2, per_user=5)
    engine.hold("a")
    await desk.start()
    desk.submit("ana", q("a"))
    desk.submit("beto", q("b"))
    with pytest.raises(UnavailableError) as error:
        desk.submit("dani", q("c"))
    assert error.value.retry_after_s
    await desk.stop()


@arun
async def test_asks_are_refused_while_the_engine_warms_up() -> None:
    desk, engine, _ = make()
    engine.state = "warming_up"
    with pytest.raises(UnavailableError) as error:
        desk.submit("ana", q("a"))
    assert error.value.retry_after_s
    await desk.start()  # the warm-up runs in the background
    await settle()
    assert engine.warmed == 1 and desk.status().health.state == "ready"
    desk.submit("ana", q("a"))
    await desk.stop()


@arun
async def test_an_unavailable_engine_still_takes_asks() -> None:
    desk, engine, _ = make()
    engine.state, engine.reason = "unavailable", "The search index is not ready."
    await desk.start()
    desk.submit("ana", q("a"))  # the cause may be fixed; the answer says
    assert desk.status().health.reason == "The search index is not ready."
    await desk.stop()


@arun
async def test_a_waiting_ask_is_cancelled_without_reaching_the_engine() -> None:
    desk, engine, _ = make()
    gate = engine.hold("first")
    await desk.start()
    desk.submit("ana", q("first"))
    second = desk.submit("beto", q("second"))
    desk.cancel("beto", second.ask_id)
    record = desk.get("beto", second.ask_id)
    assert record.state is AskState.CANCELLED
    assert await collect(desk, "beto", second.ask_id) == [("queued", 1), ("cancelled", None)]
    gate.set()
    await settle()
    assert engine.asked == ["first"]
    await desk.stop()


@arun
async def test_a_running_ask_cannot_be_stopped_but_its_answer_is_dropped() -> None:
    desk, engine, _ = make()
    gate = engine.hold("slow")
    await desk.start()
    done = desk.submit("ana", q("slow"))
    await settle()
    watcher = asyncio.ensure_future(collect(desk, "ana", done.ask_id))
    await settle()
    desk.cancel("ana", done.ask_id)
    assert await watcher == [
        ("queued", 0),
        ("retrieving", None),
        ("answering", None),
        ("cancelled", None),
    ]
    gate.set()
    await settle()
    record = desk.get("ana", done.ask_id)
    assert record.state is AskState.CANCELLED and record.answer is None
    assert desk.status().busy is False
    await desk.stop()


@arun
async def test_a_failure_dropped_with_a_cancelled_ask_is_not_logged_as_a_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    desk, engine, _ = make()
    gate = engine.hold("slow")
    engine.errors["slow"] = UpstreamError("The model server refused the request.")
    await desk.start()
    with caplog.at_level(logging.INFO, logger="research_api"):
        done = desk.submit("ana", q("slow"))
        await settle()
        desk.cancel("ana", done.ask_id)
        gate.set()
        await settle()
        failing = desk.submit("ana", q("bad"))
        engine.errors["bad"] = UpstreamError("The model server refused the request.")
        await collect(desk, "ana", failing.ask_id)
    assert desk.get("ana", done.ask_id).state is AskState.CANCELLED
    failed = [r for r in caplog.records if getattr(r, "event", "") == "ask.failed"]
    assert len(failed) == 1  # the one that reached its member, not the one that was dropped
    await desk.stop()


@arun
async def test_cancelling_a_finished_ask_forgets_it() -> None:
    desk, _, _ = make()
    await desk.start()
    done = desk.submit("ana", q("one"))
    await collect(desk, "ana", done.ask_id)
    desk.cancel("ana", done.ask_id)
    with pytest.raises(NotFoundError):
        desk.get("ana", done.ask_id)
    await desk.stop()


@arun
async def test_an_ask_belongs_to_the_member_who_made_it() -> None:
    desk, _, _ = make()
    await desk.start()
    done = desk.submit("ana", q("one"))
    for call in (desk.get, desk.cancel):
        with pytest.raises(NotFoundError):
            call("beto", done.ask_id)
    with pytest.raises(NotFoundError):
        await collect(desk, "beto", done.ask_id)
    with pytest.raises(NotFoundError):
        desk.get("ana", "no-such-id")
    await desk.stop()


@arun
async def test_a_finished_ask_is_forgotten_after_the_retention_time() -> None:
    desk, _, clock = make(retention=timedelta(minutes=30))
    await desk.start()
    done = desk.submit("ana", q("one"))
    await collect(desk, "ana", done.ask_id)
    clock.advance(minutes=29, seconds=59)
    assert desk.get("ana", done.ask_id).state is AskState.DONE
    clock.advance(seconds=1)
    with pytest.raises(NotFoundError):
        desk.get("ana", done.ask_id)
    await desk.stop()


@arun
async def test_an_open_ask_is_not_forgotten_however_long_it_takes() -> None:
    desk, engine, clock = make(retention=timedelta(minutes=30))
    engine.hold("slow")
    await desk.start()
    done = desk.submit("ana", q("slow"))
    await settle()
    clock.advance(hours=5)
    assert desk.get("ana", done.ask_id).state is AskState.RUNNING
    await desk.stop()


@arun
async def test_a_reader_that_reconnects_gets_only_what_it_missed() -> None:
    desk, _, _ = make()
    await desk.start()
    done = desk.submit("ana", q("one"))
    await collect(desk, "ana", done.ask_id)
    assert await collect(desk, "ana", done.ask_id, after=1) == [
        ("answering", None),
        ("answer", None),
    ]
    assert await collect(desk, "ana", done.ask_id, after=3) == []
    assert await collect(desk, "ana", done.ask_id, after=99) == []
    assert desk.replay_is_empty("ana", done.ask_id, 3)
    assert not desk.replay_is_empty("ana", done.ask_id, 2)
    assert not desk.replay_is_empty("ana", done.ask_id, None)
    await desk.stop()


@arun
async def test_several_readers_follow_the_same_ask() -> None:
    desk, engine, _ = make()
    gate = engine.hold("one")
    await desk.start()
    done = desk.submit("ana", q("one"))
    readers = [asyncio.ensure_future(collect(desk, "ana", done.ask_id)) for _ in range(3)]
    await settle()
    gate.set()
    results = await asyncio.gather(*readers)
    assert results[0] == results[1] == results[2]
    assert results[0][-1] == ("answer", None)
    await desk.stop()


@arun
async def test_a_reader_that_goes_away_leaves_nothing_behind() -> None:
    desk, engine, _ = make()
    gate = engine.hold("one")
    await desk.start()
    done = desk.submit("ana", q("one"))
    reader = asyncio.ensure_future(collect(desk, "ana", done.ask_id))
    await settle()
    reader.cancel()
    await settle()
    entry = desk._entries[done.ask_id]
    assert entry.waiters == []
    gate.set()
    await settle()
    assert desk.get("ana", done.ask_id).state is AskState.DONE
    await desk.stop()


@arun
async def test_the_contexts_are_kept_or_dropped_as_asked() -> None:
    desk, _, _ = make()
    await desk.start()
    with_ctx = desk.submit("ana", q("one"))
    without = desk.submit("ana", q("two", return_contexts=False))
    await collect(desk, "ana", without.ask_id)
    first, second = desk.get("ana", with_ctx.ask_id), desk.get("ana", without.ask_id)
    assert first.answer is not None and len(first.answer.contexts) == 1
    assert second.answer is not None and second.answer.contexts == ()
    assert second.answer.sources == answer_for("two").sources  # the papers are still named
    await desk.stop()


@arun
async def test_the_options_reach_the_engine_and_no_retrieval_phase_without_the_index() -> None:
    desk, engine, _ = make()
    await desk.start()
    done = desk.submit("ana", q("one", options=AskOptions(use_index=False, bm25="never")))
    events = await collect(desk, "ana", done.ask_id)
    assert [kind for kind, _ in events] == ["queued", "answering", "answer"]
    assert engine.options[0].options == AskOptions(use_index=False, bm25="never")
    await desk.stop()


@pytest.mark.parametrize(
    ("error", "kind", "status", "retry"),
    [
        (UnavailableError("The model server is down.", retry_after_s=7), "unavailable", 503, 7),
        (UpstreamError("Refused."), "upstream-error", 502, None),
        (InvalidInputError("No passage."), "invalid-input", 422, None),
    ],
)
@arun
async def test_a_failure_a_member_can_act_on_is_told_as_it_is(
    error: Exception, kind: str, status: int, retry: int | None
) -> None:
    desk, engine, _ = make()
    engine.errors["bad"] = error
    await desk.start()
    done = desk.submit("ana", q("bad"))
    events = await collect(desk, "ana", done.ask_id)
    assert events[-1] == ("error", None)
    failure = desk.get("ana", done.ask_id).failure
    assert failure is not None
    assert (failure.kind, failure.status, failure.message, failure.retry_after_s) == (
        kind,
        status,
        str(error),
        retry,
    )
    assert desk.get("ana", done.ask_id).state is AskState.FAILED
    # the lane is free for the next one
    nxt = desk.submit("ana", q("fine"))
    assert (await collect(desk, "ana", nxt.ask_id))[-1] == ("answer", None)
    await desk.stop()


@arun
async def test_an_unexpected_error_says_only_an_id_and_logs_no_question(
    caplog: pytest.LogCaptureFixture,
) -> None:
    desk, engine, _ = make()
    secret = "my very private question about transmons"
    engine.errors[secret] = RuntimeError(f"boom with the prompt inside: {secret}")
    await desk.start()
    with caplog.at_level(logging.INFO, logger="research_api"):
        done = desk.submit("ana", q(secret))
        await collect(desk, "ana", done.ask_id)
    failure = desk.get("ana", done.ask_id).failure
    assert failure is not None and failure.status == 500 and failure.kind == "internal"
    assert "Error id" in failure.message and secret not in failure.message
    assert "boom" not in failure.message
    lines = [r for r in caplog.records if getattr(r, "event", "") == "ask.error"]
    assert len(lines) == 1
    record = lines[0]
    assert record.kind == "RuntimeError" and record.username == "ana"  # type: ignore[attr-defined]
    assert failure.message.split()[-1].rstrip(".") == record.error_id  # type: ignore[attr-defined]
    assert (
        "ask_helpers.py" in record.trace
    )  # where it was raised, without its message  # type: ignore[attr-defined]
    assert secret not in caplog.text and "boom" not in caplog.text
    await desk.stop()


@arun
async def test_an_error_that_is_not_one_a_member_can_act_on_is_internal() -> None:
    desk, engine, _ = make()
    engine.errors["x"] = NotFoundError("an internal lookup failed")
    await desk.start()
    done = desk.submit("ana", q("x"))
    await collect(desk, "ana", done.ask_id)
    failure = desk.get("ana", done.ask_id).failure
    assert failure is not None and failure.status == 500
    assert "internal lookup" not in failure.message
    await desk.stop()


@arun
async def test_stopping_closes_the_lane_and_the_warm_up() -> None:
    desk, _, _ = make()
    await desk.start()
    await desk.start()  # twice is fine
    await desk.stop()
    await desk.stop()
    assert desk.lane._task is None


@arun
async def test_a_warm_up_still_running_is_cancelled_by_stop() -> None:
    engine = ScriptedEngine("warming_up")

    async def never() -> None:
        await asyncio.sleep(3600)

    engine.warm_up = never  # type: ignore[method-assign]
    desk = AskDesk(engine=engine, clock=MovableClock())
    await desk.start()
    await settle()
    await asyncio.wait_for(desk.stop(), 2)
