"""The Host allow-list."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import call, http_scope, ok_app, status_of

from research_api.bootstrap import Container, Settings
from research_api.entrypoints.http import build_app
from research_api.entrypoints.http.hosts import HostGuard, host_name, hosts_allowed_by

PROBLEM = "application/problem+json"
ClientFor = Callable[..., TestClient]
TAILNET = "http://box.tail1234.ts.net:8090"


# --- reading a Host header ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "name"),
    [
        ("localhost", "localhost"),
        ("LocalHost:8090", "localhost"),
        ("127.0.0.1:8090", "127.0.0.1"),
        ("[::1]:8090", "::1"),
        ("[::1]", "::1"),
        ("  box.tail.ts.net  ", "box.tail.ts.net"),
        ("evil.example:80", "evil.example"),
    ],
)
def test_host_name_takes_the_name_without_the_port(header: str, name: str) -> None:
    assert host_name(header) == name


@pytest.mark.parametrize(
    "header",
    ["", " ", ":8090", "host:", "host:80x", "host:80:90", "[::1", "[::1]x", "[::1]:", "[::1]:ab"],
)
def test_a_malformed_host_header_has_no_name(header: str) -> None:
    assert host_name(header) is None


# --- the allow-list itself ------------------------------------------------------------------


def test_fake_mode_serves_only_loopback_names() -> None:
    allowed = hosts_allowed_by(Settings.defaults(), fake=True)
    for ok in ("localhost", "127.0.0.1", "127.9.9.9", "::1", "::ffff:127.0.0.1"):
        assert allowed(ok), ok
    for bad in (
        "evil.example",
        "localhost.evil.example",
        "127.0.0.1.evil.example",
        "100.64.0.5",
        "0.0.0.0",
        "localhost.",
    ):
        assert not allowed(bad), bad


def test_live_mode_serves_loopback_the_public_origin_host_and_the_bind_address() -> None:
    settings = Settings(bind_host="100.64.0.5", public_origin=TAILNET)
    allowed = hosts_allowed_by(settings, fake=False)
    for ok in (
        "localhost",
        "127.0.0.1",
        "::1",
        "box.tail1234.ts.net",
        "100.64.0.5",
        "::ffff:100.64.0.5",
    ):
        assert allowed(ok), ok
    for bad in (
        "evil.example",
        "box.tail1234.ts.net.evil.example",
        "100.64.0.6",
        "tail1234.ts.net",
        "192.168.1.5",
    ):
        assert not allowed(bad), bad


def test_live_mode_without_a_public_origin_uses_the_bind_address() -> None:
    allowed = hosts_allowed_by(Settings(bind_host="100.64.0.5"), fake=False)
    assert allowed("100.64.0.5") and allowed("localhost")
    assert not allowed("box.tail1234.ts.net")


def test_an_ipv6_public_origin_is_read_as_an_address() -> None:
    settings = Settings(
        bind_host="fd7a:115c:a1e0::5", public_origin="http://[fd7a:115c:a1e0::5]:8090"
    )
    allowed = hosts_allowed_by(settings, fake=False)
    assert allowed("fd7a:115c:a1e0:0:0:0:0:5")
    assert not allowed("fd7a:115c:a1e0::6")


# --- the Host guard in a running app --------------------------------------------------------


@pytest.mark.parametrize(
    "host", ["localhost:5173", "LOCALHOST", "127.0.0.1:8090", "[::1]:8090", "127.0.0.1"]
)
def test_fake_mode_answers_to_loopback_host_names(
    container: Container, client_for: ClientFor, host: str
) -> None:
    response = client_for(build_app(container)).get("/api/v1/health", headers={"Host": host})
    assert response.status_code == 200


@pytest.mark.parametrize(
    "host",
    [
        "rebind.attacker.example:8090",
        "evil.example",
        "localhost.evil.example",
        "127.0.0.1.evil.example:8090",
        "100.64.0.5:8090",
        "0.0.0.0:8090",
        "[::2]:8090",
        "",
        "localhost:abc",
    ],
)
def test_fake_mode_refuses_other_host_names_on_any_path(
    container: Container, client_for: ClientFor, host: str
) -> None:
    app = build_app(container)
    client = client_for(app)
    for path in ("/api/v1/health", "/api/v1/status", "/api/v1/auth/me", "/nothing"):
        response = client.get(path, headers={"Host": host})
        assert response.status_code == 403, (path, host)
        assert response.headers["content-type"] == PROBLEM
        assert response.json()["detail"] == "This service does not answer to that host name."
        assert "uptime_s" not in response.text


def test_live_mode_answers_to_its_own_names_only(
    live_app: Callable[..., FastAPI], client_for: ClientFor
) -> None:
    app = live_app("100.64.0.5", public_origin=TAILNET)
    client = client_for(app, "http://100.64.0.5:8090")
    for ok in ("box.tail1234.ts.net:8090", "BOX.tail1234.ts.net", "100.64.0.5:8090", "localhost"):
        assert client.get("/api/v1/health", headers={"Host": ok}).status_code == 200, ok
    for bad in ("evil.example:8090", "box.tail1234.ts.net.evil.example", "other.tail1234.ts.net"):
        assert client.get("/api/v1/health", headers={"Host": bad}).status_code == 403, bad


def test_a_refused_host_never_reaches_the_routes(
    container: Container, client_for: ClientFor
) -> None:
    app = build_app(container)
    reached: list[str] = []

    @app.get("/probe")
    def probe() -> dict[str, str]:
        reached.append("yes")
        return {}

    client = client_for(app)
    assert client.get("/probe", headers={"Host": "evil.example"}).status_code == 403
    assert reached == []
    assert client.get("/probe").status_code == 200


def test_a_request_with_no_host_header_is_refused() -> None:
    guard = HostGuard(ok_app, allowed=lambda _: True)
    assert status_of(call(guard, http_scope())) == 403


def test_a_request_with_two_host_headers_is_refused() -> None:
    guard = HostGuard(ok_app, allowed=lambda _: True)
    twice = http_scope(headers=[(b"host", b"localhost"), (b"host", b"localhost")])
    assert status_of(call(guard, twice)) == 403
    once = http_scope(headers=[(b"host", b"localhost")])
    assert status_of(call(guard, once)) == 200


def test_a_websocket_to_a_refused_host_is_closed() -> None:
    guard = HostGuard(ok_app, allowed=lambda _: False)
    scope = {
        "type": "websocket",
        "headers": [(b"host", b"evil.example")],
        "server": ("127.0.0.1", 1),
    }
    assert call(guard, scope) == [{"type": "websocket.close", "code": 1008}]


def test_other_scope_types_pass_through() -> None:
    guard = HostGuard(ok_app, allowed=lambda _: False)
    sent = call(guard, {"type": "lifespan", "headers": []})
    assert status_of(sent) == 200
