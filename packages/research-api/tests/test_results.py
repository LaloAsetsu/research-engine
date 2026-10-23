"""Reading runs and scores: files dropped in by hand, files still growing, files that are broken."""

from __future__ import annotations

import gzip
import json
from typing import Any

import pytest
from bench_helpers import ANA_ACTOR, ScriptedArms, container_with
from helpers import arun

from research_api.application.jobs import RunRequest, ScoreRequest
from research_api.application.results import RunsDesk
from research_api.bootstrap import Container
from research_api.domain.errors import CorruptStoreError, NotFoundError
from research_api.domain.jobs import ArmSpec
from research_api.domain.sets import SetRef

DEMO = SetRef("demo-papers", "v1")


def lines(*records: dict[str, Any]) -> bytes:
    return "".join(json.dumps(r) + "\n" for r in records).encode()


def header(run_id: str, set_name: str = "demo-papers/v1", **changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "format": "qa-run/1",
        "run_id": run_id,
        "created": "2026-10-20T10:00:00+00:00",
        "set": {"name": set_name, "md5": "a" * 32},
        "arm": {"name": "harness", "variant": "old"},
        "model": {"id": "qwen-local"},
        "code": {"runner": "teammate-script"},
        "corpus": {"id": "demo-corpus"},
    }
    base.update(changes)
    return base


def answer(item_id: str, repeat: int = 1, **fields: Any) -> dict[str, Any]:
    return {"id": item_id, "repeat": repeat, "answer": f"a {item_id}", "contexts": [], **fields}


@pytest.fixture
def container() -> Container:
    box = container_with(ScriptedArms())
    yield box
    box.close()


@pytest.fixture
def runs(container: Container) -> RunsDesk:
    return container.bench.runs


def drop(container: Container, run_id: str, data: bytes, suffix: str = ".qa-run.jsonl") -> None:
    folder = container.bench.jobs._files.root / "runs"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    (folder / f"{run_id}{suffix}").write_bytes(data)


def test_a_run_a_teammate_dropped_in_is_listed_though_no_job_made_it(
    container: Container, runs: RunsDesk
) -> None:
    drop(
        container,
        "old-run-1",
        lines(header("old-run-1"), answer("DP-001"), answer("DP-002", error={"kind": "timeout"})),
    )
    (found,) = runs.list()
    assert (found.run_id, found.arm, found.variant, found.model) == (
        "old-run-1",
        "harness",
        "old",
        "qwen-local",
    )
    assert (found.answers, found.errors, found.complete) == (2, 1, False)
    assert found.job_id is None and found.owner is None and found.state is None
    assert found.expected is None and found.repeats is None


def test_a_gzip_run_is_listed_and_read(container: Container, runs: RunsDesk) -> None:
    data = lines(header("old-run-2", ended="2026-10-20T11:00:00+00:00"), answer("DP-001"))
    drop(container, "old-run-2", gzip.compress(data), ".qa-run.jsonl.gz")
    assert runs.get("old-run-2").complete is True
    page = runs.items("old-run-2")
    assert [i.id for i in page.items] == ["DP-001"]
    assert page.items[0].question and page.items[0].question.startswith("What symbol")


def test_the_questions_are_empty_when_the_set_of_the_run_is_gone_or_unreadable(
    container: Container, runs: RunsDesk
) -> None:
    drop(container, "elsewhere-1", lines(header("elsewhere-1", "other-set/v7"), answer("DP-001")))
    drop(container, "elsewhere-2", lines(header("elsewhere-2", "not a set name"), answer("DP-001")))
    for run_id in ("elsewhere-1", "elsewhere-2"):
        (item,) = runs.items(run_id).items
        assert item.question is None and item.reference_answer is None and item.answer == "a DP-001"


def test_files_that_are_not_runs_are_left_out_of_the_list(
    container: Container, runs: RunsDesk
) -> None:
    drop(container, "good-run", lines(header("good-run")))
    drop(container, "garbage", b"this is not a run\n")
    drop(container, "empty", b"")
    drop(container, "wrong-format", lines({"format": "qa-score/1"}))
    assert [r.run_id for r in runs.list()] == ["good-run"]
    for broken in ("garbage", "empty", "wrong-format"):
        with pytest.raises(NotFoundError):
            runs.get(broken)


def test_a_run_that_is_still_being_written_is_read_up_to_its_last_whole_line(
    container: Container, runs: RunsDesk
) -> None:
    whole = lines(header("growing-1"), answer("DP-001"), answer("DP-002"))
    drop(container, "growing-1", whole + b'{"id": "DP-003", "rep')
    assert runs.get("growing-1").answers == 2
    assert [i.id for i in runs.items("growing-1").items] == ["DP-001", "DP-002"]


def test_a_summary_is_read_again_when_the_file_grows(container: Container, runs: RunsDesk) -> None:
    drop(container, "growing-2", lines(header("growing-2"), answer("DP-001")))
    assert runs.get("growing-2").answers == 1
    drop(container, "growing-2", lines(header("growing-2"), answer("DP-001"), answer("DP-002")))
    assert runs.get("growing-2").answers == 2


def test_a_run_that_was_removed_is_gone_from_the_list(container: Container, runs: RunsDesk) -> None:
    drop(container, "short-lived", lines(header("short-lived")))
    assert [r.run_id for r in runs.list()] == ["short-lived"]
    (container.bench.jobs._files.root / "runs" / "short-lived.qa-run.jsonl").unlink()
    assert runs.list() == []


