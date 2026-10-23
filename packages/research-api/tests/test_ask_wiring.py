"""Settings, container and command line of the ask path, and a live container over a real store."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from ask_helpers import ScriptedEngine, ask, follow, running_app
from docingest.adapters.store.filesystem import FilesystemStore
from docingest.domain.models import SourceKind
from fastapi.testclient import TestClient
from helpers import LOOPBACK, MovableClock, live_settings, prepare_live, write_config
from test_docingest_ask import doc_id, manifest, markdown

from research_api.adapters.docingest_ask import (
    DocingestAskEngine,
    OfflineAskEngine,
)
from research_api.adapters.fake import FAKE_EPOCH, FakeAskEngine
from research_api.bootstrap import (
    Container,
    Settings,
    SettingsError,
    build_container,
    container_from_env,
)
from research_api.bootstrap.container import (
    DEFAULT_FAKE_ASK_PAUSE_S,
    FAKE_ASK_PAUSE_ENV,
    FAKE_ENV,
    MAX_FAKE_ASK_PAUSE_S,
)
from research_api.domain.auth import Role
from research_api.entrypoints import cli
from research_api.entrypoints.http import build_app

PACKAGE = Path(__file__).resolve().parents[1]
ANA_LIVE = {"username": "live", "password": "live-test-password-1"}


# --- settings -----------------------------------------------------------------------------


def test_the_docingest_settings_file_is_optional_and_absolute() -> None:
    assert Settings.defaults().docingest_config is None
    assert Settings(docingest_config="/etc/d.toml").docingest_config == Path("/etc/d.toml")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="absolute"):
        Settings(docingest_config="relative/d.toml")  # type: ignore[arg-type]


def test_a_home_folder_in_the_docingest_path_is_expanded() -> None:
    path = Settings(docingest_config="~/d.toml").docingest_config  # type: ignore[arg-type]
    assert path is not None and path.is_absolute() and "~" not in str(path)


# --- the container ------------------------------------------------------------------------


def test_fake_mode_wires_the_fake_engine_and_papers() -> None:
    container = build_container(Settings.defaults(), fake=True)
    assert isinstance(container.asks.engine, FakeAskEngine)
    assert container.documents.search("", 5).total >= 1


def test_the_fake_engine_can_be_replaced_and_is_closed_with_the_container() -> None:
    engine = ScriptedEngine()
    container = build_container(Settings.defaults(), fake=True, ask_engine=engine)
    assert container.asks.engine is engine
    container.close()
    container.close()
    assert engine.closed >= 1


def test_live_mode_without_a_docingest_file_cannot_ask_but_runs(tmp_path: Path) -> None:
    container = build_container(prepare_live(live_settings(tmp_path)), cheap_hashing=True)
    try:
        assert isinstance(container.asks.engine, OfflineAskEngine)
        assert container.documents.search("", 5).total == 0
        assert container.asks.status().health.state == "unavailable"
    finally:
        container.close()


@pytest.mark.parametrize("what", ["missing", "folder", "bad toml"])
def test_a_docingest_file_that_cannot_be_used_stops_the_start(tmp_path: Path, what: str) -> None:
    target = tmp_path / "d.toml"
    if what == "folder":
        target.mkdir()
    elif what == "bad toml":
        target.write_text("= = nope", encoding="utf-8")
    settings = prepare_live(live_settings(tmp_path / "home", docingest_config=target))
    with pytest.raises(SettingsError, match="docingest_config"):
        build_container(settings, cheap_hashing=True)


def test_a_failed_start_leaves_no_database_open(tmp_path: Path) -> None:
    """The engine is built before the database, so a refused docingest file opens nothing."""
    settings = prepare_live(live_settings(tmp_path / "home", docingest_config=tmp_path / "no.toml"))
    for _ in range(3):
        with pytest.raises(SettingsError):
            build_container(settings, cheap_hashing=True)


def test_a_docingest_file_that_works_builds_the_real_adapters_without_opening_them(
    tmp_path: Path,
) -> None:
    config = tmp_path / "d.toml"
    config.write_text(f'output_dir = "{tmp_path / "normalized"}"\n', encoding="utf-8")
    settings = prepare_live(live_settings(tmp_path / "home", docingest_config=config))
    container = build_container(settings, cheap_hashing=True)
    try:
        assert isinstance(container.asks.engine, DocingestAskEngine)
        assert container.asks.engine.health().state == "warming_up"
    finally:
        container.close()


# --- fake pause ---------------------------------------------------------------------------


def test_the_command_line_sets_the_pause_of_the_fake_engine() -> None:
    default = cli._container_for(None, True, None, None)
    slow = cli._container_for(None, True, None, None, 1.5)
    try:
        assert default.asks.engine._pause_s == DEFAULT_FAKE_ASK_PAUSE_S  # type: ignore[attr-defined]
        assert slow.asks.engine._pause_s == 1.5  # type: ignore[attr-defined]
    finally:
        default.close()
        slow.close()


@pytest.mark.parametrize("pause", [-1.0, MAX_FAKE_ASK_PAUSE_S + 1, float("nan"), float("inf")])
def test_a_pause_out_of_range_is_refused(pause: float) -> None:
    with pytest.raises(SettingsError, match="ask-pause"):
        cli._container_for(None, True, None, None, pause)


def test_serve_fake_with_a_bad_pause_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["serve", "--fake", "--ask-pause", "99"]) == 1
    assert "ask-pause" in capsys.readouterr().err


def test_a_pause_means_nothing_without_fake(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["serve", "--ask-pause", "1"]) == 1
    assert "--ask-pause only works with --fake" in capsys.readouterr().err
    with pytest.raises(SettingsError, match="--ask-pause only works with --fake"):
        cli._container_for(None, False, None, None, 0.0)


def test_fake_mode_forgets_old_asks_by_the_real_time_not_its_fixed_clock() -> None:
    container = build_container(Settings.defaults(), fake=True)
    other = MovableClock()
    moved = build_container(Settings.defaults(), fake=True, clock=other)
    try:
        first = container.asks._clock.now()  # type: ignore[attr-defined]
        time.sleep(0.01)
        assert container.asks._clock.now() > first  # type: ignore[attr-defined]
        assert first > FAKE_EPOCH  # the sessions and documents keep the fixed one
        assert container.clock.now() == FAKE_EPOCH
        assert moved.asks._clock is other  # type: ignore[attr-defined]  # a test can still move it
    finally:
        container.close()
        moved.close()


def test_the_environment_sets_the_pause_for_the_factory() -> None:
    container = container_from_env({FAKE_ENV: "1", FAKE_ASK_PAUSE_ENV: "0.25"})
    try:
        assert container.asks.engine._pause_s == 0.25  # type: ignore[attr-defined]
    finally:
        container.close()
    container = container_from_env({FAKE_ENV: "1"})
    try:
        assert container.asks.engine._pause_s == DEFAULT_FAKE_ASK_PAUSE_S  # type: ignore[attr-defined]
    finally:
        container.close()
    for bad in ("fast", "-1", "11", ""):
        with pytest.raises(SettingsError, match=FAKE_ASK_PAUSE_ENV):
            container_from_env({FAKE_ENV: "1", FAKE_ASK_PAUSE_ENV: bad})


# --- the lifespan -------------------------------------------------------------------------


def test_the_app_starts_the_lane_and_warms_the_engine_and_stops_them() -> None:
    engine = ScriptedEngine("warming_up")
    with running_app(engine) as (client, container):
        deadline = time.time() + 3
        while engine.state == "warming_up" and time.time() < deadline:
            time.sleep(0.01)
        assert engine.warmed == 1 and engine.state == "ready"
        assert follow(client, ask(client, "hello"))[-1]["event"] == "answer"
        assert container.asks.lane._task is not None
    assert container.asks.lane._task is None  # stopped with the app
    assert engine.closed >= 1


# --- a live container over a real store ------------------------------------------------------


def live_client(tmp_path: Path) -> tuple[TestClient, Container]:
    store = FilesystemStore(tmp_path / "normalized")
    store.save(
        manifest("alpha.pdf", title="Alpha study of qubits"), markdown("alpha one", "alpha two")
    )
    store.save(
        manifest(
            "delta.tex",
            kind=SourceKind.LATEX,
            pages=2,
            title="Delta",
            section_titles=True,
        ),
        markdown("intro $E=mc^2$", "method", method="latex"),
    )
    config = write_config(tmp_path / "d.toml", f'output_dir = "{tmp_path / "normalized"}"\n')
    settings = prepare_live(live_settings(tmp_path / "home", docingest_config=config))
    container = build_container(settings, cheap_hashing=True)
    container.auth.users.create(
        "live", "Live User", Role.MEMBER, password=ANA_LIVE["password"], must_change_password=False
    )
    client = TestClient(
        build_app(container),
        base_url=LOOPBACK,
        headers={"Origin": LOOPBACK},
        raise_server_exceptions=False,
    )
    return client, container


def test_a_live_container_reads_papers_from_a_docingest_store(tmp_path: Path) -> None:
    client, container = live_client(tmp_path)
    with client:
        assert client.post("/api/v1/auth/login", json=ANA_LIVE).status_code == 204
        listing = client.get("/api/v1/documents").json()
        assert [i["title"] for i in listing["items"]] == ["Alpha study of qubits", "Delta"]
        alpha = doc_id("alpha.pdf")
        page = client.get(f"/api/v1/documents/{alpha[:16]}/pages/2").json()
        assert (page["markdown"], page["pages"], page["kind"]) == ("alpha two", 2, "page")
        section = client.get(f"/api/v1/documents/{doc_id('delta.tex')[:16]}/pages/1").json()
        assert (section["kind"], section["title"], section["markdown"]) == (
            "section",
            "Section 1",
            "intro $E=mc^2$",
        )
        assert client.get(f"/api/v1/documents/{alpha}/pages/3").status_code == 404
        assert isinstance(container.asks.engine, DocingestAskEngine)  # never asked here


def test_a_live_service_without_docingest_says_why_it_cannot_answer(tmp_path: Path) -> None:
    settings = prepare_live(live_settings(tmp_path))
    container = build_container(settings, cheap_hashing=True)
    container.auth.users.create(
        "live", "Live User", Role.MEMBER, password=ANA_LIVE["password"], must_change_password=False
    )
    with TestClient(
        build_app(container), base_url=LOOPBACK, headers={"Origin": LOOPBACK}
    ) as client:
        assert client.post("/api/v1/auth/login", json=ANA_LIVE).status_code == 204
        status = client.get("/api/v1/status").json()["ask"]
        assert status["state"] == "unavailable" and "docingest configuration" in status["reason"]
        events = follow(client, ask(client, "anything"))
        assert events[-1]["event"] == "error"
        assert events[-1]["data"]["status"] == 503 and events[-1]["data"]["kind"] == "unavailable"
        assert client.get("/api/v1/documents").json() == {"items": [], "total": 0}
