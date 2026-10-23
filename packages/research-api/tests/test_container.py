from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from research_api.adapters.clock import SystemClock
from research_api.adapters.fake import FAKE_EPOCH, FixedClock
from research_api.bootstrap import (
    Settings,
    SettingsError,
    build_container,
    container_from_env,
)
from research_api.bootstrap.container import CONFIG_ENV, FAKE_ENV


def test_fake_mode_uses_fixed_time() -> None:
    container = build_container(Settings.defaults(), fake=True)
    assert container.mode == "fake"
    assert isinstance(container.clock, FixedClock)
    assert container.clock.now() == FAKE_EPOCH


def test_live_mode_uses_the_system_clock() -> None:
    container = build_container(Settings.defaults())
    assert container.mode == "live"
    assert isinstance(container.clock, SystemClock)
    assert container.clock.now().tzinfo is UTC


def test_fake_mode_refuses_a_tailscale_address() -> None:
    with pytest.raises(SettingsError, match="loopback"):
        build_container(Settings(bind_host="100.64.0.1"), fake=True)


def test_the_environment_can_ask_for_fake_mode() -> None:
    assert container_from_env({FAKE_ENV: "1"}).mode == "fake"


def test_the_environment_can_name_a_settings_file(tmp_path: Path) -> None:
    path = tmp_path / "s.toml"
    path.write_text("port = 9123\n", encoding="utf-8")
    container = container_from_env({CONFIG_ENV: str(path)})
    assert (container.mode, container.settings.port) == ("live", 9123)


def test_the_environment_must_name_one_of_them() -> None:
    with pytest.raises(SettingsError, match=CONFIG_ENV):
        container_from_env({})


def test_a_fixed_clock_can_be_set() -> None:
    at = datetime(2030, 5, 1, tzinfo=UTC)
    assert FixedClock(at).now() == at


def test_both_modes_wire_the_circuit_use_cases_with_the_settings_caps() -> None:
    settings = Settings.model_validate({"circuit_caps": {"max_ops": 7}})
    for fake in (True, False):
        container = build_container(settings, fake=fake)
        assert container.run_circuit._caps.max_ops == 7
        assert container.check_circuit._caps.max_ops == 7
        assert "conventions" in container.circuit_conventions()
