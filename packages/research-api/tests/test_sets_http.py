"""The question-set endpoints over HTTP: sign-in, roles, If-Match, validation, import, reviews, freeze."""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from helpers import LOOPBACK, live_settings, prepare_live
from qa_bench.convert import SHAPES, from_qset
from qa_bench.formats import load_bytes
from sets_helpers import SOURCES, broken, example_bytes, example_item, item, qset_bytes

from research_api.bootstrap import Container, Settings, build_container
from research_api.bootstrap.seed import SeedUser
from research_api.domain import sets as rules
from research_api.entrypoints.cli import openapi_document
from research_api.entrypoints.http import build_app
from research_api.entrypoints.http.security import CSRF_COOKIE, CSRF_HEADER
from research_api.entrypoints.logs import LOG_NAME, configure_logging

PROBLEM = "application/problem+json"
BASE = "/api/v1/sets"
ANA = {"username": "ana", "password": "test-password-ana-01"}  # admin
BETO = {"username": "beto", "password": "test-password-beto-02"}
DANI = {"username": "dani", "password": "test-password-dani-03"}
TEMP = {"username": "temp", "password": "temporary-test-0001"}
ClientFor = Callable[..., TestClient]


class Api:
    """A signed-in browser: it sends the CSRF header on writes, as the web app does."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    def get(self, path: str, **kw: Any):
        return self.client.get(BASE + path, **kw)

    def send(self, method: str, path: str, **kw: Any):
        headers = {CSRF_HEADER: self.client.cookies.get(CSRF_COOKIE, ""), **kw.pop("headers", {})}
        return self.client.request(method, BASE + path, headers=headers, **kw)

    def post(self, path: str, **kw: Any):
        if path.endswith("/reviews") and "if-match" not in {
            h.lower() for h in kw.get("headers", {})
        }:
            # A reviewer decides on the draft they just read, and says so in If-Match.
            read = self.get(path[: -len("/reviews")]).headers.get("etag")
            if read is not None:
                kw["headers"] = {"If-Match": read, **kw.get("headers", {})}
        return self.send("POST", path, **kw)

    def post_as_sent(self, path: str, **kw: Any):
        """A post with exactly the headers given (no If-Match added for a review)."""
        return self.send("POST", path, **kw)

    def put(self, path: str, **kw: Any):
        return self.send("PUT", path, **kw)

    def delete(self, path: str, **kw: Any):
        return self.send("DELETE", path, **kw)


@pytest.fixture
def apps(container: Container, client_for: ClientFor) -> Callable[[dict[str, str]], Api]:
    """One app, as many browsers (cookie jars) as the test wants."""
    app = build_app(container)

    def browser(who: dict[str, str]) -> Api:
        client = client_for(app)
        assert client.post("/api/v1/auth/login", json=who).status_code == 204
        return Api(client)

    return browser


@pytest.fixture
def ana(apps: Callable[[dict[str, str]], Api]) -> Api:
    return apps(ANA)


@pytest.fixture
def beto(apps: Callable[[dict[str, str]], Api]) -> Api:
    return apps(BETO)


@pytest.fixture
def dani(apps: Callable[[dict[str, str]], Api]) -> Api:
    return apps(DANI)


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def new_draft(
    api: Api, items: list[dict[str, Any]] | None = None, name: str = "mine"
) -> dict[str, Any]:
    """A draft ``<name>/v1`` made by import; returns its version document."""
    done = api.post(f"/import?name={name}", content=qset_bytes(items))
    assert done.status_code == 201, done.text
    return done.json()["version"]


def etag_of_version(api: Api, name: str = "mine", version: str = "v1") -> str:
    return api.get(f"/{name}/versions/{version}").headers["etag"]


def v(name: str = "mine", version: str = "v1") -> str:
    return f"/{name}/versions/{version}"


def freeze(api: Api, path: str | None = None, **kw: Any):
    """Read the plan, then freeze exactly that plan, as the web app does."""
    path = path or v()
    plan = api.get(path + "/freeze")
    assert plan.status_code == 200, plan.text
    return api.post(path + "/freeze", headers={"If-Match": plan.headers["etag"]}, **kw)


# --- who may call ---------------------------------------------------------------------------------

ROUTES = [
    ("GET", ""),
    ("POST", ""),
    ("POST", "/validate"),
    ("GET", "/import/shapes"),
    ("POST", "/import"),
    ("GET", "/demo-papers/versions/v1"),
    ("GET", "/demo-papers/versions/v1/items/DP-001"),
    ("PUT", "/demo-papers/versions/v2/items/DP-001"),
    ("POST", "/demo-papers/versions/v2/items"),
    ("DELETE", "/demo-papers/versions/v2/items/DP-001"),
    ("GET", "/demo-papers/versions/v1/export"),
    ("GET", "/demo-papers/versions/v2/freeze"),
    ("POST", "/demo-papers/versions/v2/freeze"),
    ("GET", "/demo-papers/versions/v1/reviews"),
    ("POST", "/demo-papers/versions/v2/reviews"),
]


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_every_route_needs_a_session(client: TestClient, method: str, path: str) -> None:
    response = client.request(method, BASE + path)
    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM


@pytest.mark.parametrize(("method", "path"), [r for r in ROUTES if r[0] != "GET"])
def test_every_write_needs_the_csrf_header(ana: Api, method: str, path: str) -> None:
    response = ana.client.request(method, BASE + path)  # a session, but no X-CSRF-Token
    assert response.status_code == 403
    assert "CSRF" in response.json()["detail"]


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_a_pending_password_change_blocks_every_route(
    apps: Callable[[dict[str, str]], Api], method: str, path: str
) -> None:
    pending = apps(TEMP)
    response = pending.send(method, path)
    assert response.status_code == 403
    assert "password" in response.json()["detail"]


def test_answers_are_never_cached(ana: Api) -> None:
    for path in (
        "",
        "/demo-papers/versions/v1",
        "/demo-papers/versions/v1/items/DP-001",
        "/import/shapes",
    ):
        assert ana.get(path).headers["cache-control"] == "no-store"
    assert ana.get("/demo-papers/versions/v1/export").headers["cache-control"] == "no-store"
    assert (
        ana.get("/demo-papers/versions/v1/freeze").status_code == 403
    )  # frozen: refused, but see below


# --- the catalog and the demo sets of fake mode ---------------------------------------------------


def test_the_catalog_lists_the_demo_sets(beto: Api) -> None:
    body = beto.get("").json()
    assert [s["name"] for s in body] == ["demo-circuits", "demo-papers"]
    papers = body[1]
    assert [x["version"] for x in papers["versions"]] == ["v1", "v2"]
    v1, v2 = papers["versions"]
    assert (v1["status"], v1["owner"], v1["items"]) == ("frozen", "ana", 6)
    assert (v2["status"], v2["owner"], v2["items"]) == ("draft", "beto", 7)
    assert v1["etag"] == f'"{v1["md5"]}"'
    assert v1["frozen_at"] is not None and v2["frozen_at"] is None
    assert v2["review"] == {
        "items": 7,
        "none": 3,
        "needs_second": 1,
        "accepted": 1,
        "edited": 1,
        "rejected": 1,
    }
    assert papers["corpus"] == "demo-corpus"


def test_the_frozen_demo_version_shows_its_reviews_applied(ana: Api) -> None:
    page = ana.get(v("demo-papers", "v1") + "?limit=200").json()
    assert [i["id"] for i in page["items"]] == [
        "DP-001",
        "DP-001-es",
        "DP-002",
        "DP-003",
        "DP-004",
        "DP-005",
    ]
    assert page["header"]["set"] == "demo-papers/v1"
    assert page["items"][0]["review"] in {"accepted", "needs_second"}


# --- creating and validating -------------------------------------------------------------------------


def test_a_new_draft(beto: Api) -> None:
    done = beto.post("", json={"name": "mine", "corpus": "c1"})
    assert done.status_code == 201
    body = done.json()
    assert (body["name"], body["version"], body["status"], body["owner"]) == (
        "mine",
        "v1",
        "draft",
        "beto",
    )
    assert (body["items"], body["corpus"]) == (0, "c1")
    assert done.headers["location"] == "/api/v1/sets/mine/versions/v1"
    assert done.headers["etag"] == body["etag"]


def test_a_taken_name_is_a_conflict_and_a_copy_is_the_next_version(beto: Api) -> None:
    beto.post("", json={"name": "mine"})
    taken = beto.post("", json={"name": "mine"})
    assert taken.status_code == 409
    assert taken.headers["content-type"] == PROBLEM
    copy = beto.post("", json={"name": "mine", "copy_from": "v1"})
    assert copy.status_code == 201
    assert copy.json()["version"] == "v2"


@pytest.mark.parametrize("other", ["Mine", "MINE"])
def test_a_name_that_differs_only_in_capitals_is_a_409_and_the_first_set_is_untouched(
    ana: Api, beto: Api, other: str
) -> None:
    """On a case-insensitive disk 'Mine' and 'mine' are one folder: the catalog must refuse the
    second whatever the disk is, or it writes over the first (here a frozen version)."""
    new_draft(ana, [item("T-1")])
    frozen = freeze(ana)
    assert frozen.status_code == 200
    before = beto.get(v() + "/export").content
    attempts = [
        beto.post("", json={"name": other}),
        beto.post(f"/import?name={other}", content=qset_bytes([item("T-9")])),
        beto.post("/import", content=qset_bytes([item("T-9")], f"{other}/v4")),
        beto.post(f"/import?name={other}&version=v2", content=qset_bytes([item("T-9")])),
    ]
    for response in attempts:
        assert response.status_code == 409, response.text
        assert response.headers["content-type"] == PROBLEM
        assert "same name" in response.json()["detail"]
    assert beto.get(v() + "/export").content == before
    assert hashlib.md5(before).hexdigest() == frozen.json()["version"]["md5"]
    assert beto.get(v(other)).status_code == 404
    assert [x["name"] for x in beto.get("").json() if x["name"].lower() == "mine"] == ["mine"]


def test_a_copy_of_a_set_that_does_not_exist(beto: Api) -> None:
    assert beto.post("", json={"name": "ghost", "copy_from": "v1"}).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"name": ""},
        {"name": "a/b"},
        {"name": "x" * 65},
        {"name": "ok", "copy_from": "1"},
        {"name": "ok", "corpus": ""},
        {"name": "ok", "owner": "someone"},
        {},
        [],
    ],
)
def test_a_bad_new_set_is_a_422_with_the_field(beto: Api, body: Any) -> None:
    response = beto.post("", json=body)
    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM
    assert response.json()["errors"]


def test_malformed_json_is_a_400(beto: Api) -> None:
    response = beto.post("", content=b"{not json", headers={"Content-Type": "application/json"})
    assert response.status_code == 400


def test_validating_a_good_item_and_a_good_file(beto: Api) -> None:
    assert beto.post("/validate", json={"item": item()}).json() == {"ok": True, "errors": []}
    assert beto.post("/validate", json={"text": example_bytes().decode()}).json() == {
        "ok": True,
        "errors": [],
    }


def test_validating_a_bad_item_names_each_field(beto: Api) -> None:
    bad = item(form="multi_chain", lang="fr", colour="red")
    body = beto.post("/validate", json={"item": bad}).json()
    assert body["ok"] is False
    paths = {e["path"] for e in body["errors"]}
    assert {"/lang", "/synthesis", "/necessity", "/colour"} <= paths
    assert all(e["message"] for e in body["errors"])


def test_validating_a_bad_file_names_the_line(beto: Api) -> None:
    text = qset_bytes([item("T-1"), item("T-2", form="nope")]).decode()
    body = beto.post("/validate", json={"text": text}).json()
    assert [e["path"] for e in body["errors"]] == ["/lines/3/form"]


def test_validating_stores_nothing(beto: Api) -> None:
    before = beto.get("").json()
    beto.post("/validate", json={"text": example_bytes().decode()})
    assert beto.get("").json() == before


@pytest.mark.parametrize("body", [{}, {"item": item(), "text": "x"}, {"item": 3}, {"extra": 1}])
def test_validate_needs_exactly_one_of_item_or_text(beto: Api, body: Any) -> None:
    assert beto.post("/validate", json=body).status_code == 422


# --- importing ----------------------------------------------------------------------------------------


def test_importing_a_file_keeps_its_bytes_and_says_what_it_read(beto: Api) -> None:
    data = example_bytes()
    done = beto.post("/import", content=data, headers={"Content-Type": "application/octet-stream"})
    assert done.status_code == 201, done.text
    body = done.json()
    assert (body["items_read"], body["shape"]) == (9, None)
    version = body["version"]
    assert (version["name"], version["version"], version["status"]) == (
        "example-set",
        "v1",
        "draft",
    )
    assert version["md5"] == md5(data)
    assert done.headers["location"] == "/api/v1/sets/example-set/versions/v1"
    exported = beto.get(v("example-set") + "/export")
    assert exported.content == data
    assert exported.headers["etag"] == version["etag"]


def test_export_names_the_file_and_has_the_ndjson_type(beto: Api) -> None:
    response = beto.get(v("demo-papers", "v1") + "/export")
    assert response.headers["content-type"] == "application/x-ndjson"
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="demo-papers-v1.qset.jsonl"'
    )
    assert load_bytes(response.content).ok


def test_importing_under_a_name_and_a_version(beto: Api) -> None:
    done = beto.post("/import?name=mine&version=v4", content=example_bytes())
    assert done.json()["version"]["version"] == "v4"
    assert done.json()["version"]["name"] == "mine"
    assert beto.post("/import?name=mine&version=v4", content=example_bytes()).status_code == 409


def test_a_gzip_upload(beto: Api) -> None:
    done = beto.post("/import?name=zipped", content=gzip.compress(example_bytes()))
    assert done.status_code == 201
    assert beto.get(v("zipped") + "/export").content.startswith(
        b'{"format": "qset/1", "set": "zipped/v1"'
    )


def test_a_small_gzip_that_unzips_far_past_the_cap_is_a_413_and_stores_nothing(beto: Api) -> None:
    bomb = gzip.compress(qset_bytes([item("T-1")]) + b"\n" * (rules.MAX_IMPORT_BYTES + 1))
    assert len(bomb) < 100_000  # far under the 20 MiB the upload cap counts
    response = beto.post("/import?name=bomb", content=bomb)
    assert response.status_code == 413
    assert response.headers["content-type"] == PROBLEM
    assert "unzipped" in response.json()["detail"]
    assert beto.get(v("bomb")).status_code == 404


def test_an_upload_of_empty_gzip_members_is_refused_and_stores_nothing(beto: Api) -> None:
    """160 000 members took 3.5 s and a full 20 MiB upload minutes, in a thread of the single
    process: one member could stall the team. The speed is checked by counting work in the
    domain test, not by a clock."""
    member = gzip.compress(b"")
    upload = member * 160_000
    response = beto.post("/import?name=hollow", content=upload)
    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM
    assert "members" in response.json()["detail"]
    assert beto.get(v("hollow")).status_code == 404


def test_gzip_inside_gzip_and_broken_gzip_are_a_422(beto: Api) -> None:
    twice = gzip.compress(gzip.compress(example_bytes()))
    for body in (twice, gzip.compress(example_bytes())[:-6], b"\x1f\x8b not gzip"):
        response = beto.post("/import?name=odd", content=body)
        assert response.status_code == 422, response.text
        assert response.headers["content-type"] == PROBLEM
    assert beto.get(v("odd")).status_code == 404


def test_a_refused_file_says_where_and_stores_nothing(beto: Api) -> None:
    bad = qset_bytes([item("T-1"), item("T-2", form="multi_chain")])
    response = beto.post("/import?name=mine", content=bad)
    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM
    paths = {e["path"] for e in response.json()["errors"]}
    assert {"/lines/3/synthesis", "/lines/3/necessity"} <= paths
    assert beto.get(v("mine")).status_code == 404


def test_an_item_id_over_the_length_the_routes_accept_is_refused_at_import(beto: Api) -> None:
    longest = item("a" * rules.MAX_ITEM_ID_LENGTH)
    assert beto.post("/import?name=fits", content=qset_bytes([longest])).status_code == 201
    assert beto.get(v("fits") + f"/items/{longest['id']}").status_code == 200  # and it is reachable
    response = beto.post(
        "/import?name=toolong",
        content=qset_bytes([item("T-1"), item("a" * (rules.MAX_ITEM_ID_LENGTH + 1))]),
    )
    assert response.status_code == 422
    assert [e["path"] for e in response.json()["errors"]] == ["/lines/3/id"]
    assert beto.get(v("toolong")).status_code == 404


def test_an_empty_upload_is_a_422(beto: Api) -> None:
    assert beto.post("/import?name=mine", content=b"").status_code == 422


def test_an_upload_over_the_cap_is_a_413_without_being_kept(
    beto: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rules, "MAX_IMPORT_BYTES", 500)
    response = beto.post("/import?name=big", content=example_bytes())
    assert response.status_code == 413
    assert response.headers["content-type"] == PROBLEM
    assert beto.get(v("big")).status_code == 404


def test_a_body_without_a_length_is_capped_too(beto: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rules, "MAX_IMPORT_BYTES", 500)

    def chunks():  # an iterator body is sent chunked, with no Content-Length
        for _ in range(10):
            yield b"x" * 200

    response = beto.post("/import?name=big", content=chunks())
    assert response.status_code == 413


def test_a_caller_with_no_session_is_a_401_whatever_the_size_of_the_body(
    client: TestClient,
) -> None:
    huge = b"x" * (rules.MAX_IMPORT_BYTES + 1024 * 1024)  # over every cap: read, it would be a 413
    for path, body in (
        ("/import", huge),
        ("/validate", huge),
        ("/mine/versions/v1/items", huge),
        ("/mine/versions/v1/reviews", huge),
        ("/import", b"small"),
        ("/validate", b"{}"),
    ):
        response = client.post(BASE + path, content=body)
        assert response.status_code == 401, (path, len(body))
        assert response.headers["content-type"] == PROBLEM


def test_a_session_without_the_csrf_header_is_refused_before_the_body_is_read(ana: Api) -> None:
    huge = b"x" * (rules.MAX_IMPORT_BYTES + 1024 * 1024)
    assert ana.client.post(BASE + "/import", content=huge).status_code == 403


@pytest.mark.parametrize("name", ["import", "validate", "reviews", "freeze", "items"])
def test_the_cap_follows_the_route_not_the_end_of_the_path(
    beto: Api, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    new_draft(beto, [item(name)])
    monkeypatch.setattr(rules, "MAX_ITEM_BYTES", 300)
    response = beto.put(
        v() + f"/items/{name}",
        json=item(name, notes="n" * 1000),
        headers={"If-Match": etag_of_version(beto)},
    )
    assert response.status_code == 413, response.text  # the item cap, not 20 MiB or 256 KiB


def test_the_import_and_review_routes_keep_their_own_caps(
    beto: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    new_draft(beto, [item("T-1")])
    monkeypatch.setattr(rules, "MAX_REVIEW_BYTES", 300)
    monkeypatch.setattr(rules, "MAX_ITEM_BYTES", 100_000)
    note = {"item_id": "T-1", "verdict": "reject", "note": "n" * 1000}
    assert beto.post(v() + "/reviews", json=note).status_code == 413
    big_item = item("T-9", notes="n" * 1000)
    done = beto.post(v() + "/items", json=big_item, headers={"If-Match": etag_of_version(beto)})
    assert done.status_code == 201  # an item may be bigger than a review
    monkeypatch.setattr(rules, "MAX_IMPORT_BYTES", 5000)
    assert beto.post("/validate", json={"text": "n" * 4000}).status_code == 200


def test_an_item_body_has_a_smaller_cap(beto: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    new_draft(beto)
    monkeypatch.setattr(rules, "MAX_ITEM_BYTES", 300)
    response = beto.post(
        v() + "/items",
        json=item("T-9", notes="n" * 1000),
        headers={"If-Match": etag_of_version(beto)},
    )
    assert response.status_code == 413


@pytest.mark.parametrize(
    "query", ["name=a/b", "name=-x", "version=1", "version=v0", "name=" + "x" * 65]
)
def test_a_bad_query_parameter_is_a_422(beto: Api, query: str) -> None:
    assert beto.post(f"/import?{query}", content=example_bytes()).status_code == 422


def test_the_shapes_that_can_be_imported_are_listed(beto: Api) -> None:
    shapes = beto.get("/import/shapes").json()
    assert {s["name"] for s in shapes} == set(SHAPES)
    assert all(s["description"] and s["suffix"] for s in shapes)


SHAPE_FILES = [
    ("mathcirc-v1", "mathcirc.jsonl", "demo-corpus"),
    ("circuit-tasks-v1", "circuit-tasks.jsonl", "demo-corpus"),
    ("circuit-claims-v1", "circuit-claims.tsv", "demo-corpus"),
    ("qubitqa-pilot", "qubitqa.jsonl", None),
    ("qasper", "qasper.jsonl", "qasper-demo"),
]


@pytest.mark.parametrize(("shape", "file", "corpus"), SHAPE_FILES)
def test_an_older_file_imports_and_exports_back_to_its_rows(
    beto: Api, shape: str, file: str, corpus: str | None
) -> None:
    source = (SOURCES / file).read_bytes()
    query = f"?name=old&shape={shape}&source_file={file}" + (f"&corpus={corpus}" if corpus else "")
    done = beto.post("/import" + query, content=source)
    assert done.status_code == 201, done.text
    assert done.json()["shape"] == shape
    exported = beto.get(v("old") + "/export")
    assert md5(exported.content) == done.json()["version"]["md5"]
    loaded = load_bytes(exported.content)
    assert loaded.file is not None
    assert loaded.file.header["source"] == {"file": file, "md5": md5(source)}
    back = from_qset(shape, loaded.file)
    spec = SHAPES[shape]
    assert spec.read_rows(back.decode()) == spec.read_rows(source.decode())


def test_an_older_file_that_does_not_fit_is_refused(beto: Api) -> None:
    response = beto.post("/import?name=old&shape=mathcirc-v1&corpus=c", content=b'{"id": "MC-X"}\n')
    assert response.status_code == 422
    assert "MC-X" in response.json()["detail"]
    assert beto.post("/import?name=old&shape=csv", content=b"x\n").status_code == 422
    assert beto.post("/import?shape=mathcirc-v1&corpus=c", content=b"{}\n").status_code == 422


# --- reading a version -------------------------------------------------------------------------------


def test_a_version_page_has_the_header_the_counts_and_the_items(ana: Api) -> None:
    response = ana.get(v("demo-papers", "v2"))
    page = response.json()
    assert response.headers["etag"] == page["version"]["etag"]
    assert page["header"]["set"] == "demo-papers/v2"
    assert (page["total"], page["offset"], page["limit"]) == (7, 0, 50)
    assert page["review"]["items"] == 7
    first = page["items"][0]
    assert set(first) == {
        "id",
        "lang",
        "form",
        "content",
        "corpus",
        "split",
        "question",
        "tags",
        "mirror_of",
        "review",
        "accepted_by",
    }
    assert "gold_papers" not in first


@pytest.mark.parametrize(
    ("query", "ids"),
    [
        ("form=unanswerable", ["DP-003"]),
        ("lang=es", ["DP-001-es"]),
        ("content=math", ["DP-001", "DP-001-es", "DP-002", "DP-005", "DP-007"]),
        ("split=test", ["DP-002", "DP-003"]),
        ("tag=category=notation", ["DP-001", "DP-001-es"]),
        ("tag=qtype", ["DP-004"]),
        ("review=rejected", ["DP-004"]),
        ("review=edited", ["DP-002"]),
        ("review=accepted", ["DP-001"]),
        ("review=needs_second", ["DP-007"]),
        ("q=echo", ["DP-004"]),
        ("q=ECHO&form=single", ["DP-004"]),
        ("lang=en&form=multi_chain", ["DP-002"]),
        ("q=nothing-like-this", []),
    ],
)
def test_filters_on_the_version_page(ana: Api, query: str, ids: list[str]) -> None:
    page = ana.get(v("demo-papers", "v2") + "?" + query).json()
    assert [i["id"] for i in page["items"]] == ids
    assert page["total"] == len(ids)


def test_paging_a_version(ana: Api) -> None:
    page = ana.get(v("demo-papers", "v2") + "?offset=2&limit=3").json()
    assert [i["id"] for i in page["items"]] == ["DP-002", "DP-003", "DP-004"]
    assert page["total"] == 7
    assert ana.get(v("demo-papers", "v2") + "?offset=50").json()["items"] == []


@pytest.mark.parametrize("query", ["limit=0", "limit=201", "offset=-1", "review=bogus", "limit=x"])
def test_a_bad_page_or_filter_is_a_422(ana: Api, query: str) -> None:
    assert ana.get(v("demo-papers", "v2") + "?" + query).status_code == 422


def test_an_unknown_version_or_set_is_a_404(ana: Api) -> None:
    for path in (
        v("nope"),
        v("demo-papers", "v9"),
        v("demo-papers", "v1") + "/items/NOPE",
        v("nope") + "/export",
    ):
        response = ana.get(path)
        assert response.status_code == 404, path
        assert response.headers["content-type"] == PROBLEM


@pytest.mark.parametrize(
    "path",
    [
        "/..%2F..%2Fetc/versions/v1",
        "/demo-papers/versions/..%2Fv1",
        "/demo-papers/versions/v1/items/..%2F..%2Fx",
        "/%2e%2e/versions/v1",
        "/demo-papers/versions/v1%00",
        "/demo papers/versions/v1",
    ],
)
def test_a_name_that_is_a_path_never_reaches_the_files(ana: Api, path: str) -> None:
    assert ana.get(path).status_code in {404, 422}


def test_an_item_with_its_reviews_and_its_validation(ana: Api) -> None:
    body = ana.get(v("demo-papers", "v2") + "/items/DP-002").json()
    assert body["item"]["id"] == "DP-002"
    assert body["validation"] == []
    assert body["review"]["state"] == "edited"
    assert body["review"]["deciding"]["verdict"] == "edit"
    assert [r["verdict"] for r in body["reviews"]] == ["edit"]
    assert body["reviews"][0]["edit"]["answer"].startswith("Beta replaces")
    two = ana.get(v("demo-papers", "v2") + "/items/DP-001").json()["review"]
    assert (two["state"], two["gold"], two["accepted_by"]) == ("accepted", True, ["ana", "beto"])


# --- editing items (S2) -----------------------------------------------------------------------------


def test_adding_replacing_and_deleting_items_with_the_etag(beto: Api) -> None:
    new_draft(beto, [item("T-1")])
    tag = etag_of_version(beto)
    added = beto.post(v() + "/items", json=item("T-2"), headers={"If-Match": tag})
    assert added.status_code == 201
    assert added.headers["location"] == "/api/v1/sets/mine/versions/v1/items/T-2"
    assert added.headers["etag"] != tag
    assert added.json()["version"]["items"] == 2

    replaced = beto.put(
        v() + "/items/T-1",
        json=item("T-1", answer="new"),
        headers={"If-Match": added.headers["etag"]},
    )
    assert replaced.status_code == 200
    assert replaced.json()["item"]["answer"] == "new"
    assert replaced.headers["etag"] == replaced.json()["version"]["etag"]

    deleted = beto.delete(v() + "/items/T-2", headers={"If-Match": replaced.headers["etag"]})
    assert deleted.status_code == 200
    assert deleted.json()["items"] == 1
    assert beto.get(v() + "/items/T-2").status_code == 404
    assert beto.get(v()).headers["etag"] == deleted.headers["etag"]


def test_a_write_without_if_match_is_a_428(beto: Api) -> None:
    new_draft(beto, [item("T-1")])
    for response in (
        beto.put(v() + "/items/T-1", json=item("T-1")),
        beto.post(v() + "/items", json=item("T-2")),
        beto.delete(v() + "/items/T-1"),
    ):
        assert response.status_code == 428
        assert response.headers["content-type"] == PROBLEM
        assert response.json()["type"].endswith("precondition-required")


def test_the_second_of_two_editors_gets_a_409(ana: Api, beto: Api) -> None:
    new_draft(beto, [item("T-1")])
    read_by_ana = etag_of_version(ana)
    read_by_beto = etag_of_version(beto)
    assert read_by_ana == read_by_beto
    first = beto.put(
        v() + "/items/T-1", json=item("T-1", answer="beto"), headers={"If-Match": read_by_beto}
    )
    assert first.status_code == 200
    second = ana.put(
        v() + "/items/T-1", json=item("T-1", answer="ana"), headers={"If-Match": read_by_ana}
    )
    assert second.status_code == 409
    assert "changed" in second.json()["detail"]
    assert ana.get(v() + "/items/T-1").json()["item"]["answer"] == "beto"


def test_an_invalid_item_shows_each_error_at_its_field(beto: Api) -> None:
    new_draft(beto, [item("T-1")])
    bad = item(
        "T-1", form="multi_chain", lang="fr", key_facts=[{"type": "num", "value": "x", "rel": 0.1}]
    )
    response = beto.put(v() + "/items/T-1", json=bad, headers={"If-Match": etag_of_version(beto)})
    assert response.status_code == 422
    errors = {e["path"]: e["message"] for e in response.json()["errors"]}
    assert {"/lang", "/synthesis", "/necessity", "/key_facts/0/value"} <= set(errors)
    assert beto.get(v() + "/items/T-1").json()["item"]["lang"] == "en"


def test_the_item_in_the_body_must_be_the_item_in_the_path(beto: Api) -> None:
    new_draft(beto, [item("T-1"), item("T-2")])
    response = beto.put(
        v() + "/items/T-1", json=item("T-2"), headers={"If-Match": etag_of_version(beto)}
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["path"] == "/id"


def test_adding_a_repeated_id_is_a_409_and_a_missing_item_a_404(beto: Api) -> None:
    new_draft(beto, [item("T-1")])
    tag = {"If-Match": etag_of_version(beto)}
    assert beto.post(v() + "/items", json=item("T-1"), headers=tag).status_code == 409
    assert beto.put(v() + "/items/T-9", json=item("T-9"), headers=tag).status_code == 404
    assert beto.delete(v() + "/items/T-9", headers=tag).status_code == 404


def test_an_item_that_is_the_original_of_a_mirror_cannot_be_deleted(beto: Api) -> None:
    mirror = item("T-1-es", lang="es", lang_origin="mirror", mirror_of="T-1")
    new_draft(beto, [item("T-1"), mirror])
    response = beto.delete(v() + "/items/T-1", headers={"If-Match": etag_of_version(beto)})
    assert response.status_code == 409
    assert "T-1-es" in response.json()["detail"]


def test_only_the_owner_or_an_admin_edits_a_draft(ana: Api, beto: Api, dani: Api) -> None:
    new_draft(beto, [item("T-1")])
    tag = {"If-Match": etag_of_version(dani)}
    forbidden = dani.put(v() + "/items/T-1", json=item("T-1", answer="x"), headers=tag)
    assert forbidden.status_code == 403
    assert dani.post(v() + "/items", json=item("T-2"), headers=tag).status_code == 403
    assert dani.delete(v() + "/items/T-1", headers=tag).status_code == 403
    assert (
        ana.put(v() + "/items/T-1", json=item("T-1", answer="admin"), headers=tag).status_code
        == 200
    )


def test_an_item_with_decisions_keeps_its_text_so_two_accepts_are_of_the_same_text(
    ana: Api, beto: Api, dani: Api
) -> None:
    new_draft(beto, [item("T-1", answer="Original."), item("T-2")])
    assert (
        dani.post(v() + "/reviews", json={"item_id": "T-1", "verdict": "accept"}).status_code == 201
    )
    before = beto.get(v() + "/export").content
    refused = beto.put(
        v() + "/items/T-1",
        json=item("T-1", answer="Different."),
        headers={"If-Match": etag_of_version(beto)},
    )
    assert refused.status_code == 409
    assert refused.headers["content-type"] == PROBLEM
    assert refused.json()["type"] == "urn:research-api:problem:item-has-decisions"
    assert "edit decision" in refused.json()["detail"]
    assert beto.get(v() + "/export").content == before
    assert (
        ana.post(v() + "/reviews", json={"item_id": "T-1", "verdict": "accept"}).status_code == 201
    )
    detail = beto.get(v() + "/items/T-1").json()
    assert detail["item"]["answer"] == "Original."
    assert detail["review"]["state"] == "accepted"
    assert detail["review"]["accepted_by"] == ["ana", "dani"]
    # an item nobody decided on is still the owner's to change
    free = beto.put(
        v() + "/items/T-2",
        json=item("T-2", answer="Free."),
        headers={"If-Match": etag_of_version(beto)},
    )
    assert free.status_code == 200


def test_a_deleted_item_with_decisions_cannot_have_its_id_reused(beto: Api, dani: Api) -> None:
    new_draft(beto, [item("T-1"), item("T-2")])
    dani.post(v() + "/reviews", json={"item_id": "T-2", "verdict": "accept"})
    assert (
        beto.delete(v() + "/items/T-2", headers={"If-Match": etag_of_version(beto)}).status_code
        == 200
    )
    again = beto.post(
        v() + "/items", json=item("T-2", answer="new"), headers={"If-Match": etag_of_version(beto)}
    )
    assert again.status_code == 409
    assert again.json()["type"] == "urn:research-api:problem:item-has-decisions"


def test_a_frozen_version_takes_no_write_even_from_an_admin(ana: Api) -> None:
    tag = {"If-Match": ana.get(v("demo-papers", "v1")).headers["etag"]}
    base = v("demo-papers", "v1")
    assert (
        ana.put(base + "/items/DP-001", json=example_item("EX-001"), headers=tag).status_code == 403
    )
    assert ana.post(base + "/items", json=item("T-9"), headers=tag).status_code == 403
    assert ana.delete(base + "/items/DP-001", headers=tag).status_code == 403
    assert (
        ana.post(base + "/reviews", json={"item_id": "DP-001", "verdict": "accept"}).status_code
        == 403
    )
    assert ana.post(base + "/freeze").status_code == 403
    assert ana.get(base + "/freeze").status_code == 403


# --- the qset/1 negatives of B1, through every route that takes an item ------------------------------

NEGATIVES = [pytest.param(bad, where, id=name) for name, bad, where in broken()]


def reachable_id(bad: dict[str, Any]) -> str:
    """The id to put in a path: the item's own, unless that is the very thing that is wrong."""
    return bad["id"] if rules.valid_item_id(str(bad["id"])) else "T-1"


