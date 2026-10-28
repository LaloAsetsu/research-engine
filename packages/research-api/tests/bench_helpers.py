"""Stand-ins and builders for the benchmark tests. Every value is made up."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ask_helpers import ANA, post, sign_in
from fastapi.testclient import TestClient
from helpers import LOOPBACK, SEED_FILE, live_settings, prepare_live
from sets_helpers import DOC_A, item, qset_bytes

from research_api.application.jobs import JobDesk, JobView, RunRequest
from research_api.bootstrap import Container, Settings, build_container
from research_api.bootstrap.demo_sets import seed_demo_sets
from research_api.bootstrap.seed import load_seed_users
from research_api.domain.ask import AskTimings
from research_api.domain.jobs import ArmSpec, Job, JobState, Progress, RunParams
from research_api.domain.runs import (
    ModelFacts,
    RunAnswer,
    RunHeaderInput,
    header_record,
    item_record,
)
from research_api.domain.sets import Actor, SetRef, etag_of
from research_api.entrypoints.http import build_app
from research_api.ports.jobs import ArmCall

DEMO = {"name": "demo-papers", "version": "v1"}
DEMO_IDS = ["DP-001", "DP-001-es", "DP-002", "DP-003", "DP-004", "DP-005"]
DEMO_REF = SetRef("demo-papers", "v1")
TOTAL = len(DEMO_IDS)
ANA_ACTOR = Actor("ana", admin=True)
BETO_ACTOR = Actor("beto")
RUNS = "/api/v1/runs"
JOBS = "/api/v1/jobs"
SCORES = "/api/v1/scores"


class ScriptedArms:
    """Answers at once with a recognisable text, unless a test holds a question or makes it fail.

    A hold is a ``threading.Event``, polled, because a test client runs the app in another thread.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []
        self.gates: dict[tuple[str, int], threading.Event] = {}
        self.errors: dict[tuple[str, int], BaseException] = {}
        self.refused: dict[str, str] = {}
        self.waiting: dict[str, str | None] = {}
        self.active = 0
        self.max_active = 0
        self.pause_s = 0.0

    def hold(self, item_id: str, repeat: int = 1) -> threading.Event:
        return self.gates.setdefault((item_id, repeat), threading.Event())

    def refusal(self, arm_name: str) -> str | None:
        return self.refused.get(arm_name)

    def prompt_md5(self, arm_name: str) -> str | None:
        return None

    async def waiting_for(self, arm_name: str, model: str) -> str | None:
        return self.waiting.get(arm_name)

    async def answer(self, call: ArmCall) -> RunAnswer:
        key = (call.item["id"], call.repeat)
        self.calls.append((call.arm_name, *key))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            gate = self.gates.get(key)
            while gate is not None and not gate.is_set():
                await asyncio.sleep(0.005)
            if self.pause_s:
                await asyncio.sleep(self.pause_s)
            if key in self.errors:
                raise self.errors[key]
            text = f"{call.arm_name} answer to {key[0]} #{key[1]}"
            return RunAnswer(answer=text, timings=AskTimings(0.0, 0.01, 0.01))
        finally:
            self.active -= 1


QUOTES_REF = SetRef("quotes-only", "v1")
QUOTES_GAPS = [f"T-{n}" for n in range(7)]


def add_quotes_set(container: Container) -> SetRef:
    """``quotes-only/v1``, frozen: seven questions whose gold passage has only a quote (no
    ``md_text``), one with a text, and one with no gold paper (allowed)."""
    quote = {"doc_id": DOC_A, "passages": [{"source_text": "q"}]}
    text = {"doc_id": DOC_A, "passages": [{"md_text": "a text"}]}
    items = [item(n, gold_papers=[quote]) for n in QUOTES_GAPS]
    items += [item("T-7", gold_papers=[text]), item("T-8", gold_papers=[])]
    sets = container.sets
    sets.manage.import_set(
        ANA_ACTOR, qset_bytes(items, "quotes-only/v1"), name="quotes-only", version="v1"
    )
    _, plan = sets.freeze.plan(QUOTES_REF)
    sets.freeze.freeze(ANA_ACTOR, QUOTES_REF, etag_of(plan.token))
    return QUOTES_REF


def container_with(arms: Any = None, **kwargs: Any) -> Container:
    return build_container(
        Settings.defaults(),
        fake=True,
        seed=load_seed_users(SEED_FILE),
        cheap_hashing=True,
        bench_arms=arms,
        **kwargs,
    )


def live_container(tmp_path: Path, arms: Any = None, **kwargs: Any) -> Container:
    """A live-mode container over a private folder, with the demo sets in it."""
    settings = prepare_live(live_settings(tmp_path))
    container = build_container(settings, cheap_hashing=True, bench_arms=arms, **kwargs)
    seed_demo_sets(container.sets)
    return container


