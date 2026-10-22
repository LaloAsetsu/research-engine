"""Use case: report the state of the service."""

from __future__ import annotations

from datetime import datetime

from ..domain.status import ServerStatus
from ..ports.clock import Clock


class ReportStatus:
    def __init__(self, *, clock: Clock, mode: str, version: str, started_at: datetime) -> None:
        self._clock = clock
        self._mode = mode
        self._version = version
        self._started_at = started_at

    def __call__(self) -> ServerStatus:
        uptime = (self._clock.now() - self._started_at).total_seconds()
        return ServerStatus(
            mode=self._mode,
            version=self._version,
            started_at=self._started_at,
            uptime_s=max(uptime, 0.0),
            ready=True,
        )