@pytest.mark.parametrize(("bad", "where"), NEGATIVES)
def test_validate_reports_each_qset_negative_at_its_field(
    beto: Api, bad: dict[str, Any], where: str
) -> None:
    response = beto.post("/validate", json={"item": bad})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert where in {e["path"] for e in body["errors"]}, body


@pytest.mark.parametrize(("bad", "where"), NEGATIVES)
def test_replacing_an_item_with_a_qset_negative_is_a_422_at_its_field(
    beto: Api, bad: dict[str, Any], where: str
) -> None:
    target = reachable_id(bad)
    new_draft(beto, [item(target)])
    before = beto.get(v() + "/export").content
    response = beto.put(
        v() + f"/items/{target}",
        json=bad,  # for "a bad id" the path cannot carry it: the id in the body disagrees
        headers={"If-Match": etag_of_version(beto)},
    )
    assert response.status_code == 422, response.text
    assert response.headers["content-type"] == PROBLEM
    assert where in {e["path"] for e in response.json()["errors"]}, response.json()
    assert beto.get(v() + "/export").content == before


@pytest.mark.parametrize(("bad", "where"), NEGATIVES)
def test_adding_an_item_with_a_qset_negative_is_a_422_at_its_field(
    beto: Api, bad: dict[str, Any], where: str
) -> None:
    new_draft(beto, [item("T-1")])
    before = beto.get(v() + "/export").content
    response = beto.post(v() + "/items", json=bad, headers={"If-Match": etag_of_version(beto)})
    assert response.status_code == 422, response.text
    assert where in {e["path"] for e in response.json()["errors"]}, response.json()
    assert beto.get(v() + "/export").content == before


