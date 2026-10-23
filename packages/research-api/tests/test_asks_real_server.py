"""Asking over a real socket with a real uvicorn: the stream must arrive as it happens."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress

import httpx
import pytest
import uvicorn
from ask_helpers import ANA, BETO, ScriptedEngine
from helpers import SEED_FILE

from research_api.bootstrap import Container, Settings, build_container
from research_api.bootstrap.seed import load_seed_users
from research_api.entrypoints.http import build_app
from research_api.entrypoints.http.security import CSRF_COOKIE, CSRF_HEADER


class Served:
    def __init__(self, port: int, container: Container, engine: ScriptedEngine) -> None:
        self.port, self.container, self.engine = port, container, engine
        self.origin = f"http://127.0.0.1:{port}"

    @contextmanager
    def client(self, who: dict[str, str] = ANA) -> Iterator[httpx.Client]:
        with httpx.Client(
            base_url=self.origin, headers={"Origin": self.origin}, timeout=10
        ) as client:
            done = client.post("/api/v1/auth/login", json=who)
            assert done.status_code == 204
            yield client


@contextmanager
def serving() -> Iterator[Served]:
    engine = ScriptedEngine()
    container = build_container(
        Settings.defaults(),
        fake=True,
        seed=load_seed_users(SEED_FILE),
        cheap_hashing=True,
        ask_engine=engine,
    )
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(build_app(container), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started
    try:
        yield Served(port, container, engine)
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def ask(client: httpx.Client, question: str) -> str:
    done = client.post(
        "/api/v1/asks",
        json={"question": question},
        headers={CSRF_HEADER: client.cookies[CSRF_COOKIE]},
    )
    assert done.status_code == 202, done.text
    return str(done.json()["ask_id"])


def read_events(lines: Iterator[str], count: int) -> list[str]:
    """The next ``count`` event names of a stream."""
    names: list[str] = []
    for line in lines:
        if line.startswith("event: "):
            names.append(line.removeprefix("event: "))
            if len(names) == count:
                break
    return names


def test_the_phases_arrive_while_the_answer_is_still_being_made() -> None:
    with serving() as served, served.client() as client:
        gate = served.engine.hold("slow")
        ask_id = ask(client, "slow")
        with client.stream("GET", f"/api/v1/asks/{ask_id}/events") as stream:
            assert stream.headers["content-type"].startswith("text/event-stream")
            assert stream.headers["cache-control"] == "no-store"
            started = time.perf_counter()
            lines = stream.iter_lines()
            assert read_events(lines, 3) == ["queued", "retrieving", "answering"]
            assert time.perf_counter() - started < 3  # before the answer: nothing was held back
            assert not gate.is_set()
            gate.set()
            assert read_events(lines, 1) == ["answer"]


def test_a_quiet_stream_gets_a_comment_now_and_then(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fastapi.routing._PING_INTERVAL", 0.2)
    with serving() as served, served.client() as client:
        gate = served.engine.hold("slow")
        ask_id = ask(client, "slow")
        with client.stream("GET", f"/api/v1/asks/{ask_id}/events") as stream:
            seen = []
            for line in stream.iter_lines():
                seen.append(line)
                if line.startswith(":") and any(
                    entry.startswith("event: answering") for entry in seen
                ):
                    break
            gate.set()
        assert any(line.startswith(":") for line in seen)


def test_a_reader_that_leaves_does_not_stop_the_question_or_the_service() -> None:
    with serving() as served, served.client() as client:
        gate = served.engine.hold("slow")
        ask_id = ask(client, "slow")
        with client.stream("GET", f"/api/v1/asks/{ask_id}/events") as stream:
            assert read_events(stream.iter_lines(), 1) == ["queued"]
        # the reader hung up; the question goes on and is there when it comes back
        gate.set()
        deadline = time.time() + 5
        while client.get(f"/api/v1/asks/{ask_id}").json()["state"] != "done":
            assert time.time() < deadline
            time.sleep(0.02)
        done = client.get(f"/api/v1/asks/{ask_id}/events", headers={"Last-Event-ID": "1"})
        assert [line for line in done.text.splitlines() if line.startswith("event: ")] == [
            "event: answering",
            "event: answer",
        ]
        waiters = served.container.asks._entries[ask_id].waiters
        assert waiters == []
        assert ask(client, "next")  # the lane is free


def test_cancelling_while_following_ends_the_stream() -> None:
    with serving() as served, served.client() as client:
        served.engine.hold("first")
        ask(client, "first")
        waiting = ask(client, "second")
        with client.stream("GET", f"/api/v1/asks/{waiting}/events") as stream:
            lines = stream.iter_lines()
            assert read_events(lines, 1) == ["queued"]
            cancelled = client.delete(
                f"/api/v1/asks/{waiting}", headers={CSRF_HEADER: client.cookies[CSRF_COOKIE]}
            )
            assert cancelled.status_code == 204
            assert read_events(lines, 1) == ["cancelled"]
            assert list(lines) == [] or all(not line.startswith("event:") for line in lines)
        served.engine.gates["first"].set()


def test_two_members_follow_their_own_questions_at_once() -> None:
    with serving() as served, served.client(ANA) as one, served.client(BETO) as two:
        gate = served.engine.hold("held")
        first, second = ask(one, "held"), ask(two, "other")
        results: dict[str, list[str]] = {}

        def follow(client: httpx.Client, ask_id: str, key: str) -> None:
            with client.stream("GET", f"/api/v1/asks/{ask_id}/events") as stream:
                results[key] = [
                    line.removeprefix("event: ")
                    for line in stream.iter_lines()
                    if line.startswith("event: ")
                ]

        threads = [
            threading.Thread(target=follow, args=(one, first, "one")),
            threading.Thread(target=follow, args=(two, second, "two")),
        ]
        for thread in threads:
            thread.start()
        time.sleep(0.3)
        assert one.get(f"/api/v1/asks/{second}").status_code == 404  # not each other's
        gate.set()
        for thread in threads:
            thread.join(10)
        assert results["one"][-1] == "answer" and results["two"][-1] == "answer"
        # the second waited for the first: the lane ran one at a time
        assert served.engine.asked == ["held", "other"]
        assert "queued" in results["two"]


def test_shutting_down_with_a_question_running_does_not_hang() -> None:
    started = time.perf_counter()
    with suppress(Exception), serving() as served, served.client() as client:
        served.engine.hold("never")
        ask(client, "never")
    assert time.perf_counter() - started < 15
