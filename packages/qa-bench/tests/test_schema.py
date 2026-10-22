"""The four schemas: every fixture line is valid, and each rule the specs state is enforced."""

import json
from typing import Any

import pytest
from conftest import lines_of
from jsonschema import Draft202012Validator

from qa_bench.formats import FORMATS, QA_RUN, QA_SCORE, QSET, REVIEWS, check_line, load_schema
from qa_bench.formats.schema import schema_name

FIXTURE_FILES = {
    QSET: ["example.qset.jsonl"],
    QA_RUN: [
        "example-bare.qa-run.jsonl",
        "example-harness.qa-run.jsonl",
        "example-oracle.qa-run.jsonl",
    ],
    QA_SCORE: ["example.qa-score.jsonl"],
    REVIEWS: ["example.reviews.jsonl", "example-answers.reviews.jsonl"],
}


def all_lines():
    return [
        pytest.param(fmt, record, number, id=f"{name}:{number}")
        for fmt, names in FIXTURE_FILES.items()
        for name in names
        for number, record in enumerate(lines_of(name), 1)
    ]


@pytest.mark.parametrize(("fmt", "record", "number"), all_lines())
def test_every_fixture_line_is_valid(fmt, record, number):
    assert check_line(fmt, record, header=number == 1) == []


@pytest.mark.parametrize(("fmt", "record", "number"), all_lines())
def test_the_root_schema_agrees_with_the_dispatch(fmt, record, number):
    root = Draft202012Validator(load_schema(fmt))
    assert root.is_valid(record)


@pytest.mark.parametrize("fmt", FORMATS)
def test_each_schema_is_draft_2020_12_and_names_its_format(fmt):
    schema = load_schema(fmt)
    assert schema["$id"] == fmt
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema_name(fmt).endswith(".schema.json")
    assert "DRAFT" not in json.dumps(schema)


def test_an_unknown_format_has_no_schema():
    with pytest.raises(KeyError, match="unknown format"):
        load_schema("qset/2")


def edit(base: dict[str, Any], /, **changes: Any) -> dict[str, Any]:
    out = json.loads(json.dumps(base))
    for key, value in changes.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = value
    return out


def negatives():
    """The broken lines of the B1 check, plus the rules the specs state in words."""
    qset = {r["id"]: r for r in lines_of("example.qset.jsonl")[1:]}
    run = lines_of("example-bare.qa-run.jsonl")
    harness = lines_of("example-harness.qa-run.jsonl")
    score = lines_of("example.qa-score.jsonl")
    reviews = lines_of("example.reviews.jsonl")
    answers = lines_of("example-answers.reviews.jsonl")
    chain = qset["EX-002"]
    return [
        ("an unknown field", QSET, edit(chain, colour="red")),
        ("multi_chain without necessity", QSET, edit(chain, necessity=None)),
        ("multi_chain without synthesis", QSET, edit(chain, synthesis=None)),
        (
            "multi-paper item with one paper",
            QSET,
            edit(chain, gold_papers=chain["gold_papers"][:1]),
        ),
        ("verify answer maybe", QSET, edit(qset["EX-006"], answer="maybe")),
        ("mirror without mirror_of", QSET, edit(qset["EX-002-es"], mirror_of=None)),
        (
            "unanswerable with a gold paper",
            QSET,
            edit(qset["EX-003"], gold_papers=chain["gold_papers"]),
        ),
        ("unanswerable without search_record", QSET, edit(qset["EX-003"], search_record=None)),
        (
            "num key with a string",
            QSET,
            edit(qset["EX-001"], key_facts=[{"type": "num", "value": "1.2", "rel": 0.02}]),
        ),
        (
            "text key with a unit",
            QSET,
            edit(qset["EX-001"], key_facts=[{"type": "text", "value": "J", "unit": "GHz"}]),
        ),
        ("circuit item without circuit", QSET, edit(qset["EX-006"], circuit=None)),
        ("circuit item not content circuit", QSET, edit(qset["EX-006"], content="math")),
        (
            "a bad doc id",
            QSET,
            edit(qset["EX-001"], gold_papers=[{"doc_id": "xyz", "passages": []}]),
        ),
        ("question missing", QSET, edit(qset["EX-001"], question=None)),
        ("run line without contexts", QA_RUN, edit(run[1], contexts=None)),
        ("run line with a float repeat", QA_RUN, edit(run[1], repeat=1.5)),
        ("unknown arm", QA_RUN, edit(run[0], arm={"name": "closed-book"})),
        ("run header without model", QA_RUN, edit(run[0], model=None)),
        ("unknown error kind", QA_RUN, edit(harness[2], error={"kind": "oops"})),
        ("bad record kind", QA_SCORE, edit(score[1], record="row")),
        ("no record kind", QA_SCORE, edit(score[1], record=None)),
        ("bad group name", QA_SCORE, edit(score[3], group="category:d")),
        ("bad metric name", QA_SCORE, edit(score[1], metrics={"All Keys": 1})),
        ("score header without bootstrap", QA_SCORE, edit(score[0], bootstrap=None)),
        ("edit without note", REVIEWS, edit(reviews[3], note=None)),
        ("reject without note", REVIEWS, edit(reviews[4], note="")),
        ("accept with an edit", REVIEWS, edit(reviews[2], edit={"answer": "x"})),
        ("edit of the id", REVIEWS, edit(reviews[3], edit={"id": "EX-009"})),
        ("item verdict on an answer", REVIEWS, edit(answers[1], verdict="accept")),
        ("answer review without run_id", REVIEWS, edit(answers[1], run_id=None)),
        ("no target", REVIEWS, edit(answers[1], target=None)),
    ]


