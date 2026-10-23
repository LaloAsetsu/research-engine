"""The lane: a queue that runs one job at a time.

The GPU lane of the scope: asks join it with the highest priority and every question of a
benchmark run joins it with a lower one, so an ask goes before the next benchmark question. The
CPU lane (scorings) is a second lane of the same kind. Jobs of the same priority run in the order
they came. A lane lives in the event loop of the API process and needs no thread of its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

log = logging.getLogger("research_api.lane")

ASK_PRIORITY = 0
RUN_PRIORITY = 10


@dataclass(eq=False)
class Ticket:
    """A place in the lane. ``on_position`` hears how many jobs are ahead (the running one
    counts) every time that number changes."""

    priority: int
    seq: int
    work: Callable[[], Awaitable[None]] | None
    on_position: Callable[[int], None] | None
    position: int = field(default=-1)


class Lane:
    def __init__(self, name: str = "gpu-lane") -> None:
        self._name = name
        self._finished: dict[int, int] = {}
        self._waiting: list[Ticket] = []
        self._running: Ticket | None = None
        self._seq = 0
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None

    @property
    def queued(self) -> int:
        return len(self._waiting)

    @property
    def busy(self) -> bool:
        return self._running is not None

    def finished(self, priority: int) -> int:
        """How many jobs of ``priority`` have ended since the lane was made (cancelled ones
        that never ran are not counted)."""
        return self._finished.get(priority, 0)

    def start(self) -> None:
        """Run the worker in the current event loop (again, if the loop it ran in is gone)."""
        loop = asyncio.get_running_loop()
        if self._task is not None and not self._task.done() and self._loop is loop:
            return
        self._loop, self._wake = loop, asyncio.Event()
        self._running = None
        self._task = loop.create_task(self._worker(self._wake), name=self._name)
        if self._waiting:
            self._wake.set()

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    def submit(
        self,
        work: Callable[[], Awaitable[None]],
        on_position: Callable[[int], None] | None = None,
        *,
        priority: int = ASK_PRIORITY,
    ) -> Ticket:
        """Queue ``work``, which must handle its own errors. Starts the worker if needed."""
        self.start()
        self._seq += 1
        ticket = Ticket(priority, self._seq, work, on_position)
        self._waiting.append(ticket)
        self._waiting.sort(key=lambda t: (t.priority, t.seq))
        self._tell()
        assert self._wake is not None
        self._wake.set()
        return ticket

    def cancel(self, ticket: Ticket) -> bool:
        """Take a job out of the queue. False when it is running (it cannot be stopped) or gone."""
        if ticket not in self._waiting:
            return False
        self._waiting.remove(ticket)
        ticket.work = ticket.on_position = None
        self._tell()
        return True

    def _tell(self) -> None:
        ahead = 1 if self._running is not None else 0
        for index, ticket in enumerate(self._waiting):
            position = ahead + index
            if position != ticket.position:
                ticket.position = position
                if ticket.on_position is not None:
                    ticket.on_position(position)

    async def _worker(self, wake: asyncio.Event) -> None:
        while True:
            while not self._waiting:
                wake.clear()
                await wake.wait()
            ticket = self._waiting.pop(0)
            self._running = ticket
            self._tell()
            try:
                assert ticket.work is not None
                await ticket.work()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Jobs report their own failures; this only keeps the lane alive. The message is
                # left out on purpose: it could hold a question.
                log.error("a job of the lane raised", extra={"event": "lane.error"})
            finally:
                self._finished[ticket.priority] = self._finished.get(ticket.priority, 0) + 1
                ticket.work = ticket.on_position = None
                self._running = None
                self._tell()


GpuLane = Lane
"""The lane the model servers sit behind, under the name the ask path uses."""