@pytest.mark.parametrize(("bad", "where"), NEGATIVES)
def test_importing_a_file_with_a_qset_negative_is_a_422_at_its_line_and_field(
    beto: Api, bad: dict[str, Any], where: str
) -> None:
    response = beto.post("/import?name=neg", content=qset_bytes([bad]))
    assert response.status_code == 422, response.text
    assert f"/lines/2{where}" in {e["path"] for e in response.json()["errors"]}, response.json()
    assert beto.get(v("neg")).status_code == 404


VALID_START = '{"id": "T-1", "lang": "en", "question": "q", "form": "single", "corpus": "c", "gold_papers": []'


@pytest.mark.parametrize(
    "body",
    [
        "[" * 20000 + "]" * 20000,
        VALID_START + ', "x": ' + "[" * 5000 + "]" * 5000 + "}",
        VALID_START + ', "key_facts": [{"type": "num", "value": NaN, "rel": 1}]}',
        VALID_START + ', "key_facts": [{"type": "num", "value": 1e999, "rel": 1}]}',
        VALID_START + ', "notes": "bad \\ud800 surrogate"}',
        '"just a string"',
        "[]",
        "null",
        "not json",
        "",
        "{",
    ],
    ids=[
        "deep list", "deep field", "NaN", "huge number", "lone surrogate",
        "a string", "a list", "null", "not json", "empty", "cut off",
    ],
)  # fmt: skip
def test_a_hostile_item_body_is_refused_not_a_crash(beto: Api, body: str) -> None:
    new_draft(beto, [item("T-1")])
    before = beto.get(v() + "/export").content
    tag = {"If-Match": etag_of_version(beto), "Content-Type": "application/json"}
    for response in (
        beto.put(v() + "/items/T-1", content=body, headers=tag),
        beto.post(v() + "/items", content=body.replace("T-1", "T-9"), headers=tag),
    ):
        assert response.status_code in {400, 422}, response.text
        assert response.headers["content-type"] == PROBLEM
    assert beto.get(v() + "/export").content == before


