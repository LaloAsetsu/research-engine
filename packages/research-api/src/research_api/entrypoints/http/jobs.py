"""Benchmark routes: start runs and scorings, follow jobs, read runs, scores and models.

Every route needs a signed-in member and nothing is cached by a browser: answers, papers and
questions are corpus text. Cancelling and resuming a job is for its owner or an admin.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from ...application.auth import Authenticated
from ...domain.jobs import JobKind, JobState
from ...domain.sets import Actor
from .auth import DEFAULT, INVALID, PROTECTED
from .job_schemas import (
    JOB_ID_REGEX,
    RUN_ID_REGEX,
    JobOut,
    ModelOut,
    RunItemsOut,
    RunRequestBody,
    RunSummaryOut,
    ScoreItemsOut,
    ScoreOut,
    ScoreRequestBody,
    ScoreSummaryOut,
)
from .schemas import Problem
from .security import container_of, member

Member = Annotated[Authenticated, Depends(member)]
NO_STORE = {"Cache-Control": "no-store"}
router = APIRouter(
    tags=["benchmarks"],
    dependencies=[Depends(member)],
    responses={**DEFAULT, **PROTECTED},
)

JobId = Annotated[str, Path(pattern=JOB_ID_REGEX, description="A job id.")]
RunId = Annotated[str, Path(pattern=RUN_ID_REGEX, description="A run id.")]
NO_SUCH = {404: {"model": Problem, "description": "No such job, run or score."}}
CONFLICT = {409: {"model": Problem, "description": "The job is in a state that does not allow it."}}
FORBIDDEN = {
    403: {"model": Problem, "description": "Only the job's owner or an admin can do this."}
}


def _actor(who: Authenticated) -> Actor:
    return Actor.of(who.user)


# --- models ---------------------------------------------------------------------------------


@router.get("/models", operation_id="listModels", summary="The models a run may ask")
async def list_models(request: Request) -> list[ModelOut]:
    """From the settings, each probed with a short timeout. A model whose server does not answer
    has `available: false` and cannot be chosen for a run."""
    return [ModelOut.from_domain(m) for m in await container_of(request).bench.models.models()]


# --- jobs -----------------------------------------------------------------------------------


@router.post(
    "/runs",
    operation_id="startRuns",
    summary="Start benchmark runs",
    status_code=202,
    responses={
        **NO_SUCH,
        409: {"model": Problem, "description": "The set version is a draft: freeze it first."},
        422: INVALID,
        429: {"model": Problem, "description": "You have too many jobs waiting."},
        503: {"model": Problem, "description": "A model or an arm is not available now."},
    },
)
async def start_runs(body: RunRequestBody, request: Request, who: Member) -> list[JobOut]:
    """One queued job for each arm and model, on a frozen set version. The GPU lane runs one run at
    a time and, inside it, one question at a time; asks go before the next question."""
    views = await container_of(request).bench.jobs.start_runs(_actor(who), body.to_domain())
    return [JobOut.from_domain(v) for v in views]


@router.get("/jobs", operation_id="listJobs", summary="Jobs, newest first")
def list_jobs(
    request: Request,
    who: Member,
    state: Annotated[JobState | None, Query()] = None,
    kind: Annotated[JobKind | None, Query()] = None,
    mine: Annotated[bool, Query(description="Only the jobs you started.")] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[JobOut]:
    views = container_of(request).bench.jobs.list(
        states=[state] if state else None,
        kind=kind,
        owner=who.user.username if mine else None,
        limit=limit,
        offset=offset,
    )
    return [JobOut.from_domain(v) for v in views]


@router.get(
    "/jobs/{job_id}",
    operation_id="getJob",
    summary="One job",
    responses={**NO_SUCH, 422: INVALID},
)
def get_job(job_id: JobId, request: Request) -> JobOut:
    """Poll this every two seconds until `settled` is true."""
    return JobOut.from_domain(container_of(request).bench.jobs.get(job_id))


@router.get(
    "/jobs/{job_id}/log",
    operation_id="getJobLog",
    summary="The log of a job, from a byte offset",
    response_class=Response,
    responses={
        **NO_SUCH,
        200: {
            "content": {"text/plain": {"schema": {"type": "string"}}},
            "headers": {
                "X-Log-Next-Offset": {
                    "description": "Ask for the next part from here.",
                    "schema": {"type": "integer"},
                },
                "X-Log-Size": {
                    "description": "The size of the log now, in bytes.",
                    "schema": {"type": "integer"},
                },
            },
        },
    },
)
def get_job_log(
    job_id: JobId,
    request: Request,
    offset: Annotated[int, Query(ge=0, description="Bytes to skip.")] = 0,
) -> Response:
    """Plain text lines with their times, for a live tail: send back `X-Log-Next-Offset`. The log
    never holds a question or an answer."""
    chunk = container_of(request).bench.jobs.read_log(job_id, offset)
    return Response(
        chunk.text,
        media_type="text/plain; charset=utf-8",
        headers={"X-Log-Next-Offset": str(chunk.next_offset), "X-Log-Size": str(chunk.size)},
    )


@router.post(
    "/jobs/{job_id}/cancel",
    operation_id="cancelJob",
    summary="Cancel a job",
    responses={**NO_SUCH, **FORBIDDEN, **CONFLICT},
)
async def cancel_job(job_id: JobId, request: Request, who: Member) -> JobOut:
    """A queued job is cancelled at once. A running one becomes `cancelling` and stops before its
    next question; the answers so far stay in the run and the run is marked partial."""
    return JobOut.from_domain(container_of(request).bench.jobs.cancel(_actor(who), job_id))


@router.post(
    "/jobs/{job_id}/resume",
    operation_id="resumeJob",
    summary="Resume a cancelled or interrupted job",
    responses={**NO_SUCH, **FORBIDDEN, **CONFLICT},
)
async def resume_job(job_id: JobId, request: Request, who: Member) -> JobOut:
    """Queues the job again. The run continues from the first (question, repeat) missing in its
    file, so every pair ends up once."""
    return JobOut.from_domain(container_of(request).bench.jobs.resume(_actor(who), job_id))


# --- runs -----------------------------------------------------------------------------------


@router.get("/runs", operation_id="listRuns", summary="Runs, finished and partial")
def list_runs(request: Request) -> list[RunSummaryOut]:
    """Every `qa-run/1` file in the runs folder, whoever made it, newest first."""
    return [RunSummaryOut.from_domain(s) for s in container_of(request).bench.runs.list()]


@router.get(
    "/runs/{run_id}", operation_id="getRun", summary="One run", responses={**NO_SUCH, 422: INVALID}
)
def get_run(run_id: RunId, request: Request) -> RunSummaryOut:
    return RunSummaryOut.from_domain(container_of(request).bench.runs.get(run_id))


@router.get(
    "/runs/{run_id}/items",
    operation_id="listRunItems",
    summary="The answers of a run, with their questions",
    responses={**NO_SUCH, 422: INVALID},
)
def list_run_items(
    run_id: RunId,
    request: Request,
    filter: Annotated[
        Literal["all", "errors", "answered"], Query(description="Which lines to list.")
    ] = "all",
    id: Annotated[str | None, Query(max_length=200, description="One question.")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    contexts: Annotated[bool, Query(description="Include the chunks the model read.")] = True,
) -> RunItemsOut:
    page = container_of(request).bench.runs.items(
        run_id, filter=filter, item_id=id, offset=offset, limit=limit, contexts=contexts
    )
    return RunItemsOut.from_domain(page)


# --- scores ---------------------------------------------------------------------------------


@router.post(
    "/scores",
    operation_id="startScore",
    summary="Score runs and compare them",
    status_code=202,
    responses={
        **NO_SUCH,
        409: {"model": Problem, "description": "A run has not ended yet."},
        422: INVALID,
        429: {"model": Problem, "description": "You have too many jobs waiting."},
    },
)
async def start_score(body: ScoreRequestBody, request: Request, who: Member) -> JobOut:
    """A job on the CPU lane that runs the qa-bench scorer over the runs and writes one
    `qa-score/1` file, which `GET /scores/{score_id}` reads once the job is `done`."""
    view = await container_of(request).bench.jobs.start_score(_actor(who), body.to_domain())
    return JobOut.from_domain(view)


@router.get("/scores", operation_id="listScores", summary="Scores, newest first")
def list_scores(request: Request) -> list[ScoreSummaryOut]:
    return [ScoreSummaryOut.from_domain(s) for s in container_of(request).bench.scores.list()]


@router.get(
    "/scores/{score_id}",
    operation_id="getScore",
    summary="The tables of a score",
    responses={**NO_SUCH, 422: INVALID},
)
def get_score(score_id: Annotated[str, Path(pattern=JOB_ID_REGEX)], request: Request) -> ScoreOut:
    """The header, a record per run, group and metric (mean and 95% interval) and a record per
    pair, group and metric (difference, interval, up and down, noise floor)."""
    return ScoreOut.from_domain(container_of(request).bench.scores.get(score_id))


@router.get(
    "/scores/{score_id}/items",
    operation_id="listScoreItems",
    summary="The metrics of each answer",
    responses={**NO_SUCH, 422: INVALID},
)
def list_score_items(
    score_id: Annotated[str, Path(pattern=JOB_ID_REGEX)],
    request: Request,
    run_id: Annotated[str | None, Query(pattern=RUN_ID_REGEX)] = None,
    id: Annotated[str | None, Query(max_length=200)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> ScoreItemsOut:
    page = container_of(request).bench.scores.items(
        score_id, run_id=run_id, item_id=id, offset=offset, limit=limit
    )
    return ScoreItemsOut.from_domain(page)
