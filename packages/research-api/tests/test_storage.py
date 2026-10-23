from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from helpers import live_settings, prepare_live

from research_api.bootstrap import (
    Settings,
    SettingsError,
    build_container,
    open_database,
    open_user_admin,
)
from research_api.bootstrap.storage import open_current_database


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_both_storage_keys_are_needed() -> None:
    with pytest.raises(SettingsError, match="data_dir and db_path"):
        open_database(Settings.defaults())
    with pytest.raises(SettingsError, match="data_dir and db_path"):
        open_database(Settings(data_dir=Path("/tmp/x")))


def test_db_upgrade_makes_private_folders_and_a_private_file(tmp_path: Path) -> None:
    settings = live_settings(tmp_path, log_dir=tmp_path / "logs")
    database = open_database(settings, create=True)
    database.upgrade()
    database.close()
    assert settings.data_dir and settings.db_path and settings.log_dir
    assert (
        mode(settings.data_dir) == mode(settings.db_path.parent) == mode(settings.log_dir) == 0o700
    )
    assert mode(settings.db_path) == 0o600
    for side in ("-wal", "-shm"):
        side_file = settings.db_path.with_name(settings.db_path.name + side)
        if side_file.exists():
            assert mode(side_file) == 0o600


def test_opening_without_create_never_makes_the_database(tmp_path: Path) -> None:
    settings = live_settings(tmp_path)
    with pytest.raises(SettingsError, match="research-api db upgrade"):
        open_database(settings)
    assert settings.db_path and not settings.db_path.exists()


def test_an_open_data_folder_stops_the_start(tmp_path: Path) -> None:
    settings = prepare_live(live_settings(tmp_path))
    assert settings.data_dir
    settings.data_dir.chmod(0o755)
    with pytest.raises(SettingsError, match=r"data.*chmod 700"):
        build_container(settings)


def test_an_open_database_folder_stops_the_start(tmp_path: Path) -> None:
    settings = prepare_live(live_settings(tmp_path))
    assert settings.db_path
    settings.db_path.parent.chmod(0o750)
    with pytest.raises(SettingsError, match="chmod 700"):
        build_container(settings)


def test_an_open_log_folder_stops_the_start(tmp_path: Path) -> None:
    settings = prepare_live(live_settings(tmp_path, log_dir=tmp_path / "logs"))
    assert settings.log_dir
    settings.log_dir.chmod(0o755)
    with pytest.raises(SettingsError, match="logs"):
        build_container(settings)


def test_an_open_database_file_stops_the_start(tmp_path: Path) -> None:
    settings = prepare_live(live_settings(tmp_path))
    assert settings.db_path
    settings.db_path.chmod(0o644)
    with pytest.raises(SettingsError, match="chmod 600"):
        build_container(settings)


def test_an_open_wal_file_stops_the_start(tmp_path: Path) -> None:
    settings = prepare_live(live_settings(tmp_path))
    assert settings.db_path
    wal = settings.db_path.with_name(settings.db_path.name + "-wal")
    wal.write_bytes(b"")
    wal.chmod(0o644)
    with pytest.raises(SettingsError, match="-wal"):
        build_container(settings)


def test_a_folder_that_belongs_to_someone_else_stops_the_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = prepare_live(live_settings(tmp_path))
    monkeypatch.setattr(os, "geteuid", lambda: os.getuid() + 1)
    with pytest.raises(SettingsError, match="another account"):
        build_container(settings)


def test_a_database_that_is_behind_the_code_stops_the_start(tmp_path: Path) -> None:
    settings = live_settings(tmp_path)
    database = open_database(settings, create=True)  # file exists, no tables yet
    database.close()
    with pytest.raises(SettingsError, match="not up to date; run: research-api db upgrade"):
        build_container(settings)
    with pytest.raises(SettingsError, match="db upgrade"):
        open_user_admin(settings)


def test_a_file_that_is_not_a_database_stops_the_start_with_a_message(tmp_path: Path) -> None:
    settings = live_settings(tmp_path)
    database = open_database(settings, create=True)
    database.close()
    assert settings.db_path
    settings.db_path.write_bytes(b"this is not a sqlite file at all, just text" * 10)
    settings.db_path.chmod(0o600)
    with pytest.raises(SettingsError, match="cannot read the database"):
        open_current_database(settings)


def test_a_current_database_starts_and_the_container_closes_cleanly(tmp_path: Path) -> None:
    container = build_container(prepare_live(live_settings(tmp_path)), cheap_hashing=True)
    assert container.mode == "live"
    assert container.auth.users.all() == []
    container.close()
    container.close()  # closing twice is fine
