"""Use cases: start, follow, cancel and resume benchmark runs and scorings.

``JobDesk`` is the scheduler. Runs go through the GPU lane the asks use: one run at a time (the
next waits, first in first out), and inside a run one question at a time, each question a ticket
of its own with a lower priority than an ask, so an ask that arrives goes before the run's next
question and the run notes how many it let through. A scoring goes through the CPU lane, in a
worker thread, in parallel with the GPU lane.

A run appends each answer to its file as it gets it and flushes, so whatever stops it (a cancel, a
model server that goes away, the process) leaves a file that a resume continues, after the last
whole line, at the first (question, repeat) that is missing. Cancelling is cooperative: it takes
effect between questions, so no half answer is ever written.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..domain.errors import (
    ConflictError,
    CorruptStoreError,
    FieldError,
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
    ResearchApiError,
    UnavailableError,
)
from ..domain.jobs import (
    MAX_ARMS,
    MAX_MODELS,
    MAX_NOTES,
    MAX_OPEN_PER_MEMBER,
    MAX_PAIRS,
    MAX_REPEATS,
    MAX_SCORE_RUNS,
    OPEN_STATES,
    RESUMABLE_STATES,
    ArmSpec,
    IdMaker,
    Job,
    JobError,
    JobKind,
    JobState,
    Progress,
    RunParams,
    ScoreOptions,
    ScoreParams,
    base_notes,
    expected_keys,
    header_notes,
    interleaved_in,
    require_owner_or_admin,
    run_id_for,
    valid_job_id,
    valid_run_id,
)
from ..domain.runs import (
    AnswerError,
    ModelFacts,
    RunAnswer,
    RunHeaderInput,
    header_record,
    item_record,
    partial_text,
)
from ..domain.sets import Actor, SetRef
from ..ports.clock import Clock
from ..ports.jobs import (
    ArmCall,
    Arms,
    BenchFiles,
    JobRepo,
    LogChunk,
    RunData,
    RunFormats,
    Scorer,
)
from ..ports.models import ModelServer
from ..ports.sets import SetFormats
from .lanes import ASK_PRIORITY, RUN_PRIORITY, Lane
from .sets import ManageSets

log = logging.getLogger("research_api.jobs")

RECHECK_S = 5.0
SCHEDULER_RETRY_S = 1.0
LOG_PAGE = 64 * 1024
DEFAULT_PAGE = 50
MAX_PAGE = 200
BUSY_RETRY_S = 60
MODEL_RETRY_S = 30
MESSAGE_CHARS = 300


@dataclass(frozen=True)
class RunRequest:
    set: SetRef
    arms: tuple[ArmSpec, ...]
    models: tuple[str, ...]
    repeats: int = 1
    notes: str = ""


@dataclass(frozen=True)
class ScoreRequest:
    set: SetRef
    runs: tuple[str, ...]
    options: ScoreOptions = field(default_factory=ScoreOptions)


@dataclass(frozen=True)
class JobView:
    job: Job
    position: int | None = None
    """For a queued run: how many jobs are ahead (the running one counts)."""
    waiting_for: str | None = None
    """Why a queued run has not started although it is first: a model server that is down."""


@dataclass(frozen=True)
class BenchStatus:
    running_job: str | None
    queued_runs: int
    scoring: bool
    queued_scores: int


def _bad(path: str, message: str) -> InvalidInputError:
    return InvalidInputError("The request is not valid.", (FieldError(path, message),))


def _short(text: str) -> str:
    return text if len(text) <= MESSAGE_CHARS else text[: MESSAGE_CHARS - 1] + "."


def _reasons(error: ResearchApiError) -> str:
    extra = " ".join(e.message for e in getattr(error, "errors", ()))
    return _short(" ".join(p for p in (error.detail, extra) if p) or error.slug)


class JobDesk:
    def __init__(
        self,
        *,
        repo: JobRepo,
        files: BenchFiles,
        formats: RunFormats,
        scorer: Scorer,
        sets: ManageSets,
        set_formats: SetFormats,
        arms: Arms,
        models: ModelServer,
        clock: Clock,
        gpu: Lane,
        cpu: Lane | None = None,
        runner: str = "research-api",
        recheck_s: float = RECHECK_S,
    ) -> None:
        self._repo = repo
        self._files = files
        self._formats = formats
        self._scorer = scorer
        self._sets = sets
        self._set_formats = set_formats
        self._arms = arms
        self._models = models
        self._clock = clock
        self._gpu = gpu
        self._cpu = cpu or Lane("cpu-lane")
        self._runner = runner
        self._recheck_s = recheck_s
        self._ids = IdMaker()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._recovered = False
        self._running_id: str | None = None
        self._scoring_id: str | None = None
        self._waiting: dict[str, str] = {}
        self._interleave: tuple[int, int] = (0, 0)

    # --- the life of the desk ---------------------------------------------------------------

    async def start(self) -> None:
        """Recover what a stop left behind (once), start the lanes and the run scheduler."""
        loop = asyncio.get_running_loop()
        if not self._recovered:
            self._recovered = True
            for job in self._repo.recover(self._clock.now()):
                self._log(job.id, f"found at start-up and now {job.state.value}")
                if job.kind == "run":
                    self._mark_partial(job, "Stopped")
        if self._task is not None and not self._task.done() and self._loop is loop:
            return
        self._loop, self._wake = loop, asyncio.Event()
        self._gpu.start()
        self._cpu.start()
        self._task = loop.create_task(self._scheduler(self._wake), name="bench-runs")
        for job in self._repo.queued("score"):
            self._submit_score(job.id)

    async def stop(self) -> None:
        """Stop scheduling. A run in flight is marked interrupted by its own cancellation."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._cpu.stop()

    def status(self) -> BenchStatus:
        return BenchStatus(
            self._running_id,
            len(self._repo.queued("run")),
            self._scoring_id is not None,
            len(self._repo.queued("score")),
        )

    def _poke(self) -> None:
        if self._wake is not None:
            self._wake.set()

    # --- starting ---------------------------------------------------------------------------

    async def start_runs(self, actor: Actor, request: RunRequest) -> list[JobView]:
        """One queued job for each arm and model. Everything is checked before any is made."""
        await self.start()
        self._check_request(request)
        row, data = self._sets.export(request.set)
        if not row.frozen:
            raise ConflictError(
                f"{request.set.header_name} is a draft. Only a frozen version can be run: "
                "freeze it first, so the questions never change under a run."
            )
        items = self._set_formats.peek(data).items
        for arm in request.arms:
            reason = self._arms.refusal(arm.name)
            if reason is not None:
                raise UnavailableError(reason)
        for index, name in enumerate(request.models):
            info = await self._models.get(name)
            if info is None:
                raise _bad(f"/models/{index}", "No such model. See GET /models.")
            if not info.available:
                raise UnavailableError(
                    f"The model {info.label} does not answer right now.",
                    retry_after_s=MODEL_RETRY_S,
                )
        wanted = len(request.arms) * len(request.models)
        if self._repo.open_count(actor.username) + wanted > MAX_OPEN_PER_MEMBER:
            raise RateLimitedError(
                f"You can have {MAX_OPEN_PER_MEMBER} jobs waiting or running at once. "
                "Wait for some to finish or cancel some.",
                retry_after_s=BUSY_RETRY_S,
            )
        now = self._clock.now()
        created: list[Job] = []
        for arm in request.arms:
            for model in request.models:
                job_id = self._ids.new(now)
                job = Job(
                    id=job_id,
                    kind="run",
                    state=JobState.QUEUED,
                    owner=actor.username,
                    created_at=now,
                    queued_at=now,
                    params=RunParams(
                        set=request.set,
                        set_md5=row.md5,
                        arm=arm,
                        model=model,
                        repeats=request.repeats,
                        notes=request.notes,
                    ),
                    progress=Progress(0, len(items) * request.repeats),
                    artifact_id=run_id_for(arm.name, request.set, job_id),
                )
                self._repo.add(job)
                self._log(job_id, f"queued by {actor.username}")
                created.append(job)
        self._poke()
        queued = self._repo.queued("run")
        return [self._view(j, queued) for j in created]

    @staticmethod
    def _check_request(request: RunRequest) -> None:
        if not 1 <= len(request.arms) <= MAX_ARMS:
            raise _bad("/arms", f"Give 1 to {MAX_ARMS} arms.")
        if len(
            {(a.name, a.variant, repr(sorted(a.overrides.items()))) for a in request.arms}
        ) != len(request.arms):
            raise _bad("/arms", "An arm is listed twice.")
        if not 1 <= len(request.models) <= MAX_MODELS:
            raise _bad("/models", f"Give 1 to {MAX_MODELS} models.")
        if len(set(request.models)) != len(request.models):
            raise _bad("/models", "A model is listed twice.")
        if not 1 <= request.repeats <= MAX_REPEATS:
            raise _bad("/repeats", f"Repeats are 1 to {MAX_REPEATS}.")
        if len(request.notes) > MAX_NOTES:
            raise _bad("/notes", f"The notes are at most {MAX_NOTES} characters.")

    async def start_score(self, actor: Actor, request: ScoreRequest) -> JobView:
        await self.start()
        self._check_score(request)
        self._sets.export(request.set)  # NotFoundError when there is no such version
        busy = self._open_run_ids()
        for index, run_id in enumerate(request.runs):
            if self._files.run_stamp(run_id) is None:
                raise _bad(f"/runs/{index}", "No such run.")
            if run_id in busy:
                raise _not_ended(run_id)
            try:
                header = self._read_run(run_id).header
            except NotFoundError:  # a file that is not a run of this service (see RunsDesk)
                raise _bad(f"/runs/{index}", "No such run.") from None
            if header.get("set", {}).get("name") != request.set.header_name:
                raise _bad(f"/runs/{index}", f"This run is not on {request.set.header_name}.")
        if self._repo.open_count(actor.username) + 1 > MAX_OPEN_PER_MEMBER:
            raise RateLimitedError(
                f"You can have {MAX_OPEN_PER_MEMBER} jobs waiting or running at once.",
                retry_after_s=BUSY_RETRY_S,
            )
        now = self._clock.now()
        job_id = self._ids.new(now)
        job = Job(
            id=job_id,
            kind="score",
            state=JobState.QUEUED,
            owner=actor.username,
            created_at=now,
            queued_at=now,
            params=ScoreParams(request.set, request.runs, request.options),
            progress=Progress(0, 1),
            artifact_id=job_id,
        )
        self._repo.add(job)
        self._log(job_id, f"queued by {actor.username}")
        self._submit_score(job_id)
        return self._view(job, [])

    def _open_run_ids(self) -> set[str]:
        """The runs that are queued, running or being cancelled: their files may still grow."""
        return {
            j.artifact_id
            for j in self._repo.list(kind="run", states=OPEN_STATES, limit=1000)
            if j.artifact_id
        }

    @staticmethod
    def _check_score(request: ScoreRequest) -> None:
        runs = request.runs
        if not 1 <= len(runs) <= MAX_SCORE_RUNS:
            raise _bad("/runs", f"Give 1 to {MAX_SCORE_RUNS} runs.")
        if len(set(runs)) != len(runs):
            raise _bad("/runs", "A run is listed twice.")
        for index, run_id in enumerate(runs):
            if not valid_run_id(run_id):
                raise _bad(f"/runs/{index}", "Not a run id.")
        JobDesk._check_options(runs, request.options)

    @staticmethod
    def _check_options(runs: Sequence[str], options: ScoreOptions) -> None:
        if len(options.pairs) > MAX_PAIRS:
            raise _bad("/pairs", f"At most {MAX_PAIRS} pairs.")
        for index, pair in enumerate(options.pairs):
            for run_id in pair:
                if run_id not in runs:
                    raise _bad(f"/pairs/{index}", f"{run_id} is not among the runs to score.")
            if pair[0] == pair[1]:
                raise _bad(f"/pairs/{index}", "A run cannot be compared with itself.")
        if len(set(options.pairs)) != len(options.pairs):
            raise _bad("/pairs", "A pair is listed twice.")
        if options.noise is not None:
            for run_id in options.noise:
                if run_id not in runs:
                    raise _bad("/noise", f"{run_id} is not among the runs to score.")
            if options.noise[0] == options.noise[1]:
                raise _bad("/noise", "Give two different runs.")

    # --- reading ----------------------------------------------------------------------------

    def get(self, job_id: str) -> JobView:
        return self._view(self._job(job_id), self._repo.queued("run"))

    def list(
        self,
        *,
        states: Sequence[JobState] | None = None,
        kind: JobKind | None = None,
        owner: str | None = None,
        limit: int = DEFAULT_PAGE,
        offset: int = 0,
    ) -> list[JobView]:
        limit = min(max(limit, 1), MAX_PAGE)
        jobs = self._repo.list(
            states=states, kind=kind, owner=owner, limit=limit, offset=max(offset, 0)
        )
        queued = self._repo.queued("run")
        return [self._view(j, queued) for j in jobs]

    def read_log(self, job_id: str, offset: int = 0, limit: int = LOG_PAGE) -> LogChunk:
        self._job(job_id)
        chunk = self._files.read_log(job_id, offset, limit)
        return chunk or LogChunk("", 0, 0)

    def _job(self, job_id: str) -> Job:
        job = self._repo.get(job_id) if valid_job_id(job_id) else None
        if job is None:
            raise NotFoundError("No such job.")
        return job

    def _view(self, job: Job, queued_runs: Sequence[Job]) -> JobView:
        position = None
        if job.kind == "run" and job.state is JobState.QUEUED:
            ahead = [j.id for j in queued_runs]
            if job.id in ahead:
                position = ahead.index(job.id) + (1 if self._running_id is not None else 0)
        return JobView(job, position, self._waiting.get(job.id))

    # --- cancel and resume ------------------------------------------------------------------

    def cancel(self, actor: Actor, job_id: str) -> JobView:
        job = self._job(job_id)
        require_owner_or_admin(actor, job)
        if job.state in (JobState.CANCELLING, JobState.CANCELLED):
            return self._view(job, self._repo.queued("run"))  # asking twice changes nothing
        moved = self._cancel_move(job)
        if moved is None:  # it moved between the read and the change: say what it is now
            current = self._job(job_id)
            if current.state not in (JobState.CANCELLING, JobState.CANCELLED):
                raise ConflictError(f"The job is now {current.state.value}; it is not cancelled.")
            return self._view(current, self._repo.queued("run"))
        self._log(job_id, f"cancel asked by {actor.username}")
        self._poke()
        return self._view(moved, self._repo.queued("run"))

    def _cancel_move(self, job: Job) -> Job | None:
        if job.state is JobState.QUEUED:
            return self._repo.move(
                job.id,
                expected={JobState.QUEUED},
                to=JobState.CANCELLED,
                ended_at=self._clock.now(),
            )
        if job.state is JobState.RUNNING and job.kind == "run":
            return self._repo.move(job.id, expected={JobState.RUNNING}, to=JobState.CANCELLING)
        if job.state is JobState.RUNNING:
            raise ConflictError(
                "A scoring cannot be stopped once it has started; it takes seconds."
            )
        raise ConflictError(f"A job that is {job.state.value} cannot be cancelled.")

    def resume(self, actor: Actor, job_id: str) -> JobView:
        job = self._job(job_id)
        require_owner_or_admin(actor, job)
        if job.state not in RESUMABLE_STATES:
            raise ConflictError(
                f"A job that is {job.state.value} cannot be resumed; only a cancelled or "
                "interrupted one can."
            )
        if isinstance(job.params, RunParams):
            row = self._sets.export(job.params.set)[0]
            if row.md5 != job.params.set_md5:
                raise ConflictError("The question set is no longer the file this run started on.")
        moved = self._repo.move(
            job_id,
            expected=RESUMABLE_STATES,
            to=JobState.QUEUED,
            queued_at=self._clock.now(),
            ended_at=None,
            error=None,
        )
        if moved is None:
            raise ConflictError("The job changed while this request ran; look at it again.")
        self._log(job_id, f"resume asked by {actor.username}")
        if moved.kind == "score":
            self._submit_score(job_id)
        self._poke()
        return self._view(moved, self._repo.queued("run"))

    # --- the log ----------------------------------------------------------------------------

    def _log(self, job_id: str, message: str) -> None:
        stamp = self._clock.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            self._files.append_log(job_id, f"{stamp}Z {message}\n")
        except OSError:  # a log that cannot be written must not stop a run
            log.error("a job log could not be written", extra={"event": "job.log_failed"})

    # --- the run scheduler ------------------------------------------------------------------

    async def _scheduler(self, wake: asyncio.Event) -> None:
        while True:
            try:
                await self._schedule_once(wake)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # a bug or a closed database must not end the scheduler
                log.error(
                    "the run scheduler raised",
                    extra={"event": "job.scheduler_error", "kind": type(error).__name__},
                )
                await asyncio.sleep(SCHEDULER_RETRY_S)

    async def _schedule_once(self, wake: asyncio.Event) -> None:
        wake.clear()
        queued = self._repo.queued("run")
        if not queued:
            await wake.wait()
            return
        job = queued[0]
        assert isinstance(job.params, RunParams)
        reason = await self._reason_to_wait(job.params)
        if reason is not None:
            if self._waiting.get(job.id) != reason:
                self._waiting[job.id] = reason
                self._log(job.id, f"waiting: {reason}")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(wake.wait(), self._recheck_s)
            return
        self._waiting.pop(job.id, None)
        await self._run_job(job)

    async def _reason_to_wait(self, params: RunParams) -> str | None:
        try:
            return await self._arms.waiting_for(params.arm.name, params.model)
        except Exception:
            log.error("a model check raised", extra={"event": "job.check_failed"})
            return "The model servers cannot be checked right now."

    async def _run_job(self, job: Job) -> None:
        now = self._clock.now()
        moved = self._repo.move(
            job.id,
            expected={JobState.QUEUED},
            to=JobState.RUNNING,
            started_at=job.started_at or now,
            ended_at=None,
            error=None,
        )
        if moved is None:  # cancelled in the moment since the queue was read
            return
        self._running_id = job.id
        try:
            await self._execute_run(moved)
        except asyncio.CancelledError:
            self._stop_run(moved, "Stopped", None)
            raise
        except UnavailableError as error:
            self._stop_run(moved, "Interrupted", JobError("unavailable", _reasons(error)))
        except ResearchApiError as error:
            self._fail(moved, JobError(error.slug, _reasons(error)))
        except Exception as error:
            error_id = uuid.uuid4().hex[:12]
            log.error(
                "a run raised",
                extra={"event": "job.error", "error_id": error_id, "kind": type(error).__name__},
            )
            self._fail(moved, JobError("internal", f"The run failed. Error id {error_id}."))
        finally:
            self._running_id = None

    async def _execute_run(self, job: Job) -> None:
        params = job.params
        assert isinstance(params, RunParams)
        assert job.artifact_id is not None
        run_id = job.artifact_id
        row, data = self._sets.export(params.set)
        if row.md5 != params.set_md5:
            raise ConflictError("The question set is no longer the file this run started on.")
        items = {i["id"]: i for i in self._set_formats.peek(data).items}
        expected = list(expected_keys(list(items), params.repeats))
        info = await self._models.get(params.model)
        facts = ModelFacts(
            params.model, info.label if info else None, info.revision if info else None
        )
        header, done = self._open_run_file(job, params, run_id, facts, row.corpus)
        todo = [key for key in expected if key not in done]
        have = len(expected) - len(todo)
        self._repo.set_progress(job.id, Progress(have, len(expected)))
        self._log(
            job.id,
            f"started: {have} of {len(expected)} answers are already in the file, "
            f"{len(todo)} to ask ({params.arm.name}, model {params.model})",
        )
        self._interleave = (
            self._gpu.finished(ASK_PRIORITY),
            interleaved_in(header.get("notes", "")),
        )
        stopped_by_cancel = False
        for item_id, repeat in todo:
            current = self._repo.get(job.id)
            if current is None or current.state is JobState.CANCELLING:
                stopped_by_cancel = True
                break
            answer = await self._answer(params, items[item_id], repeat)
            self._files.append_run(
                run_id, self._formats.item_line(item_record(item_id, repeat, answer))
            )
            have += 1
            self._repo.set_progress(job.id, Progress(have, len(expected)))
            self._log(job.id, _answered(item_id, repeat, have, len(expected), answer))
        interleaved = self._interleaved_now()
        now = self._clock.now()
        if stopped_by_cancel:
            text = partial_text(have, len(expected), "Cancelled", now)
            self._rewrite_header(run_id, header, ended=None, interleaved=interleaved, partial=text)
            self._repo.move(
                job.id, expected={JobState.CANCELLING}, to=JobState.CANCELLED, ended_at=now
            )
            self._log(job.id, f"cancelled with {have} of {len(expected)} answers")
            return
        self._rewrite_header(run_id, header, ended=now, interleaved=interleaved, partial=None)
        self._repo.move(
            job.id,
            expected={JobState.RUNNING, JobState.CANCELLING},
            to=JobState.DONE,
            ended_at=now,
            progress=Progress(len(expected), len(expected)),
        )
        self._log(job.id, f"done: {len(expected)} answers, {interleaved} asks shared the GPU")

    def _interleaved_now(self) -> int:
        """Asks that went through the GPU lane during this run, this session and before."""
        asks_before, base = self._interleave
        return base + self._gpu.finished(ASK_PRIORITY) - asks_before

    def _open_run_file(
        self, job: Job, params: RunParams, run_id: str, facts: ModelFacts, corpus: str | None
    ) -> tuple[dict[str, Any], set[tuple[str, int]]]:
        """Make the run file, or take up the one a stop left: cut a half line off its end and
        read which (question, repeat) pairs it already has."""
        existing = self._files.read_run(run_id)
        if existing is None:
            now = self._clock.now()
            header = header_record(
                RunHeaderInput(
                    run_id=run_id,
                    created=job.created_at,
                    started=now,
                    set_name=params.set.header_name,
                    set_md5=params.set_md5,
                    corpus=corpus,
                    arm=params.arm,
                    model=facts,
                    repeats=params.repeats,
                    notes=params.notes,
                    runner=self._runner,
                    prompt_md5=self._arms.prompt_md5(params.arm.name),
                )
            )
            self._files.create_run(run_id, self._formats.header_line(header))
            return header, set()
        run = self._formats.read(existing)
        if run.complete_bytes != len(existing):
            self._files.replace_run(run_id, existing[: run.complete_bytes])
        done = {(str(i["id"]), int(i["repeat"])) for i in run.items}
        return run.header, done

    def _rewrite_header(
        self,
        run_id: str,
        header: Mapping[str, Any],
        *,
        ended: datetime | None,
        interleaved: int,
        partial: str | None,
    ) -> None:
        """Replace the first line of the run file, which says how the run ended."""
        data = self._files.read_run(run_id)
        if data is None:
            return
        rebuilt = dict(header)
        rebuilt.pop("ended", None)
        if ended is not None:
            rebuilt["ended"] = ended.isoformat()
        rebuilt["notes"] = header_notes(
            base_notes(str(header.get("notes", ""))), interleaved, partial
        )
        first = data.find(b"\n") + 1
        self._files.replace_run(run_id, self._formats.header_line(rebuilt) + data[first:])

    def _mark_partial(self, job: Job, why: str, interleaved: int | None = None) -> None:
        """Write the stop into the header of a run file, for a job found stopped at start-up."""
        if job.artifact_id is None or self._files.run_stamp(job.artifact_id) is None:
            return
        try:
            data = self._files.read_run(job.artifact_id)
            if data is None:
                return
            run = self._formats.read(data)
            done = len(run.items)
            text = partial_text(done, job.progress.total, why, self._clock.now())
            self._rewrite_header(
                job.artifact_id,
                run.header,
                ended=None,
                interleaved=interleaved_in(run.header.get("notes", ""))
                if interleaved is None
                else interleaved,
                partial=text,
            )
        except (ResearchApiError, OSError, ValueError):
            log.error("a stopped run's header was not updated", extra={"event": "job.mark_failed"})

    def _stop_run(self, job: Job, why: str, error: JobError | None) -> None:
        """The run stops without being done: interrupted (or cancelled, if that was asked)."""
        now = self._clock.now()
        current = self._repo.get(job.id)
        asked = current is not None and current.state is JobState.CANCELLING
        have = current.progress.done if current else job.progress.done
        if job.artifact_id is not None and self._files.run_stamp(job.artifact_id) is not None:
            self._mark_partial(
                job.with_changes(progress=Progress(have, job.progress.total)),
                "Cancelled" if asked else why,
                self._interleaved_now(),
            )
        if asked:
            self._repo.move(
                job.id, expected={JobState.CANCELLING}, to=JobState.CANCELLED, ended_at=now
            )
            self._log(job.id, f"cancelled with {have} of {job.progress.total} answers")
            return
        self._repo.move(
            job.id,
            expected={JobState.RUNNING},
            to=JobState.INTERRUPTED,
            ended_at=now,
            error=error or JobError("interrupted", "The service stopped while this job ran."),
        )
        self._log(job.id, f"interrupted with {have} of {job.progress.total} answers")

    def _fail(self, job: Job, error: JobError) -> None:
        self._repo.move(
            job.id,
            expected={JobState.RUNNING, JobState.CANCELLING},
            to=JobState.FAILED,
            ended_at=self._clock.now(),
            error=error,
        )
        self._log(job.id, f"failed: {error.kind}: {error.message}")

    # --- one question -----------------------------------------------------------------------

    async def _answer(self, params: RunParams, item: Mapping[str, Any], repeat: int) -> RunAnswer:
        call = ArmCall(
            arm_name=params.arm.name,
            variant=params.arm.variant,
            overrides=params.arm.overrides,
            model=params.model,
            item=item,
            repeat=repeat,
            set_ref=params.set,
        )
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            return await self._through_lane(lambda: self._arms.answer(call))
        except UnavailableError:
            raise  # the model server went away: the run is interrupted, not marked with errors
        except TimeoutError:
            return RunAnswer.failed(
                AnswerError("timeout", "The model took too long to answer."), loop.time() - started
            )
        except ResearchApiError as error:
            return RunAnswer.failed(AnswerError("other", _reasons(error)), loop.time() - started)
        except Exception as error:
            error_id = uuid.uuid4().hex[:12]
            log.error(
                "a question of a run raised",
                extra={
                    "event": "job.question_error",
                    "error_id": error_id,
                    "kind": type(error).__name__,
                },
            )
            return RunAnswer.failed(
                AnswerError("other", f"Unexpected error {error_id}."), loop.time() - started
            )

    async def _through_lane[T](self, work: Callable[[], Awaitable[T]]) -> T:
        """Run ``work`` as one job of the GPU lane, with the priority of a run's question."""
        future: asyncio.Future[T] = asyncio.get_running_loop().create_future()

        async def job() -> None:
            try:
                result = await work()
            except asyncio.CancelledError:
                if not future.done():
                    future.cancel()
                raise
            except Exception as error:
                if not future.done():
                    future.set_exception(error)
            else:
                if not future.done():
                    future.set_result(result)

        ticket = self._gpu.submit(job, priority=RUN_PRIORITY)
        try:
            return await future
        except asyncio.CancelledError:
            self._gpu.cancel(ticket)
            raise

    # --- scoring ----------------------------------------------------------------------------

    def _submit_score(self, job_id: str) -> None:
        self._cpu.submit(lambda: self._score_job(job_id), priority=RUN_PRIORITY)

    async def _score_job(self, job_id: str) -> None:
        now = self._clock.now()
        job = self._repo.move(
            job_id,
            expected={JobState.QUEUED},
            to=JobState.RUNNING,
            started_at=now,
            ended_at=None,
            error=None,
        )
        if job is None:
            return
        self._scoring_id = job_id
        self._log(job_id, "scoring started")
        try:
            warnings = await asyncio.to_thread(self._score_blocking, job)
        except ResearchApiError as error:
            self._fail(job, JobError(error.slug, _reasons(error)))
        except Exception as error:
            error_id = uuid.uuid4().hex[:12]
            log.error(
                "a scoring raised",
                extra={"event": "job.error", "error_id": error_id, "kind": type(error).__name__},
            )
            self._fail(job, JobError("internal", f"The scoring failed. Error id {error_id}."))
        else:
            for warning in warnings:
                self._log(job_id, f"warning: {_short(warning)}")
            self._repo.move(
                job_id,
                expected={JobState.RUNNING},
                to=JobState.DONE,
                ended_at=self._clock.now(),
                progress=Progress(1, 1),
            )
            self._log(job_id, "scoring done")
        finally:
            self._scoring_id = None

    def _score_blocking(self, job: Job) -> Sequence[str]:
        params = job.params
        assert isinstance(params, ScoreParams)
        _, set_data = self._sets.export(params.set)
        runs: list[tuple[str, bytes]] = []
        for run_id in params.runs:
            data = self._files.read_run(run_id)
            if data is None:
                raise NotFoundError(f"The run {run_id} is gone.")
            runs.append((f"{run_id}.qa-run.jsonl", data))
        scored = self._scorer.score(
            set_data, runs, params.options, self._clock.now().isoformat(timespec="seconds")
        )
        self._files.write_score(job.id, scored.data)
        return scored.warnings

    def _read_run(self, run_id: str) -> RunData:
        """A run as ``GET /runs`` would show it: the file's name is the run id of its header."""
        data = self._files.read_run(run_id)
        if data is None:
            raise NotFoundError("No such run.")
        try:
            run = self._formats.read(data)
        except CorruptStoreError:
            raise NotFoundError("No such run.") from None
        if run.header.get("run_id") != run_id:
            raise NotFoundError("No such run.")
        return run


def _not_ended(run_id: str) -> ConflictError:
    return ConflictError(
        f"The run {run_id} has not ended. Wait for it, or cancel it, and score it then."
    )


def _answered(item_id: str, repeat: int, have: int, total: int, answer: RunAnswer) -> str:
    where = f"{have}/{total} {item_id} repeat {repeat}"
    if answer.error is not None:
        return f"{where}: error {answer.error.kind}"
    wall = f" in {answer.timings.wall_s:.1f}s" if answer.timings is not None else ""
    return f"{where}: answered{wall}"
