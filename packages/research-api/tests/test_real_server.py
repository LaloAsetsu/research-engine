"""The sign-in flow over a real socket with a real uvicorn, the way the web app will use it."""

from __future__ import annotations

import http.client
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import replace

import httpx
import pytest
import uvicorn

from research_api.bootstrap import Container
from research_api.entrypoints.http import build_app
from research_api.entrypoints.http.security import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE


class Running:
    def __init__(self, port: int, closed: list[str]) -> None:
        self.port = port
        self.closed = closed
        self.origin = f"http://127.0.0.1:{port}"


@pytest.fixture
def running(container: Container) -> Iterator[Running]:
    closed: list[str] = []
    spy = replace(container, close=lambda: (closed.append("closed"), container.close())[1])
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(build_app(spy), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started
    try:
        yield Running(port, closed)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    assert closed == ["closed"]  # shutting the server down lets go of the database


def test_a_browser_like_client_signs_in_reads_and_signs_out(running: Running) -> None:
    with httpx.Client(base_url=running.origin, headers={"Origin": running.origin}) as client:
        login = client.post(
            "/api/v1/auth/login", json={"username": "ana", "password": "test-password-ana-01"}
        )
        assert login.status_code == 204
        assert client.cookies[SESSION_COOKIE]
        me = client.get("/api/v1/auth/me")
        assert me.json()["username"] == "ana"
        assert "no-store" in me.headers["cache-control"]
        assert client.get("/api/v1/admin/sessions").status_code == 200
        csrf = {CSRF_HEADER: client.cookies[CSRF_COOKIE]}
        assert client.post("/api/v1/auth/logout").status_code == 403
        assert client.post("/api/v1/auth/logout", headers=csrf).status_code == 204
        assert client.get("/api/v1/auth/me").status_code == 401


def test_a_page_on_another_origin_cannot_sign_in(running: Running) -> None:
    body = {"username": "ana", "password": "test-password-ana-01"}
    with httpx.Client(base_url=running.origin) as client:
        for origin in ("http://evil.example", "http://localhost.evil.example:5173", "null"):
            done = client.post("/api/v1/auth/login", json=body, headers={"Origin": origin})
            assert done.status_code == 403, origin
        assert client.post("/api/v1/auth/login", json=body).status_code == 403  # none at all


def test_a_rebinding_host_name_is_refused_over_a_real_connection(running: Running) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", running.port, timeout=5)
    conn.putrequest("GET", "/api/v1/status", skip_host=True)
    conn.putheader("Host", f"rebind.attacker.example:{running.port}")
    conn.endheaders()
    reply = conn.getresponse()
    assert reply.status == 403
    assert b"uptime_s" not in reply.read()


def test_a_request_without_a_host_header_is_not_served_over_a_real_connection(
    running: Running,
) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", running.port, timeout=5)
    conn.putrequest("GET", "/api/v1/health", skip_host=True)
    conn.endheaders()
    assert conn.getresponse().status in (400, 403)  # uvicorn itself answers 400 to HTTP/1.1


def test_the_session_cookie_is_not_readable_by_script_but_the_csrf_cookie_is(
    running: Running,
) -> None:
    with httpx.Client(base_url=running.origin, headers={"Origin": running.origin}) as client:
        login = client.post(
            "/api/v1/auth/login", json={"username": "beto", "password": "test-password-beto-02"}
        )
    raw = {c.split("=")[0]: c.lower() for c in login.headers.get_list("set-cookie")}
    assert "httponly" in raw[SESSION_COOKIE]
    assert "httponly" not in raw[CSRF_COOKIE]
