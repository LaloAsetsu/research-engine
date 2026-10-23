from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import LOOPBACK, SEED_FILE, live_settings, prepare_live

from research_api.bootstrap import Container, Settings, build_container
from research_api.bootstrap.seed import SeedUser, load_seed_users
from research_api.entrypoints.http import build_app


@pytest.fixture
def client_for() -> Callable[..., TestClient]:
    """Build a client whose requests arrive on ``base_url``; the bind guard reads that address."""

    def make(
        app: FastAPI, base_url: str = LOOPBACK, headers: dict[str, str] | None = None
    ) -> TestClient:
        # A browser sends its own address as Origin on every write; so does this client.
        return TestClient(
            app,
            base_url=base_url,
            raise_server_exceptions=False,
            headers={"Origin": base_url, **(headers or {})},
        )

    return make


@pytest.fixture(autouse=True)
def restore_logging():
    """`research-api serve` rewires the service's loggers; give the next test a clean slate."""
    logger = logging.getLogger("research_api")
    handlers, propagate, level = list(logger.handlers), logger.propagate, logger.level
    yield
    for handler in list(logger.handlers):
        if handler not in handlers:
            logger.removeHandler(handler)
            handler.close()
    logger.propagate, logger.level = propagate, level


@pytest.fixture
def seed() -> list[SeedUser]:
    return load_seed_users(SEED_FILE)


@pytest.fixture
def container(seed: list[SeedUser]) -> Container:
    return build_container(Settings.defaults(), fake=True, seed=seed, cheap_hashing=True)


@pytest.fixture
def client(container: Container, client_for: Callable[..., TestClient]) -> TestClient:
    return client_for(build_app(container))


@pytest.fixture
def live_app(tmp_path: Path) -> Callable[..., FastAPI]:
    """Build a live-mode app bound to ``bind_host`` over a private database in ``tmp_path``."""

    def make(bind_host: str = "127.0.0.1", **overrides: object) -> FastAPI:
        settings = prepare_live(live_settings(tmp_path, bind_host=bind_host, **overrides))
        return build_app(build_container(settings, cheap_hashing=True))

    return make
