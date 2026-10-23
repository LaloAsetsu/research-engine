"""The job table: ``ports/jobs.JobRepo`` over SQLAlchemy. One short transaction each."""

from __future__ import annotations

import json
from collections.abc import Collection
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, Engine, func, select, update
from sqlalchemy.orm import Session as DbSession

from ...domain.jobs import (
    OPEN_STATES,
    Job,
    JobError,
    JobKind,
    JobState,
    Params,
    Progress,
    RunParams,
    ScoreParams,
)
from ...ports.jobs import KEEP, Keep
from .models import JobRow

ERROR_MESSAGE_CHARS = 500


def _params(kind: str, text: str) -> Params:
    data = json.loads(text)
    return RunParams.from_json(data) if kind == "run" else ScoreParams.from_json(data)


def _job(row: JobRow) -> Job:
    return Job(
        id=row.id,
        kind=cast(JobKind, row.kind),
        state=JobState(row.state),
        owner=row.owner,
        created_at=row.created_at,
        queued_at=row.queued_at,
        params=_params(row.kind, row.params),
        progress=Progress(row.done, row.total),
        started_at=row.started_at,
        ended_at=row.ended_at,
        artifact_id=row.artifact_id,
        error=None if row.error_kind is None else JobError(row.error_kind, row.error_message or ""),
    )


def _count(result: Any) -> int:
    return cast("CursorResult[Any]", result).rowcount


class SqlJobs:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, job: Job) -> None:
        row = JobRow(
            id=job.id,
            kind=job.kind,
            state=job.state.value,
            owner=job.owner,
            created_at=job.created_at,
            queued_at=job.queued_at,
            started_at=job.started_at,
            ended_at=job.ended_at,
            params=json.dumps(job.params.to_json(), ensure_ascii=False, sort_keys=True),
            done=job.progress.done,
            total=job.progress.total,
            artifact_id=job.artifact_id,
            error_kind=job.error.kind if job.error else None,
            error_message=job.error.message[:ERROR_MESSAGE_CHARS] if job.error else None,
        )
        with DbSession(self._engine) as db, db.begin():
            db.add(row)

    def get(self, job_id: str) -> Job | None:
        with DbSession(self._engine) as db:
            row = db.get(JobRow, job_id)
            return None if row is None else _job(row)

    def list(
        self,
        *,
        states: Collection[JobState] | None = None,
        kind: JobKind | None = None,
        owner: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Job]:
        query = select(JobRow).order_by(JobRow.id.desc()).limit(limit).offset(offset)
        if states is not None:
            query = query.where(JobRow.state.in_([s.value for s in states]))
        if kind is not None:
            query = query.where(JobRow.kind == kind)
        if owner is not None:
            query = query.where(JobRow.owner == owner)
        with DbSession(self._engine) as db:
            return [_job(row) for row in db.scalars(query)]

    def queued(self, kind: JobKind) -> list[Job]:
        query = (
            select(JobRow)
            .where(JobRow.state == JobState.QUEUED.value, JobRow.kind == kind)
            .order_by(JobRow.queued_at, JobRow.id)
        )
        with DbSession(self._engine) as db:
            return [_job(row) for row in db.scalars(query)]

    def open_count(self, owner: str) -> int:
        query = select(func.count()).where(
            JobRow.owner == owner, JobRow.state.in_([s.value for s in OPEN_STATES])
        )
        with DbSession(self._engine) as db:
            return int(db.scalar(query) or 0)

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
        values: dict[str, Any] = {"state": to.value}
        if not isinstance(queued_at, Keep):
            values["queued_at"] = queued_at
        if not isinstance(started_at, Keep):
            values["started_at"] = started_at
        if not isinstance(ended_at, Keep):
            values["ended_at"] = ended_at
        if not isinstance(error, Keep):
            values["error_kind"] = None if error is None else error.kind
            values["error_message"] = None if error is None else error.message[:ERROR_MESSAGE_CHARS]
        if not isinstance(artifact_id, Keep):
            values["artifact_id"] = artifact_id
        if not isinstance(progress, Keep):
            values["done"], values["total"] = progress.done, progress.total
        with DbSession(self._engine) as db, db.begin():
            result = db.execute(
                update(JobRow)
                .where(JobRow.id == job_id, JobRow.state.in_([s.value for s in expected]))
                .values(**values)
            )
            if _count(result) != 1:
                return None
            row = db.get(JobRow, job_id)
            assert row is not None
            db.refresh(row)
            return _job(row)

    def set_progress(self, job_id: str, progress: Progress) -> None:
        with DbSession(self._engine) as db, db.begin():
            db.execute(
                update(JobRow)
                .where(JobRow.id == job_id)
                .values(done=progress.done, total=progress.total)
            )

    def recover(self, now: datetime) -> list[Job]:
        changed: list[Job] = []
        with DbSession(self._engine) as db, db.begin():
            rows = db.scalars(
                select(JobRow)
                .where(JobRow.state.in_([JobState.RUNNING.value, JobState.CANCELLING.value]))
                .order_by(JobRow.id)
            ).all()
            for row in rows:
                if row.state == JobState.CANCELLING.value:
                    row.state = JobState.CANCELLED.value
                else:
                    row.state = JobState.INTERRUPTED.value
                    row.error_kind = "interrupted"
                    row.error_message = "The service stopped while this job ran."
                row.ended_at = now
            db.flush()
            for row in rows:
                db.refresh(row)
                changed.append(_job(row))
        return changed
