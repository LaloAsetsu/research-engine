from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from research_api.bootstrap import Container, Settings, build_container
from research_api.entrypoints.http import build_app

LOOPBACK = "http://127.0.0.1:8090"


@pytest.fixture
def client_for() -> Callable[..., TestClient]:
    """Build a client whose requests arrive on ``base_url``; the bind guard reads that address."""

    def make(app: FastAPI, base_url: str = LOOPBACK) -> TestClient:
        return TestClient(app, base_url=base_url, raise_server_exceptions=False)

    return make


@pytest.fixture
def container() -> Container:
    return build_container(Settings.defaults(), fake=True)


@pytest.fixture
def client(container: Container, client_for: Callable[..., TestClient]) -> TestClient:
    return client_for(build_app(container))
