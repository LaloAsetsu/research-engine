from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from helpers import live_settings, prepare_live, write_config

from research_api.adapters.clock import SystemClock
from research_api.adapters.fake import FAKE_EPOCH, FixedClock
from research_api.bootstrap import (
    Settings,
    SettingsError,
    build_container,
    container_from_env,
    load_settings,
)
from research_api.bootstrap.container import CONFIG_ENV, FAKE_ENV


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
    assert [u.username for u in container.auth.users.all()] == ["ana", "beto", "dani", "luis"]


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
