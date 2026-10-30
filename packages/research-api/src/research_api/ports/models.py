"""Port: the models that benchmark runs can ask, and the call to one of them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ModelInfo:
    """A model of the settings list. ``available`` is what the endpoint answered a moment ago.
    The address is not here: it never leaves the adapter."""

    name: str
    label: str
    revision: str | None
    available: bool


@dataclass(frozen=True)
class ChatReply:
    text: str
    finish: str | None
    prompt_tokens: int | None
    completion_tokens: int | None


class ModelServer(Protocol):
    async def models(self) -> list[ModelInfo]:
        """Every model of the settings, each probed (a short timeout), in settings order."""
        ...

    async def get(self, name: str) -> ModelInfo | None: ...

    async def chat(
        self, name: str, system: str, user: str, *, max_tokens: int, temperature: float
    ) -> ChatReply:
        """One chat request. ``UnavailableError`` when the server cannot be reached,
        ``TimeoutError`` when it takes too long, ``UpstreamError`` (no body in the message) when
        it refuses."""
        ...


@dataclass(frozen=True)
class ModelEntry:
    """A model of the settings: the name the server serves it under, and where."""

    name: str
    endpoint: str
    label: str
    revision: str | None = None
