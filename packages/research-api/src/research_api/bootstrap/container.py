"""Composition root: builds the use cases and wires the adapters to their ports."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from ..adapters.clock import SystemClock
from ..adapters.fake import FixedClock
from ..application.status import ReportStatus
from ..domain.status import Mode
from ..ports.clock import Clock
from .settings import Settings, SettingsError, is_loopback, load_settings

CONFIG_ENV = "RESEARCH_API_CONFIG"
FAKE_ENV = "RESEARCH_API_FAKE"


@dataclass(frozen=True)
class Container:
    mode: Mode
    settings: Settings
    version: str
    clock: Clock
    report_status: ReportStatus


def build_container(settings: Settings, *, fake: bool = False) -> Container:
    """Wire the service. Fake mode uses in-memory stand-ins and accepts loopback only."""
    if fake and not is_loopback(settings.bind_host):
        raise SettingsError("fake mode serves test data and may only bind a loopback address")
    clock: Clock = FixedClock() if fake else SystemClock()
    mode: Mode = "fake" if fake else "live"
    api_version = version("research-api")
    return Container(
        mode=mode,
        settings=settings,
        version=api_version,
        clock=clock,
        report_status=ReportStatus(
            clock=clock, mode=mode, version=api_version, started_at=clock.now()
        ),
    )


def container_from_env(environ: Mapping[str, str] | None = None) -> Container:
    """The container for ``uvicorn --factory``: fake mode, or the settings file named in the env."""
    env = os.environ if environ is None else environ
    if env.get(FAKE_ENV) == "1":
        return build_container(Settings.defaults(), fake=True)
    path = env.get(CONFIG_ENV)
    if not path:
        raise SettingsError(f"set {CONFIG_ENV} to a settings file, or {FAKE_ENV}=1 for fake mode")
    return build_container(load_settings(Path(path)))
