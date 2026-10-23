"""A run of one arm: the answers it writes, the prompt of the closed-book arm, and the header.

The arms (``qa-run/1``): ``bare`` asks the model with no papers, ``harness`` asks through
docingest with its retrieval, ``oracle`` hands the model the gold passages. This module only
holds the values and the rules that turn them into the lines of a run file; it knows no model
server and no file. Standard library only.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .ask import AskAnswer, AskContext, AskTimings, Citation
from .jobs import ArmSpec, header_notes

FORMAT = "qa-run/1"
MODEL_MAX_TOKENS = 1024
MODEL_TEMPERATURE = 0.0
ANSWER_LENGTH = "about 200 words, but can be longer"
CANNOT_ANSWER_PHRASE = "I cannot answer"

# The closed-book prompt of the C0 arm of R-281 (PaperQA2's answer step without context or
# citations), word for word, so a run made here is comparable with that one.
BARE_SYSTEM = (
    "Answer in a direct and concise tone. "
    "Your audience is an expert, so be highly specific. "
    "If there are ambiguous terms or acronyms, first define them."
)
BARE_USER = (
    "Answer the question below from your general knowledge. No context or source documents are "
    "provided.\n\n"
    "Question: {question}\n\n"
    "Write an answer based on your own knowledge. "
    "If your knowledge provides insufficient information, or you are unsure, reply "
    f'"{CANNOT_ANSWER_PHRASE}." '
    "Write in the style of a scientific article, with concise sentences and "
    "coherent paragraphs. This answer will be used directly, "
    "so do not add any extraneous information.\n\n"
    "Answer ({answer_length}):"
)
BARE_PROMPT_MD5 = hashlib.md5(
    (BARE_SYSTEM + "\n" + BARE_USER).encode(), usedforsecurity=False
).hexdigest()


def bare_messages(question: str) -> tuple[str, str]:
    """The system and user messages of the closed-book arm for one question."""
    return BARE_SYSTEM, BARE_USER.format(question=question.strip(), answer_length=ANSWER_LENGTH)


@dataclass(frozen=True)
class AnswerError:
    """Why one question has no answer; the run goes on with the next. ``kind`` is a ``qa-run/1``
    error kind. The message is fixed text: a model's own message can hold the prompt."""

    kind: str
    message: str


@dataclass(frozen=True)
class Usage:
    llm_requests: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True)
class RunAnswer:
    """What an arm made of one question: the fields of a ``qa-run/1`` item."""

    answer: str | None
    citations: tuple[Citation, ...] = ()
    contexts: tuple[AskContext, ...] = ()
    timings: AskTimings | None = None
    usage: Usage | None = None
    error: AnswerError | None = None
    prompt_md5: str | None = None

    @classmethod
    def from_ask(cls, answer: AskAnswer) -> RunAnswer:
        return cls(
            answer=answer.answer,
            citations=answer.citations,
            contexts=answer.contexts,
            timings=answer.timings,
        )

    @classmethod
    def failed(cls, error: AnswerError, wall_s: float | None = None) -> RunAnswer:
        timings = AskTimings(0.0, 0.0, wall_s) if wall_s is not None else None
        return cls(answer=None, timings=timings, error=error)


def _context(c: AskContext) -> dict[str, Any]:
    return {
        "doc_id": c.doc_id,
        "name": c.name,
        "start": c.start,
        "first_page": c.first_page,
        "last_page": c.last_page,
        "is_reference": c.is_reference,
        "text": c.text,
    }


def item_record(item_id: str, repeat: int, answer: RunAnswer) -> dict[str, Any]:
    """One line of a run file. Nothing is added that the format does not name."""
    record: dict[str, Any] = {
        "id": item_id,
        "repeat": repeat,
        "answer": answer.answer,
        "citations": [
            {
                "key": c.key,
                "doc_id": c.doc_id,
                "first_page": c.first_page,
                "last_page": c.last_page,
            }
            for c in answer.citations
        ],
        "contexts": [_context(c) for c in answer.contexts],
    }
    if answer.timings is not None:
        timings = answer.timings
        record["timings"] = {
            "wall_s": timings.wall_s,
            "retrieve_s": timings.retrieve_s,
            "answer_s": timings.answer_s,
        }
    if answer.usage is not None:
        usage = {
            k: v
            for k, v in (
                ("llm_requests", answer.usage.llm_requests),
                ("prompt_tokens", answer.usage.prompt_tokens),
                ("completion_tokens", answer.usage.completion_tokens),
            )
            if v is not None
        }
        if usage:
            record["usage"] = usage
    if answer.prompt_md5 is not None:
        record["answer_prompt_md5"] = answer.prompt_md5
    if answer.error is not None:
        record["error"] = {"kind": answer.error.kind, "message": answer.error.message}
    return record


@dataclass(frozen=True)
class ModelFacts:
    """The model as the run header records it: names only, never an address with a key."""

    name: str
    label: str | None = None
    revision: str | None = None


@dataclass(frozen=True)
class RunHeaderInput:
    run_id: str
    created: datetime
    started: datetime
    set_name: str  # as the set header writes it: "name/version"
    set_md5: str
    corpus: str | None
    arm: ArmSpec
    model: ModelFacts
    repeats: int
    notes: str
    runner: str
    prompt_md5: str | None = None
    interleaved_asks: int = 0
    partial: str | None = None
    ended: datetime | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


def header_record(h: RunHeaderInput) -> dict[str, Any]:
    arm: dict[str, Any] = {"name": h.arm.name}
    if h.arm.variant:
        arm["variant"] = h.arm.variant
    if h.prompt_md5:
        arm["prompt_md5"] = h.prompt_md5
    if h.arm.overrides:
        arm["overrides"] = dict(h.arm.overrides)
    model: dict[str, Any] = {"id": h.model.name}
    if h.model.revision:
        model["revision"] = h.model.revision
    if h.model.label:
        model["endpoint"] = h.model.label
    if h.arm.name == "bare":
        model["temperature"] = MODEL_TEMPERATURE
        model["max_tokens"] = MODEL_MAX_TOKENS
    header: dict[str, Any] = {
        "format": FORMAT,
        "run_id": h.run_id,
        "created": h.created.isoformat(),
        "started": h.started.isoformat(),
    }
    if h.ended is not None:
        header["ended"] = h.ended.isoformat()
    header.update(
        {
            "set": {"name": h.set_name, "md5": h.set_md5},
            "arm": arm,
            "model": model,
            "code": {"runner": h.runner},
            # a closed-book run searches no corpus; the scorer accepts "none" for that
            "corpus": {"id": "none" if h.arm.name == "bare" else (h.corpus or "unknown")},
            "repeats": h.repeats,
            "concurrency": 1,
            "notes": header_notes(h.notes, h.interleaved_asks, h.partial),
        }
    )
    return header


def partial_text(done: int, total: int, why: str, at: datetime) -> str:
    return f"{why} at {at.isoformat(timespec='seconds')} with {done} of {total} answers"


def question_of(item: Mapping[str, Any]) -> str:
    return str(item["question"])
