"""Private folders (0700) and files (0600).

The server's disk is shared and its parent folder is open to everyone, so a folder made with a
loose mode would expose accounts and corpus text. A folder or file that exists with any
group or other permission, or that belongs to someone else, is refused, never fixed behind
the owner's back; one that is missing is created private.
"""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile
from pathlib import Path

DIR_MODE = 0o700
FILE_MODE = 0o600


class PrivacyError(OSError):
    """A folder or file is readable by others, belongs to someone else, or is the wrong kind."""


def _refuse_if_loose(path: Path, info: os.stat_result, *, mode: int, kind: str) -> None:
    if info.st_uid != os.geteuid():
        raise PrivacyError(f"{kind} {path} belongs to another account")
    if info.st_mode & 0o077:
        raise PrivacyError(
            f"{kind} {path} is open to other accounts "
            f"(mode {stat.S_IMODE(info.st_mode):04o}); run: chmod {mode:o} {path}"
        )


def ensure_private_dir(path: Path) -> None:
    """Create ``path`` (and missing parents) private, or check that it already is."""
    if not path.exists():
        missing = [path, *path.parents]
        for folder in reversed([p for p in missing if not p.exists()]):
            folder.mkdir(mode=DIR_MODE)
            folder.chmod(DIR_MODE)  # mkdir's mode is cut by the umask; make it exact
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise PrivacyError(f"{path} is not a folder")
    _refuse_if_loose(path, info, mode=DIR_MODE, kind="folder")


def create_private_file(path: Path) -> None:
    """Create an empty file with mode 0600 if there is none."""
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
    except FileExistsError:
        return
    os.close(descriptor)


def ensure_private_file(path: Path, *, with_sqlite_siblings: bool = False) -> None:
    """Check that ``path`` is a file only this account can read (and its ``-wal``, ``-shm``)."""
    names = [path]
    if with_sqlite_siblings:
        names += [path.with_name(path.name + suffix) for suffix in ("-wal", "-shm", "-journal")]
    for name in names:
        if not name.exists():
            if name is path:
                raise PrivacyError(f"file {path} does not exist")
            continue
        info = name.stat()
        if not stat.S_ISREG(info.st_mode):
            raise PrivacyError(f"{name} is not a file")
        _refuse_if_loose(name, info, mode=FILE_MODE, kind="file")


def sync_folder(folder: Path) -> None:
    """Flush the folder's own entry, or a power cut can lose a rename."""
    descriptor = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def replace_file(folder: Path, target: Path, data: bytes) -> None:
    """Put ``data`` at ``target`` all at once: written beside it (mode 0600), flushed, renamed
    over it, and the folder flushed, so a reader never sees half of a file."""
    descriptor, temp_name = tempfile.mkstemp(dir=folder, prefix=".tmp-", suffix=".part")
    temp = Path(temp_name)
    try:
        os.fchmod(descriptor, FILE_MODE)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(target)
        sync_folder(folder)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            temp.unlink()
        raise