def test_a_key_given_twice_is_refused_as_qa_bench_refuses_it_in_a_file(beto: Api) -> None:
    new_draft(beto, [item("T-1")])
    before = beto.get(v() + "/export").content
    json_type = {"Content-Type": "application/json"}
    tag = {"If-Match": etag_of_version(beto), **json_type}
    item_body = VALID_START + ', "answer": "first", "answer": "second"}'
    for response in (
        beto.put(v() + "/items/T-1", content=item_body, headers=tag),
        beto.post(v() + "/items", content=item_body.replace("T-1", "T-9"), headers=tag),
        beto.post("/validate", content='{"item": ' + item_body + "}", headers=json_type),
        beto.post(
            v() + "/reviews",
            content='{"item_id": "T-1", "verdict": "accept", "verdict": "reject"}',
            headers=json_type,
        ),
        beto.post("", content='{"name": "a", "name": "b"}', headers=json_type),
    ):
        assert response.status_code == 400, response.text
        assert response.headers["content-type"] == PROBLEM
        assert "twice" in response.json()["detail"]
        assert "first" not in response.text and "second" not in response.text
    assert beto.get(v() + "/export").content == before
    assert beto.get(v() + "/reviews").json() == []


def test_accents_math_and_symbols_survive_every_step(beto: Api) -> None:
    text = "¿Qué símbolo usa \u03c8 para \u2202/\u2202t en \u91cf\u5b50? \U0001f9ea $\\hat{H}|\\psi\\rangle$"
    new_draft(beto, [item("T-1")])
    done = beto.put(
        v() + "/items/T-1",
        json=item("T-1", question=text, lang="es"),
        headers={"If-Match": etag_of_version(beto)},
    )
    assert done.status_code == 200
    assert done.json()["item"]["question"] == text
    exported = beto.get(v() + "/export")
    raw = exported.content.decode("utf-8")
    assert "símbolo" in raw and "量子" in raw  # written as UTF-8, not as \\u escapes
    assert json.loads(raw.splitlines()[1])["question"] == text
    assert md5(exported.content) == beto.get(v()).json()["version"]["md5"]
    assert beto.get(v() + "?q=s%C3%ADmbolo").json()["total"] == 1
    kept = beto.post(
        "/import?name=again",
        content=exported.content,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert kept.status_code == 201
    assert beto.get(v("again") + "/items/T-1").json()["item"]["question"] == text


def test_an_item_sent_as_plain_text_is_refused(beto: Api) -> None:
    new_draft(beto, [item("T-1")])
    response = beto.put(
        v() + "/items/T-1",
        content=json.dumps(item("T-1")),
        headers={"If-Match": etag_of_version(beto), "Content-Type": "text/plain"},
    )
    assert response.status_code in {400, 422}


def test_hostile_review_and_validate_bodies_are_refused(beto: Api) -> None:
    new_draft(beto, [item("T-1")])
    json_type = {"Content-Type": "application/json"}
    for path, body in (
        ("/reviews", '{"item_id": "T-1", "verdict": "accept", "note": "bad \\ud800"}'),
        ("/reviews", '{"item_id": "T-1", "verdict": "accept", "verdict": "reject"}'),
        ("/reviews", '{"item_id": "T-1", "verdict": "edit", "note": "n", "edit": {"a": NaN}}'),
        ("/reviews", "[]"),
    ):
        response = beto.post(v() + path, content=body, headers=json_type)
        assert response.status_code in {201, 400, 422}, (body, response.text)
        assert response.status_code != 500
    # the file of decisions is still a file the format accepts
    lines = beto.get(v() + "/reviews").json()
    assert all(line["item_id"] == "T-1" for line in lines)
    for body in ('{"text": "bad \\ud800"}', '{"item": {"id": 1e999}}', '{"item": []}', "{"):
        response = beto.post("/validate", content=body, headers=json_type)
        assert response.status_code in {200, 400, 422}, (body, response.text)


# --- reviews (S3) ------------------------------------------------------------------------------------


def test_a_review_is_a_line_with_the_reviewer_the_server_knows(dani: Api) -> None:
    new_draft(dani, [item("T-1")])
    response = dani.post(
        v() + "/reviews", json={"item_id": "T-1", "verdict": "reject", "note": "unclear"}
    )
    assert response.status_code == 201
    line = response.json()
    assert (line["reviewer"], line["role"], line["target"], line["verdict"]) == (
        "dani",
        "human",
        "item",
        "reject",
    )
    assert line["set_md5"] == etag_of_version(dani).strip('"')
    assert line["note"] == "unclear"
    assert line["date"] == "2026-01-01T00:00:00+00:00"


def test_the_client_cannot_name_the_reviewer_or_the_role(dani: Api) -> None:
    new_draft(dani, [item("T-1")])
    for extra in (
        {"reviewer": "ana"},
        {"role": "checker"},
        {"date": "2020-01-01T00:00:00Z"},
        {"set_md5": "0" * 32},
    ):
        body = {"item_id": "T-1", "verdict": "accept", **extra}
        assert dani.post(v() + "/reviews", json=body).status_code == 422


def test_two_different_people_make_an_item_gold(ana: Api, beto: Api, dani: Api) -> None:
    new_draft(dani, [item("T-1"), item("T-2")])
    path = v() + "/items/T-1"
    assert ana.get(path).json()["review"]["state"] == "none"
    beto.post(v() + "/reviews", json={"item_id": "T-1", "verdict": "accept"})
    one = ana.get(path).json()["review"]
    assert (one["state"], one["gold"]) == ("needs_second", False)
    beto.post(
        v() + "/reviews", json={"item_id": "T-1", "verdict": "accept"}
    )  # the same person again
    assert ana.get(path).json()["review"]["state"] == "needs_second"
    ana.post(v() + "/reviews", json={"item_id": "T-1", "verdict": "accept"})
    two = ana.get(path).json()["review"]
    assert (two["state"], two["gold"], two["accepted_by"]) == ("accepted", True, ["ana", "beto"])
    page = ana.get(v() + "?review=accepted").json()
    assert [i["id"] for i in page["items"]] == ["T-1"]
    assert page["review"] == {
        "items": 2,
        "none": 1,
        "needs_second": 0,
        "accepted": 1,
        "edited": 0,
        "rejected": 0,
    }


def test_a_later_reject_takes_the_item_out_of_gold(ana: Api, beto: Api, dani: Api) -> None:
    new_draft(dani, [item("T-1")])
    for who in (ana, beto):
        who.post(v() + "/reviews", json={"item_id": "T-1", "verdict": "accept"})
    dani.post(v() + "/reviews", json={"item_id": "T-1", "verdict": "reject", "note": "wrong paper"})
    assert ana.get(v() + "/items/T-1").json()["review"]["state"] == "rejected"


def test_the_review_history_lists_who_and_when(ana: Api, beto: Api) -> None:
    new_draft(beto, [item("T-1"), item("T-2")])
    ana.post(v() + "/reviews", json={"item_id": "T-1", "verdict": "accept"})
    beto.post(v() + "/reviews", json={"item_id": "T-2", "verdict": "accept"})
    beto.post(v() + "/reviews", json={"item_id": "T-1", "verdict": "reject", "note": "no"})
    everything = ana.get(v() + "/reviews").json()
    assert [(r["item_id"], r["reviewer"], r["verdict"]) for r in everything] == [
        ("T-1", "ana", "accept"),
        ("T-2", "beto", "accept"),
        ("T-1", "beto", "reject"),
    ]
    only = ana.get(v() + "/reviews?item_id=T-1").json()
    assert [r["reviewer"] for r in only] == ["ana", "beto"]
    assert ana.get(v("demo-circuits") + "/reviews").json() == []


def test_an_edit_review_names_the_fields_and_waits_for_the_freeze(ana: Api, beto: Api) -> None:
    new_draft(beto, [item("T-1")])
    done = ana.post(
        v() + "/reviews",
        json={
            "item_id": "T-1",
            "verdict": "edit",
            "note": "add the method",
            "edit": {"answer": "better"},
        },
    )
    assert done.status_code == 201
    assert done.json()["edit"] == {"answer": "better"}
    assert ana.get(v() + "/items/T-1").json()["item"]["answer"] == "Synthetic answer."
    assert ana.get(v() + "/items/T-1").json()["review"]["state"] == "edited"


@pytest.mark.parametrize(
    ("body", "path"),
    [
        ({"item_id": "T-1", "verdict": "reject"}, "/note"),
        ({"item_id": "T-1", "verdict": "edit", "edit": {"answer": "x"}}, "/note"),
        ({"item_id": "T-1", "verdict": "edit", "note": "n"}, "/edit"),
        (
            {"item_id": "T-1", "verdict": "edit", "note": "n", "edit": {"form": "nope"}},
            "/edit/form",
        ),
        ({"item_id": "T-1", "verdict": "edit", "note": "n", "edit": {"id": "T-9"}}, "/edit"),
        ({"item_id": "T-1", "verdict": "accept", "edit": {"answer": "x"}}, "/edit"),
        ({"item_id": "T-1", "verdict": "maybe"}, "/verdict"),
    ],
)
def test_a_bad_review_is_a_422_at_its_field(beto: Api, body: dict[str, Any], path: str) -> None:
    new_draft(beto, [item("T-1")])
    response = beto.post(v() + "/reviews", json=body)
    assert response.status_code == 422
    assert path in {e["path"] for e in response.json()["errors"]}
    assert beto.get(v() + "/reviews").json() == []


def test_reviewing_an_item_that_is_not_there_is_a_404(beto: Api) -> None:
    new_draft(beto, [item("T-1")])
    assert (
        beto.post(v() + "/reviews", json={"item_id": "T-9", "verdict": "accept"}).status_code == 404
    )


def test_a_review_without_if_match_is_a_428_and_stores_nothing(beto: Api, dani: Api) -> None:
    new_draft(beto, [item("T-1")])
    body = {"item_id": "T-1", "verdict": "accept"}
    for headers in ({}, {"If-Match": "*"}, {"If-Match": ""}):
        response = dani.post_as_sent(v() + "/reviews", json=body, headers=headers)
        assert response.status_code == 428, headers
        assert response.headers["content-type"] == PROBLEM
    assert dani.get(v() + "/reviews").json() == []


def test_a_review_made_on_an_old_draft_is_a_409_and_stores_nothing(beto: Api, dani: Api) -> None:
    new_draft(beto, [item("T-1"), item("T-2")])
    read = etag_of_version(dani)
    changed = beto.put(
        v() + "/items/T-2", json=item("T-2", answer="Changed."), headers={"If-Match": read}
    )
    assert changed.status_code == 200
    body = {"item_id": "T-1", "verdict": "accept"}
    for sent in (read, 'W/"x"', '"0123456789abcdef0123456789abcdef"', "junk"):
        response = dani.post_as_sent(v() + "/reviews", json=body, headers={"If-Match": sent})
        assert response.status_code == 409, sent
        assert response.headers["content-type"] == PROBLEM
    assert dani.get(v() + "/reviews").json() == []


def test_a_reviewer_who_read_the_old_text_cannot_accept_the_one_that_replaced_it(
    ana: Api, beto: Api, dani: Api
) -> None:
    """The race of the review: beto opens T-1, ana replaces its answer, beto accepts. That accept
    is refused, so 'accepted by beto and dani' can never be about a text beto did not read."""
    new_draft(ana, [item("T-1", answer="Original answer.")])
    read_by_beto = beto.get(v() + "/items/T-1").headers["etag"]
    assert read_by_beto == etag_of_version(ana)
    replaced = ana.put(
        v() + "/items/T-1",
        json=item("T-1", answer="Replaced answer."),
        headers={"If-Match": read_by_beto},
    )
    assert replaced.status_code == 200
    body = {"item_id": "T-1", "verdict": "accept"}
    late = beto.post_as_sent(v() + "/reviews", json=body, headers={"If-Match": read_by_beto})
    assert late.status_code == 409
    fresh = dani.get(v() + "/items/T-1")
    assert fresh.json()["item"]["answer"] == "Replaced answer."
    accepted = dani.post(v() + "/reviews", json=body, headers={"If-Match": fresh.headers["etag"]})
    assert accepted.status_code == 201
    assert accepted.json()["set_md5"] == fresh.headers["etag"].strip('"')
    state = ana.get(v() + "/items/T-1").json()["review"]
    assert (state["state"], state["accepted_by"], state["gold"]) == (
        "needs_second",
        ["dani"],
        False,
    )
    # beto reads the new text and accepts it: now two people read it
    assert beto.post(v() + "/reviews", json=body).status_code == 201
    state = ana.get(v() + "/items/T-1").json()["review"]
    assert (state["state"], state["accepted_by"], state["gold"]) == (
        "accepted",
        ["beto", "dani"],
        True,
    )


def test_every_review_line_carries_the_md5_of_the_draft_that_was_read(beto: Api, dani: Api) -> None:
    new_draft(beto, [item("T-1")])
    read = etag_of_version(dani)
    dani.post(v() + "/reviews", json={"item_id": "T-1", "verdict": "accept"})
    assert [line["set_md5"] for line in dani.get(v() + "/reviews").json()] == [read.strip('"')]


def test_a_frozen_version_is_a_403_whatever_the_review_sends(beto: Api, dani: Api) -> None:
    new_draft(beto, [item("T-1")])
    assert freeze(beto).status_code == 200
    body = {"item_id": "T-1", "verdict": "accept"}
    assert dani.post_as_sent(v() + "/reviews", json=body).status_code == 403


# --- freezing (S5) -----------------------------------------------------------------------------------


def reviewed_draft(owner: Api, other: Api) -> None:
    new_draft(owner, [item("T-1"), item("T-2"), item("T-3"), item("T-4")])
    owner.post(v() + "/reviews", json={"item_id": "T-1", "verdict": "accept"})
    other.post(v() + "/reviews", json={"item_id": "T-1", "verdict": "accept"})
    other.post(v() + "/reviews", json={"item_id": "T-2", "verdict": "reject", "note": "duplicate"})
    other.post(
        v() + "/reviews",
        json={"item_id": "T-3", "verdict": "edit", "note": "fix", "edit": {"answer": "fixed"}},
    )


def test_the_preview_shows_the_reviews_it_would_apply_and_changes_nothing(
    ana: Api, beto: Api
) -> None:
    reviewed_draft(beto, ana)
    before = beto.get(v()).headers["etag"]
    body = beto.get(v() + "/freeze").json()
    plan = body["plan"]
    assert body["version"]["status"] == "draft"
    assert (plan["items_before"], plan["items_after"]) == (4, 3)
    assert (plan["accepted"], plan["rejected"], plan["edited"], plan["unreviewed"]) == (
        ["T-1"],
        ["T-2"],
        ["T-3"],
        ["T-4"],
    )
    assert set(plan) >= {"needs_second", "stale", "unknown"}
    assert beto.get(v()).headers["etag"] == before


def test_the_preview_names_its_plan_and_the_freeze_applies_only_that_plan(
    ana: Api, beto: Api
) -> None:
    reviewed_draft(beto, ana)
    preview = beto.get(v() + "/freeze")
    tag = preview.headers["etag"]
    assert preview.json()["plan"]["etag"] == tag
    before = beto.get(v() + "/export").content
    no_header = beto.post(v() + "/freeze")
    assert no_header.status_code == 428
    assert no_header.headers["content-type"] == PROBLEM
    assert beto.post(v() + "/freeze", headers={"If-Match": "*"}).status_code == 428
    wrong = beto.post(v() + "/freeze", headers={"If-Match": '"' + "0" * 32 + '"'})
    assert wrong.status_code == 409
    # a decision added after the plan was read is not applied unseen
    ana.post(v() + "/reviews", json={"item_id": "T-4", "verdict": "reject", "note": "late"})
    late = beto.post(v() + "/freeze", headers={"If-Match": tag})
    assert late.status_code == 409
    assert "changed since you read the plan" in late.json()["detail"]
    assert beto.get(v() + "/export").content == before
    assert beto.get(v()).json()["version"]["status"] == "draft"
    fresh = beto.get(v() + "/freeze")
    assert fresh.headers["etag"] != tag
    assert (
        beto.post(v() + "/freeze", headers={"If-Match": fresh.headers["etag"]}).status_code == 200
    )


def test_the_preview_refuses_what_the_freeze_would_and_names_the_item(beto: Api) -> None:
    mirror = item("T-1-es", lang="es", lang_origin="mirror", mirror_of="T-1")
    new_draft(beto, [item("T-0"), item("T-1"), mirror])
    beto.post(v() + "/reviews", json={"item_id": "T-1", "verdict": "reject", "note": "no"})
    beto.post(v() + "/reviews", json={"item_id": "T-1-es", "verdict": "accept"})
    for response in (beto.get(v() + "/freeze"), beto.post(v() + "/freeze")):
        assert response.status_code == 422, response.text
        assert [e["path"] for e in response.json()["errors"]] == ["/items/T-1-es/mirror_of"]
    assert beto.get(v()).json()["version"]["status"] == "draft"


def test_a_wildcard_if_match_is_not_a_version(beto: Api) -> None:
    new_draft(beto, [item("T-1")])
    for send in (
        lambda: beto.put(
            v() + "/items/T-1", json=item("T-1", answer="a"), headers={"If-Match": "*"}
        ),
        lambda: beto.post(v() + "/items", json=item("T-9"), headers={"If-Match": "*"}),
        lambda: beto.delete(v() + "/items/T-1", headers={"If-Match": "*"}),
    ):
        response = send()
        assert response.status_code == 428
        assert "does not say which version" in response.json()["detail"]


def test_freezing_applies_the_reviews_and_the_md5_never_changes_again(ana: Api, beto: Api) -> None:
    reviewed_draft(beto, ana)
    done = freeze(beto)
    assert done.status_code == 200
    version = done.json()["version"]
    assert version["status"] == "frozen"
    assert version["items"] == 3
    assert version["frozen_at"] == "2026-01-01T00:00:00Z"
    assert done.json()["plan"]["rejected"] == ["T-2"]
    exported = ana.get(v() + "/export")
    assert md5(exported.content) == version["md5"]
    assert exported.headers["etag"] == version["etag"]
    items = {i["id"]: i for i in ana.get(v() + "?limit=200").json()["items"]}
    assert set(items) == {"T-1", "T-3", "T-4"}
    assert ana.get(v() + "/items/T-3").json()["item"]["answer"] == "fixed"
    assert ana.get(v() + "/items/T-1").json()["item"]["reviewer"] in {"ana", "beto"}
    assert ana.get(v()).json()["version"]["md5"] == version["md5"]
    assert beto.post(v() + "/freeze").status_code == 403
    assert ana.get(v() + "/export").content == exported.content


def test_a_frozen_set_shows_in_the_catalog_and_can_seed_a_new_version(
    ana: Api, beto: Api, dani: Api
) -> None:
    reviewed_draft(beto, ana)
    freeze(beto)
    catalog = {s["name"]: s for s in dani.get("").json()}
    assert catalog["mine"]["versions"][0]["status"] == "frozen"
    copy = dani.post("", json={"name": "mine", "copy_from": "v1"})
    assert copy.status_code == 201
    assert copy.json()["owner"] == "dani"
    assert dani.get(v("mine", "v2")).json()["total"] == 3


def test_only_the_owner_or_an_admin_freezes(ana: Api, beto: Api, dani: Api) -> None:
    reviewed_draft(beto, ana)
    assert dani.post(v() + "/freeze").status_code == 403
    assert beto.get(v()).json()["version"]["status"] == "draft"
    assert freeze(ana).status_code == 200  # an admin


def test_freezing_to_nothing_is_a_422(beto: Api) -> None:
    new_draft(beto, [item("T-1")])
    beto.post(v() + "/reviews", json={"item_id": "T-1", "verdict": "reject", "note": "no"})
    for response in (beto.get(v() + "/freeze"), beto.post(v() + "/freeze")):
        assert response.status_code == 422
        assert "Nothing would be left" in response.json()["detail"]
    assert beto.get(v()).json()["version"]["status"] == "draft"


# --- round trips and rules between files ------------------------------------------------------------


def test_every_demo_version_exports_to_a_file_the_formats_accept(ana: Api) -> None:
    for name, version in (("demo-papers", "v1"), ("demo-papers", "v2"), ("demo-circuits", "v1")):
        data = ana.get(v(name, version) + "/export").content
        loaded = load_bytes(data)
        assert loaded.ok, loaded.problems
        assert loaded.file is not None
        assert loaded.file.header["set"] == f"{name}/{version}"
        assert loaded.file.md5 == md5(data)


def test_a_file_exported_and_imported_again_has_the_same_items(ana: Api) -> None:
    exported = ana.get(v("demo-papers", "v2") + "/export").content
    again = ana.post("/import?name=copy", content=exported)
    assert again.status_code == 201
    first = load_bytes(exported).file
    second = load_bytes(ana.get(v("copy") + "/export").content).file
    assert first is not None and second is not None
    assert second.records == first.records
    assert second.header["set"] == "copy/v1"


def test_importing_the_exported_file_under_its_own_name_keeps_the_md5(beto: Api) -> None:
    data = beto.get(v("demo-papers", "v2") + "/export").content
    # the name is taken, so ask for a free version of the same name: the header is rewritten
    done = beto.post("/import?name=demo-papers&version=v8", content=data)
    assert done.status_code == 201
    assert done.json()["version"]["md5"] != md5(data)
    # a fresh name that the header already says keeps every byte
    fresh = qset_bytes([item("T-1")], name="brand-new/v3")
    kept = beto.post("/import", content=fresh).json()["version"]
    assert (kept["name"], kept["version"], kept["md5"]) == ("brand-new", "v3", md5(fresh))


# --- the contract --------------------------------------------------------------------------------------


def test_the_openapi_document_describes_every_set_operation(container: Container) -> None:
    document = json.loads(openapi_document())
    operations = {
        op["operationId"]: (method, path)
        for path, item_ in document["paths"].items()
        for method, op in item_.items()
        if path.startswith("/api/v1/sets")
    }
    assert set(operations) == {
        "listSets", "createSet", "validateSet", "listImportShapes", "importSet", "getSetVersion",
        "getSetItem", "putSetItem", "addSetItem", "deleteSetItem", "exportSetVersion",
        "previewFreeze", "freezeSetVersion", "listSetReviews", "addSetReview",
    }  # fmt: skip
    put = document["paths"]["/api/v1/sets/{name}/versions/{version}/items/{item_id}"]["put"]
    assert {p["name"] for p in put["parameters"]} >= {"name", "version", "item_id", "if-match"}
    assert "428" in put["responses"] and "409" in put["responses"]
    review = document["paths"]["/api/v1/sets/{name}/versions/{version}/reviews"]["post"]
    assert "if-match" in {p["name"] for p in review["parameters"]}
    assert {"409", "428"} <= set(review["responses"])
    imported = document["paths"]["/api/v1/sets/import"]["post"]
    assert "application/octet-stream" in imported["requestBody"]["content"]
    assert document["paths"]["/api/v1/sets/{name}/versions/{version}/export"]["get"]["responses"][
        "200"
    ]["content"]["application/x-ndjson"]


def test_every_error_of_the_set_routes_is_a_problem_document(beto: Api) -> None:
    responses = [
        beto.get(v("nope")),
        beto.post("", json={"name": "!"}),
        beto.put(v("demo-papers", "v2") + "/items/DP-001", json={}),
        beto.post(v("demo-papers", "v1") + "/freeze"),
        beto.get("/demo-papers/versions/v1/items/NOPE"),
    ]
    for response in responses:
        assert response.status_code >= 400
        assert response.headers["content-type"] == PROBLEM
        body = response.json()
        assert {"type", "title", "status", "detail", "instance"} <= set(body)


# --- live mode: files on disk, and nothing about the content in the log --------------------------------


@pytest.fixture
def live(tmp_path: Path, seed: list[SeedUser]) -> tuple[Api, Path, Path]:
    settings = prepare_live(live_settings(tmp_path / "home", log_dir=tmp_path / "home" / "logs"))
    container = build_container(settings, cheap_hashing=True)
    for account in seed:
        container.auth.users.create(
            account.username, account.display_name, account.role, password=account.password,
            must_change_password=account.must_change_password,
        )  # fmt: skip
    assert settings.log_dir and settings.data_dir
    configure_logging(settings.log_dir)
    client = TestClient(
        build_app(container),
        base_url=LOOPBACK,
        headers={"Origin": LOOPBACK},
        raise_server_exceptions=False,
    )
    assert client.post("/api/v1/auth/login", json=BETO).status_code == 204
    return Api(client), settings.data_dir, settings.log_dir / LOG_NAME


def test_a_live_service_starts_with_no_sets_and_keeps_its_files_private(
    live: tuple[Api, Path, Path],
) -> None:
    api, data_dir, _ = live
    assert api.get("").json() == []
    api.post("/import?name=mine", content=qset_bytes([item("T-1")]))
    api.post(v() + "/reviews", json={"item_id": "T-1", "verdict": "accept"})
    sets = data_dir / "sets"
    folders = [sets, sets / "mine"]
    files = sorted((sets / "mine").iterdir())
    assert [f.name for f in files] == ["v1.qset.jsonl", "v1.reviews.jsonl"]
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o700 for p in folders)
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in files)


