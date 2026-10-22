"""Value: what the service reports about itself."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ServerStatus:
    mode: str
    version: str
    started_at: datetime
    uptime_s: float
    ready: bool
