"""The Origin check on writes."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import LOOPBACK, call, http_scope, ok_app, status_of

from research_api.bootstrap import Container, Settings
from research_api.entrypoints.http import build_app
from research_api.entrypoints.http.origin import OriginGuard, origin_allowed_by

PROBLEM = "application/problem+json"
ClientFor = Callable[..., TestClient]
TAILNET = "http://box.tail1234.ts.net:8090"


# --- the Origin check -----------------------------------------------------------------------


def test_fake_mode_takes_any_loopback_origin_and_nothing_else() -> None:
    allowed = origin_allowed_by(Settings.defaults(), fake=True)
    for ok in (
        "http://127.0.0.1:8090",
        "http://localhost:5173",
        "http://127.0.0.1:4173",
        "http://[::1]:5173",
        "http://LOCALHOST:5173",
        "https://localhost:5173",
        "http://localhost",
    ):
        assert allowed(ok), ok
    for bad in (
        "null",
        "",
        "http://evil.example",
        "http://localhost.evil.example:5173",
        "http://127.0.0.1.evil.example:8090",
        "http://100.64.0.5:8090",
        "http://localhost:5173/path",
        "ftp://localhost",
        "localhost:5173",
        "http://user@localhost:5173",
    ):
        assert not allowed(bad), bad


def test_live_mode_takes_the_public_origin_exactly() -> None:
    allowed = origin_allowed_by(Settings(bind_host="100.64.0.5", public_origin=TAILNET), fake=False)
    assert allowed(TAILNET)
    assert allowed("HTTP://BOX.tail1234.ts.net:8090")  # written the way a browser might case it
    for bad in (
        "http://box.tail1234.ts.net",
        "http://box.tail1234.ts.net:8091",
        "https://box.tail1234.ts.net:8090",
        "http://100.64.0.5:8090",
        "http://localhost:8090",
        "http://127.0.0.1:5173",
        "null",
        "http://evil.example:8090",
    ):
        assert not allowed(bad), bad


def test_live_mode_without_a_public_origin_expects_the_bind_address() -> None:
    allowed = origin_allowed_by(Settings(bind_host="100.64.0.5", port=9000), fake=False)
    assert allowed("http://100.64.0.5:9000")
    assert not allowed("http://100.64.0.5:8090")


def test_the_origin_guard_leaves_reads_alone(container: Container, client_for: ClientFor) -> None:
    bare = TestClient(build_app(container), base_url=LOOPBACK, raise_server_exceptions=False)
    assert bare.get("/api/v1/health").status_code == 200
    assert bare.head("/api/v1/health").status_code in (200, 405)
    assert bare.options("/api/v1/health").status_code in (200, 405)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_every_write_method_needs_a_matching_origin(method: str) -> None:
    guard = OriginGuard(ok_app, allowed=lambda value: value == "http://good")
    assert status_of(call(guard, http_scope(method))) == 403
    wrong = http_scope(method, headers=[(b"origin", b"http://bad")])
    assert status_of(call(guard, wrong)) == 403
    right = http_scope(method, headers=[(b"origin", b"http://good")])
    assert status_of(call(guard, right)) == 200


def test_two_origin_headers_are_refused() -> None:
    guard = OriginGuard(ok_app, allowed=lambda _: True)
    twice = http_scope("POST", headers=[(b"origin", b"http://good"), (b"origin", b"http://good")])
    assert status_of(call(guard, twice)) == 403


def test_the_refusal_is_a_problem_document(container: Container) -> None:
    bare = TestClient(build_app(container), base_url=LOOPBACK, raise_server_exceptions=False)
    response = bare.post("/api/v1/auth/login", json={})
    assert response.status_code == 403
    assert response.headers["content-type"] == PROBLEM
    assert response.json()["detail"].startswith("A write request must come from")
    # the check runs before the body is read, so a bad body is not a 422 here
    assert response.json()["type"] == "urn:research-api:problem:forbidden"


def test_an_evil_origin_cannot_log_in_even_with_the_right_password(
    container: Container, client_for: ClientFor
) -> None:
    attacker = client_for(build_app(container), headers={"Origin": "http://evil.example"})
    response = attacker.post(
        "/api/v1/auth/login", json={"username": "ana", "password": "test-password-ana-01"}
    )
    assert response.status_code == 403
    assert "set-cookie" not in response.headers


def test_live_mode_refuses_a_loopback_origin_that_is_not_the_public_one(
    live_app: Callable[..., FastAPI], client_for: ClientFor
) -> None:
    app = live_app("100.64.0.5", public_origin=TAILNET)
    app.state.container.auth.users.create(
        "ana", "Ana", password="a-long-enough-password", must_change_password=False
    )
    body = {"username": "ana", "password": "a-long-enough-password"}
    url = "http://100.64.0.5:8090"
    for origin in ("http://localhost:8090", "http://100.64.0.5:8090", "http://evil.example"):
        client = client_for(app, url, headers={"Origin": origin})
        assert client.post("/api/v1/auth/login", json=body).status_code == 403, origin
    good = client_for(app, url, headers={"Origin": TAILNET})
    assert good.post("/api/v1/auth/login", json=body).status_code == 204
