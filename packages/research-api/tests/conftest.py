from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from research_api.bootstrap import Container, Settings, build_container
from research_api.entrypoints.http import build_app


@pytest.fixture
def container() -> Container:
    return build_container(Settings.defaults(), fake=True)


@pytest.fixture
def client(container: Container) -> TestClient:
    return TestClient(build_app(container), raise_server_exceptions=False)
