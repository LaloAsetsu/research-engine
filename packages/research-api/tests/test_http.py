from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from helpers import live_settings, prepare_live, sign_in_as_member, write_config
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from research_api.bootstrap import Container
from research_api.domain.errors import (
    BadRequestError,
    ConflictError,
    FieldError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
    ResearchApiError,
    TooLargeError,
    UnauthenticatedError,
    UnavailableError,
    UpstreamError,
)
from research_api.entrypoints.http import build_app, create_app

PROBLEM = "application/problem+json"
ClientFor = Callable[..., TestClient]


def test_health(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_status_in_fake_mode(client: TestClient) -> None:
    body = sign_in_as_member(client).get("/api/v1/status").json()
    assert body["mode"] == "fake"
    assert body["started_at"] == "2026-01-01T00:00:00Z"
    assert body["uptime_s"] == 0
    assert body["ready"] is True
    assert body["api_version"]


def test_status_is_for_members_because_it_shows_when_someone_is_asking(
    client: TestClient,
) -> None:
    refused = client.get("/api/v1/status")
    assert refused.status_code == 401 and refused.headers["content-type"] == PROBLEM
    assert "uptime_s" not in refused.text and "ask" not in refused.json()
    temporary = {"username": "temp", "password": "temporary-test-0001"}
    assert client.post("/api/v1/auth/login", json=temporary).status_code == 204
    pending = client.get("/api/v1/status")
    assert pending.status_code == 403 and pending.json()["detail"] == "Change your password first."


def test_no_documentation_pages_and_no_served_schema(client: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json", "/api/v1/openapi.json"):
        assert client.get(path).status_code == 404


def test_an_unknown_path_is_a_problem(client: TestClient) -> None:
    response = client.get("/api/v1/nothing")
    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM
    body = response.json()
    assert body["type"] == "urn:research-api:problem:not-found"
    assert body["title"] == "Not Found"
    assert body["status"] == 404
    assert body["instance"] == "/api/v1/nothing"


def test_a_wrong_method_is_a_problem_that_keeps_allow(client: TestClient) -> None:
    response = client.post("/api/v1/health")
    assert response.status_code == 405
    assert response.headers["content-type"] == PROBLEM
    assert "GET" in response.headers["allow"]
    assert response.json()["type"] == "urn:research-api:problem:method-not-allowed"


class Item(BaseModel):
    name: str
    sizes: list[int]


@pytest.fixture
def app_with_routes(container: Container):
    app = build_app(container)
    extra = APIRouter()

    @extra.post("/items")
    def make(item: Item) -> Item:
        return item

    @extra.get("/items/{n}")
    def one(n: int) -> dict[str, int]:
        return {"n": n}

    @extra.get("/raise/{kind}")
    def raise_it(kind: str) -> None:
        raise {
            "bad": BadRequestError("duplicate key"),
            "unauth": UnauthenticatedError("sign in"),
            "forbidden": ForbiddenError("no"),
            "missing": NotFoundError("no such set"),
            "conflict": ConflictError("changed"),
            "big": TooLargeError("body over the cap"),
            "invalid": InvalidInputError(
                "bad item", (FieldError("/key_facts/0", "required"), FieldError("/a~b", "x"))
            ),
            "limited": RateLimitedError("slow down", retry_after_s=30),
            "upstream": UpstreamError("model server error"),
            "down": UnavailableError("warming up", retry_after_s=5),
            "down-no-retry": UnavailableError("down"),
            "other": ResearchApiError("odd"),
            "crash": RuntimeError("secret detail: question text"),
        }[kind]

    app.include_router(extra, prefix="/api/v1")
    return app


@pytest.fixture
def probe(app_with_routes, client_for: ClientFor) -> TestClient:
    return client_for(app_with_routes)


@pytest.mark.parametrize(
    ("kind", "status", "slug"),
    [
        ("bad", 400, "bad-request"),
        ("unauth", 401, "unauthenticated"),
        ("forbidden", 403, "forbidden"),
        ("missing", 404, "not-found"),
        ("conflict", 409, "conflict"),
        ("big", 413, "too-large"),
        ("invalid", 422, "invalid-input"),
        ("limited", 429, "rate-limited"),
        ("upstream", 502, "upstream-error"),
        ("down", 503, "unavailable"),
        ("other", 500, "internal"),
    ],
)
def test_domain_errors_map_to_their_status(
    probe: TestClient, kind: str, status: int, slug: str
) -> None:
    response = probe.get(f"/api/v1/raise/{kind}")
    assert response.status_code == status
    assert response.headers["content-type"] == PROBLEM
    body = response.json()
    assert body["type"] == f"urn:research-api:problem:{slug}"
    assert body["status"] == status
    assert body["instance"] == f"/api/v1/raise/{kind}"


def test_retry_after_is_sent_when_the_error_has_one(probe: TestClient) -> None:
    assert probe.get("/api/v1/raise/limited").headers["retry-after"] == "30"
    assert probe.get("/api/v1/raise/down").headers["retry-after"] == "5"
    assert "retry-after" not in probe.get("/api/v1/raise/down-no-retry").headers


def test_invalid_input_lists_each_error_with_its_pointer(probe: TestClient) -> None:
    body = probe.get("/api/v1/raise/invalid").json()
    assert body["detail"] == "bad item"
    assert body["errors"] == [
        {"path": "/key_facts/0", "message": "required"},
        {"path": "/a~b", "message": "x"},
    ]


def test_a_request_that_fails_validation_names_the_paths(probe: TestClient) -> None:
    response = probe.post("/api/v1/items", json={"name": 3, "sizes": [1, "x"]})
    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM
    paths = {e["path"] for e in response.json()["errors"]}
    assert paths == {"/name", "/sizes/1"}


def test_a_validation_problem_never_echoes_the_input(probe: TestClient) -> None:
    response = probe.post("/api/v1/items", json={"name": "ok", "sizes": ["private question text"]})
    assert "private question text" not in response.text


def test_a_bad_path_parameter_keeps_its_source(probe: TestClient) -> None:
    body = probe.get("/api/v1/items/abc").json()
    assert body["errors"][0]["path"] == "/path/n"


def test_malformed_json_is_a_400_problem(probe: TestClient) -> None:
    response = probe.post(
        "/api/v1/items", content=b"{not json", headers={"content-type": "application/json"}
    )
    assert response.status_code == 400
    assert response.headers["content-type"] == PROBLEM
    body = response.json()
    assert body["type"] == "urn:research-api:problem:bad-request"
    assert "errors" not in body
    assert "not json" not in response.text


def test_json_that_parses_but_has_the_wrong_shape_stays_a_422(probe: TestClient) -> None:
    response = probe.post("/api/v1/items", json=["not", "an", "object"])
    assert response.status_code == 422


def test_an_unmapped_domain_error_is_a_logged_500_without_its_detail(
    probe: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="research_api"):
        response = probe.get("/api/v1/raise/other")
    body = response.json()
    assert response.status_code == 500
    assert "odd" not in response.text
    assert len(body["error_id"]) == 32
    assert any(body["error_id"] in record.getMessage() for record in caplog.records)


def test_an_unexpected_error_hides_its_cause_and_gives_an_id(
    probe: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="research_api"):
        response = probe.get("/api/v1/raise/crash")
    assert response.status_code == 500
    assert response.headers["content-type"] == PROBLEM
    body = response.json()
    assert "secret detail" not in response.text
    assert "Traceback" not in response.text
    assert len(body["error_id"]) == 32
    assert any(body["error_id"] in record.getMessage() for record in caplog.records)


def test_openapi_describes_problems_with_the_problem_media_type(app_with_routes) -> None:
    schema = app_with_routes.openapi()
    assert schema["openapi"].startswith("3.1")
    health = schema["paths"]["/api/v1/health"]["get"]
    assert health["operationId"] == "getHealth"
    assert list(health["responses"]["default"]["content"]) == [PROBLEM]
    make = schema["paths"]["/api/v1/items"]["post"]
    assert make["responses"]["422"]["content"] == {
        PROBLEM: {"schema": {"$ref": "#/components/schemas/Problem"}}
    }
    schemas = schema["components"]["schemas"]
    assert "HTTPValidationError" not in schemas
    assert "ValidationError" not in schemas
    assert {"Problem", "ProblemField", "Health", "Status"} <= set(schemas)
    assert "ValidationError" not in json.dumps(schema)
    problem = schemas["Problem"]["properties"]
    assert "null" not in json.dumps(problem)
    assert problem["errors"]["items"] == {"$ref": "#/components/schemas/ProblemField"}
    assert problem["error_id"]["type"] == "string"


def test_openapi_is_built_once(app_with_routes) -> None:
    assert app_with_routes.openapi() is app_with_routes.openapi()


def test_an_http_status_without_a_phrase_still_gets_a_title(
    container: Container, client_for: ClientFor
) -> None:
    app = build_app(container)

    @app.get("/odd")
    def odd() -> None:
        raise HTTPException(599)

    response = client_for(app).get("/odd")
    assert response.status_code == 599
    assert response.json()["title"] == "Error"


def test_the_factory_builds_the_app_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, client_for: ClientFor
) -> None:
    monkeypatch.setenv("RESEARCH_API_FAKE", "1")
    assert client_for(create_app()).get("/api/v1/health").json() == {"ok": True}


def test_the_factory_sets_up_the_audit_log_and_silences_uvicorns_access_log(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], client_for: ClientFor
) -> None:
    access = logging.getLogger("uvicorn.access")
    monkeypatch.setattr(access, "disabled", False)  # put back when the test ends
    monkeypatch.setenv("RESEARCH_API_FAKE", "1")
    client = client_for(create_app())
    client.post("/api/v1/auth/login", json={"username": "ana", "password": "wrong-wrong-wrong"})
    assert access.disabled
    events = [json.loads(line)["event"] for line in capsys.readouterr().err.splitlines()]
    assert "login.failure" in events


def test_the_factory_writes_the_live_log_to_the_log_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, client_for: ClientFor
) -> None:
    settings = prepare_live(live_settings(tmp_path, log_dir=tmp_path / "logs"))
    config = tmp_path / "live.toml"
    write_config(
        config,
        f'data_dir = "{settings.data_dir}"\ndb_path = "{settings.db_path}"\n'
        f'log_dir = "{settings.log_dir}"\n',
    )
    monkeypatch.setattr(logging.getLogger("uvicorn.access"), "disabled", False)
    monkeypatch.delenv("RESEARCH_API_FAKE", raising=False)
    monkeypatch.setenv("RESEARCH_API_CONFIG", str(config))
    client = client_for(create_app())
    client.post("/api/v1/auth/login", json={"username": "ana", "password": "wrong-wrong-wrong"})
    written = (tmp_path / "logs" / "api.log").read_text(encoding="utf-8")
    assert '"event": "login.failure"' in written
