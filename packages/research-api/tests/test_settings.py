from __future__ import annotations

from pathlib import Path

import pytest

from research_api.bootstrap import Settings, SettingsError, load_settings
from research_api.bootstrap.settings import is_loopback, is_tailscale
from research_api.domain.circuits import CircuitCaps

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


def test_the_example_file_states_the_default_circuit_caps() -> None:
    settings = load_settings(PACKAGE / "config.example.toml")
    assert settings.circuit_caps == Settings.defaults().circuit_caps
    assert settings.circuit_caps.caps() == CircuitCaps(1024 * 1024, 2000, 30.0)


def test_circuit_caps_can_be_set(tmp_path: Path) -> None:
    path = write(tmp_path, "[circuit_caps]\nmax_ops = 50\ntimeout_s = 2.5\n")
    assert load_settings(path).circuit_caps.caps() == CircuitCaps(1024 * 1024, 50, 2.5)


@pytest.mark.parametrize(
    ("line", "key"),
    [
        ("max_body_bytes = 0", "max_body_bytes"),
        ("max_body_bytes = 33554433", "max_body_bytes"),
        ("max_body_bytes = 1.5", "max_body_bytes"),
        ("max_ops = 0", "max_ops"),
        ("max_ops = 10001", "max_ops"),
        ("max_ops = true", "max_ops"),
        ("timeout_s = 0", "timeout_s"),
        ("timeout_s = -1", "timeout_s"),
        ("timeout_s = 601", "timeout_s"),
        ("timeout_s = nan", "timeout_s"),
        ("timeout_s = true", "timeout_s"),
        ('timeout_s = "30"', "timeout_s"),
        ("max_op = 5", "max_op"),
    ],
)
def test_a_circuit_cap_out_of_range_is_refused_by_name(tmp_path: Path, line: str, key: str) -> None:
    with pytest.raises(SettingsError, match=rf"circuit_caps\.{key}"):
        load_settings(write(tmp_path, f"[circuit_caps]\n{line}\n"))


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
        "http://:pw@tool.example",
        "http://tool.example?",
        "http://tool.example#",
        "http://tool.example:99999",
        "http://tool.example:port",
    ],
)
def test_a_value_that_is_not_an_origin_is_refused(origin: str) -> None:
    with pytest.raises(ValueError, match="origin"):
        Settings(public_origin=origin)


@pytest.mark.parametrize(
    "origin",
    [
        "http://host:",
        "http://host:0",
        "http://host:00",
        "http://host:+80",
        "http://host:8 0",
        "http://host:123456",
        "http://h ost",
        "http://host;x",
        "http://host,x",
        "http://host\n",
        "http://ho\nst",
        "http://\thost",
        " http://host",
        "http://host ",
        "http://host.",
        "http://host..example",
        "http://.host",
        "http://-host",
        "http://host-",
        "http://ho_st",
        "http://h\u00f4st",
        "http://host:\uff18\uff10",
        "http://" + "a" * 64 + ".example",
        "http://1234",
        "http://1.2.3",
        "http://010.1.1.1",
        "http://256.1.1.1",
        "http://[::1",
        "http://[::g]",
        "http://[::1]x",
        "http://[::1%25en0]",
        "http://[]",
        "http:///host",
        "http://host:80:80",
        "https:/host",
    ],
)
def test_a_value_a_browser_never_sends_as_origin_is_refused(origin: str) -> None:
    with pytest.raises(ValueError, match="public_origin"):
        Settings(public_origin=origin)


@pytest.mark.parametrize(
    ("given", "origin"),
    [
        ("http://HOST:8090", "http://host:8090"),
        ("HTTP://host", "http://host"),
        ("Https://Tool.Example/", "https://tool.example"),
        ("http://host:080", "http://host"),
        ("http://host:80", "http://host"),
        ("https://host:443", "https://host"),
        ("https://host:80", "https://host:80"),
        ("http://host:08090", "http://host:8090"),
        ("http://127.0.0.1:8090", "http://127.0.0.1:8090"),
        ("http://[::1]:8090", "http://[::1]:8090"),
        ("http://[0:0:0:0:0:0:0:1]:8090", "http://[::1]:8090"),
        ("http://[FD7A:115C:A1E0::5]", "http://[fd7a:115c:a1e0::5]"),
        ("http://tool-2.example.org:65535", "http://tool-2.example.org:65535"),
        ("http://" + "a" * 63 + ".example", "http://" + "a" * 63 + ".example"),
    ],
)
def test_an_origin_is_written_the_way_a_browser_sends_it(given: str, origin: str) -> None:
    settings = Settings(public_origin=given)
    assert settings.public_origin == settings.origin == origin


def test_the_default_origin_leaves_out_the_default_port() -> None:
    assert Settings(port=80).origin == "http://127.0.0.1"


@pytest.mark.parametrize("port", [True, "8090", 8090.0])
def test_a_port_must_be_an_integer(port: object) -> None:
    with pytest.raises(ValueError, match="port"):
        Settings.model_validate({"port": port})


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


# --- the sign-in and storage keys -----------------------------------------------------------


def test_the_storage_keys_default_to_unset_and_the_session_keys_to_the_scope_values() -> None:
    settings = Settings.defaults()
    assert (settings.data_dir, settings.db_path, settings.log_dir) == (None, None, None)
    assert (settings.session_idle_hours, settings.session_max_days) == (12, 7)
    assert (settings.login_max_failures, settings.login_window_minutes) == (5, 15)


def test_the_storage_paths_are_read_as_paths(tmp_path: Path) -> None:
    settings = load_settings(
        write(
            tmp_path,
            'data_dir = "/srv/ra/data"\ndb_path = "/srv/ra/db/api.sqlite3"\nlog_dir = "/srv/ra/log"\n',
        )
    )
    assert settings.data_dir == Path("/srv/ra/data")
    assert settings.db_path == Path("/srv/ra/db/api.sqlite3")
    assert settings.log_dir == Path("/srv/ra/log")


def test_a_leading_tilde_is_expanded() -> None:
    assert Settings(data_dir=Path("~/ra")).data_dir == Path.home() / "ra"


@pytest.mark.parametrize("key", ["data_dir", "db_path", "log_dir"])
def test_a_relative_path_is_refused(key: str) -> None:
    with pytest.raises(ValueError, match="absolute path"):
        Settings.model_validate({key: "relative/path"})


@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("session_idle_hours", 0),
        ("session_idle_hours", 721),
        ("session_max_days", 0),
        ("session_max_days", 91),
        ("login_max_failures", 0),
        ("login_max_failures", 101),
        ("login_window_minutes", 0),
        ("login_window_minutes", 1441),
        ("session_idle_hours", 1.5),
        ("login_max_failures", "5"),
    ],
)
def test_the_limits_must_be_whole_numbers_in_range(key: str, bad: object) -> None:
    with pytest.raises(ValueError, match=key):
        Settings.model_validate({key: bad})


def test_an_idle_time_longer_than_the_session_is_refused() -> None:
    with pytest.raises(ValueError, match="session_idle_hours"):
        Settings(session_idle_hours=49, session_max_days=2)
    assert Settings(session_idle_hours=48, session_max_days=2).session_idle_hours == 48
