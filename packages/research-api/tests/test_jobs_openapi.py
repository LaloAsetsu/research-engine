"""What the contract says about the benchmark (the web app generates its client from it)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from research_api.entrypoints.cli import openapi_document

SPEC: dict[str, Any] = json.loads(openapi_document())
PATHS = SPEC["paths"]
SCHEMAS = SPEC["components"]["schemas"]

OPERATIONS = {
    ("get", "/api/v1/models"): "listModels",
    ("post", "/api/v1/runs"): "startRuns",
    ("get", "/api/v1/runs"): "listRuns",
    ("get", "/api/v1/runs/{run_id}"): "getRun",
    ("get", "/api/v1/runs/{run_id}/items"): "listRunItems",
    ("post", "/api/v1/scores"): "startScore",
    ("get", "/api/v1/scores"): "listScores",
    ("get", "/api/v1/scores/{score_id}"): "getScore",
    ("get", "/api/v1/scores/{score_id}/items"): "listScoreItems",
    ("get", "/api/v1/jobs"): "listJobs",
    ("get", "/api/v1/jobs/{job_id}"): "getJob",
    ("get", "/api/v1/jobs/{job_id}/log"): "getJobLog",
    ("post", "/api/v1/jobs/{job_id}/cancel"): "cancelJob",
    ("post", "/api/v1/jobs/{job_id}/resume"): "resumeJob",
}


@pytest.mark.parametrize(("key", "operation_id"), OPERATIONS.items())
def test_every_operation_has_its_id_needs_the_session_cookie_and_a_summary(
    key: tuple[str, str], operation_id: str
) -> None:
    method, path = key
    operation = PATHS[path][method]
    assert operation["operationId"] == operation_id
    assert operation["security"] == [{"sessionCookie": []}]
    assert {"401", "403"} <= set(operation["responses"])
    assert operation["summary"] and operation["tags"] == ["benchmarks"]


def test_no_schema_took_a_module_qualified_name() -> None:
    # two models with one name would be renamed with their module path in the generated types
    assert [name for name in SCHEMAS if "__" in name] == []


def test_the_run_request_is_a_list_of_arms_and_a_list_of_models() -> None:
    body = SCHEMAS["RunRequestBody"]
    assert set(body["required"]) == {"set", "arms", "models"}
    assert body["additionalProperties"] is False
    assert (
        body["properties"]["arms"]["minItems"] == 1 and body["properties"]["arms"]["maxItems"] == 3
    )
    assert (
        body["properties"]["repeats"]["maximum"] == 10
        and body["properties"]["repeats"]["default"] == 1
    )
    assert SCHEMAS["ArmIn"]["properties"]["name"]["enum"] == ["bare", "harness", "oracle"]
    start = PATHS["/api/v1/runs"]["post"]
    assert "202" in start["responses"]
    ok = start["responses"]["202"]["content"]["application/json"]["schema"]
    assert ok["type"] == "array" and ok["items"]["$ref"].endswith("/JobOut")
    assert {"404", "409", "422", "429", "503"} <= set(start["responses"])


def test_the_score_request_takes_pairs_as_two_run_ids() -> None:
    body = SCHEMAS["ScoreRequestBody"]
    assert set(body["required"]) == {"set", "runs"}
    pairs = body["properties"]["pairs"]
    assert pairs["items"]["prefixItems"] == [{"type": "string"}, {"type": "string"}]
    assert body["properties"]["bootstrap_engine"]["enum"] == ["python_random", "numpy_pcg64"]
    assert "202" in PATHS["/api/v1/scores"]["post"]["responses"]


def test_a_job_says_its_state_and_when_to_stop_polling() -> None:
    job = SCHEMAS["JobOut"]
    assert {"id", "kind", "state", "settled", "owner", "progress", "set"} <= set(job["required"])
    assert SCHEMAS["JobState"]["enum"] == [
        "queued",
        "running",
        "cancelling",
        "cancelled",
        "done",
        "failed",
        "interrupted",
    ]
    assert job["properties"]["kind"]["enum"] == ["run", "score"]
    assert set(SCHEMAS["ProgressOut"]["required"]) == {"done", "total"}
    props = job["properties"]
    assert "position" in props and "waiting_for" in props and "run" in props and "score" in props


def test_the_log_is_text_with_its_next_offset_in_a_header() -> None:
    log = PATHS["/api/v1/jobs/{job_id}/log"]["get"]
    ok = log["responses"]["200"]
    assert "text/plain" in ok["content"]
    assert set(ok["headers"]) == {"X-Log-Next-Offset", "X-Log-Size"}
    offset = next(p for p in log["parameters"] if p["name"] == "offset")
    assert offset["schema"]["minimum"] == 0 and offset["in"] == "query"


def test_the_ids_in_a_path_have_a_pattern() -> None:
    for path in ("/api/v1/jobs/{job_id}", "/api/v1/scores/{score_id}"):
        parameter = next(p for p in PATHS[path]["get"]["parameters"] if p["in"] == "path")
        assert parameter["schema"]["pattern"].startswith("^")
    run = next(p for p in PATHS["/api/v1/runs/{run_id}"]["get"]["parameters"] if p["in"] == "path")
    assert run["schema"]["pattern"].startswith("^[A-Za-z0-9]")


def test_a_run_item_has_the_shape_of_a_qa_run_item_plus_its_question() -> None:
    item = SCHEMAS["RunItemOut"]
    assert set(item["required"]) >= {"id", "repeat", "question", "answer", "citations", "contexts"}
    assert set(SCHEMAS["RunCitationOut"]["required"]) == {"key", "doc_id"}
    assert (
        "doc_id" in SCHEMAS["RunContextOut"]["required"]
        and len(SCHEMAS["RunContextOut"]["required"]) == 1
    )
    filters = next(
        p
        for p in PATHS["/api/v1/runs/{run_id}/items"]["get"]["parameters"]
        if p["name"] == "filter"
    )
    assert filters["schema"]["enum"] == ["all", "errors", "answered"]


def test_a_score_has_a_group_record_and_a_compare_record_with_the_fields_of_the_format() -> None:
    assert set(SCHEMAS["ScoreGroupOut"]["required"]) == {"run_id", "group", "metric", "n", "mean"}
    compare = SCHEMAS["ScoreCompareOut"]
    assert set(compare["required"]) == {
        "base_run",
        "var_run",
        "group",
        "metric",
        "n",
        "base",
        "var",
        "diff",
        "ci",
        "up",
        "down",
    }
    assert set(compare["properties"]) >= {"floor", "counts"}
    assert set(SCHEMAS["ScoreOut"]["required"]) == {"summary", "header", "groups", "compares"}


def test_status_has_the_benchmark_lanes() -> None:
    assert "bench" in SCHEMAS["Status"]["required"]
    assert set(SCHEMAS["BenchStatusOut"]["required"]) == {
        "running_job",
        "queued_runs",
        "scoring",
        "queued_scores",
    }


def test_a_model_is_named_and_says_if_it_answers() -> None:
    model = SCHEMAS["ModelOut"]
    assert set(model["required"]) == {"name", "label", "available"}
    assert set(model["properties"]) == {"name", "label", "revision", "available"}
