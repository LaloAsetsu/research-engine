"""Value: what the service reports about itself."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Mode = Literal["live", "fake"]


@dataclass(frozen=True)
class ServerStatus:
    mode: Mode
    version: str
    started_at: datetime
    uptime_s: float
    ready: bool
