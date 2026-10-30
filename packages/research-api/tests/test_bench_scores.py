"""The scores the service makes are the scores qa-bench makes: same files in, same bytes out."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from bench_helpers import ANA_ACTOR, container_with
from helpers import MovableClock, arun
from qa_bench.formats import load_bytes
from qa_bench.score import NamedRun, Options, score

from research_api.application.jobs import RunRequest, ScoreRequest
from research_api.domain.jobs import ArmSpec, JobState, ScoreOptions
from research_api.domain.sets import SetRef

DEMO = SetRef("demo-papers", "v1")


async def finish(desk, job_id: str) -> None:  # type: ignore[no-untyped-def]
    for _ in range(2000):
        if desk.get(job_id).job.settled:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("never settled")


@arun
async def test_a_score_made_by_the_service_equals_the_one_qa_bench_makes_from_the_same_files() -> (
    None
):
    clock = MovableClock(datetime(2026, 10, 23, 9, 30, 0, tzinfo=UTC))
    container = container_with(None, clock=clock)
    try:
        desk = container.bench.jobs
        views = await desk.start_runs(
            ANA_ACTOR,
            RunRequest(
                DEMO, (ArmSpec("bare"), ArmSpec("harness"), ArmSpec("oracle")), ("fake-model",), 2
            ),
        )
        for view in views:
            await finish(desk, view.job.id)
        runs = [v.job.artifact_id or "" for v in views]
        options = ScoreOptions(
            pairs=((runs[0], runs[1]), (runs[1], runs[2])), noise=(runs[1], runs[2])
        )
        score_job = await desk.start_score(ANA_ACTOR, ScoreRequest(DEMO, tuple(runs), options))
        await finish(desk, score_job.job.id)
        assert desk.get(score_job.job.id).job.state is JobState.DONE
        made = desk._files.read_score(score_job.job.id)

        _, set_bytes = container.sets.manage.export(DEMO)
        qset = load_bytes(set_bytes).file
        assert qset is not None
        named = []
        for run_id in runs:
            data = desk._files.read_run(run_id)
            loaded = load_bytes(data or b"").file
            assert loaded is not None
            named.append(NamedRun(f"{run_id}.qa-run.jsonl", loaded))
        expected = score(
            qset,
            named,
            created=clock.at.isoformat(timespec="seconds"),
            options=Options(pairs=options.pairs, noise=options.noise),
        )
        assert made == expected.file.to_bytes()
        assert load_bytes(made or b"", "qa-score/1").ok  # and it is a valid file of its format
    finally:
        container.close()


@arun
async def test_the_score_file_sits_beside_the_runs_in_private_files(tmp_path: Path) -> None:
    container = container_with(None)
    try:
        desk = container.bench.jobs
        (view,) = await desk.start_runs(
            ANA_ACTOR, RunRequest(DEMO, (ArmSpec("harness"),), ("fake-model",), 1)
        )
        await finish(desk, view.job.id)
        score_job = await desk.start_score(
            ANA_ACTOR, ScoreRequest(DEMO, (view.job.artifact_id or "",))
        )
        await finish(desk, score_job.job.id)
        root = desk._files.root
        assert (root / "scores" / f"{score_job.job.id}.qa-score.jsonl").is_file()
        assert (root / "runs" / f"{view.job.artifact_id}.qa-run.jsonl").is_file()
        assert (root / "jobs" / f"{score_job.job.id}.log").is_file()
        assert str(tmp_path) not in str(root)
    finally:
        container.close()


@arun
async def test_the_md5_a_run_lists_is_the_md5_its_score_records_and_follows_the_file() -> None:
    container = container_with(None)
    try:
        desk = container.bench.jobs
        views = await desk.start_runs(
            ANA_ACTOR, RunRequest(DEMO, (ArmSpec("bare"), ArmSpec("harness")), ("fake-model",), 2)
        )
        for view in views:
            await finish(desk, view.job.id)
        runs = tuple(v.job.artifact_id or "" for v in views)
        job = await desk.start_score(
            ANA_ACTOR, ScoreRequest(DEMO, runs, ScoreOptions(pairs=((runs[0], runs[1]),)))
        )
        await finish(desk, job.job.id)
        header = container.bench.scores.get(job.job.id).header
        scored = {r["run_id"]: r["md5"] for r in header["runs"]}
        listed = {r.run_id: r.md5 for r in container.bench.runs.list()}
        assert scored == listed and len(set(scored.values())) == 2
        desk._files.append_run(runs[0], b'{"id": "DP-001", "repeat": 3, "answer": "x"}\n')
        assert container.bench.runs.get(runs[0]).md5 != scored[runs[0]]
        assert container.bench.runs.get(runs[1]).md5 == scored[runs[1]]
    finally:
        container.close()
