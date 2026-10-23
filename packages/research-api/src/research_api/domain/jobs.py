"""Benchmark jobs: what a job is, how its state may change, and who may change it.

A job is one run of one arm on one model over one frozen question set (``run``, in the GPU lane)
or one scoring of runs (``score``, in the CPU lane). The state machine here is the whole rulebook;
the application moves a job only through ``move_allowed``. Standard library only.

    queued -> running -> done | failed | interrupted
    queued -> cancelled
    running -> cancelling -> cancelled | done
    cancelled | interrupted -> queued            (resume)

``interrupted`` is a run that stopped without being told to: the process went away while it ran
(found at start-up) or the model server stopped answering. Its answers so far stay in the run
file and a resume continues after them.
"""

from __future__ import annotations

import re
import secrets
import threading
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from .errors import FieldError, ForbiddenError, InvalidInputError
from .sets import Actor, SetRef

JobKind = Literal["run", "score"]
KINDS: tuple[JobKind, ...] = ("run", "score")

ARMS = ("bare", "harness", "oracle")
MAX_REPEATS = 10
MAX_ARMS = 3
MAX_MODELS = 4
MAX_NOTES = 1000
MAX_VARIANT = 80
MAX_OPEN_PER_MEMBER = 12  # jobs waiting or running that one member may have at once
MAX_SCORE_RUNS = 12
MAX_PAIRS = 12

BM25_MODES = ("english", "always", "never")
HARNESS_OVERRIDES = {"use_index": "a boolean", "whole_blocks": "a boolean", "bm25": "a mode"}


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    DONE = "done"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.QUEUED: frozenset({JobState.RUNNING, JobState.CANCELLED}),
    JobState.RUNNING: frozenset(
        {JobState.DONE, JobState.FAILED, JobState.CANCELLING, JobState.INTERRUPTED}
    ),
    JobState.CANCELLING: frozenset({JobState.CANCELLED, JobState.DONE}),
    JobState.CANCELLED: frozenset({JobState.QUEUED}),
    JobState.INTERRUPTED: frozenset({JobState.QUEUED}),
    JobState.DONE: frozenset(),
    JobState.FAILED: frozenset(),
}
OPEN_STATES = frozenset({JobState.QUEUED, JobState.RUNNING, JobState.CANCELLING})
"""States that hold the lane or wait for it. Everything else has stopped."""
RESUMABLE_STATES = frozenset({JobState.CANCELLED, JobState.INTERRUPTED})
SETTLED_STATES = frozenset(set(JobState) - OPEN_STATES)
"""States a client may stop polling at (a cancelled or interrupted job waits for a resume)."""


def move_allowed(current: JobState, to: JobState) -> bool:
    return to in TRANSITIONS[current]


@dataclass(frozen=True)
class Progress:
    done: int
    total: int


@dataclass(frozen=True)
class JobError:
    kind: str
    message: str


@dataclass(frozen=True)
class ArmSpec:
    name: str
    variant: str | None = None
    overrides: Mapping[str, Any] = field(default_factory=dict)


def _bad(path: str, message: str) -> InvalidInputError:
    return InvalidInputError("The request is not valid.", (FieldError(path, message),))


def arm_spec(name: str, variant: str | None, overrides: Mapping[str, Any], *, at: str) -> ArmSpec:
    """The arm as the run header will hold it, or an ``InvalidInputError`` pointing at ``at``."""
    if name not in ARMS:
        raise _bad(f"{at}/name", f"The arm must be one of {', '.join(ARMS)}.")
    if variant is not None and not (1 <= len(variant) <= MAX_VARIANT):
        raise _bad(f"{at}/variant", f"The variant is 1 to {MAX_VARIANT} characters.")
    if name != "harness" and overrides:
        raise _bad(f"{at}/overrides", f"The {name} arm has no settings to override.")
    for key, value in overrides.items():
        if key not in HARNESS_OVERRIDES:
            raise _bad(
                f"{at}/overrides/{key}", f"Unknown setting; known: {', '.join(HARNESS_OVERRIDES)}."
            )
        if key == "bm25":
            if value not in BM25_MODES:
                raise _bad(f"{at}/overrides/{key}", f"bm25 is one of {', '.join(BM25_MODES)}.")
        elif not isinstance(value, bool):
            raise _bad(f"{at}/overrides/{key}", "This setting is true or false.")
    return ArmSpec(name, variant, dict(overrides))


@dataclass(frozen=True)
class RunParams:
    """What a run job was asked to do. ``set_md5`` is the frozen file it was started on."""

    set: SetRef
    set_md5: str
    arm: ArmSpec
    model: str
    repeats: int
    notes: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "set": {"name": self.set.name, "version": self.set.version, "md5": self.set_md5},
            "arm": {
                "name": self.arm.name,
                "variant": self.arm.variant,
                "overrides": dict(self.arm.overrides),
            },
            "model": self.model,
            "repeats": self.repeats,
            "notes": self.notes,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> RunParams:
        arm = data["arm"]
        return cls(
            set=SetRef(data["set"]["name"], data["set"]["version"]),
            set_md5=data["set"]["md5"],
            arm=ArmSpec(arm["name"], arm.get("variant"), arm.get("overrides") or {}),
            model=data["model"],
            repeats=data["repeats"],
            notes=data.get("notes") or "",
        )


@dataclass(frozen=True)
class ScoreOptions:
    """What to compute: pairs are (base run, variant run); ``noise`` is a pair of repeats of one
    arm. The engine names are the ones of ``qa-score/1``."""

    pairs: tuple[tuple[str, str], ...] = ()
    noise: tuple[str, str] | None = None
    group_by: tuple[str, ...] | None = None
    groups: tuple[str, ...] = ()
    engine: str = "python_random"
    number_words: bool = False


