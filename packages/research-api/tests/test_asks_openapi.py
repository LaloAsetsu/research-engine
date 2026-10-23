"""What the contract says about asking and reading papers (the web app generates from it)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from research_api.entrypoints.cli import openapi_document

SPEC: dict[str, Any] = json.loads(openapi_document())
PATHS = SPEC["paths"]
SCHEMAS = SPEC["components"]["schemas"]

OPERATIONS = {
    ("post", "/api/v1/asks"): "createAsk",
    ("get", "/api/v1/asks/{ask_id}"): "getAsk",
    ("delete", "/api/v1/asks/{ask_id}"): "cancelAsk",
    ("get", "/api/v1/asks/{ask_id}/events"): "followAsk",
    ("get", "/api/v1/documents"): "listDocuments",
    ("get", "/api/v1/documents/{doc_id}"): "getDocument",
    ("get", "/api/v1/documents/{doc_id}/pages/{page}"): "getDocumentPage",
}


@pytest.mark.parametrize(("key", "operation_id"), OPERATIONS.items())
def test_every_operation_has_its_id_and_needs_the_session_cookie(
    key: tuple[str, str], operation_id: str
) -> None:
    method, path = key
    operation = PATHS[path][method]
    assert operation["operationId"] == operation_id
    assert operation["security"] == [{"sessionCookie": []}]
    assert {"401", "403"} <= set(operation["responses"])
    assert operation["summary"]


def test_asking_returns_202_with_the_position_and_the_stream_is_event_stream() -> None:
    create = PATHS["/api/v1/asks"]["post"]["responses"]
    assert create["202"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/AskAccepted"
    }
    assert {"422", "429", "503"} <= set(create)
    follow = PATHS["/api/v1/asks/{ask_id}/events"]["get"]["responses"]
    assert "text/event-stream" in follow["200"]["content"]
    assert "204" in follow
    assert PATHS["/api/v1/asks/{ask_id}"]["delete"]["responses"]["204"]


def test_the_problems_use_the_problem_media_type() -> None:
    for (method, path), _ in OPERATIONS.items():
        for code, response in PATHS[path][method]["responses"].items():
            if code in {"401", "403", "404", "409", "422", "429", "503"}:
                assert "application/problem+json" in response["content"], (path, code)


def test_the_request_is_strict_and_bounded() -> None:
    body = SCHEMAS["AskRequestBody"]
    assert body["required"] == ["question"]
    assert body["additionalProperties"] is False
    question = body["properties"]["question"]
    assert (question["minLength"], question["maxLength"]) == (1, 4000)
    assert body["properties"]["use_index"]["default"] is True
    assert body["properties"]["return_contexts"]["default"] is True
    bm25 = body["properties"]["bm25"]["anyOf"]
    assert {"type": "null"} in bm25
    assert next(o for o in bm25 if "enum" in o)["enum"] == ["english", "always", "never"]


def test_the_answer_has_the_run_item_shape() -> None:
    answer = SCHEMAS["AskResponse"]
    assert set(answer["required"]) == {
        "ask_id",
        "answer",
        "references_text",
        "citations",
        "contexts",
        "sources",
        "warnings",
        "index_generation",
        "timings",
    }
    assert set(SCHEMAS["CitationOut"]["required"]) == {"key", "doc_id", "first_page", "last_page"}
    assert set(SCHEMAS["ContextOut"]["required"]) == {
        "doc_id",
        "name",
        "start",
        "first_page",
        "last_page",
        "is_reference",
        "text",
    }
    assert set(SCHEMAS["TimingsOut"]["required"]) == {"retrieve_s", "answer_s", "wall_s"}


def test_the_view_names_every_state_and_the_error_carries_the_status() -> None:
    assert SCHEMAS["AskState"]["enum"] == ["queued", "running", "done", "failed", "cancelled"]
    assert set(SCHEMAS["AskErrorOut"]["required"]) == {"ask_id", "kind", "status", "message"}
    assert SCHEMAS["AskView"]["properties"]["phase"]["anyOf"][0]["enum"] == [
        "retrieving",
        "answering",
    ]


def test_a_paper_says_whether_its_pages_are_sections() -> None:
    summary = SCHEMAS["DocumentSummaryOut"]
    assert summary["properties"]["page_kind"]["enum"] == ["page", "section"]
    assert "short_id" in summary["required"]
    assert SCHEMAS["DocumentPageOut"]["properties"]["kind"]["enum"] == ["page", "section"]


def test_the_document_id_is_pattern_checked_and_pages_start_at_one() -> None:
    by_name = {
        p["name"]: p for p in PATHS["/api/v1/documents/{doc_id}/pages/{page}"]["get"]["parameters"]
    }
    assert by_name["doc_id"]["schema"]["pattern"] == "^[0-9a-f]{16,64}$"
    assert by_name["page"]["schema"]["minimum"] == 1
    listing = {p["name"]: p for p in PATHS["/api/v1/documents"]["get"]["parameters"]}
    assert (listing["limit"]["schema"]["minimum"], listing["limit"]["schema"]["maximum"]) == (
        1,
        100,
    )


def test_the_status_carries_the_engine_and_the_lane() -> None:
    assert "ask" in SCHEMAS["Status"]["required"]
    ask = SCHEMAS["AskStatus"]
    assert set(ask["required"]) == {"state", "queued", "busy"}
    assert ask["properties"]["state"]["enum"] == ["warming_up", "ready", "unavailable"]


def test_the_document_is_the_same_text_every_time() -> None:
    assert openapi_document() == openapi_document()
