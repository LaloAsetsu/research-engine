"""Sign-in over HTTP: cookies, CSRF, roles, expiry, the password change, admin session control."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from helpers import LOOPBACK, MovableClock

from research_api.application.auth import Authenticated
from research_api.bootstrap import Container, Settings, build_container
from research_api.bootstrap.seed import SeedUser
from research_api.entrypoints.cli import openapi_document
from research_api.entrypoints.http import build_app
from research_api.entrypoints.http.problems import use_problem_media_in_openapi
from research_api.entrypoints.http.security import (
    CSRF_COOKIE,
    CSRF_HEADER,
    SESSION_COOKIE,
    admin,
    member,
)

PROBLEM = "application/problem+json"
ANA = {"username": "ana", "password": "test-password-ana-01"}
BETO = {"username": "beto", "password": "test-password-beto-02"}
LUIS = {"username": "luis", "password": "temporary-test-0001"}
LOGIN = "/api/v1/auth/login"
ClientFor = Callable[..., TestClient]


def write(client: TestClient, method: str, path: str, **kwargs: Any):
    """A write request that carries the CSRF header the way the web app does."""
    headers = {CSRF_HEADER: client.cookies.get(CSRF_COOKIE, ""), **kwargs.pop("headers", {})}
    return client.request(method, path, headers=headers, **kwargs)


def sign_in(client: TestClient, who: dict[str, str]) -> None:
    done = client.post(LOGIN, json=who)
    assert done.status_code == 204, done.text


def guarded_app(container: Container) -> FastAPI:
    """The app plus two throwaway routes that stand for what later pull requests will add."""
    app = build_app(container)
    router = APIRouter()

    @router.get("/members-only")
    def members_only(who: Authenticated = Depends(member)) -> dict[str, str]:  # noqa: B008
        return {"user": who.user.username}

    @router.post("/members-write")
    def members_write(who: Authenticated = Depends(member)) -> dict[str, str]:  # noqa: B008
        return {"user": who.user.username}

    @router.get("/admins-only")
    def admins_only(who: Authenticated = Depends(admin)) -> dict[str, str]:  # noqa: B008
        return {"user": who.user.username}

    app.include_router(router, prefix="/api/v1")
    return app


@pytest.fixture
def web(container: Container, client_for: ClientFor) -> TestClient:
    return client_for(guarded_app(container))


# --- login ----------------------------------------------------------------------------------


def test_login_answers_204_and_sets_two_cookies(client: TestClient) -> None:
    response = client.post(LOGIN, json=ANA)
    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["cache-control"] == "no-store"
    cookies = {c.split("=")[0]: c for c in response.headers.get_list("set-cookie")}
    assert set(cookies) == {SESSION_COOKIE, CSRF_COOKIE}
    session = cookies[SESSION_COOKIE].lower()
    assert "httponly" in session
    assert "samesite=strict" in session
    assert "path=/" in session
    assert "max-age=604800" in session
    assert "secure" not in session
    csrf = cookies[CSRF_COOKIE].lower()
    assert "httponly" not in csrf  # the web app reads this one
    assert "samesite=strict" in csrf
    assert "path=/" in csrf


def test_the_session_cookie_is_a_long_random_value_unlike_the_csrf_one(client: TestClient) -> None:
    sign_in(client, ANA)
    token, csrf = client.cookies[SESSION_COOKIE], client.cookies[CSRF_COOKIE]
    assert len(token) >= 43 and len(csrf) >= 43
    assert token != csrf


def test_the_cookies_are_secure_when_the_public_origin_is_https(
    live_app: Callable[..., FastAPI], client_for: ClientFor
) -> None:
    app = live_app(public_origin="https://tool.example")
    live = client_for(app, headers={"Origin": "https://tool.example"})
    app.state.container.auth.users.create(
        "ana", "Ana", password=ANA["password"], must_change_password=False
    )
    done = live.post(LOGIN, json=ANA)
    assert done.status_code == 204
    assert all("secure" in c.lower() for c in done.headers.get_list("set-cookie"))


def test_a_wrong_password_and_an_unknown_user_look_the_same(client: TestClient) -> None:
    wrong = client.post(LOGIN, json={**ANA, "password": "not-the-password-1"})
    unknown = client.post(LOGIN, json={"username": "ghost", "password": "not-the-password-1"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.headers["content-type"] == unknown.headers["content-type"] == PROBLEM
    assert wrong.json() == unknown.json() | {"instance": wrong.json()["instance"]}
    assert wrong.json()["type"] == "urn:research-api:problem:unauthenticated"
    assert wrong.json()["detail"] == "Wrong username or password."
    assert "set-cookie" not in wrong.headers


def test_a_disabled_user_gets_the_same_401(container: Container, client: TestClient) -> None:
    container.auth.users.disable("beto")
    done = client.post(LOGIN, json=BETO)
    assert done.status_code == 401
    assert done.json()["detail"] == "Wrong username or password."


def test_the_sixth_failure_is_a_429_with_retry_after(client: TestClient) -> None:
    for _ in range(5):
        assert client.post(LOGIN, json={**ANA, "password": "wrong-wrong-wrong"}).status_code == 401
    blocked = client.post(LOGIN, json=ANA)  # even the right password
    assert blocked.status_code == 429
    assert blocked.headers["content-type"] == PROBLEM
    assert blocked.headers["retry-after"] == "900"
    assert blocked.json()["type"] == "urn:research-api:problem:rate-limited"
    assert "set-cookie" not in blocked.headers
    assert client.post(LOGIN, json=BETO).status_code == 204  # others are not affected


@pytest.mark.parametrize(
    ("body", "pointer"),
    [
        ({}, "/username"),
        ({"username": "ana"}, "/password"),
        ({"username": "", "password": "x"}, "/username"),
        ({"username": "ana", "password": ""}, "/password"),
        ({"username": "ana", "password": "x" * 1025}, "/password"),
        ({"username": "x" * 129, "password": "x"}, "/username"),
        ({"username": "ana", "password": "x", "remember": True}, "/remember"),
        ({"username": 5, "password": "x"}, "/username"),
    ],
)
def test_a_malformed_login_is_a_422_that_names_the_field(
    client: TestClient, body: dict[str, Any], pointer: str
) -> None:
    response = client.post(LOGIN, json=body)
    assert response.status_code == 422
    assert pointer in [e["path"] for e in response.json()["errors"]]


def test_a_login_body_that_is_not_json_is_a_400(client: TestClient) -> None:
    response = client.post(LOGIN, content=b"username=ana", headers={"Content-Type": "text/plain"})
    assert response.status_code in (400, 422)
    response = client.post(LOGIN, content=b"{nope", headers={"Content-Type": "application/json"})
    assert response.status_code == 400


def test_a_rejected_login_does_not_echo_the_password(client: TestClient) -> None:
    response = client.post(LOGIN, json={"username": "ana", "password": "x" * 1100})
    assert "xxxxxxxx" not in response.text


def test_login_needs_the_origin_header(client_for: ClientFor, container: Container) -> None:
    bare = TestClient(build_app(container), base_url=LOOPBACK, raise_server_exceptions=False)
    assert bare.post(LOGIN, json=ANA).status_code == 403
    assert bare.post(LOGIN, json=ANA, headers={"Origin": "http://evil.example"}).status_code == 403
    assert bare.post(LOGIN, json=ANA, headers={"Origin": LOOPBACK}).status_code == 204


# --- who am i -------------------------------------------------------------------------------


def test_me_without_a_session_is_a_401_problem(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM
    assert response.json()["type"] == "urn:research-api:problem:unauthenticated"


def test_me_describes_the_user(client: TestClient) -> None:
    sign_in(client, ANA)
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "username": "ana",
        "display_name": "Ana Prueba",
        "role": "admin",
        "must_change_password": False,
        "language": "es",
    }


def test_me_never_carries_the_password_hash(client: TestClient) -> None:
    sign_in(client, ANA)
    assert "argon2" not in client.get("/api/v1/auth/me").text


def test_a_made_up_cookie_is_a_401(client: TestClient) -> None:
    client.cookies.set(SESSION_COOKIE, "x" * 43)
    assert client.get("/api/v1/auth/me").status_code == 401


# --- sign out and CSRF ----------------------------------------------------------------------


def test_logout_ends_the_session_and_clears_both_cookies(client: TestClient) -> None:
    sign_in(client, ANA)
    stolen = client.cookies[SESSION_COOKIE]
    response = write(client, "POST", "/api/v1/auth/logout")
    assert response.status_code == 204
    cleared = response.headers.get_list("set-cookie")
    assert {c.split("=")[0] for c in cleared} == {SESSION_COOKIE, CSRF_COOKIE}
    assert all("max-age=0" in c.lower() for c in cleared)
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, stolen)  # a copy of the old cookie no longer works
    assert client.get("/api/v1/auth/me").status_code == 401


def test_a_write_without_the_csrf_header_is_refused_and_changes_nothing(client: TestClient) -> None:
    sign_in(client, ANA)
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 403
    assert response.json()["detail"] == "The CSRF token is missing or wrong."
    assert client.get("/api/v1/auth/me").status_code == 200


@pytest.mark.parametrize("value", ["wrong", "x" * 43, " ", "ñ" * 43, "é"])
def test_a_wrong_csrf_header_is_a_403_never_a_500(client: TestClient, value: str) -> None:
    sign_in(client, ANA)
    response = client.post("/api/v1/auth/logout", headers={CSRF_HEADER: value.encode("latin-1")})
    assert response.status_code == 403


def test_the_csrf_token_of_another_session_does_not_work(
    container: Container, client_for: ClientFor
) -> None:
    mine, theirs = client_for(build_app(container)), client_for(build_app(container))
    sign_in(mine, ANA)
    sign_in(theirs, BETO)
    response = mine.post("/api/v1/auth/logout", headers={CSRF_HEADER: theirs.cookies[CSRF_COOKIE]})
    assert response.status_code == 403


def test_reads_need_no_csrf_header(web: TestClient) -> None:
    sign_in(web, BETO)
    assert web.get("/api/v1/members-only").json() == {"user": "beto"}


def test_a_write_with_the_header_but_no_session_is_a_401_not_a_403(web: TestClient) -> None:
    assert web.post("/api/v1/members-write", headers={CSRF_HEADER: "abc"}).status_code == 401


# --- roles and the forced password change ---------------------------------------------------


def test_a_member_route_needs_a_session(web: TestClient) -> None:
    assert web.get("/api/v1/members-only").status_code == 401


def test_an_admin_route_is_for_admins_only(web: TestClient) -> None:
    assert web.get("/api/v1/admins-only").status_code == 401
    sign_in(web, BETO)
    refused = web.get("/api/v1/admins-only")
    assert refused.status_code == 403
    assert refused.json()["detail"] == "Only an admin can do this."
    web.cookies.clear()
    sign_in(web, ANA)
    assert web.get("/api/v1/admins-only").json() == {"user": "ana"}


def test_a_write_route_needs_the_csrf_header_even_for_an_admin(web: TestClient) -> None:
    sign_in(web, ANA)
    assert web.post("/api/v1/members-write").status_code == 403
    assert write(web, "POST", "/api/v1/members-write").status_code == 200


def test_until_the_password_is_changed_only_me_password_and_logout_work(web: TestClient) -> None:
    sign_in(web, LUIS)
    assert web.get("/api/v1/auth/me").json()["must_change_password"] is True
    blocked = web.get("/api/v1/members-only")
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "Change your password first."
    assert write(web, "POST", "/api/v1/members-write").status_code == 403
    done = write(
        web,
        "POST",
        "/api/v1/auth/password",
        json={"current": LUIS["password"], "new": "luis-now-has-his-own-password"},
    )
    assert done.status_code == 204
    assert web.get("/api/v1/auth/me").json()["must_change_password"] is False
    assert web.get("/api/v1/members-only").json() == {"user": "luis"}


def test_a_person_who_must_change_can_still_sign_out(web: TestClient) -> None:
    sign_in(web, LUIS)
    assert write(web, "POST", "/api/v1/auth/logout").status_code == 204


# --- change password ------------------------------------------------------------------------

NEW = "ana-picked-a-new-password"


def test_changing_the_password_replaces_the_cookies_and_ends_older_sessions(
    container: Container, client_for: ClientFor
) -> None:
    first, second = client_for(build_app(container)), client_for(build_app(container))
    sign_in(first, ANA)
    sign_in(second, ANA)
    old_token, old_csrf = first.cookies[SESSION_COOKIE], first.cookies[CSRF_COOKIE]
    done = write(
        first, "POST", "/api/v1/auth/password", json={"current": ANA["password"], "new": NEW}
    )
    assert done.status_code == 204
    assert first.cookies[SESSION_COOKIE] != old_token
    assert first.cookies[CSRF_COOKIE] != old_csrf
    assert first.get("/api/v1/auth/me").status_code == 200  # the new cookie works
    assert second.get("/api/v1/auth/me").status_code == 401  # every other session ended
    fresh = client_for(build_app(container))
    assert fresh.post(LOGIN, json=ANA).status_code == 401
    assert fresh.post(LOGIN, json={**ANA, "password": NEW}).status_code == 204


def test_the_old_cookie_does_not_survive_a_password_change(client: TestClient) -> None:
    sign_in(client, ANA)
    old = client.cookies[SESSION_COOKIE]
    write(client, "POST", "/api/v1/auth/password", json={"current": ANA["password"], "new": NEW})
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, old)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_a_wrong_current_password_is_a_403(client: TestClient) -> None:
    sign_in(client, ANA)
    response = write(
        client, "POST", "/api/v1/auth/password", json={"current": "not-the-password-1", "new": NEW}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "The current password is wrong."
    assert client.get("/api/v1/auth/me").status_code == 200  # the session stays


@pytest.mark.parametrize("new", ["short", "password1234", "ana", ANA["password"]])
def test_a_refused_new_password_is_a_422_on_the_new_field(client: TestClient, new: str) -> None:
    sign_in(client, ANA)
    response = write(
        client, "POST", "/api/v1/auth/password", json={"current": ANA["password"], "new": new}
    )
    assert response.status_code == 422
    assert {e["path"] for e in response.json()["errors"]} == {"/new"}


def test_wrong_current_passwords_run_into_the_rate_limit(client: TestClient) -> None:
    sign_in(client, ANA)
    for _ in range(5):
        write(
            client,
            "POST",
            "/api/v1/auth/password",
            json={"current": "wrong-wrong-wrong", "new": NEW},
        )
    blocked = write(
        client, "POST", "/api/v1/auth/password", json={"current": ANA["password"], "new": NEW}
    )
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0


def test_the_password_endpoint_needs_a_session_and_the_csrf_header(client: TestClient) -> None:
    body = {"current": ANA["password"], "new": NEW}
    assert client.post("/api/v1/auth/password", json=body).status_code == 401
    sign_in(client, ANA)
    assert client.post("/api/v1/auth/password", json=body).status_code == 403


# --- expiry and disabled accounts -----------------------------------------------------------


def test_an_idle_session_ends_at_twelve_hours(client_for: ClientFor, seed: list[SeedUser]) -> None:
    clock = MovableClock()
    container = build_container(
        Settings.defaults(), fake=True, seed=seed, cheap_hashing=True, clock=clock
    )
    client = client_for(build_app(container))
    sign_in(client, ANA)
    clock.advance(hours=11)
    assert client.get("/api/v1/auth/me").status_code == 200
    clock.advance(hours=12)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_a_disabled_account_is_refused_on_its_next_request(
    container: Container, client: TestClient
) -> None:
    sign_in(client, BETO)
    container.auth.users.disable("beto")
    assert client.get("/api/v1/auth/me").status_code == 401


# --- admin: sessions ------------------------------------------------------------------------


def test_the_session_list_is_for_admins_and_hides_tokens(
    container: Container, client_for: ClientFor
) -> None:
    ana, beto = client_for(build_app(container)), client_for(build_app(container))
    sign_in(ana, ANA)
    sign_in(beto, BETO)
    assert beto.get("/api/v1/admin/sessions").status_code == 403
    assert client_for(build_app(container)).get("/api/v1/admin/sessions").status_code == 401
    listed = ana.get("/api/v1/admin/sessions")
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store"
    rows = listed.json()
    assert sorted((r["username"], r["current"]) for r in rows) == [("ana", True), ("beto", False)]
    assert set(rows[0]) == {"id", "username", "created_at", "last_seen_at", "expires_at", "current"}
    for secret in (
        ana.cookies[SESSION_COOKIE],
        beto.cookies[SESSION_COOKIE],
        beto.cookies[CSRF_COOKIE],
    ):
        assert secret not in listed.text


def test_an_admin_ends_a_session_and_that_user_is_signed_out(
    container: Container, client_for: ClientFor
) -> None:
    ana, beto = client_for(build_app(container)), client_for(build_app(container))
    sign_in(ana, ANA)
    sign_in(beto, BETO)
    target = next(r["id"] for r in ana.get("/api/v1/admin/sessions").json() if not r["current"])
    assert ana.delete(f"/api/v1/admin/sessions/{target}").status_code == 403  # no CSRF header
    done = write(ana, "DELETE", f"/api/v1/admin/sessions/{target}")
    assert done.status_code == 204
    assert beto.get("/api/v1/auth/me").status_code == 401
    assert write(ana, "DELETE", f"/api/v1/admin/sessions/{target}").status_code == 404


def test_a_member_cannot_end_sessions(container: Container, client_for: ClientFor) -> None:
    ana, beto = client_for(build_app(container)), client_for(build_app(container))
    sign_in(ana, ANA)
    sign_in(beto, BETO)
    target = next(r["id"] for r in ana.get("/api/v1/admin/sessions").json() if r["current"])
    assert write(beto, "DELETE", f"/api/v1/admin/sessions/{target}").status_code == 403
    assert ana.get("/api/v1/auth/me").status_code == 200


# --- the contract ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    return json.loads(openapi_document())


def test_the_contract_lists_the_sign_in_endpoints(spec: dict[str, Any]) -> None:
    paths = spec["paths"]
    assert set(paths["/api/v1/auth/login"]) == {"post"}
    assert set(paths["/api/v1/auth/logout"]) == {"post"}
    assert set(paths["/api/v1/auth/me"]) == {"get"}
    assert set(paths["/api/v1/auth/password"]) == {"post"}
    assert set(paths["/api/v1/admin/sessions"]) == {"get"}
    assert set(paths["/api/v1/admin/sessions/{session_id}"]) == {"delete"}


def test_login_and_password_answer_204_without_a_body(spec: dict[str, Any]) -> None:
    for path in ("login", "logout", "password"):
        responses = spec["paths"][f"/api/v1/auth/{path}"]["post"]["responses"]
        assert "204" in responses
        assert "content" not in responses["204"]


def test_the_contract_documents_the_errors_in_problem_format(spec: dict[str, Any]) -> None:
    login = spec["paths"]["/api/v1/auth/login"]["post"]["responses"]
    assert set(login["401"]["content"]) == {PROBLEM}
    assert set(login["429"]["content"]) == {PROBLEM}
    assert login["429"]["headers"]["Retry-After"]["schema"] == {"type": "integer"}
    assert set(login["422"]["content"]) == {PROBLEM}
    password = spec["paths"]["/api/v1/auth/password"]["post"]["responses"]
    assert set(password["403"]["content"]) == {PROBLEM}


def test_the_session_cookie_is_the_security_scheme_of_protected_routes(
    spec: dict[str, Any],
) -> None:
    scheme = spec["components"]["securitySchemes"]["sessionCookie"]
    assert (scheme["type"], scheme["in"], scheme["name"]) == ("apiKey", "cookie", SESSION_COOKIE)
    assert CSRF_HEADER in scheme["description"]
    for path, method in (
        ("/api/v1/auth/me", "get"),
        ("/api/v1/auth/logout", "post"),
        ("/api/v1/auth/password", "post"),
        ("/api/v1/admin/sessions", "get"),
        ("/api/v1/admin/sessions/{session_id}", "delete"),
    ):
        assert spec["paths"][path][method]["security"] == [{"sessionCookie": []}]
    assert "security" not in spec["paths"]["/api/v1/auth/login"]["post"]
    assert "security" not in spec["paths"]["/api/v1/health"]["get"]


def test_the_csrf_header_is_not_a_parameter_of_every_write(spec: dict[str, Any]) -> None:
    for item in spec["paths"].values():
        for operation in item.values():
            assert all(p["name"] != CSRF_HEADER for p in operation.get("parameters", []))


def test_the_schemas_match_what_the_web_app_expects(spec: dict[str, Any]) -> None:
    schemas = spec["components"]["schemas"]
    me = schemas["Me"]
    assert set(me["properties"]) == {
        "username",
        "display_name",
        "role",
        "must_change_password",
        "language",
    }
    assert set(me["required"]) == set(me["properties"])
    assert schemas["Role"]["enum"] == ["admin", "member"]
    assert me["properties"]["language"]["enum"] == ["es", "en"]
    assert set(schemas["LoginRequest"]["properties"]) == {"username", "password"}
    assert set(schemas["PasswordChange"]["properties"]) == {"current", "new"}
    assert schemas["LoginRequest"]["additionalProperties"] is False
    assert "SessionOut" in schemas


def test_every_response_carries_a_request_id(client: TestClient) -> None:
    ids = {client.get("/api/v1/health").headers["x-request-id"] for _ in range(3)}
    assert len(ids) == 3
    assert all(len(i) == 16 for i in ids)
    assert client.get("/api/v1/auth/me").headers["x-request-id"]  # also on a 401


def test_the_contract_rewrite_skips_path_level_entries_that_are_not_operations() -> None:
    schema: dict[str, Any] = {
        "components": {
            "schemas": {
                "Problem": {
                    "properties": {
                        "errors": {"anyOf": [{"type": "null"}, {"type": "array"}]},
                        "error_id": {"anyOf": [{"type": "null"}, {"type": "string"}]},
                    }
                }
            }
        },
        "paths": {"/x": {"parameters": [{"name": "id"}], "get": {"responses": {}}}},
    }
    use_problem_media_in_openapi(schema)
    assert schema["paths"]["/x"]["parameters"] == [{"name": "id"}]
