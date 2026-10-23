"""How the benchmark is put together: settings, the container, the command line, the lanes."""

from __future__ import annotations

from pathlib import Path

import pytest
from bench_helpers import bench_app, live_container
from helpers import arun, live_settings, prepare_live, write_config
from pydantic import ValidationError

from research_api.adapters.fake import FakeArms, FakeModelServer
from research_api.adapters.models import OpenAiModels
from research_api.application.arms import LiveArms
from research_api.application.lanes import ASK_PRIORITY, RUN_PRIORITY, Lane
from research_api.bootstrap import (
    Settings,
    SettingsError,
    build_container,
    container_from_env,
    load_settings,
)
from research_api.bootstrap.container import (
    DEFAULT_FAKE_BENCH_PAUSE_S,
    FAKE_BENCH_PAUSE_ENV,
    FAKE_ENV,
    MAX_FAKE_BENCH_PAUSE_S,
)
from research_api.bootstrap.settings import ModelSetting
from research_api.entrypoints import cli

# --- settings -------------------------------------------------------------------------------


def test_models_come_from_the_settings_file(tmp_path: Path) -> None:
    path = write_config(
        tmp_path / "s.toml",
        """
[[models]]
name = "qwen-local"
endpoint = "http://127.0.0.1:8001/v1/"
label = "Qwen (local)"
revision = "abc"

[[models]]
name = "other:7b"
endpoint = "https://models.example.test/v1"
""",
    )
    models = load_settings(path).models
    assert [(m.name, m.endpoint, m.label, m.revision) for m in models] == [
        ("qwen-local", "http://127.0.0.1:8001/v1", "Qwen (local)", "abc"),
        ("other:7b", "https://models.example.test/v1", None, None),
    ]
    assert Settings.defaults().models == ()


@pytest.mark.parametrize(
    ("entry", "why"),
    [
        ({"name": "m"}, "endpoint"),
        ({"name": "", "endpoint": "http://x/v1"}, "name"),
        ({"name": "has space", "endpoint": "http://x/v1"}, "name"),
        ({"name": "m", "endpoint": "ftp://x/v1"}, "http://127.0.0.1"),
        ({"name": "m", "endpoint": "http://"}, "http://127.0.0.1"),
        ({"name": "m", "endpoint": "http://user:secret@x/v1"}, "credentials"),
        ({"name": "m", "endpoint": "http://x/v1?key=1"}, "credentials"),
        ({"name": "m", "endpoint": "http://x/v1#frag"}, "credentials"),
        ({"name": "m", "endpoint": "http://x/v1", "api_key": "k"}, "api_key"),
        ({"name": "m", "endpoint": "http://x/v1", "label": ""}, "label"),
    ],
)
def test_a_model_entry_that_could_leak_a_key_or_cannot_work_is_refused(
    entry: dict[str, str], why: str
) -> None:
    with pytest.raises(ValidationError, match=why):
        Settings.model_validate({"models": [entry]})


def test_a_model_is_listed_once() -> None:
    entry = {"name": "m", "endpoint": "http://x/v1"}
    with pytest.raises(ValidationError, match="listed twice"):
        Settings.model_validate({"models": [entry, {**entry, "endpoint": "http://y/v1"}]})
    assert ModelSetting.model_validate(entry).label is None


# --- the container --------------------------------------------------------------------------


def test_fake_mode_has_the_fake_arms_and_models_on_the_ask_lane() -> None:
    container = build_container(Settings.defaults(), fake=True)
    try:
        desk = container.bench.jobs
        assert isinstance(desk._arms, FakeArms) and isinstance(
            container.bench.models, FakeModelServer
        )
        assert desk._gpu is container.asks.lane
        assert container.bench.runs is not container.bench.scores
    finally:
        container.close()


def test_live_mode_has_the_live_arms_over_the_models_of_the_settings(tmp_path: Path) -> None:
    settings = prepare_live(
        live_settings(
            tmp_path, models=[{"name": "qwen-local", "endpoint": "http://127.0.0.1:8001/v1"}]
        )
    )
    container = build_container(settings, cheap_hashing=True)
    try:
        assert isinstance(container.bench.jobs._arms, LiveArms)
        assert isinstance(container.bench.models, OpenAiModels)
        assert container.bench.jobs._runner.startswith("research-api ")
    finally:
        container.close()


