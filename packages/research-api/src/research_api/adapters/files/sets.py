"""The files of the question sets: ``<root>/<name>/<version>.qset.jsonl`` and its reviews.

Every file is private (mode 0600) in a private folder. A file is replaced all at once (written
beside it, flushed, renamed over it, and the folder flushed), so a reader never sees half of a set.
Reviews are only ever appended.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import threading
from pathlib import Path

from ...domain.sets import SetRef, valid_name, valid_version
from .private import FILE_MODE, ensure_private_dir

SET_SUFFIX = ".qset.jsonl"
REVIEWS_SUFFIX = ".reviews.jsonl"


class SetFileStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._folders = threading.Lock()  # making a folder is not safe against itself

    def _folder(self, ref: SetRef) -> Path:
        # The patterns keep a name from being a path; this is the second lock on the same door.
        if not valid_name(ref.name) or not valid_version(ref.version):
            raise ValueError(f"not a set reference: {ref.name!r} {ref.version!r}")
        return self.root / ref.name

    def _path(self, ref: SetRef, suffix: str) -> Path:
        return self._folder(ref) / f"{ref.version}{suffix}"

    def _make_folder(self, ref: SetRef) -> Path:
        folder = self._folder(ref)
        with self._folders:
            ensure_private_dir(self.root)
            ensure_private_dir(folder)
        return folder

    @staticmethod
    def _read(path: Path) -> bytes | None:
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    def read(self, ref: SetRef) -> bytes | None:
        return self._read(self._path(ref, SET_SUFFIX))

    def write(self, ref: SetRef, data: bytes) -> None:
        folder = self._make_folder(ref)
        self._replace(folder, self._path(ref, SET_SUFFIX), data)

    def _copy_path(self, ref: SetRef, label: str) -> Path:
        if not label.isalnum():
            raise ValueError("a copy label is letters and digits only")
        return self._folder(ref) / f"{ref.version}.{label}{SET_SUFFIX}"

    def keep_copy(self, ref: SetRef, label: str, data: bytes) -> None:
        folder = self._make_folder(ref)
        self._replace(folder, self._copy_path(ref, label), data)

    def read_copy(self, ref: SetRef, label: str) -> bytes | None:
        return self._read(self._copy_path(ref, label))

    def read_reviews(self, ref: SetRef) -> bytes | None:
        return self._read(self._path(ref, REVIEWS_SUFFIX))

    def write_reviews(self, ref: SetRef, data: bytes) -> None:
        folder = self._make_folder(ref)
        self._replace(folder, self._path(ref, REVIEWS_SUFFIX), data)

    def append_reviews(self, ref: SetRef, data: bytes) -> None:
        self._make_folder(ref)
        descriptor = os.open(
            self._path(ref, REVIEWS_SUFFIX), os.O_WRONLY | os.O_APPEND | os.O_CREAT, FILE_MODE
        )
        with os.fdopen(descriptor, "ab") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _sync_folder(folder: Path) -> None:
        """Flush the folder's own entry, or a power cut can lose the rename."""
        descriptor = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _replace(folder: Path, target: Path, data: bytes) -> None:
        descriptor, temp_name = tempfile.mkstemp(dir=folder, prefix=".tmp-", suffix=".part")
        temp = Path(temp_name)
        try:
            os.fchmod(descriptor, FILE_MODE)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            temp.replace(target)
            SetFileStore._sync_folder(folder)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                temp.unlink()
            raise
