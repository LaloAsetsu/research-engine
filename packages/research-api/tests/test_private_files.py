from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from research_api.adapters.files import (
    PrivacyError,
    create_private_file,
    ensure_private_dir,
    ensure_private_file,
)


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_a_missing_folder_is_made_private_with_its_missing_parents(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "c"
    ensure_private_dir(target)
    assert target.is_dir()
    assert [mode(p) for p in (tmp_path / "a", tmp_path / "a" / "b", target)] == [0o700] * 3


def test_the_umask_does_not_loosen_a_new_folder(tmp_path: Path) -> None:
    old = os.umask(0)
    try:
        ensure_private_dir(tmp_path / "x")
    finally:
        os.umask(old)
    assert mode(tmp_path / "x") == 0o700


def test_a_private_folder_passes_and_is_left_alone(tmp_path: Path) -> None:
    folder = tmp_path / "ok"
    folder.mkdir(mode=0o700)
    (folder / "keep").write_text("x")
    ensure_private_dir(folder)
    assert (folder / "keep").read_text() == "x"


@pytest.mark.parametrize("loose", [0o755, 0o750, 0o701, 0o777, 0o770])
def test_a_folder_open_to_group_or_others_is_refused_and_not_fixed(
    tmp_path: Path, loose: int
) -> None:
    folder = tmp_path / "open"
    folder.mkdir()
    folder.chmod(loose)
    with pytest.raises(PrivacyError, match="chmod 700"):
        ensure_private_dir(folder)
    assert mode(folder) == loose


def test_a_file_in_place_of_a_folder_is_refused(tmp_path: Path) -> None:
    (tmp_path / "f").write_text("x")
    with pytest.raises(PrivacyError, match="not a folder"):
        ensure_private_dir(tmp_path / "f")


def test_a_folder_of_another_account_is_refused(tmp_path: Path, monkeypatch) -> None:
    folder = tmp_path / "theirs"
    folder.mkdir(mode=0o700)
    monkeypatch.setattr(os, "geteuid", lambda: os.stat(folder).st_uid + 1)
    with pytest.raises(PrivacyError, match="another account"):
        ensure_private_dir(folder)


def test_a_new_file_is_0600_whatever_the_umask(tmp_path: Path) -> None:
    old = os.umask(0)
    try:
        create_private_file(tmp_path / "db")
    finally:
        os.umask(old)
    assert mode(tmp_path / "db") == 0o600


def test_creating_a_file_that_exists_keeps_its_content(tmp_path: Path) -> None:
    path = tmp_path / "db"
    path.write_text("data")
    path.chmod(0o600)
    create_private_file(path)
    assert path.read_text() == "data"


@pytest.mark.parametrize("loose", [0o644, 0o640, 0o604, 0o666])
def test_a_file_open_to_others_is_refused(tmp_path: Path, loose: int) -> None:
    path = tmp_path / "db"
    path.write_text("x")
    path.chmod(loose)
    with pytest.raises(PrivacyError, match="chmod 600"):
        ensure_private_file(path)


def test_a_private_file_passes(tmp_path: Path) -> None:
    path = tmp_path / "db"
    create_private_file(path)
    ensure_private_file(path)


def test_a_missing_file_and_a_folder_in_its_place_are_refused(tmp_path: Path) -> None:
    with pytest.raises(PrivacyError, match="does not exist"):
        ensure_private_file(tmp_path / "nope")
    (tmp_path / "dir").mkdir(mode=0o700)
    with pytest.raises(PrivacyError, match="not a file"):
        ensure_private_file(tmp_path / "dir")


def test_the_sqlite_side_files_are_checked_when_asked(tmp_path: Path) -> None:
    db = tmp_path / "db"
    create_private_file(db)
    wal = tmp_path / "db-wal"
    wal.write_text("x")
    wal.chmod(0o644)
    ensure_private_file(db)  # without the option the side files are not looked at
    with pytest.raises(PrivacyError, match="db-wal"):
        ensure_private_file(db, with_sqlite_siblings=True)