@pytest.mark.parametrize(
    ("fmt", "record"),
    [pytest.param(fmt, record, id=name) for name, fmt, record in negatives()],
)
def test_a_broken_line_is_refused(fmt, record):
    assert check_line(fmt, record, header=False, line=7)


def test_a_problem_names_the_line_and_the_pointer():
    broken = edit(lines_of("example.qset.jsonl")[1], form="nope")
    (problem, *_) = check_line(QSET, broken, header=False, line=7)
    assert problem.line == 7
    assert problem.path == "/form"
    assert "nope" in problem.message
    assert str(problem).startswith("line 7 /form:")


def test_a_message_never_echoes_a_whole_item():
    broken = edit(lines_of("example.qset.jsonl")[1], question="x" * 5000, colour="y" * 5000)
    for problem in check_line(QSET, broken, header=False):
        assert len(problem.message) <= 240


@pytest.mark.parametrize(
    "created",
    [
        "2026-01-01",
        "2026-01-01T00:00:00",
        "yesterday",
        "2026-13-01T00:00:00Z",
        "2026-01-01 00:00:00Z",
    ],
)
def test_a_date_time_needs_an_offset_and_a_real_date(created):
    header = edit(lines_of("example.qset.jsonl")[0], created=created)
    assert check_line(QSET, header, header=True)


@pytest.mark.parametrize(
    "created", ["2026-01-01T00:00:00Z", "2026-01-01T00:00:00.5+00:00", "2026-01-01T01:00:00-05:00"]
)
def test_a_date_time_with_an_offset_is_valid(created):
    header = edit(lines_of("example.qset.jsonl")[0], created=created)
    assert check_line(QSET, header, header=True) == []


@pytest.mark.parametrize("written", ["2026-02-30", "02/01/2026", "2026-1-2"])
def test_a_date_is_a_real_calendar_day(written):
    item = edit(lines_of("example.qset.jsonl")[1], written=written)
    assert check_line(QSET, item, header=False)


def test_a_header_cannot_be_an_item_and_an_item_cannot_be_a_header():
    qset = lines_of("example.qset.jsonl")
    assert check_line(QSET, qset[1], header=True)
    assert check_line(QSET, qset[0], header=False)
