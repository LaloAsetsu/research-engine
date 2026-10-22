"""HTTP request and response schemas. They are the wire contract that OpenAPI publishes."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from ...domain.status import Mode, ServerStatus


class Health(BaseModel):
    ok: bool


class Status(BaseModel):
    mode: Mode = Field(description="`live`, or `fake` when the service runs on test data.")
    api_version: str
    started_at: datetime
    uptime_s: float
    ready: bool

    @classmethod
    def from_domain(cls, status: ServerStatus) -> Status:
        return cls(
            mode=status.mode,
            api_version=status.version,
            started_at=status.started_at,
            uptime_s=status.uptime_s,
            ready=status.ready,
        )


class ProblemField(BaseModel):
    path: str = Field(description="JSON pointer to the offending value.")
    message: str


class Problem(BaseModel):
    """RFC 9457 problem details (`application/problem+json`)."""

    type: str
    title: str
    status: int
    detail: str
    instance: str
    errors: list[ProblemField] | None = None
    error_id: str | None = Field(default=None, description="Only on 500: the id in the server log.")
