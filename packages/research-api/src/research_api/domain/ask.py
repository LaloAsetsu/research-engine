"""Asking a question: the values, the rule that reads a formatted answer, and the life of one ask.

Standard library only. An ask is a record with a state and an append-only list of events; the
application decides when each step happens, the record only keeps the rules (which step may follow
which) so that the HTTP layer can replay the events to a browser that reconnects.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal

KeywordMode = Literal["english", "always", "never"]
KEYWORD_MODES: tuple[KeywordMode, ...] = ("english", "always", "never")
Phase = Literal["retrieving", "answering"]

MAX_QUESTION_CHARS = 4000
SHORT_ID = 16  # characters of a document id that a citation prints

REFERENCES_MARKER = "\nReferences\n"
# One entry of the References list: ``3. (aaaaaaaaaaaaaaaa pages 3-4): Title...`` (the rule of the
# qa-bench converters, so an asked question and a ``harness`` run line carry the same citations).
REFERENCE_ENTRY = re.compile(r"^\d+\. \(([0-9a-f]{16}) pages (\d+)-(\d+)\)", re.MULTILINE)


@dataclass(frozen=True)
class AskOptions:
    """What one question may change about the configured setup (``None``: the server default)."""

    use_index: bool = True
    bm25: KeywordMode | None = None
    whole_blocks: bool | None = None


@dataclass(frozen=True)
class AskQuestion:
    text: str
    options: AskOptions = AskOptions()
    return_contexts: bool = True


@dataclass(frozen=True)
class Citation:
    key: str  # as printed: "<16 hex> pages 3-4"
    doc_id: str  # the full id when the answer's sources name it, else the 16 printed
    first_page: int
    last_page: int


@dataclass(frozen=True)
class AskContext:
    """A chunk the answer model read."""

    doc_id: str
    name: str
    start: int
    first_page: int
    last_page: int
    is_reference: bool
    text: str


@dataclass(frozen=True)
class AskTimings:
    retrieve_s: float
    answer_s: float
    wall_s: float


@dataclass(frozen=True)
class AskAnswer:
    """An answer split the way a ``qa-run/1`` item holds it."""

    answer: str  # without the References list
    references_text: str
    citations: tuple[Citation, ...]
    contexts: tuple[AskContext, ...]
    sources: tuple[str, ...]  # full doc ids, by first appearance
    warnings: tuple[str, ...]
    index_generation: str | None
    timings: AskTimings

    def without_contexts(self) -> AskAnswer:
        return AskAnswer(
            self.answer,
            self.references_text,
            self.citations,
            (),
            self.sources,
            self.warnings,
            self.index_generation,
            self.timings,
        )


@dataclass(frozen=True)
class ReadAnswer:
    answer: str
    references_text: str
    citations: tuple[Citation, ...]


def read_formatted_answer(
    text: str, *, question: str = "", full_ids: Iterable[str] = ()
) -> ReadAnswer:
    """Split the text docingest returns into the answer, its References list and the citations.

    The text may open with ``Question: <question>`` and a blank line (PaperQA2 writes it); that
    line is dropped when it is exactly this question's. The list starts at the last line that
    reads ``References`` and is taken as one only when an entry of it reads like a citation, so a
    heading the model wrote inside its answer stays in the answer. A citation's doc id is the full
    id from ``full_ids`` that starts with the 16 printed characters, else the 16 as printed.
    """
    body = text
    prefix = f"Question: {question}\n\n"
    if question and body.startswith(prefix):
        body = body[len(prefix) :]
    answer, references = body, ""
    at = body.rfind(REFERENCES_MARKER)
    if at != -1 and REFERENCE_ENTRY.search(body, at):
        answer, references = body[:at], body[at + len(REFERENCES_MARKER) :]
    by_prefix = {doc_id[:SHORT_ID]: doc_id for doc_id in full_ids}
    citations = tuple(
        Citation(
            key=f"{m[1]} pages {m[2]}-{m[3]}",
            doc_id=by_prefix.get(m[1], m[1]),
            first_page=int(m[2]),
            last_page=int(m[3]),
        )
        for m in REFERENCE_ENTRY.finditer(references)
    )
    return ReadAnswer(answer.strip(), references.strip(), citations)


class AskState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


FINAL_STATES = frozenset({AskState.DONE, AskState.FAILED, AskState.CANCELLED})


class EventKind(StrEnum):
    QUEUED = "queued"
    RETRIEVING = "retrieving"
    ANSWERING = "answering"
    ANSWER = "answer"
    ERROR = "error"
    CANCELLED = "cancelled"


FINAL_EVENTS = frozenset({EventKind.ANSWER, EventKind.ERROR, EventKind.CANCELLED})


@dataclass(frozen=True)
class AskFailure:
    """Why an ask produced no answer, in words a member can read. ``status`` is the HTTP status
    the same failure would have had as a response, so a client can tell "try again" (503) from a
    refusal (422) without a second vocabulary."""

    kind: str  # the slug of the error: "unavailable", "upstream-error", "internal", ...
    status: int
    message: str
    retry_after_s: int | None = None


@dataclass(frozen=True)
class AskEvent:
    id: int  # 0, 1, 2, ... in order: the SSE ``id``, so a reconnect resumes after it
    kind: EventKind
    position: int | None = None  # QUEUED: jobs ahead of this one (0: next)
    answer: AskAnswer | None = None  # ANSWER
    failure: AskFailure | None = None  # ERROR


@dataclass
class AskRecord:
    """One ask from the moment it is accepted until it is forgotten."""

    ask_id: str
    owner: str
    created_at: datetime
    state: AskState = AskState.QUEUED
    events: list[AskEvent] = field(default_factory=list)
    finished_at: datetime | None = None
    dropped: bool = False  # cancelled while running: the answer is thrown away when it arrives

    @property
    def final(self) -> bool:
        return self.state in FINAL_STATES

    @property
    def position(self) -> int | None:
        for event in reversed(self.events):
            if event.kind is EventKind.QUEUED:
                return event.position if self.state is AskState.QUEUED else None
        return None

    @property
    def phase(self) -> Phase | None:
        for event in reversed(self.events):
            if event.kind is EventKind.RETRIEVING:
                return "retrieving"
            if event.kind is EventKind.ANSWERING:
                return "answering"
        return None

    @property
    def answer(self) -> AskAnswer | None:
        last = self.events[-1] if self.events else None
        return last.answer if last and last.kind is EventKind.ANSWER else None

    @property
    def failure(self) -> AskFailure | None:
        last = self.events[-1] if self.events else None
        return last.failure if last and last.kind is EventKind.ERROR else None

    def _add(
        self,
        kind: EventKind,
        *,
        position: int | None = None,
        answer: AskAnswer | None = None,
        failure: AskFailure | None = None,
    ) -> AskEvent:
        event = AskEvent(len(self.events), kind, position, answer, failure)
        self.events.append(event)
        return event

    def waiting(self, position: int) -> AskEvent | None:
        """Say how many jobs are ahead; nothing is added when it has not changed."""
        if self.state is not AskState.QUEUED:
            return None
        last = next((e for e in reversed(self.events) if e.kind is EventKind.QUEUED), None)
        if last is not None and last.position == position:
            return None
        return self._add(EventKind.QUEUED, position=position)

    def start(self) -> None:
        if self.state is AskState.QUEUED:
            self.state = AskState.RUNNING

    def entered(self, phase: Phase) -> AskEvent | None:
        """The engine moved to ``phase``. Ignored once the ask is over or was cancelled."""
        if self.state is not AskState.RUNNING or self.dropped:
            return None
        kind = EventKind.RETRIEVING if phase == "retrieving" else EventKind.ANSWERING
        if self.events and self.events[-1].kind is kind:
            return None
        return self._add(kind)

    def finish(self, answer: AskAnswer, now: datetime) -> AskEvent | None:
        if self.state is not AskState.RUNNING or self.dropped:
            return None
        self.state, self.finished_at = AskState.DONE, now
        return self._add(EventKind.ANSWER, answer=answer)

    def fail(self, failure: AskFailure, now: datetime) -> AskEvent | None:
        if self.final or self.dropped:
            return None
        self.state, self.finished_at = AskState.FAILED, now
        return self._add(EventKind.ERROR, failure=failure)

    def cancel(self, now: datetime) -> AskEvent | None:
        """Cancel a queued ask now, or mark a running one so its answer is dropped on arrival."""
        if self.state is AskState.QUEUED:
            self.state, self.finished_at = AskState.CANCELLED, now
            return self._add(EventKind.CANCELLED)
        if self.state is AskState.RUNNING and not self.dropped:
            self.dropped = True
            self.state, self.finished_at = AskState.CANCELLED, now
            return self._add(EventKind.CANCELLED)
        return None
