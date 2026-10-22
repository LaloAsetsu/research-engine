from __future__ import annotations

from datetime import UTC, datetime, timedelta

from research_api.application.status import ReportStatus
from research_api.domain.errors import (
    ConflictError,
    InvalidInputError,
    ResearchApiError,
)


class Ticking:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


def test_uptime_is_the_time_since_start() -> None:
    start = datetime(2026, 3, 1, tzinfo=UTC)
    clock = Ticking(start)
    report = ReportStatus(clock=clock, mode="live", version="1.2.3", started_at=start)
    assert report().uptime_s == 0
    clock.at = start + timedelta(seconds=90)
    status = report()
    assert (status.mode, status.version, status.uptime_s, status.ready) == (
        "live",
        "1.2.3",
        90,
        True,
    )
    assert status.started_at == start


def test_uptime_never_goes_negative() -> None:
    start = datetime(2026, 3, 1, tzinfo=UTC)
    clock = Ticking(start - timedelta(seconds=5))
    assert ReportStatus(clock=clock, mode="fake", version="x", started_at=start)().uptime_s == 0


def test_domain_errors_keep_their_detail() -> None:
    assert str(ConflictError("stale")) == "stale"
    assert ResearchApiError().detail == ""
    assert InvalidInputError("bad").errors == ()
