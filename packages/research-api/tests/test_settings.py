from __future__ import annotations

from pathlib import Path

import pytest

from research_api.bootstrap import Settings, SettingsError, load_settings
from research_api.bootstrap.settings import is_loopback, is_tailscale

PACKAGE = Path(__file__).resolve().parents[1]


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "settings.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_listen_on_loopback() -> None:
    settings = Settings.defaults()
    assert (settings.bind_host, settings.port) == ("127.0.0.1", 8090)
    assert settings.origin == "http://127.0.0.1:8090"


def test_the_example_file_loads() -> None:
    settings = load_settings(PACKAGE / "config.example.toml")
    assert settings.origin == "http://127.0.0.1:8090"


def test_an_empty_file_is_all_defaults(tmp_path: Path) -> None:
    assert load_settings(write(tmp_path, "")) == Settings.defaults()


@pytest.mark.parametrize("host", ["127.0.0.1", "127.9.9.9", "::1", "100.64.0.1", "100.127.255.254"])
def test_private_bind_addresses_are_accepted(host: str) -> None:
    assert Settings(bind_host=host).bind_host == host


def test_a_tailscale_ipv6_address_is_accepted() -> None:
    assert Settings(bind_host="fd7a:115c:a1e0::1").bind_host == "fd7a:115c:a1e0::1"


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",
        "::",
        "192.168.1.20",
        "10.0.0.5",
        "100.128.0.1",
        "8.8.8.8",
        "localhost",
        "host.example",
    ],
)
def test_other_bind_addresses_are_refused(host: str) -> None:
    with pytest.raises(ValueError, match="loopback address or a Tailscale address"):
        Settings(bind_host=host)


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_a_port_out_of_range_is_refused(port: int) -> None:
    with pytest.raises(ValueError, match="port"):
        Settings(port=port)


def test_an_origin_without_a_trailing_slash_is_kept_clean() -> None:
    assert Settings(public_origin="https://tool.example/").origin == "https://tool.example"


@pytest.mark.parametrize(
    "origin",
    [
        "tool.example",
        "ftp://tool.example",
        "http://",
        "http://tool.example/app",
        "http://tool.example?x=1",
        "http://user@tool.example",
    ],
)
def test_a_value_that_is_not_an_origin_is_refused(origin: str) -> None:
    with pytest.raises(ValueError, match="origin"):
        Settings(public_origin=origin)


@pytest.mark.parametrize(
    ("host", "loopback", "tailscale"),
    [
        ("127.0.0.1", True, False),
        ("::1", True, False),
        ("::ffff:127.0.0.1", True, False),
        ("100.64.0.5", False, True),
        ("::ffff:100.64.0.5", False, True),
        ("fd7a:115c:a1e0::5", False, True),
        ("fe80::1%en0", False, False),
        ("192.168.1.2", False, False),
        ("testserver", False, False),
        ("", False, False),
    ],
)
def test_address_classes(host: str, loopback: bool, tailscale: bool) -> None:
    assert (is_loopback(host), is_tailscale(host)) == (loopback, tailscale)


def test_the_default_origin_brackets_an_ipv6_host() -> None:
    assert Settings(bind_host="::1", port=9000).origin == "http://[::1]:9000"


def test_an_unknown_key_is_an_error_that_names_it(tmp_path: Path) -> None:
    path = write(tmp_path, 'bind_hots = "127.0.0.1"\n')
    with pytest.raises(SettingsError, match="bind_hots"):
        load_settings(path)


def test_a_rule_failure_names_the_key(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match=r"bind_host: .*loopback"):
        load_settings(write(tmp_path, 'bind_host = "0.0.0.0"\n'))


def test_a_missing_file_is_a_settings_error(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="cannot read"):
        load_settings(tmp_path / "nope.toml")


def test_a_file_that_is_not_toml_is_a_settings_error(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="not valid TOML"):
        load_settings(write(tmp_path, "port = = 1"))


def test_an_explicit_null_origin_means_the_default() -> None:
    assert Settings(public_origin=None).origin == "http://127.0.0.1:8090"