def test_a_name_in_other_capitals_does_not_reach_the_disk_where_the_first_set_lives(
    live: tuple[Api, Path, Path],
) -> None:
    """The case that corrupted a frozen version on a macOS disk, run on whatever disk this is."""
    api, data_dir, _ = live
    api.post("/import?name=mine", content=qset_bytes([item("T-1")]))
    frozen = freeze(api)
    assert frozen.status_code == 200
    path = data_dir / "sets" / "mine" / "v1.qset.jsonl"
    on_disk = path.read_bytes()
    assert api.post("", json={"name": "Mine"}).status_code == 409
    assert api.post("/import?name=MINE", content=qset_bytes([item("T-2")])).status_code == 409
    assert path.read_bytes() == on_disk
    assert sorted(p.name for p in (data_dir / "sets").iterdir()) == ["mine"]
    assert api.get(v()).status_code == 200
    assert api.get(v() + "/items/T-1").status_code == 200


def test_a_frozen_file_changed_on_disk_is_a_500_and_its_md5_is_not_rewritten(
    live: tuple[Api, Path, Path],
) -> None:
    api, data_dir, _ = live
    api.post("/import?name=mine", content=qset_bytes([item("T-1"), item("T-2")]))
    frozen = freeze(api)
    assert frozen.status_code == 200
    frozen_md5 = frozen.json()["version"]["md5"]
    path = data_dir / "sets" / "mine" / "v1.qset.jsonl"
    path.write_bytes(qset_bytes([item("T-1", answer="changed on disk")], name="mine/v1"))
    tag = {"If-Match": f'"{frozen_md5}"'}
    put = api.put(v() + "/items/T-1", json=item("T-1", answer="x"), headers=tag)
    assert put.status_code == 403
    for response in (api.get(v()), api.get(v() + "/export"), api.get(v() + "/items/T-1")):
        assert response.status_code == 500
        assert response.headers["content-type"] == PROBLEM
        assert "error_id" in response.json()
        assert "changed on disk" not in response.text
    catalog = {x["name"]: x for x in api.get("").json()}["mine"]["versions"][0]
    assert (catalog["status"], catalog["md5"]) == ("frozen", frozen_md5)


