"""``research-api users`` and ``research-api db``, run the way the administrator runs them."""

from __future__ import annotations

import json
import sqlite3
import stat
from pathlib import Path
from typing import Any

import pytest

from research_api.bootstrap import build_container, load_settings
from research_api.domain.auth import ONE_TIME_ALPHABET, ONE_TIME_LENGTH
from research_api.entrypoints import cli

Capture = pytest.CaptureFixture[str]


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A settings file for a private folder, its database already made (what `db upgrade` does)."""
    monkeypatch.delenv("RESEARCH_API_CONFIG", raising=False)
    home = tmp_path / "home"
    path = tmp_path / "api.toml"
    path.write_text(
        f'data_dir = "{home}/data"\ndb_path = "{home}/db/api.sqlite3"\nlog_dir = "{home}/logs"\n',
        encoding="utf-8",
    )
    assert cli.main(["db", "upgrade", "--config", str(path)]) == 0
    return path


def run(config: Path, *args: str) -> int:
    return cli.main([*args, "--config", str(config)])


def make_admin(config: Path) -> None:
    assert run(config, "users", "add", "ana", "--name", "Ana", "--role", "admin") == 0


# --- db -------------------------------------------------------------------------------------


def test_db_upgrade_makes_the_private_folders_and_the_tables(
    tmp_path: Path, capsys: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RESEARCH_API_CONFIG", raising=False)
    home = tmp_path / "fresh"
    path = tmp_path / "api.toml"
    path.write_text(
        f'data_dir = "{home}/data"\ndb_path = "{home}/db/api.sqlite3"\n', encoding="utf-8"
    )
    assert cli.main(["db", "status", "--config", str(path)]) == 1
    assert "db upgrade" in capsys.readouterr().err
    assert cli.main(["db", "upgrade", "--config", str(path)]) == 0
    assert "revision 0001" in capsys.readouterr().out
    assert stat.S_IMODE((home / "db" / "api.sqlite3").stat().st_mode) == 0o600
    assert stat.S_IMODE((home / "db").stat().st_mode) == 0o700
    assert stat.S_IMODE((home / "data").stat().st_mode) == 0o700
    assert cli.main(["db", "status", "--config", str(path)]) == 0
    assert "revision 0001; code expects 0001" in capsys.readouterr().out


def test_db_upgrade_twice_is_fine(config: Path, capsys: Capture) -> None:
    assert run(config, "db", "upgrade") == 0
    assert "0001" in capsys.readouterr().out


def test_db_status_says_when_the_tables_are_behind(config: Path, capsys: Capture) -> None:
    settings = load_settings(config)
    assert settings.db_path
    with sqlite3.connect(settings.db_path) as connection:
        connection.execute("delete from alembic_version")
    capsys.readouterr()
    assert run(config, "db", "status") == 1
    assert "revision none; code expects 0001" in capsys.readouterr().out


def test_db_commands_need_a_settings_file(capsys: Capture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RESEARCH_API_CONFIG", raising=False)
    assert cli.main(["db", "upgrade"]) == 1
    assert "--config" in capsys.readouterr().err


def test_db_upgrade_refuses_an_open_folder(tmp_path: Path, capsys: Capture) -> None:
    home = tmp_path / "home"
    (home / "db").mkdir(parents=True, mode=0o755)
    (home / "db").chmod(0o755)
    path = tmp_path / "api.toml"
    path.write_text(
        f'data_dir = "{home}/data"\ndb_path = "{home}/db/api.sqlite3"\n', encoding="utf-8"
    )
    assert cli.main(["db", "upgrade", "--config", str(path)]) == 1
    assert "chmod 700" in capsys.readouterr().err
    assert not (home / "db" / "api.sqlite3").exists()


# --- users add ------------------------------------------------------------------------------


def test_add_prints_the_one_time_password_alone_on_standard_output(
    config: Path, capsys: Capture
) -> None:
    capsys.readouterr()
    assert run(config, "users", "add", "marta", "--name", "Marta Pérez", "--language", "en") == 0
    out, err = capsys.readouterr()
    password = out.strip()
    assert out == password + "\n"
    assert len(password) == ONE_TIME_LENGTH and set(password) <= set(ONE_TIME_ALPHABET)
    assert 'Created "marta" (member)' in err
    assert "only now" in err and "change it" in err
    assert password not in err


def test_the_made_account_signs_in_with_that_password_and_must_change_it(
    config: Path, capsys: Capture
) -> None:
    capsys.readouterr()
    run(config, "users", "add", "marta", "--name", "Marta")
    password = capsys.readouterr().out.strip()
    container = build_container(load_settings(config), cheap_hashing=True)
    opened = container.auth.sign_in("marta", password, "10.0.0.1")
    assert opened.user.must_change_password and opened.user.language == "es"
    container.close()


def test_the_password_is_stored_only_as_a_hash(config: Path, capsys: Capture) -> None:
    capsys.readouterr()
    run(config, "users", "add", "marta", "--name", "Marta")
    password = capsys.readouterr().out.strip()
    settings = load_settings(config)
    assert settings.db_path
    assert password.encode() not in settings.db_path.read_bytes()
    wal = settings.db_path.with_name(settings.db_path.name + "-wal")
    if wal.exists():
        assert password.encode() not in wal.read_bytes()
    assert b"$argon2id$" in settings.db_path.read_bytes() + (
        wal.read_bytes() if wal.exists() else b""
    )


def test_two_accounts_get_different_passwords(config: Path, capsys: Capture) -> None:
    capsys.readouterr()
    run(config, "users", "add", "a1", "--name", "A")
    run(config, "users", "add", "a2", "--name", "B")
    first, second = capsys.readouterr().out.split()
    assert first != second


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["bad name", "--name", "X"], "username is not valid"),
        (["ok", "--name", "   "], "display name is not valid"),
        (["ana", "--name", "Other Ana"], "already taken"),
    ],
)
def test_add_refuses_bad_input_with_a_reason(
    config: Path, capsys: Capture, args: list[str], message: str
) -> None:
    make_admin(config)
    capsys.readouterr()
    assert run(config, "users", "add", *args) == 1
    out, err = capsys.readouterr()
    assert out == ""  # no password is printed for an account that was not made
    assert message in err


def test_add_needs_a_name_and_a_known_role(config: Path) -> None:
    with pytest.raises(SystemExit) as stop:
        run(config, "users", "add", "x")
    assert stop.value.code == 2
    with pytest.raises(SystemExit) as stop:
        run(config, "users", "add", "x", "--name", "X", "--role", "root")
    assert stop.value.code == 2
    with pytest.raises(SystemExit) as stop:
        run(config, "users", "add", "x", "--name", "X", "--language", "fr")
    assert stop.value.code == 2


# --- the other actions ----------------------------------------------------------------------


def test_list_shows_every_account_with_its_state(config: Path, capsys: Capture) -> None:
    make_admin(config)
    run(config, "users", "add", "beto", "--name", "Beto Prueba")
    run(config, "users", "disable", "beto")
    capsys.readouterr()
    assert run(config, "users", "list") == 0
    rows = [line.split("\t") for line in capsys.readouterr().out.splitlines()]
    assert rows[0][:4] == ["ana", "admin", "active", "es"]
    assert rows[1][:4] == ["beto", "member", "disabled", "es"]
    assert rows[1][4].startswith("Beto Prueba")
    assert "must-change-password" in rows[1][4]


def test_list_of_nobody_prints_nothing(config: Path, capsys: Capture) -> None:
    capsys.readouterr()
    assert run(config, "users", "list") == 0
    assert capsys.readouterr().out == ""


def test_disable_enable_and_role(config: Path, capsys: Capture) -> None:
    make_admin(config)
    run(config, "users", "add", "beto", "--name", "Beto")
    capsys.readouterr()
    assert run(config, "users", "disable", "BETO") == 0
    assert run(config, "users", "enable", "beto") == 0
    assert run(config, "users", "role", "beto", "admin") == 0
    out = capsys.readouterr().out
    assert "disabled beto" in out and "enabled beto" in out and "beto is now admin" in out


def test_reset_prints_a_new_one_time_password(config: Path, capsys: Capture) -> None:
    capsys.readouterr()
    run(config, "users", "add", "beto", "--name", "Beto")
    old = capsys.readouterr().out.strip()
    assert run(config, "users", "reset", "beto") == 0
    out, err = capsys.readouterr()
    new = out.strip()
    assert new != old and len(new) == ONE_TIME_LENGTH
    assert 'Reset "beto"' in err
    container = build_container(load_settings(config), cheap_hashing=True)
    assert container.auth.sign_in("beto", new, "10.0.0.1")
    container.close()


@pytest.mark.parametrize("action", ["disable", "enable", "reset"])
def test_an_unknown_account_is_an_error_not_a_crash(
    config: Path, capsys: Capture, action: str
) -> None:
    assert run(config, "users", action, "ghost") == 1
    assert "No user named 'ghost'" in capsys.readouterr().err
    assert run(config, "users", "role", "ghost", "admin") == 1


def test_the_last_admin_cannot_be_disabled_or_demoted(config: Path, capsys: Capture) -> None:
    make_admin(config)
    capsys.readouterr()
    assert run(config, "users", "disable", "ana") == 1
    assert run(config, "users", "role", "ana", "member") == 1
    assert "only active admin" in capsys.readouterr().err


# --- setting up -----------------------------------------------------------------------------


def test_users_commands_take_the_settings_from_the_environment(
    config: Path, capsys: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RESEARCH_API_CONFIG", str(config))
    capsys.readouterr()
    assert cli.main(["users", "add", "env", "--name", "Env"]) == 0
    assert cli.main(["users", "list"]) == 0
    assert "env\tmember" in capsys.readouterr().out


def test_users_commands_need_settings(capsys: Capture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RESEARCH_API_CONFIG", raising=False)
    assert cli.main(["users", "list"]) == 1
    assert "--config" in capsys.readouterr().err


def test_users_commands_refuse_a_database_that_was_never_made(
    tmp_path: Path, capsys: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RESEARCH_API_CONFIG", raising=False)
    path = tmp_path / "api.toml"
    home = tmp_path / "h"
    path.write_text(f'data_dir = "{home}/d"\ndb_path = "{home}/db/a.sqlite3"\n', encoding="utf-8")
    assert cli.main(["users", "add", "x", "--name", "X", "--config", str(path)]) == 1
    assert "db upgrade" in capsys.readouterr().err


def test_users_commands_refuse_open_folders(config: Path, capsys: Capture) -> None:
    settings = load_settings(config)
    assert settings.data_dir
    settings.data_dir.chmod(0o755)
    assert run(config, "users", "list") == 1
    assert "chmod 700" in capsys.readouterr().err


# --- the audit trail of the commands --------------------------------------------------------


def test_each_command_leaves_a_line_in_the_private_log_and_no_password(
    config: Path, capsys: Capture
) -> None:
    capsys.readouterr()
    run(config, "users", "add", "marta", "--name", "Marta")
    password = capsys.readouterr().out.strip()
    run(config, "users", "disable", "marta")
    run(config, "users", "enable", "marta")
    run(config, "users", "role", "marta", "admin")
    run(config, "users", "reset", "marta")
    reset_password = capsys.readouterr().out.strip()
    settings = load_settings(config)
    assert settings.log_dir
    log = settings.log_dir / "api.log"
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    text = log.read_text(encoding="utf-8")
    entries: list[dict[str, Any]] = [json.loads(line) for line in text.splitlines()]
    assert [e["event"] for e in entries] == [
        "user.created",
        "user.disabled",
        "user.enabled",
        "user.role",
        "user.reset",
    ]
    assert {e["actor"] for e in entries} == {"cli"}
    assert {e["username"] for e in entries} == {"marta"}
    assert password not in text and reset_password not in text