def test_the_items_can_be_paged_and_filtered(container: Container, runs: RunsDesk) -> None:
    items = [answer(f"DP-00{n}", error={"kind": "other"} if n == 2 else None) for n in range(1, 6)]
    items = [{k: v for k, v in i.items() if v is not None} for i in items]
    drop(container, "paged", lines(header("paged"), *items))
    page = runs.items("paged", offset=1, limit=2)
    assert [i.id for i in page.items] == ["DP-002", "DP-003"] and page.total == 5
    assert (page.offset, page.limit) == (1, 2)
    assert (
        runs.items("paged", limit=0).limit == 1 and runs.items("paged", limit=10_000).limit == 200
    )
    assert runs.items("paged", offset=-3).offset == 0
    assert [i.id for i in runs.items("paged", filter="errors").items] == ["DP-002"]
    assert runs.items("paged", filter="answered").total == 4
    assert [i.id for i in runs.items("paged", item_id="DP-004").items] == ["DP-004"]
    assert runs.items("paged", item_id="nothing").items == []


def test_the_contexts_can_be_left_out(container: Container, runs: RunsDesk) -> None:
    context = {"doc_id": "ab" * 32, "text": "t"}
    drop(container, "ctx", lines(header("ctx"), answer("DP-001", contexts=[context])))
    assert runs.items("ctx").items[0].contexts == [context]
    assert runs.items("ctx", contexts=False).items[0].contexts == []


def test_an_id_that_is_not_a_run_id_is_not_found(runs: RunsDesk) -> None:
    for bad in ("../x", "a/b", ".hidden", ""):
        with pytest.raises(NotFoundError):
            runs.get(bad)
        with pytest.raises(NotFoundError):
            runs.items(bad)


@arun
async def test_scores_are_listed_cached_and_filtered(container: Container) -> None:
    import asyncio  # noqa: PLC0415

    desk = container.bench.jobs
    views = await desk.start_runs(
        ANA_ACTOR, RunRequest(DEMO, (ArmSpec("bare"), ArmSpec("harness")), ("fake-model",), 1)
    )
    for view in views:
        while not desk.get(view.job.id).job.settled:
            await asyncio.sleep(0.005)
    runs_ = tuple(v.job.artifact_id or "" for v in views)
    from research_api.domain.jobs import ScoreOptions  # noqa: PLC0415

    scores = []
    for _ in range(2):
        job = await desk.start_score(
            ANA_ACTOR, ScoreRequest(DEMO, runs_, ScoreOptions(pairs=((runs_[0], runs_[1]),)))
        )
        while not desk.get(job.job.id).job.settled:
            await asyncio.sleep(0.005)
        scores.append(job.job.id)
    desk_scores = container.bench.scores
    listed = desk_scores.list()
    assert [s.score_id for s in listed] == sorted(scores, reverse=True)
    first = desk_scores.get(scores[0])
    assert first.summary.pairs == [(runs_[0], runs_[1])] and first.summary.owner == "ana"
    assert first.header["format"] == "qa-score/1" and first.groups and first.compares
    assert desk_scores.get(scores[0]) is not None  # served from the cache the second time
    items = desk_scores.items(scores[0], run_id=runs_[0])
    assert items.total == 6 and {i["run_id"] for i in items.items} == {runs_[0]}
    assert desk_scores.items(scores[0], offset=2, limit=3).items[0]["id"] == "DP-002"
    assert desk_scores.items(scores[0], item_id="DP-001").total == 2
    with pytest.raises(NotFoundError):
        desk_scores.get("0" * 26)
    with pytest.raises(NotFoundError):
        desk_scores.get("not-an-id")


@arun
async def test_the_score_cache_keeps_only_a_few() -> None:
    from research_api.application import results  # noqa: PLC0415

    container = container_with(ScriptedArms())
    try:
        desk_scores = container.bench.scores
        files = desk_scores._files
        header_line = json.dumps(
            {
                "format": "qa-score/1",
                "created": "x",
                "set": {"name": "s", "md5": "a" * 32},
                "runs": [],
            }
        )
        ids = [f"{n:026d}" for n in range(results.SCORE_CACHE + 4)]
        for score_id in ids:
            files.write_score(score_id, (header_line + "\n").encode())
            desk_scores.get(score_id)
        assert len(desk_scores._cache) == results.SCORE_CACHE
        assert len(desk_scores.list()) == len(ids)
    finally:
        container.close()


def test_a_broken_score_file_is_left_out_of_the_list(container: Container) -> None:
    files = container.bench.scores._files
    files.write_score("1" * 26, b"garbage\n")
    assert container.bench.scores.list() == []
    with pytest.raises(CorruptStoreError, match="no longer reads"):
        container.bench.scores.get("1" * 26)


def test_runs_from_different_time_zones_are_listed_by_the_moment_they_started(
    container: Container, runs: RunsDesk
) -> None:
    # 07:00 at -06:00 is 13:00 UTC, later than 09:00 UTC, though "07" sorts before "09" as text
    drop(container, "early-utc", lines(header("early-utc", started="2026-10-22T09:00:00+00:00")))
    drop(container, "late-local", lines(header("late-local", started="2026-10-22T07:00:00-06:00")))
    drop(container, "no-zone", lines(header("no-zone", started="2026-10-22T08:00:00")))
    drop(container, "no-time", lines(header("no-time", created="not a time")))
    assert [r.run_id for r in runs.list()] == ["late-local", "early-utc", "no-zone", "no-time"]