def test_a_half_written_review_line_on_disk_does_not_close_the_version(
    live: tuple[Api, Path, Path],
) -> None:
    api, data_dir, _ = live
    api.post("/import?name=mine", content=qset_bytes([item("T-1"), item("T-2")]))
    api.post(v() + "/reviews", json={"item_id": "T-1", "verdict": "accept"})
    reviews = data_dir / "sets" / "mine" / "v1.reviews.jsonl"
    with reviews.open("ab") as stream:
        stream.write(b'{"item_id": "T-2", "target": "ite')  # a crash in the middle of an append
    for path in ("", "/items/T-1", "/reviews", "/freeze"):
        assert api.get(v() + path).status_code == 200, path
    assert api.get("").status_code == 200
    done = api.post(v() + "/reviews", json={"item_id": "T-2", "verdict": "reject", "note": "no"})
    assert done.status_code == 201
    assert [x["item_id"] for x in api.get(v() + "/reviews").json()] == ["T-1", "T-2"]
    assert reviews.read_bytes().endswith(b"\n")
    assert b'"ite{' not in reviews.read_bytes()


def test_no_item_text_or_note_reaches_the_log(live: tuple[Api, Path, Path]) -> None:
    api, _, log_path = live
    secret_text = "zebra-quantum-marmalade"
    private_note = "walrus-justification-note"
    api.post("/import?name=mine", content=qset_bytes([item("T-1", question=f"{secret_text}?")]))
    tag = {"If-Match": etag_of_version(api)}
    api.put(v() + "/items/T-1", json=item("T-1", answer=secret_text), headers=tag)
    api.post(v() + "/reviews", json={"item_id": "T-1", "verdict": "reject", "note": private_note})
    api.post("/validate", json={"text": secret_text})
    api.get(v() + "/export")
    api.get(f"/?q={secret_text}")
    for handler in logging.getLogger("research_api").handlers:
        handler.flush()
    text = log_path.read_text(encoding="utf-8")
    assert secret_text not in text
    assert private_note not in text
    routes = [json.loads(line).get("route") for line in text.splitlines()]
    assert "/api/v1/sets/{name}/versions/{version}/items/{item_id}" in routes


def test_the_settings_of_a_fake_service_are_untouched_by_the_demo_sets() -> None:
    container = build_container(Settings.defaults(), fake=True, cheap_hashing=True)
    try:
        assert [e.name for e in container.sets.manage.list_sets()] == [
            "demo-circuits",
            "demo-papers",
        ]
    finally:
        container.close()
