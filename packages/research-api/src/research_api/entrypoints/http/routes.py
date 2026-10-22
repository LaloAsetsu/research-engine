"""Routes of the ``/api/v1`` surface that exist so far."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ...bootstrap import Container
from .schemas import Health, Problem, Status

router = APIRouter(responses={"default": {"model": Problem, "description": "A problem."}})


def _container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


@router.get("/health", operation_id="getHealth", summary="Liveness check")
def health() -> Health:
    return Health(ok=True)


@router.get("/status", operation_id="getStatus", summary="State of the service")
def status(request: Request) -> Status:
    return Status.from_domain(_container(request).report_status())
