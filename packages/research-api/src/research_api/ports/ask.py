"""Port: what answers a question."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from ..domain.ask import AskAnswer, AskQuestion, GoldPassage, Phase
from ..domain.runs import ModelFacts

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

    async def answer_from(self, question: str, passages: Sequence[GoldPassage]) -> AskAnswer:
        """Answer one question from exactly these passages, in this order, with no retrieval.
        The answer's contexts are the passages as given. ``AskAnswer.truncated`` says the engine
        cut them before the model read them. No passage is a question with no evidence: the
        engine's own "cannot answer" comes back, with no contexts. Raises like ``ask``;
        ``InvalidInputError`` for a paper that is not in the corpus."""
        ...

    def answer_model(self) -> ModelFacts:
        """The model that writes this engine's answers, named as a ``[[models]]`` entry names a
        model (the name its server serves it as) and with its revision when the engine pins one.
        Settings only: it asks no server. ``UnavailableError`` when the engine cannot say."""
        ...

    def close(self) -> None: ...
