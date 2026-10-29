"""Ports of the benchmark jobs: the job table, the files of runs and scores, their formats, the
scorer, and the arms that answer a question."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Protocol

from ..domain.jobs import Job, JobError, JobKind, JobState, Progress, ScoreOptions
from ..domain.runs import RunAnswer
from ..domain.sets import SetRef


class Keep:
    """The marker for a column ``JobRepo.move`` leaves as it is."""


KEEP: Final = Keep()


class JobRepo(Protocol):
    def add(self, job: Job) -> None: ...

    def get(self, job_id: str) -> Job | None: ...

    def list(
        self,
        *,
        states: Collection[JobState] | None = None,
        kind: JobKind | None = None,
        owner: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Job]:
        """Newest first (by id)."""
        ...

    def queued(self, kind: JobKind) -> list[Job]:
        """Jobs waiting for their lane, first in first out."""
        ...

    def open_count(self, owner: str) -> int:
        """Jobs of ``owner`` that are queued, running or being cancelled."""
        ...

    def move(
        self,
        job_id: str,
        *,
        expected: Collection[JobState],
        to: JobState,
        queued_at: datetime | Keep = KEEP,
        started_at: datetime | Keep | None = KEEP,
        ended_at: datetime | Keep | None = KEEP,
        error: JobError | Keep | None = KEEP,
        artifact_id: str | Keep | None = KEEP,
        progress: Progress | Keep = KEEP,
    ) -> Job | None:
        """Change the state if it is one of ``expected`` right now (one atomic statement), and
        any column given. None when the job is gone or was not in an expected state."""
        ...

    def set_progress(self, job_id: str, progress: Progress) -> None: ...

    def recover(self, now: datetime) -> list[Job]:
        """At start-up: a running job becomes ``interrupted`` and one being cancelled becomes
        ``cancelled``, with ``ended_at`` ``now``. Returns the jobs it changed."""
        ...


@dataclass(frozen=True)
class LogChunk:
    text: str
    next_offset: int
    size: int


class BenchFiles(Protocol):
    """The files under the private data folder: runs, scores and job logs. Bytes only."""

    def run_ids(self) -> list[str]: ...

    def read_run(self, run_id: str) -> bytes | None: ...

    def run_stamp(self, run_id: str) -> tuple[int, int] | None:
        """(modification time in ns, size) to tell a changed file from an unchanged one."""
        ...

    def create_run(self, run_id: str, data: bytes) -> None:
        """A new run file; ``ConflictError`` when the run id is taken."""
        ...

    def append_run(self, run_id: str, data: bytes) -> None: ...

    def replace_run(self, run_id: str, data: bytes) -> None: ...

    def score_ids(self) -> list[str]: ...

    def read_score(self, score_id: str) -> bytes | None: ...

    def write_score(self, score_id: str, data: bytes) -> None: ...

    def append_log(self, job_id: str, text: str) -> None: ...

    def read_log(self, job_id: str, offset: int, limit: int) -> LogChunk | None: ...


@dataclass(frozen=True)
class RunData:
    """A run file read leniently: the header, the complete item lines, and where the last
    complete line ends (a half line left by a crash is not part of it). ``md5`` is the file's
    identity as the formats define it (of the unzipped bytes, half line included), the value a
    scoring records for the run."""

    header: dict[str, Any]
    items: tuple[dict[str, Any], ...]
    complete_bytes: int
    md5: str


class RunFormats(Protocol):
    def header_line(self, header: Mapping[str, Any]) -> bytes:
        """The header as a line ending in a line feed. ``ValueError`` when the format refuses it
        (that is a bug here, not bad input)."""
        ...

    def item_line(self, item: Mapping[str, Any]) -> bytes: ...

    def item_fits(self, item: Mapping[str, Any]) -> bool:
        """Whether a line read from a file follows ``qa-run/1`` as an item line."""
        ...

    def read(self, data: bytes) -> RunData:
        """Header and items of a run file (plain or gzip) without checking every item line again.
        ``CorruptStoreError`` when there is no readable header, or the header is not a valid
        ``qa-run/1`` header (a file dropped by hand may have any JSON on its first line)."""
        ...


@dataclass(frozen=True)
class ScoredFile:
    data: bytes
    header: dict[str, Any]
    records: int
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ScoreData:
    header: dict[str, Any]
    records: tuple[dict[str, Any], ...]


class Scorer(Protocol):
    def score(
        self,
        set_data: bytes,
        runs: Sequence[tuple[str, bytes]],
        options: ScoreOptions,
        created: str,
    ) -> ScoredFile:
        """Score runs (named by file, with their bytes) against a set file. ``InvalidInputError``
        when they do not fit together (a run of another set, a pair that names a run not given)."""
        ...

    def read(self, data: bytes) -> ScoreData:
        """A score file this service wrote."""
        ...


@dataclass(frozen=True)
class ArmCall:
    """One question for an arm: the item of the set, and the run it belongs to."""

    arm_name: str
    variant: str | None
    overrides: Mapping[str, Any]
    model: str
    item: Mapping[str, Any]
    repeat: int
    set_ref: SetRef


class Arms(Protocol):
    def refusal(self, arm_name: str) -> str | None:
        """Why this arm cannot run at all in this service, or None."""
        ...

    async def waiting_for(self, arm_name: str, model: str) -> str | None:
        """Why a run of this arm should wait a while (a model server that does not answer, an
        engine still starting), or None when it can start."""
        ...

    def prompt_md5(self, arm_name: str) -> str | None: ...

    async def answer(self, call: ArmCall) -> RunAnswer:
        """Answer one question. ``UnavailableError`` when the model server went away (the run is
        interrupted), ``UpstreamError``, ``InvalidInputError`` and ``TimeoutError`` for this
        question only (it is recorded as an error and the run goes on)."""
        ...
