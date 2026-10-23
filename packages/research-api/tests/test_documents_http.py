"""Finding a cited paper and reading it at a page, over HTTP and the fake reader."""

from __future__ import annotations

import pytest
from ask_helpers import running_app

from research_api.adapters.fake.ask import FakeDocumentReader
from research_api.adapters.fake.corpus import GROVER, PAPERS, TELEPORT, TRANSMON
from research_api.domain.errors import NotFoundError

DOCS = "/api/v1/documents"


def test_the_papers_are_listed_by_title() -> None:
    with running_app() as (client, _):
        done = client.get(DOCS)
        assert done.status_code == 200 and "no-store" in done.headers["cache-control"]
        body = done.json()
        assert body["total"] == len(PAPERS) == len(body["items"])
        titles = [item["title"] for item in body["items"]]
        assert titles == sorted(titles)
        first = next(i for i in body["items"] if i["doc_id"] == TRANSMON.doc_id)
        assert first == {
            "doc_id": TRANSMON.doc_id,
            "short_id": TRANSMON.short_id,
            "title": TRANSMON.title,
            "source_name": TRANSMON.source_name,
            "source_kind": "pdf",
            "pages": 3,
            "page_kind": "page",
        }


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("transmon", {TRANSMON.doc_id}),
        ("TRANSMON", {TRANSMON.doc_id}),
        (TRANSMON.short_id, {TRANSMON.doc_id}),
        ("nothing like this", set()),
        ("  transmon  ", {TRANSMON.doc_id}),
    ],
)
def test_the_search_matches_a_part_of_the_title_or_the_start_of_an_id(
    query: str, expected: set[str]
) -> None:
    with running_app() as (client, _):
        body = client.get(DOCS, params={"query": query}).json()
        assert {i["doc_id"] for i in body["items"]} == expected
        assert body["total"] == len(expected)


def test_the_limit_cuts_the_list_but_not_the_total() -> None:
    with running_app() as (client, _):
        body = client.get(DOCS, params={"limit": 2}).json()
        assert len(body["items"]) == 2 and body["total"] == len(PAPERS)


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 101}, {"limit": "x"}, {"query": "q" * 201}]
)
def test_bad_search_parameters_are_a_422(params: dict) -> None:
    with running_app() as (client, _):
        assert client.get(DOCS, params=params).status_code == 422


def test_a_paper_is_found_by_its_full_id_or_the_prefix_a_citation_prints() -> None:
    with running_app() as (client, _):
        full = client.get(f"{DOCS}/{TRANSMON.doc_id}").json()
        short = client.get(f"{DOCS}/{TRANSMON.short_id}").json()
        assert full == short
        assert full["summary"]["title"] == TRANSMON.title
        assert full["authors"] == list(TRANSMON.authors) and full["year"] == TRANSMON.year
        assert [p["page"] for p in full["pages"]] == [1, 2, 3]
        assert full["pages"][0]["method"] == "text_layer"


def test_a_latex_paper_says_its_pages_are_sections() -> None:
    with running_app() as (client, _):
        detail = client.get(f"{DOCS}/{TELEPORT.short_id}").json()
        assert (
            detail["summary"]["page_kind"] == "section"
            and detail["summary"]["source_kind"] == "latex"
        )
        assert [p["title"] for p in detail["pages"]] == [
            "Introduction",
            "The circuit",
            "Correctness",
        ]
        page = client.get(f"{DOCS}/{TELEPORT.short_id}/pages/2").json()
        assert page["kind"] == "section" and page["title"] == "The circuit"
        assert "\\begin{quantikz}" in page["markdown"]


def test_a_page_comes_as_markdown_with_its_math() -> None:
    with running_app() as (client, _):
        page = client.get(f"{DOCS}/{TRANSMON.short_id}/pages/2")
        assert page.status_code == 200 and "no-store" in page.headers["cache-control"]
        body = page.json()
        assert body["doc_id"] == TRANSMON.doc_id
        assert (body["page"], body["pages"], body["kind"]) == (2, 3, "page")
        assert body["title"] is None
        assert body["markdown"] == TRANSMON.pages[1].markdown
        assert "$$" in body["markdown"]


@pytest.mark.parametrize(
    ("path", "status"),
    [
        (f"{DOCS}/{'f' * 64}", 404),
        (f"{DOCS}/{'f' * 16}", 404),
        (f"{DOCS}/{TRANSMON.short_id}/pages/4", 404),
        (f"{DOCS}/{TRANSMON.short_id}/pages/0", 422),
        (f"{DOCS}/{TRANSMON.short_id}/pages/-1", 422),
        (f"{DOCS}/{TRANSMON.short_id}/pages/x", 422),
        (f"{DOCS}/short", 422),
        (f"{DOCS}/{'A' * 16}", 422),
        (f"{DOCS}/{'a' * 65}", 422),
        (f"{DOCS}/..%2F..%2Fetc%2Fpasswd", 404),
        (f"{DOCS}/..%2F..%2Fetc%2Fpasswd/pages/1", 404),
    ],
)
def test_a_missing_or_malformed_paper_or_page_is_refused(path: str, status: int) -> None:
    with running_app() as (client, _):
        done = client.get(path)
        assert done.status_code == status, done.text
        assert done.headers["content-type"] == "application/problem+json"


def test_two_papers_that_share_a_prefix_are_a_409_for_the_short_id() -> None:
    reader = FakeDocumentReader(())
    twin_a, twin_b = "ab" + "0" * 14 + "0" * 48, "ab" + "0" * 14 + "1" * 48
    reader._papers = {twin_a: TRANSMON, twin_b: GROVER}
    with running_app(document_reader=reader) as (client, _):
        done = client.get(f"{DOCS}/{twin_a[:16]}")
        assert done.status_code == 409
        assert "more characters" in done.json()["detail"]
        assert client.get(f"{DOCS}/{twin_a[:17]}").status_code == 200  # one more character
        assert client.get(f"{DOCS}/{twin_b}/pages/1").status_code == 200
        assert client.get(f"{DOCS}/{twin_a[:16]}/pages/1").status_code == 409


def test_the_reader_answers_without_http() -> None:
    reader = FakeDocumentReader()
    assert reader.get(TRANSMON.doc_id).summary.doc_id == TRANSMON.doc_id
    with pytest.raises(NotFoundError):
        reader.page(TRANSMON.doc_id, 9)
    with pytest.raises(NotFoundError):
        reader.get("f" * 64)
