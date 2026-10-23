"""The bind rule holds whichever way the server is started, because the app checks the address."""

from __future__ import annotations

import asyncio
import http.client
import socket
import threading
import time
from collections.abc import Callable

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from research_api.bootstrap import Container
from research_api.entrypoints.http import build_app

PROBLEM = "application/problem+json"
ClientFor = Callable[..., TestClient]


@pytest.mark.parametrize("host", ["127.0.0.1", "127.8.8.8", "[::1]", "[::ffff:127.0.0.1]"])
def test_fake_mode_answers_on_loopback(
    container: Container, client_for: ClientFor, host: str
) -> None:
    response = client_for(build_app(container), f"http://{host}:8090").get("/api/v1/health")
    assert response.status_code == 200


@pytest.mark.parametrize(
    "host", ["192.168.1.20", "10.0.0.5", "100.64.0.5", "8.8.8.8", "testserver", "example.test"]
)
def test_fake_mode_refuses_every_other_address(
    container: Container, client_for: ClientFor, host: str
) -> None:
    response = client_for(build_app(container), f"http://{host}:8090").get("/api/v1/health")
    assert response.status_code == 403
    assert response.headers["content-type"] == PROBLEM
    assert response.json()["type"] == "urn:research-api:problem:forbidden"


@pytest.mark.parametrize("host", ["127.0.0.1", "127.8.8.8", "[::1]", "100.64.0.5"])
def test_live_mode_answers_on_loopback_and_on_the_bind_address(
    live_app: Callable[..., FastAPI], client_for: ClientFor, host: str
) -> None:
    response = client_for(live_app("100.64.0.5"), f"http://{host}:8090").get("/api/v1/health")
    assert response.status_code == 200


@pytest.mark.parametrize(
    ("bind_host", "host"),
    [
        ("100.64.0.5", "[::ffff:100.64.0.5]"),
        ("fd7a:115c:a1e0::5", "[fd7a:115c:a1e0:0:0:0:0:5]"),
        ("fd7a:115c:a1e0::5", "[FD7A:115C:A1E0::5]"),
    ],
)
def test_live_mode_reads_the_bind_address_as_an_address_not_as_text(
    live_app: Callable[..., FastAPI], client_for: ClientFor, bind_host: str, host: str
) -> None:
    response = client_for(live_app(bind_host), f"http://{host}:8090").get("/api/v1/health")
    assert response.status_code == 200


@pytest.mark.parametrize(
    "host",
    [
        "100.80.0.9",
        "100.64.0.6",
        "100.127.255.254",
        "[fd7a:115c:a1e0::6]",
        "192.168.1.20",
        "10.0.0.5",
        "100.128.0.1",
        "8.8.8.8",
        "testserver",
    ],
)
def test_live_mode_refuses_every_other_address_even_in_the_tailscale_block(
    live_app: Callable[..., FastAPI], client_for: ClientFor, host: str
) -> None:
    response = client_for(live_app("100.64.0.5"), f"http://{host}:8090").get("/api/v1/status")
    assert response.status_code == 403
    assert "uptime_s" not in response.text


@pytest.mark.parametrize("host", ["100.64.0.5", "[fd7a:115c:a1e0::5]", "192.168.1.20"])
def test_live_mode_on_loopback_refuses_the_tailscale_addresses(
    live_app: Callable[..., FastAPI], client_for: ClientFor, host: str
) -> None:
    response = client_for(live_app(), f"http://{host}:8090").get("/api/v1/health")
    assert response.status_code == 403


def test_a_refused_connection_never_reaches_the_routes(
    container: Container, client_for: ClientFor
) -> None:
    app = build_app(container)
    reached: list[str] = []

    @app.get("/api/v1/spy")
    def spy() -> None:
        reached.append("yes")

    client_for(app, "http://192.168.1.20:8090").get("/api/v1/spy")
    assert reached == []


def test_a_server_without_an_ip_address_is_refused(container: Container) -> None:
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/health",
        "headers": [],
        "query_string": b"",
        "server": ("/run/api.sock", None),
    }
    asyncio.run(build_app(container)(scope, receive, send))  # type: ignore[arg-type]
    assert sent[0]["status"] == 403


def test_a_websocket_on_a_refused_address_is_closed(
    container: Container, client_for: ClientFor
) -> None:
    client = client_for(build_app(container), "http://192.168.1.20:8090")
    with pytest.raises(WebSocketDisconnect) as stop, client.websocket_connect("/api/v1/health"):
        pass
    assert stop.value.code == 1008


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_uvicorn_reports_the_address_the_connection_arrived_on(container: Container) -> None:
    """The guard trusts ``scope["server"]``: under `--host 0.0.0.0` it is still the local address.

    This test binds 0.0.0.0 for a moment, on a free port, to show that.
    """
    app = build_app(container)
    seen: list[object] = []

    @app.middleware("http")
    async def record(request, call_next):
        seen.append(request.scope["server"])
        return await call_next(request)

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/v1/health")
        assert conn.getresponse().status == 200
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    assert seen == [("127.0.0.1", port)]
