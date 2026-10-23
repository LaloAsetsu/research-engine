"""Use cases: ask a question and follow the answer.

``AskDesk`` keeps the asks of the last half hour in memory (never on disk: an answer holds corpus
text), puts each in the GPU lane so the model servers see one question at a time, and lets any
number of readers follow an ask's events, one that reconnects included.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import secrets
import traceback
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta

from ..domain.ask import (
    FINAL_EVENTS,
    AskEvent,
    AskFailure,
    AskQuestion,
    AskRecord,
    Phase,
)
from ..domain.errors import (
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
    ResearchApiError,
    UnavailableError,
    UpstreamError,
)
from ..ports.ask import AskEngine, EngineHealth
from ..ports.clock import Clock
from .lanes import ASK_PRIORITY, GpuLane, Ticket

log = logging.getLogger("research_api.ask")

RETENTION = timedelta(minutes=30)
PER_USER_OPEN = 3  # asks one member may have waiting or running at once
CAPACITY = 60  # asks waiting or running, all members together
STARTING_RETRY_S = 5
BUSY_RETRY_S = 15

_STATUS = {InvalidInputError: 422, UpstreamError: 502, UnavailableError: 503}
NO_ANSWER = "The answer could not be produced."


@dataclass(frozen=True)
class Accepted:
    ask_id: str
    position: int


@dataclass(frozen=True)
class DeskStatus:
    health: EngineHealth
    queued: int
    busy: bool


@dataclass(eq=False)
class _Entry:
    record: AskRecord
    ticket: Ticket | None = None
    waiters: list[asyncio.Future[None]] = field(default_factory=list)


def failure_of(error: ResearchApiError) -> AskFailure | None:
    """The failure a member is told about, or None when ``error`` is not one they can act on."""
    for cls, status in _STATUS.items():
        if isinstance(error, cls):
            return AskFailure(
                error.slug,
                status,
                error.detail or NO_ANSWER,
                getattr(error, "retry_after_s", None),
            )
    return None


def trace_of(error: BaseException) -> str:
    """Where an exception was raised, without its message (which may hold a question)."""
    frames = traceback.extract_tb(error.__traceback__)
    return " > ".join(f"{f.filename.rsplit('/', 1)[-1]}:{f.lineno} {f.name}" for f in frames[-6:])


class AskDesk:
    def __init__(
        self,
        *,
        engine: AskEngine,
        clock: Clock,
        lane: GpuLane | None = None,
        retention: timedelta = RETENTION,
        per_user: int = PER_USER_OPEN,
        capacity: int = CAPACITY,
    ) -> None:
        self.engine = engine
        self._clock = clock
        self.lane = lane or GpuLane()
        self._retention = retention
        self._per_user = per_user
        self._capacity = capacity
        self._entries: dict[str, _Entry] = {}
        self._warming: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the lane and warm the engine up without holding back the server."""
        self.lane.start()
        if self._warming is None or self._warming.done():
            self._warming = asyncio.get_running_loop().create_task(
                self.engine.warm_up(), name="ask-warm-up"
            )

    async def stop(self) -> None:
        if self._warming is not None:
            self._warming.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._warming
        await self.lane.stop()

    def status(self) -> DeskStatus:
        return DeskStatus(self.engine.health(), self.lane.queued, self.lane.busy)

    # --- asking -------------------------------------------------------------------------

    def submit(self, owner: str, question: AskQuestion) -> Accepted:
        self._forget_old()
        if self.engine.health().state == "warming_up":
            raise UnavailableError(
                "The answer engine is starting; try again in a few seconds.",
                retry_after_s=STARTING_RETRY_S,
            )
        open_asks = [e.record for e in self._entries.values() if not e.record.final]
        if sum(r.owner == owner for r in open_asks) >= self._per_user:
            raise RateLimitedError(
                f"You already have {self._per_user} questions waiting. "
                "Wait for one to finish or cancel one.",
                retry_after_s=BUSY_RETRY_S,
            )
        if len(open_asks) >= self._capacity:
            raise UnavailableError(
                "Too many questions are waiting for the model servers; try again soon.",
                retry_after_s=BUSY_RETRY_S,
            )
        record = AskRecord(secrets.token_urlsafe(16), owner, self._clock.now())
        entry = _Entry(record)
        self._entries[record.ask_id] = entry
        entry.ticket = self.lane.submit(
            lambda: self._run(entry, question),
            lambda position: self._publish(entry, record.waiting(position)),
            priority=ASK_PRIORITY,
        )
        return Accepted(record.ask_id, record.position or 0)

    def get(self, owner: str, ask_id: str) -> AskRecord:
        """The ask, if it is this member's and has not been forgotten (``NotFoundError``)."""
        self._forget_old()
        entry = self._entries.get(ask_id)
        if entry is None or entry.record.owner != owner:
            raise NotFoundError("No such question. Answers are kept for half an hour.")
        return entry.record

    def cancel(self, owner: str, ask_id: str) -> None:
        """Cancel a waiting or running ask; for one that is over, forget it (and its answer)."""
        record = self.get(owner, ask_id)
        entry = self._entries[ask_id]
        if record.final:
            del self._entries[ask_id]
            return
        if entry.ticket is not None:
            self.lane.cancel(entry.ticket)
        self._publish(entry, record.cancel(self._clock.now()))

    def replay_is_empty(self, owner: str, ask_id: str, after: int | None) -> bool:
        """True when the ask is over and the reader has already seen its last event."""
        record = self.get(owner, ask_id)
        return record.final and (after if after is not None else -1) >= len(record.events) - 1

    async def events(
        self, owner: str, ask_id: str, after: int | None = None
    ) -> AsyncIterator[AskEvent]:
        """The events after ``after`` (all, when None), then new ones as they happen, up to the
        last one of the ask."""
        self.get(owner, ask_id)
        entry = self._entries[ask_id]
        index = 0 if after is None else max(after + 1, 0)
        loop = asyncio.get_running_loop()
        while True:
            while index < len(entry.record.events):
                event = entry.record.events[index]
                index += 1
                yield event
                if event.kind in FINAL_EVENTS:
                    return
            if entry.record.final:  # nothing more will come
                return
            waiter: asyncio.Future[None] = loop.create_future()
            entry.waiters.append(waiter)
            try:
                await waiter
            finally:
                with contextlib.suppress(ValueError):
                    entry.waiters.remove(waiter)

    # --- inside ---------------------------------------------------------------------------

    def _publish(self, entry: _Entry, event: AskEvent | None) -> None:
        if event is None:
            return
        for waiter in entry.waiters:
            if not waiter.done():
                waiter.set_result(None)

    def _forget_old(self) -> None:
        now = self._clock.now()
        for ask_id, entry in list(self._entries.items()):
            done = entry.record.finished_at
            if done is not None and now - done >= self._retention:
                del self._entries[ask_id]

    async def _run(self, entry: _Entry, question: AskQuestion) -> None:
        record = entry.record
        record.start()
        failure: AskFailure | None = None

        def on_phase(phase: Phase) -> None:
            self._publish(entry, record.entered(phase))

        try:
            answer = await self.engine.ask(question, on_phase)
        except asyncio.CancelledError:
            raise
        except ResearchApiError as error:
            failure = failure_of(error) or self._unexpected(record, error)
        except Exception as error:
            failure = self._unexpected(record, error)
        else:
            if not question.return_contexts:
                answer = answer.without_contexts()
            self._publish(entry, record.finish(answer, self._clock.now()))
        finally:
            entry.ticket = None
        if failure is not None:
            event = record.fail(failure, self._clock.now())
            self._publish(entry, event)
            if event is not None:  # an ask cancelled while it ran had its failure dropped
                log.info(
                    "ask failed",
                    extra={
                        "event": "ask.failed",
                        "ask_id": hashlib.sha256(record.ask_id.encode()).hexdigest()[:12],
                        "username": record.owner,
                        "kind": failure.kind,
                        "status": failure.status,
                    },
                )

    @staticmethod
    def _unexpected(record: AskRecord, error: BaseException) -> AskFailure:
        error_id = uuid.uuid4().hex[:12]
        log.error(
            "ask raised",
            extra={
                "event": "ask.error",
                "error_id": error_id,
                "username": record.owner,
                "kind": type(error).__name__,
                "trace": trace_of(error),
            },
        )
        return AskFailure("internal", 500, f"{NO_ANSWER} Error id {error_id}.")
