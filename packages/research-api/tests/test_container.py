from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from ask_helpers import ScriptedEngine
from helpers import live_settings, prepare_live, write_config

from research_api.adapters.clock import SystemClock
from research_api.adapters.fake import FAKE_EPOCH, FixedClock
from research_api.adapters.persistence import Database
from research_api.bootstrap import (
    Settings,
    SettingsError,
    build_container,
    container_from_env,
    load_settings,
)
from research_api.bootstrap import container as container_module
from research_api.bootstrap.container import CONFIG_ENV, FAKE_ENV
from research_api.bootstrap.seed import SeedUser
from research_api.domain.errors import InvalidInputError


def test_fake_mode_uses_fixed_time() -> None:
    container = build_container(Settings.defaults(), fake=True)
    assert container.mode == "fake"
    assert isinstance(container.clock, FixedClock)
    assert container.clock.now() == FAKE_EPOCH


def test_live_mode_uses_the_system_clock(tmp_path: Path) -> None:
    container = build_container(prepare_live(live_settings(tmp_path)), cheap_hashing=True)
    assert container.mode == "live"
    assert isinstance(container.clock, SystemClock)
    assert container.clock.now().tzinfo is UTC


def test_fake_mode_refuses_a_tailscale_address() -> None:
    with pytest.raises(SettingsError, match="loopback"):
        build_container(Settings(bind_host="100.64.0.1"), fake=True)


def test_the_environment_can_ask_for_fake_mode() -> None:
    container = container_from_env({FAKE_ENV: "1"})
    assert container.mode == "fake"
    assert [u.username for u in container.auth.users.all()] == ["ana", "beto", "dani", "temp"]


def test_the_environment_can_name_a_settings_file(tmp_path: Path) -> None:
    path = tmp_path / "s.toml"
    folder = tmp_path / "home"
    write_config(
        path, f'port = 9123\ndata_dir = "{folder}/data"\ndb_path = "{folder}/db/a.sqlite3"\n'
    )
    prepare_live(load_settings(path))
    container = container_from_env({CONFIG_ENV: str(path)})
    assert (container.mode, container.settings.port) == ("live", 9123)


def test_the_environment_cannot_name_a_settings_file_others_can_change(tmp_path: Path) -> None:
    path = write_config(tmp_path / "s.toml", "port = 9124\n")
    path.chmod(0o664)
    with pytest.raises(SettingsError, match=f"chmod 600 {path}"):
        container_from_env({CONFIG_ENV: str(path)})


def test_the_environment_must_name_one_of_them() -> None:
    with pytest.raises(SettingsError, match=CONFIG_ENV):
        container_from_env({})


def test_a_fixed_clock_can_be_set() -> None:
    at = datetime(2030, 5, 1, tzinfo=UTC)
    assert FixedClock(at).now() == at


def test_both_modes_wire_the_circuit_use_cases_with_the_settings_caps(tmp_path: Path) -> None:
    caps = {"circuit_caps": {"max_ops": 7}}
    for fake in (True, False):
        settings = live_settings(tmp_path / str(fake), **caps)
        if not fake:
            prepare_live(settings)
        container = build_container(settings, fake=fake)
        assert container.run_circuit._caps.max_ops == 7
        assert container.check_circuit._caps.max_ops == 7
        assert "conventions" in container.circuit_conventions()
        container.close()


# --- a start that fails releases what it opened --------------------------------------------


class _Refused(Exception):
    """The failure a test injects, to tell it from any error a release might raise."""


class _BrokenEngine(ScriptedEngine):
    def close(self) -> None:
        super().close()
        raise OSError("the engine will not close")


def test_fake_mode_leaves_no_folder_when_the_tables_cannot_be_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(self: Database) -> None:
        raise _Refused

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(Database, "create_all", refuse)
    engine = ScriptedEngine()
    with pytest.raises(_Refused):
        build_container(Settings.defaults(), fake=True, ask_engine=engine)
    assert list(tmp_path.iterdir()) == []
    assert engine.closed == 1


def test_live_mode_closes_the_engine_when_the_database_cannot_be_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = ScriptedEngine()
    monkeypatch.setattr(container_module, "_live_ask_parts", lambda settings: (engine, object()))
    settings = live_settings(tmp_path)  # the database was never made: opening it is refused
    with pytest.raises(SettingsError):
        build_container(settings, cheap_hashing=True)
    assert engine.closed == 1


def test_the_error_of_the_start_is_not_replaced_by_one_from_a_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    seed = [SeedUser(username="x", display_name="X", password="short")]
    engine = _BrokenEngine()
    with pytest.raises(InvalidInputError):  # the seed's, not the engine's OSError
        build_container(
            Settings.defaults(), fake=True, seed=seed, cheap_hashing=True, ask_engine=engine
        )
    assert engine.closed == 1
    assert list(tmp_path.iterdir()) == []  # the folder went although the engine failed to close


def test_the_error_of_the_storage_is_not_replaced_by_one_from_the_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _BrokenEngine()
    monkeypatch.setattr(container_module, "_live_ask_parts", lambda settings: (engine, object()))
    with pytest.raises(SettingsError):
        build_container(live_settings(tmp_path), cheap_hashing=True)
    assert engine.closed == 1


def test_the_fake_folder_is_removed_even_when_the_database_will_not_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    container = build_container(Settings.defaults(), fake=True)
    assert list(tmp_path.iterdir()) != []

    close = Database.close
    refused: list[bool] = []

    def refuse_once(self: Database) -> None:
        if not refused:  # the finalizer closes again when the container is collected
            refused.append(True)
            raise OSError("the database will not close")
        close(self)

    monkeypatch.setattr(Database, "close", refuse_once)
    with pytest.raises(OSError, match="will not close"):
        container.close()
    assert list(tmp_path.iterdir()) == []