@dataclass(frozen=True)
class ScoreParams:
    set: SetRef
    runs: tuple[str, ...]
    options: ScoreOptions = field(default_factory=ScoreOptions)

    def to_json(self) -> dict[str, Any]:
        o = self.options
        return {
            "set": {"name": self.set.name, "version": self.set.version},
            "runs": list(self.runs),
            "pairs": [list(p) for p in o.pairs],
            "noise": list(o.noise) if o.noise else None,
            "group_by": list(o.group_by) if o.group_by is not None else None,
            "groups": list(o.groups),
            "engine": o.engine,
            "number_words": o.number_words,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> ScoreParams:
        noise = data.get("noise")
        group_by = data.get("group_by")
        return cls(
            set=SetRef(data["set"]["name"], data["set"]["version"]),
            runs=tuple(data["runs"]),
            options=ScoreOptions(
                pairs=tuple((a, b) for a, b in data.get("pairs") or []),
                noise=(noise[0], noise[1]) if noise else None,
                group_by=tuple(group_by) if group_by is not None else None,
                groups=tuple(data.get("groups") or []),
                engine=data.get("engine", "python_random"),
                number_words=bool(data.get("number_words", False)),
            ),
        )


Params = RunParams | ScoreParams


@dataclass(frozen=True)
class Job:
    id: str
    kind: JobKind
    state: JobState
    owner: str
    created_at: datetime
    queued_at: datetime
    params: Params
    progress: Progress
    started_at: datetime | None = None
    ended_at: datetime | None = None
    artifact_id: str | None = None
    """The run id or score id of the file the job writes."""
    error: JobError | None = None

    @property
    def settled(self) -> bool:
        return self.state in SETTLED_STATES

    def with_changes(self, **changes: Any) -> Job:
        return replace(self, **changes)


def require_owner_or_admin(actor: Actor, job: Job) -> None:
    if not actor.admin and actor.username != job.owner:
        raise ForbiddenError("Only the person who started this job, or an admin, can do that.")


# --- ids ------------------------------------------------------------------------------------

_CROCKFORD = "0123456789abcdefghjkmnpqrstvwxyz"
ID_LENGTH = 26
_ENTROPY_BITS = 80
RUN_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
_RUN_ID = re.compile(RUN_ID_PATTERN)
JOB_ID_PATTERN = r"[0-9a-hjkmnp-tv-z]{26}"
_JOB_ID = re.compile(JOB_ID_PATTERN)


def valid_run_id(text: str) -> bool:
    return _RUN_ID.fullmatch(text) is not None


def valid_job_id(text: str) -> bool:
    return _JOB_ID.fullmatch(text) is not None


def _encode(value: int) -> str:
    out = []
    for _ in range(ID_LENGTH):
        out.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(out))


class IdMaker:
    """Sortable ids: 48 bits of milliseconds then 80 of randomness, in 26 characters of base 32.

    Within one millisecond the random part counts up, so ids made in a row sort in the order they
    were made (the queue is first in, first out by id).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_ms = -1
        self._last_random = 0

    def new(self, now: datetime) -> str:
        ms = int(now.timestamp() * 1000)
        with self._lock:
            if ms <= self._last_ms:
                ms = self._last_ms
                self._last_random += 1
                if self._last_random >> _ENTROPY_BITS:  # the 80 bits ran out: next millisecond
                    ms += 1
                    self._last_random = secrets.randbits(_ENTROPY_BITS - 1)
            else:
                self._last_random = secrets.randbits(_ENTROPY_BITS - 1)  # room to count up
            self._last_ms = ms
            return _encode((ms << _ENTROPY_BITS) | self._last_random)


def run_id_for(arm: str, ref: SetRef, job_id: str) -> str:
    """A readable run id: ``harness-demo-papers-v1-3f9x2k7q`` (the tail of the job id)."""
    return f"{arm}-{ref.name}-{ref.version}-{job_id[-8:]}"


# --- the order of the questions ----------------------------------------------------------


def expected_keys(item_ids: Sequence[str], repeats: int) -> Iterator[tuple[str, int]]:
    """Every (id, repeat) of a run in the order it is asked: the whole set for repeat 1, then for
    repeat 2, so a run that stops early still has complete earlier repeats."""
    for repeat in range(1, repeats + 1):
        for item_id in item_ids:
            yield item_id, repeat


# --- notes the run header carries ---------------------------------------------------------

_INTERLEAVED = re.compile(r"Interleaved asks: (\d+)\.")
_INTERLEAVED_SENTENCE = re.compile(r" ?Interleaved asks: \d+\.")
_PARTIAL_SENTENCE = re.compile(r" ?Partial: [^.]*\.")


def interleaved_in(notes: str) -> int:
    found = _INTERLEAVED.search(notes)
    return int(found.group(1)) if found else 0


def base_notes(notes: str) -> str:
    """The member's own words in run-header notes, without the sentences the service adds."""
    notes = _INTERLEAVED_SENTENCE.sub("", notes)
    notes = _PARTIAL_SENTENCE.sub("", notes)
    return notes.strip()


def header_notes(base: str, interleaved: int, partial: str | None) -> str:
    """The notes of a run header: the member's own words, how many asks shared the GPU with the
    run, and, for a run that has not finished, how far it got."""
    parts = [base.strip()] if base.strip() else []
    parts.append(f"Interleaved asks: {interleaved}.")
    if partial:
        parts.append(f"Partial: {partial}.")
    return " ".join(parts)
