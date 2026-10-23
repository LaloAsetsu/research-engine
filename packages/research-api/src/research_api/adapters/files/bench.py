"""The files of the benchmark: runs, scores and job logs, under the private data folder.

    <root>/runs/<run_id>.qa-run.jsonl[.gz]      a run, appended line by line while it runs
    <root>/scores/<score_id>.qa-score.jsonl     one scoring
    <root>/jobs/<job_id>.log                    the log of a job, plain text lines

Every folder is private (0700) and every file 0600. A run file grows by appending whole lines
(one ``write`` each, flushed), so a crash leaves at most one half line, which a resume cuts off.
A file that is replaced (the header changes when a run ends) is written beside it and renamed.
Ids are checked against their patterns before they become a path.
"""

from __future__ import annotations

import contextlib
import os
import threading
from pathlib import Path

from ...domain.errors import ConflictError
from ...domain.jobs import valid_job_id, valid_run_id
from ...ports.jobs import LogChunk
from .private import FILE_MODE, ensure_private_dir, replace_file

RUN_SUFFIX = ".qa-run.jsonl"
RUN_SUFFIXES = (RUN_SUFFIX, RUN_SUFFIX + ".gz")
SCORE_SUFFIX = ".qa-score.jsonl"
LOG_SUFFIX = ".log"
MAX_LOG_READ = 1024 * 1024


class BenchFileStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._folders = threading.Lock()

    # --- paths ------------------------------------------------------------------------

    def _folder(self, name: str) -> Path:
        folder = self.root / name
        with self._folders:
            ensure_private_dir(self.root)
            ensure_private_dir(folder)
        return folder

    def _run_path(self, run_id: str) -> Path | None:
        """The file of a run, plain or gzip, whichever exists; the plain name when neither."""
        if not valid_run_id(run_id):
            raise ValueError(f"not a run id: {run_id!r}")
        folder = self.root / "runs"
        for suffix in RUN_SUFFIXES:
            if (folder / f"{run_id}{suffix}").exists():
                return folder / f"{run_id}{suffix}"
        return None

    def _plain_run_path(self, run_id: str) -> Path:
        if not valid_run_id(run_id):
            raise ValueError(f"not a run id: {run_id!r}")
        return self._folder("runs") / f"{run_id}{RUN_SUFFIX}"

    def _score_path(self, score_id: str) -> Path:
        if not valid_job_id(score_id):
            raise ValueError(f"not a score id: {score_id!r}")
        return self._folder("scores") / f"{score_id}{SCORE_SUFFIX}"

    def _log_path(self, job_id: str) -> Path:
        if not valid_job_id(job_id):
            raise ValueError(f"not a job id: {job_id!r}")
        return self._folder("jobs") / f"{job_id}{LOG_SUFFIX}"

    # --- runs -------------------------------------------------------------------------

    def run_ids(self) -> list[str]:
        folder = self.root / "runs"
        if not folder.is_dir():
            return []
        found = []
        for path in folder.iterdir():
            for suffix in RUN_SUFFIXES:
                if path.name.endswith(suffix):
                    run_id = path.name.removesuffix(suffix)
                    if valid_run_id(run_id):
                        found.append(run_id)
                    break
        return sorted(set(found))

    def read_run(self, run_id: str) -> bytes | None:
        path = self._run_path(run_id)
        if path is None:
            return None
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    def run_stamp(self, run_id: str) -> tuple[int, int] | None:
        path = self._run_path(run_id)
        if path is None:
            return None
        try:
            info = path.stat()
        except FileNotFoundError:
            return None
        return info.st_mtime_ns, info.st_size

    def create_run(self, run_id: str, data: bytes) -> None:
        if self._run_path(run_id) is not None:
            raise ConflictError(f"The run {run_id} already exists.")
        path = self._plain_run_path(run_id)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
        except FileExistsError:
            raise ConflictError(f"The run {run_id} already exists.") from None
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

    def append_run(self, run_id: str, data: bytes) -> None:
        path = self._run_path(run_id)
        if path is None or path.name.endswith(".gz"):
            raise FileNotFoundError(f"no plain run file {run_id}")
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND)
        with os.fdopen(descriptor, "ab") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

    def replace_run(self, run_id: str, data: bytes) -> None:
        path = self._plain_run_path(run_id)
        replace_file(path.parent, path, data)

    # --- scores -----------------------------------------------------------------------

    def score_ids(self) -> list[str]:
        folder = self.root / "scores"
        if not folder.is_dir():
            return []
        return sorted(
            p.name.removesuffix(SCORE_SUFFIX)
            for p in folder.iterdir()
            if p.name.endswith(SCORE_SUFFIX) and valid_job_id(p.name.removesuffix(SCORE_SUFFIX))
        )

    def read_score(self, score_id: str) -> bytes | None:
        if not valid_job_id(score_id):
            return None
        try:
            return (self.root / "scores" / f"{score_id}{SCORE_SUFFIX}").read_bytes()
        except FileNotFoundError:
            return None

    def write_score(self, score_id: str, data: bytes) -> None:
        path = self._score_path(score_id)
        replace_file(path.parent, path, data)

    # --- logs -------------------------------------------------------------------------

    def append_log(self, job_id: str, text: str) -> None:
        descriptor = os.open(
            self._log_path(job_id), os.O_WRONLY | os.O_APPEND | os.O_CREAT, FILE_MODE
        )
        with os.fdopen(descriptor, "ab") as stream:
            stream.write(text.encode("utf-8"))
            stream.flush()

    def read_log(self, job_id: str, offset: int, limit: int) -> LogChunk | None:
        """The log from byte ``offset``, at most ``limit`` bytes, cut at a line end so a reader
        that continues from ``next_offset`` never starts in the middle of a character."""
        if not valid_job_id(job_id):
            return None
        path = self.root / "jobs" / f"{job_id}{LOG_SUFFIX}"
        try:
            with path.open("rb") as stream:
                size = os.fstat(stream.fileno()).st_size
                stream.seek(min(max(offset, 0), size))
                data = stream.read(min(max(limit, 1), MAX_LOG_READ))
        except FileNotFoundError:
            return None
        start = min(max(offset, 0), size)
        if start + len(data) < size and b"\n" in data:
            data = data[: data.rfind(b"\n") + 1]
        with contextlib.suppress(UnicodeDecodeError):
            return LogChunk(data.decode("utf-8"), start + len(data), size)
        return LogChunk(data.decode("utf-8", errors="replace"), start + len(data), size)