@contextmanager
def bench_app(
    arms: Any = None, signed_in: dict[str, str] | None = ANA, **kwargs: Any
) -> Iterator[tuple[TestClient, Container]]:
    """A fake-mode app with its lifespan running, signed in as ana."""
    container = container_with(arms, **kwargs)
    with TestClient(
        build_app(container),
        base_url=LOOPBACK,
        headers={"Origin": LOOPBACK},
        raise_server_exceptions=False,
    ) as client:
        if signed_in is not None:
            sign_in(client, signed_in)
        yield client, container


def run_body(arm: str = "bare", model: str = "fake-model", **extra: Any) -> dict[str, Any]:
    return {"set": DEMO, "arms": [{"name": arm}], "models": [model], **extra}


def start_run(client: TestClient, arm: str = "bare", **extra: Any) -> dict[str, Any]:
    done = post(client, RUNS, json=run_body(arm, **extra))
    assert done.status_code == 202, done.text
    jobs = done.json()
    assert len(jobs) == 1
    return dict(jobs[0])


def wait_for(
    client: TestClient, job_id: str, until: Callable[[dict[str, Any]], bool], seconds: float = 20
) -> dict[str, Any]:
    import time  # noqa: PLC0415

    deadline = time.monotonic() + seconds
    while True:
        job = client.get(f"{JOBS}/{job_id}").json()
        if until(job):
            return dict(job)
        if time.monotonic() > deadline:
            raise AssertionError(f"job {job_id} never got there; it is {job}")
        time.sleep(0.01)


def settled(client: TestClient, job_id: str) -> dict[str, Any]:
    return wait_for(client, job_id, lambda j: bool(j["settled"]))


def in_state(client: TestClient, job_id: str, state: str) -> dict[str, Any]:
    return wait_for(client, job_id, lambda j: j["state"] == state)


def run_lines(container: Container, run_id: str) -> list[dict[str, Any]]:
    """The items of a run file, read the way the service reads it."""
    data = container.bench.jobs._files.read_run(run_id)
    assert data is not None
    run = container.bench.jobs._formats.read(data)
    return [dict(i) for i in run.items]


# --- driving a desk directly -----------------------------------------------------------------


def request(
    *arms: str,
    models: tuple[str, ...] = ("fake-model",),
    repeats: int = 1,
    ref: SetRef = DEMO_REF,
    **kw: Any,
) -> RunRequest:
    return RunRequest(ref, tuple(ArmSpec(a) for a in arms or ("bare",)), models, repeats, **kw)


async def until(check: Callable[[], bool], seconds: float = 10.0) -> None:
    async def wait() -> None:
        while not check():
            await asyncio.sleep(0.005)

    try:
        await asyncio.wait_for(wait(), seconds)
    except TimeoutError:
        raise AssertionError("the condition never came true") from None


def state_of(desk: JobDesk, job_id: str) -> JobState:
    return desk.get(job_id).job.state


async def finished(desk: JobDesk, view: JobView) -> JobView:
    await until(lambda: desk.get(view.job.id).job.settled)
    return desk.get(view.job.id)


def run_file(desk: JobDesk, view: JobView) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    run_id = view.job.artifact_id
    assert run_id is not None
    data = desk._files.read_run(run_id)
    assert data is not None
    run = desk._formats.read(data)
    return run.header, [dict(i) for i in run.items]


def pairs(items: list[dict[str, Any]]) -> list[tuple[str, int]]:
    return [(i["id"], i["repeat"]) for i in items]


def every_pair(repeats: int) -> list[tuple[str, int]]:
    return [(i, r) for r in range(1, repeats + 1) for i in DEMO_IDS]


def seed_stopped_run(
    container: Container, state: JobState, answers: int, half_line: bool = False
) -> tuple[JobDesk, str]:
    """A run that a hard stop left: the job row says ``state`` and the file has some answers."""
    desk = container.bench.jobs
    repo, files = desk._repo, desk._files
    job_id = "0" * 25 + "1"
    run_id = f"bare-demo-papers-v1-{job_id[-8:]}"
    md5 = container.sets.manage.get_version(DEMO_REF).version.md5
    now = desk._clock.now()
    header = header_record(
        RunHeaderInput(
            run_id,
            now,
            now,
            "demo-papers/v1",
            md5,
            "demo-corpus",
            ArmSpec("bare"),
            ModelFacts("fake-model"),
            1,
            "",
            "test",
        )
    )
    files.create_run(run_id, desk._formats.header_line(header))
    for item_id in DEMO_IDS[:answers]:
        line = desk._formats.item_line(item_record(item_id, 1, RunAnswer(f"old {item_id}")))
        files.append_run(run_id, line)
    if half_line:
        line = desk._formats.item_line(item_record(DEMO_IDS[answers], 1, RunAnswer("cut")))
        files.append_run(run_id, line[: len(line) // 2])
    repo.add(
        Job(
            job_id,
            "run",
            state,
            "ana",
            now,
            now,
            RunParams(DEMO_REF, md5, ArmSpec("bare"), "fake-model", 1),
            Progress(answers, TOTAL),
            started_at=now,
            artifact_id=run_id,
        )
    )
    return desk, job_id
