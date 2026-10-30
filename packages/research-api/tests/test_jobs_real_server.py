"""A run over a real socket: the progress is visible while it goes on, an ask goes between its
questions, and stopping the server with a run in flight leaves a run that can be resumed."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from ask_helpers import ANA, ScriptedEngine
from bench_helpers import DEMO, DEMO_IDS, ScriptedArms
from helpers import SEED_FILE, live_settings, prepare_live

from research_api.adapters.fake import FakeModelServer
from research_api.bootstrap import Container, Settings, build_container
from research_api.bootstrap.demo_sets import seed_demo_sets
from research_api.bootstrap.seed import load_seed_users
from research_api.domain.auth import Role
from research_api.entrypoints.http import build_app
from research_api.entrypoints.http.security import CSRF_COOKIE, CSRF_HEADER


class Served:
    def __init__(self, port: int, container: Container, arms: ScriptedArms) -> None:
        self.port, self.container, self.arms = port, container, arms
        self.origin = f"http://127.0.0.1:{port}"

    @contextmanager
    def client(self) -> Iterator[httpx.Client]:
        with httpx.Client(base_url=self.origin, headers={"Origin": self.origin}, timeout=10) as c:
            assert c.post("/api/v1/auth/login", json=ANA).status_code == 204
            c.headers[CSRF_HEADER] = c.cookies[CSRF_COOKIE]
            yield c


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextmanager
def serving(
    arms: ScriptedArms | None = None, container: Container | None = None, port: int | None = None
) -> Iterator[tuple[Served, uvicorn.Server]]:
    arms = arms or ScriptedArms()
    port = port or free_port()
    container = container or build_container(
        Settings.defaults(),
        fake=True,
        seed=load_seed_users(SEED_FILE),
        cheap_hashing=True,
        ask_engine=ScriptedEngine(),
        bench_arms=arms,
    )
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
        yield Served(port, container, arms), server
    finally:
        server.should_exit = True
        thread.join(timeout=15)


def start(client: httpx.Client, arm: str = "bare") -> dict[str, Any]:
    done = client.post(
        "/api/v1/runs",
        json={"set": DEMO, "arms": [{"name": arm}], "models": ["fake-model"]},
    )
    assert done.status_code == 202, done.text
    return dict(done.json()[0])


def poll(client: httpx.Client, job_id: str, until: Any, seconds: float = 15) -> dict[str, Any]:
    deadline = time.monotonic() + seconds
    while True:
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        if until(job):
            return dict(job)
        assert time.monotonic() < deadline, job
        time.sleep(0.02)


def test_the_progress_is_visible_while_the_run_goes_on_and_a_tail_follows_the_log() -> None:
    arms = ScriptedArms()
    gate = arms.hold("DP-002")
    with serving(arms) as (served, _), served.client() as client:
        job = start(client)
        middle = poll(client, job["id"], lambda j: j["progress"]["done"] == 2)
        assert middle["state"] == "running" and not middle["settled"]
        first = client.get(f"/api/v1/jobs/{job['id']}/log")
        assert "2/6 DP-001-es" in first.text and "3/6" not in first.text
        gate.set()
        end = poll(client, job["id"], lambda j: j["settled"])
        assert end["state"] == "done"
        tail = client.get(
            f"/api/v1/jobs/{job['id']}/log", params={"offset": first.headers["x-log-next-offset"]}
        )
        assert "3/6 DP-002" in tail.text and "done: 6 answers" in tail.text


def test_an_ask_is_answered_between_two_questions_of_a_run() -> None:
    arms = ScriptedArms()
    arms.pause_s = (
        0.1  # each question takes a moment, so the run is still going when the ask is done
    )
    gate = arms.hold("DP-002")
    with serving(arms) as (served, _), served.client() as client:
        job = start(client)
        poll(client, job["id"], lambda j: j["progress"]["done"] == 2)
        asked = client.post("/api/v1/asks", json={"question": "between"})
        assert asked.status_code == 202
        ask_id = asked.json()["ask_id"]
        assert asked.json()["position"] == 1  # behind the question in flight, not behind the run
        gate.set()
        deadline = time.monotonic() + 15
        while client.get(f"/api/v1/asks/{ask_id}").json()["state"] != "done":
            assert time.monotonic() < deadline
            time.sleep(0.02)
        mid_run = client.get(f"/api/v1/jobs/{job['id']}").json()
        assert mid_run["progress"]["done"] <= 4  # the ask did not wait for the run to end
        poll(client, job["id"], lambda j: j["settled"])
        run = client.get(f"/api/v1/runs/{job['run']['run_id']}").json()
        assert "Interleaved asks: 1." in run["notes"]


def test_cancelling_over_the_socket_stops_between_questions() -> None:
    arms = ScriptedArms()
    gate = arms.hold("DP-002")
    with serving(arms) as (served, _), served.client() as client:
        job = start(client)
        poll(client, job["id"], lambda j: j["progress"]["done"] == 2)
        assert client.post(f"/api/v1/jobs/{job['id']}/cancel").json()["state"] == "cancelling"
        gate.set()
        stopped = poll(client, job["id"], lambda j: j["settled"])
        assert stopped["state"] == "cancelled" and stopped["progress"]["done"] == 3
        assert client.post(f"/api/v1/jobs/{job['id']}/resume").status_code == 200
        done = poll(client, job["id"], lambda j: j["state"] == "done")
        assert done["progress"]["done"] == len(DEMO_IDS)


def test_stopping_the_server_with_a_run_in_flight_leaves_a_run_that_a_restart_can_resume(
    tmp_path: Path,
) -> None:
    port = free_port()
    settings = prepare_live(live_settings(tmp_path, port=port))
    arms = ScriptedArms()
    arms.hold("DP-003")  # never released: the question is in flight when the server stops
    first = build_container(
        settings, cheap_hashing=True, bench_arms=arms, model_server=FakeModelServer()
    )
    seed_demo_sets(first.sets)
    first.auth.users.create(
        "ana",
        "Ana",
        Role.ADMIN,
        password=ANA["password"],
        must_change_password=False,
        language="en",
    )
    with serving(arms, first, port) as (served, _), served.client() as client:
        job = start(client)
        poll(client, job["id"], lambda j: j["progress"]["done"] == 3)
    # the server is down; a new process starts on the same files
    second = build_container(
        settings, cheap_hashing=True, bench_arms=ScriptedArms(), model_server=FakeModelServer()
    )
    desk = second.bench.jobs
    stopped = desk._repo.get(job["id"])
    assert stopped is not None and stopped.state.value == "interrupted"
    assert stopped.progress.done == 3
    data = desk._files.read_run(stopped.artifact_id or "")
    assert data is not None and data.count(b"\n") == 1 + 3
    with serving(None, second, port) as (served, _), served.client() as client:
        assert client.post(f"/api/v1/jobs/{job['id']}/resume").status_code == 200
        done = poll(client, job["id"], lambda j: j["settled"])
        assert done["state"] == "done" and done["progress"]["done"] == len(DEMO_IDS)
        items = client.get(
            f"/api/v1/runs/{job['run']['run_id']}/items", params={"limit": 200}
        ).json()["items"]
        assert [i["id"] for i in items] == DEMO_IDS