def test_a_live_container_keeps_its_runs_in_the_data_folder(tmp_path: Path) -> None:
    container = live_container(tmp_path)
    try:
        assert container.bench.jobs._files.root == tmp_path / "data"
    finally:
        container.close()


def test_the_container_takes_arms_models_and_a_recheck_time_from_a_test() -> None:
    arms, models = FakeArms(), FakeModelServer()
    container = build_container(
        Settings.defaults(), fake=True, bench_arms=arms, model_server=models, bench_recheck_s=0.5
    )
    try:
        desk = container.bench.jobs
        assert desk._arms is arms and desk._models is models and desk._recheck_s == 0.5
    finally:
        container.close()


# --- the pause of the fake benchmark --------------------------------------------------------


def test_the_command_line_sets_the_pause_of_the_fake_answers() -> None:
    default = cli._container_for(None, True, None, None)
    slow = cli._container_for(None, True, None, None, bench_pause=1.5)
    try:
        assert default.bench.jobs._arms._pause_s == DEFAULT_FAKE_BENCH_PAUSE_S  # type: ignore[attr-defined]
        assert slow.bench.jobs._arms._pause_s == 1.5  # type: ignore[attr-defined]
    finally:
        default.close()
        slow.close()


@pytest.mark.parametrize("pause", [-1.0, MAX_FAKE_BENCH_PAUSE_S + 1, float("nan"), float("inf")])
def test_a_bench_pause_out_of_range_is_refused(pause: float) -> None:
    with pytest.raises(SettingsError, match="bench-pause"):
        cli._container_for(None, True, None, None, bench_pause=pause)


def test_serve_with_a_bad_bench_pause_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["serve", "--fake", "--bench-pause", "99"]) == 1
    assert "bench-pause" in capsys.readouterr().err
    assert cli.main(["serve", "--bench-pause", "1"]) == 1
    assert "--bench-pause only works with --fake" in capsys.readouterr().err


def test_the_environment_sets_the_bench_pause_for_the_factory() -> None:
    container = container_from_env({FAKE_ENV: "1", FAKE_BENCH_PAUSE_ENV: "0.5"})
    try:
        assert container.bench.jobs._arms._pause_s == 0.5  # type: ignore[attr-defined]
    finally:
        container.close()
    default = container_from_env({FAKE_ENV: "1"})
    try:
        assert default.bench.jobs._arms._pause_s == DEFAULT_FAKE_BENCH_PAUSE_S  # type: ignore[attr-defined]
    finally:
        default.close()
    for bad in ("fast", "-1", "99"):
        with pytest.raises(SettingsError, match=FAKE_BENCH_PAUSE_ENV):
            container_from_env({FAKE_ENV: "1", FAKE_BENCH_PAUSE_ENV: bad})


# --- the app --------------------------------------------------------------------------------


def test_the_app_starts_the_desk_with_its_lifespan_and_stops_it() -> None:
    with bench_app() as (client, container):
        desk = container.bench.jobs
        assert desk._task is not None and not desk._task.done()
        assert client.get("/api/v1/status").json()["bench"]["running_job"] is None
    assert desk._task is None


def test_status_names_the_lanes_and_nobody() -> None:
    with bench_app() as (client, _):
        status = client.get("/api/v1/status").json()
        assert status["bench"] == {
            "running_job": None,
            "queued_runs": 0,
            "scoring": False,
            "queued_scores": 0,
        }


# --- the lane counts what it ran ------------------------------------------------------------


@arun
async def test_a_lane_counts_the_jobs_it_finished_by_priority() -> None:
    lane = Lane("test-lane")

    async def nothing() -> None:
        return None

    async def fails() -> None:
        raise RuntimeError("boom")

    lane.submit(nothing, priority=ASK_PRIORITY)
    lane.submit(fails, priority=RUN_PRIORITY)
    lane.submit(nothing, priority=RUN_PRIORITY)
    ticket = lane.submit(nothing, priority=ASK_PRIORITY)
    assert lane.cancel(ticket)  # a job taken out of the line never ran and is not counted
    import asyncio  # noqa: PLC0415

    for _ in range(50):
        await asyncio.sleep(0)
    assert (lane.finished(ASK_PRIORITY), lane.finished(RUN_PRIORITY), lane.finished(99)) == (
        1,
        2,
        0,
    )
    assert lane._task is not None and lane._task.get_name() == "test-lane"
    await lane.stop()
