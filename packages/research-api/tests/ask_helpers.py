"""Stand-ins for the answer engine that a test controls by hand."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from docingest.application.ask import AskSession
from docingest.domain.models import AnswerModel
from fastapi.testclient import TestClient
from helpers import LOOPBACK, SEED_FILE

from research_api.adapters.docingest_ask import DocingestAskEngine
from research_api.bootstrap import Container, Settings, build_container
from research_api.bootstrap.seed import load_seed_users
from research_api.domain.ask import (
    AskAnswer,
    AskContext,
    AskQuestion,
    AskTimings,
    GoldPassage,
    Phase,
)
from research_api.domain.runs import ModelFacts
from research_api.entrypoints.http import build_app
from research_api.entrypoints.http.security import CSRF_COOKIE, CSRF_HEADER
from research_api.ports.ask import AskEngine, EngineHealth, EngineState

DOC = "c" * 64
LITELLM_MODEL = AnswerModel(name="openai/qwen-local", revision=None)


def engine_over(
    open_session: Callable[[], AskSession],
    model: AnswerModel = LITELLM_MODEL,
    **kwargs: Any,
) -> DocingestAskEngine:
    """The docingest engine over a stand-in session, naming ``model`` as its answer model."""
    kwargs.setdefault("answer_max_sources", 3)
    return DocingestAskEngine(open_session, read_answer_model=lambda: model, **kwargs)


def answer_for(text: str) -> AskAnswer:
    context = AskContext(DOC, f"{DOC[:16]} pages 1-1", 0, 1, 1, False, "chunk text")
    return AskAnswer(
        answer=f"Answer to: {text}",
        references_text=f"1. ({DOC[:16]} pages 1-1): Paper.",
        citations=(),
        contexts=(context,),
        sources=(DOC,),
        warnings=(),
        index_generation="g1",
        timings=AskTimings(0.1, 0.2, 0.3),
    )


class ScriptedEngine:
    """Answers at once, unless the test holds a question (``hold``) or tells it to fail."""

    def __init__(self, state: EngineState = "ready") -> None:
        self.state: EngineState = state
        self.model = ModelFacts("fake-model")
        self.model_error: Exception | None = None
        self.reason: str | None = None
        self.asked: list[str] = []
        self.options: list[AskQuestion] = []
        self.given: list[tuple[str, tuple[GoldPassage, ...]]] = []
        self.truncated = False
        self.warnings: tuple[str, ...] = ()
        self.gates: dict[str, threading.Event] = {}
        self.errors: dict[str, Exception] = {}
        self.warmed = 0
        self.closed = 0

    def hold(self, text: str) -> threading.Event:
        """Keep ``text`` in its answering phase until the returned event is set (from any
        thread: a test client runs the app in another one)."""
        return self.gates.setdefault(text, threading.Event())

    def health(self) -> EngineHealth:
        return EngineHealth(self.state, self.reason, "g1")

    def answer_model(self) -> ModelFacts:
        if self.model_error is not None:
            raise self.model_error
        return self.model

    async def warm_up(self) -> None:
        self.warmed += 1
        if self.state == "warming_up":
            self.state = "ready"

    async def ask(self, question: AskQuestion, on_phase: Callable[[Phase], None]) -> AskAnswer:
        self.asked.append(question.text)
        self.options.append(question)
        if question.options.use_index:
            on_phase("retrieving")
        on_phase("answering")
        gate = self.gates.get(question.text)
        while gate is not None and not gate.is_set():
            await asyncio.sleep(0.005)
        if question.text in self.errors:
            raise self.errors[question.text]
        return answer_for(question.text)

    async def answer_from(self, question: str, passages: Sequence[GoldPassage]) -> AskAnswer:
        self.given.append((question, tuple(passages)))
        if question in self.errors:
            raise self.errors[question]
        contexts = tuple(
            AskContext(p.doc_id, f"{p.doc_id[:16]} pages 1-1", 0, 1, 1, False, p.text)
            for p in passages
        )
        return AskAnswer(
            answer=f"Answer from the passages to: {question}",
            references_text="",
            citations=(),
            contexts=contexts,
            sources=tuple(dict.fromkeys(p.doc_id for p in passages)),
            warnings=self.warnings,
            index_generation=None,
            timings=AskTimings(0.01, 0.2, 0.21),
            truncated=self.truncated,
        )

    def close(self) -> None:
        self.closed += 1


async def settle(seconds: float = 0.05) -> None:
    """Let the loop run: tasks start, polling engines notice their gate."""
    for _ in range(10):
        await asyncio.sleep(0)
    await asyncio.sleep(seconds)


ANA = {"username": "ana", "password": "test-password-ana-01"}
BETO = {"username": "beto", "password": "test-password-beto-02"}
TEMP = {"username": "temp", "password": "temporary-test-0001"}


@contextmanager
def running_app(
    engine: AskEngine | None = None, **kwargs: Any
) -> Iterator[tuple[TestClient, Container]]:
    """A fake-mode app with its lifespan running (the GPU lane lives in it), signed in as ana."""
    container = build_container(
        Settings.defaults(),
        fake=True,
        seed=load_seed_users(SEED_FILE),
        cheap_hashing=True,
        ask_engine=engine,
        **kwargs,
    )
    app = build_app(container)
    with TestClient(
        app, base_url=LOOPBACK, headers={"Origin": LOOPBACK}, raise_server_exceptions=False
    ) as client:
        sign_in(client, ANA)
        yield client, container


def sign_in(client: TestClient, who: dict[str, str]) -> None:
    client.cookies.clear()
    done = client.post("/api/v1/auth/login", json=who)
    assert done.status_code == 204, done.text


def post(client: TestClient, path: str, **kwargs: Any) -> Any:
    headers = {CSRF_HEADER: client.cookies.get(CSRF_COOKIE, ""), **kwargs.pop("headers", {})}
    return client.post(path, headers=headers, **kwargs)


def delete(client: TestClient, path: str) -> Any:
    return client.delete(path, headers={CSRF_HEADER: client.cookies.get(CSRF_COOKIE, "")})


def ask(client: TestClient, question: str, **options: Any) -> str:
    done = post(client, "/api/v1/asks", json={"question": question, **options})
    assert done.status_code == 202, done.text
    return str(done.json()["ask_id"])


def parse_sse(text: str) -> list[dict[str, Any]]:
    """The events of an event stream as ``{event, id, data}`` (comments are skipped)."""
    events = []
    for block in text.split("\n\n"):
        fields: dict[str, Any] = {}
        for line in block.splitlines():
            if line.startswith(":") or ":" not in line:
                continue
            name, _, value = line.partition(":")
            fields[name] = value.removeprefix(" ")
        if "event" in fields:
            fields["data"] = json.loads(fields["data"])
            events.append(fields)
    return events


def follow(client: TestClient, ask_id: str, **headers: str) -> list[dict[str, Any]]:
    done = client.get(f"/api/v1/asks/{ask_id}/events", headers=headers)
    assert done.status_code == 200, done.text
    return parse_sse(done.text)
