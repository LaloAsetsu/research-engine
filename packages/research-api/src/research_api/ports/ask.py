"""Port: what answers a question."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from ..domain.ask import AskAnswer, AskQuestion, Phase

EngineState = Literal["warming_up", "ready", "unavailable"]


@dataclass(frozen=True)
class EngineHealth:
    """Whether the engine can take a question. ``reason`` is fixed text, safe to show to anyone
    who can read ``/status``: it never holds a path, a setting value or a question."""

    state: EngineState
    reason: str | None = None
    index_generation: str | None = None


class AskEngine(Protocol):
    def health(self) -> EngineHealth: ...

    async def warm_up(self) -> None:
        """Get ready (open the index, make the vector copy). Never raises: a failure is the
        ``unavailable`` state and its reason."""
        ...

    async def ask(self, question: AskQuestion, on_phase: Callable[[Phase], None]) -> AskAnswer:
        """Answer one question. ``on_phase`` is called in the caller's event loop. Raises a
        ``ResearchApiError`` for a failure a member can act on (``UnavailableError``,
        ``UpstreamError``, ``InvalidInputError``); any other exception is unexpected."""
        ...

    def close(self) -> None: ...
