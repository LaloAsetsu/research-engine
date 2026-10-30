"""The qa-bench adapter: how question-set files are read, checked, built and converted."""

from __future__ import annotations

import gzip
import hashlib
import json
from typing import Any

import pytest
from sets_helpers import (
    SOURCES,
    broken,
    example_bytes,
    example_items,
    example_lines,
    header,
    item,
    qset_bytes,
)

from research_api.adapters.bench import QaBenchSetFormats
from research_api.domain.errors import CorruptStoreError, InvalidInputError

formats = QaBenchSetFormats()


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def refused(call: Any) -> InvalidInputError:
    with pytest.raises(InvalidInputError) as caught:
        call()
    return caught.value


def paths(error: InvalidInputError) -> list[str]:
    return [e.path for e in error.errors]


# --- reading an uploaded file -----------------------------------------------------------------


def test_a_valid_file_is_read_with_its_own_md5() -> None:
    data = example_bytes()
    file = formats.read(data)
    assert file.md5 == md5(data)
    assert file.data == data
    assert file.header["set"] == "example-set/v1"
    assert [i["id"] for i in file.items][:2] == ["EX-001", "EX-002"]
    assert len(file.items) == 9


def test_the_adapter_takes_plain_bytes_only_because_qa_bench_unzips_without_a_limit() -> None:
    zipped = gzip.compress(example_bytes())
    assert "unzipped" in refused(lambda: formats.read(zipped)).detail
    assert "unzipped" in refused(
        lambda: formats.convert(
            "mathcirc-v1", zipped, set_name="x/v1", created="2026-01-01T00:00:00Z",
            corpus="c", source_file=None,
        )
    ).detail  # fmt: skip


def test_a_file_that_is_not_in_canonical_form_keeps_its_own_bytes() -> None:
    text = example_bytes().decode().replace("\n", "\r\n").encode()
    file = formats.read(text)
    assert file.data == text
    assert file.md5 == md5(text)


def test_a_broken_line_is_named_by_line_and_pointer() -> None:
    lines = example_lines()
    lines[2]["form"] = "nope"
    data = "".join(json.dumps(x) + "\n" for x in lines).encode()
    error = refused(lambda: formats.read(data))
    assert "/lines/3/form" in paths(error)
    assert error.detail == "The file is not a valid qset/1 file."


def test_every_problem_is_reported_up_to_a_hundred_then_counted() -> None:
    lines = [header(), *[{"id": f"B-{n}"} for n in range(150)]]
    data = "".join(json.dumps(x) + "\n" for x in lines).encode()
    error = refused(lambda: formats.read(data))
    assert len(error.errors) == 101
    assert error.errors[-1].path == "/"
    assert "more problems" in error.errors[-1].message


def test_a_file_of_another_format_is_refused() -> None:
    reviews = json.dumps({"format": "reviews/1", "set": "x/v1", "created": "2026-01-01T00:00:00Z"})
    error = refused(lambda: formats.read((reviews + "\n").encode()))
    assert "expected qset/1" in error.errors[0].message


@pytest.mark.parametrize(
    "data", [b"", b"\n\n", b"not json\n", b"[1, 2]\n", b'{"format": "qset/1"}\n', b"\xff\xfe\x00"]
)
def test_unreadable_files_are_refused_with_a_reason(data: bytes) -> None:
    error = refused(lambda: formats.read(data))
    assert error.errors


def test_a_key_given_twice_is_refused() -> None:
    line = '{"id": "T-1", "id": "T-2"'
    data = (json.dumps(header()) + "\n" + line + ', "lang": "en"}\n').encode()
    error = refused(lambda: formats.read(data))
    assert "given twice" in error.errors[0].message


def test_the_rules_between_lines_are_checked() -> None:
    data = qset_bytes([item("T-1"), item("T-1")])
    error = refused(lambda: formats.read(data))
    assert any("already on line" in e.message for e in error.errors)
    data = qset_bytes([item("T-1", mirror_of="NOPE", lang_origin="mirror")])
    error = refused(lambda: formats.read(data))
    assert any("not in the file" in e.message for e in error.errors)


# --- the dry run --------------------------------------------------------------------------------


def test_the_dry_run_of_a_good_file_finds_nothing() -> None:
    assert formats.check_text(example_bytes().decode()) == ()


def test_the_dry_run_reports_every_problem_without_raising() -> None:
    lines = example_lines()
    lines[1]["form"] = "nope"
    lines[3]["lang"] = "fr"
    text = "".join(json.dumps(x) + "\n" for x in lines)
    found = formats.check_text(text)
    assert {e.path for e in found} >= {"/lines/2/form", "/lines/4/lang"}


def test_the_dry_run_of_text_that_is_not_json() -> None:
    (error,) = formats.check_text("hello\n")
    assert error.path == "/lines/1"
    assert "not valid JSON" in error.message


# --- one item -----------------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_item", example_items(), ids=lambda i: i["id"])
def test_every_example_item_is_valid(fixture_item: dict[str, Any]) -> None:
    assert formats.check_item(fixture_item) == ()


@pytest.mark.parametrize(("bad", "where"), [pytest.param(b, w, id=name) for name, b, w in broken()])
def test_a_broken_item_is_refused_with_a_pointer(bad: dict[str, Any], where: str) -> None:
    errors = formats.check_item(bad)
    assert errors
    assert where in {e.path for e in errors}


def test_a_message_never_echoes_a_whole_item() -> None:
    errors = formats.check_item({**item(), "question": "x" * 5000, "colour": "y" * 5000})
    assert errors
    assert all(len(e.message) <= 240 for e in errors)


# --- building a file ------------------------------------------------------------------------------


def test_a_built_file_reads_back_with_the_same_md5() -> None:
    built = formats.build(header("demo/v3"), [item("T-1"), item("T-2")])
    assert built.md5 == md5(built.data)
    again = formats.read(built.data)
    assert again.md5 == built.md5
    assert again.items == built.items


def test_building_is_deterministic() -> None:
    first = formats.build(header(), [item("T-1")])
    second = formats.build(header(), [item("T-1")])
    assert first.data == second.data


def test_the_items_of_a_built_file_are_not_the_callers_objects() -> None:
    mine = item("T-1")
    built = formats.build(header(), [mine])
    mine["question"] = "changed afterwards"
    assert built.items[0]["question"] != "changed afterwards"
    assert formats.read(built.data).md5 == built.md5


def test_an_empty_set_is_a_file() -> None:
    built = formats.build(header(), [])
    assert built.items == ()
    assert built.data.count(b"\n") == 1


def test_a_built_file_names_the_item_that_breaks_a_rule() -> None:
    error = refused(lambda: formats.build(header(), [item("T-1"), item("T-2", form="nope")]))
    assert "/items/1/form" in paths(error)


def test_a_header_problem_is_named_under_header() -> None:
    error = refused(lambda: formats.build(header(created="yesterday"), []))
    assert any(p.startswith("/header") for p in paths(error))


def test_a_repeated_id_is_named_by_position() -> None:
    error = refused(lambda: formats.build(header(), [item("T-1"), item("T-1")]))
    assert any(p.startswith("/items/1") for p in paths(error))
    assert any("already on line" in e.message for e in error.errors)


def test_text_that_cannot_be_written_is_a_problem_not_a_crash() -> None:
    error = refused(lambda: formats.build(header(), [item("T-1", answer="bad \ud800 text")]))
    assert any("cannot be written" in e.message for e in error.errors)
    error = refused(lambda: formats.build(header(), [item("T-1", answer=float("nan"))]))
    assert error.errors


# --- stored files (trusted) -------------------------------------------------------------------------


def test_a_stored_file_is_read_without_the_schema() -> None:
    data = qset_bytes([item("T-1")])
    file = formats.peek(data)
    assert file.md5 == md5(data)
    assert file.data == data
    assert file.items[0]["id"] == "T-1"
    assert file.header["set"] == "demo/v1"


def test_a_stored_file_that_is_not_valid_would_still_be_served_as_is() -> None:
    data = qset_bytes([{"id": "T-1", "oops": True}])
    assert formats.peek(data).items == ({"id": "T-1", "oops": True},)


@pytest.mark.parametrize("data", [b"", b"not json\n", b"[1]\n", b'{"a": 1}\n[2]\n', b"\xff\xfe"])
def test_a_stored_file_that_cannot_be_read_is_a_server_problem(data: bytes) -> None:
    with pytest.raises(CorruptStoreError):
        formats.peek(data)


def test_stored_reviews_skip_the_header() -> None:
    header_line = formats.review_header("demo/v1", "2026-01-01T00:00:00+00:00")
    one = formats.review_line({"item_id": "a", "verdict": "accept"})
    assert formats.peek_reviews(header_line + one) == [{"item_id": "a", "verdict": "accept"}]
    assert formats.peek_reviews(header_line) == []


def test_a_last_review_line_without_its_line_feed_is_an_append_that_stopped() -> None:
    header_line = formats.review_header("demo/v1", "2026-01-01T00:00:00+00:00")
    one = formats.review_line({"item_id": "a", "verdict": "accept"})
    assert formats.peek_reviews(header_line + one + b'{"item_id": "b", "verd') == [
        {"item_id": "a", "verdict": "accept"}
    ]
    assert formats.peek_reviews(header_line + b'{"item_id"') == []
    # a whole line that only lacks its line feed is not acknowledged either
    assert formats.peek_reviews(header_line + one + b'{"item_id": "b", "verdict": "accept"}') == [
        {"item_id": "a", "verdict": "accept"}
    ]


@pytest.mark.parametrize("data", [b"not json\n", b"\xff", b"{broken}\n" + b'{"a": 1}\n'])
def test_stored_reviews_that_cannot_be_read_are_a_server_problem(data: bytes) -> None:
    with pytest.raises(CorruptStoreError):
        formats.peek_reviews(data)


# --- converting an older file -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("shape", "file", "corpus", "count"),
    [
        ("mathcirc-v1", "mathcirc.jsonl", "demo-corpus", 3),
        ("circuit-tasks-v1", "circuit-tasks.jsonl", "demo-corpus", 3),
        ("circuit-claims-v1", "circuit-claims.tsv", "demo-corpus", 2),
        ("qubitqa-pilot", "qubitqa.jsonl", None, 2),
        ("qasper", "qasper.jsonl", "qasper-demo", 2),
    ],
)
def test_every_older_shape_converts_to_a_valid_file(
    shape: str, file: str, corpus: str | None, count: int
) -> None:
    data = (SOURCES / file).read_bytes()
    built = formats.convert(
        shape,
        data,
        set_name="old/v1",
        created="2026-01-01T00:00:00+00:00",
        corpus=corpus,
        source_file=file,
    )
    assert len(built.items) == count
    assert built.header["set"] == "old/v1"
    assert built.header["source"] == {"file": file, "md5": md5(data)}
    assert formats.read(built.data).md5 == built.md5


def test_a_shape_that_needs_a_corpus_says_so() -> None:
    data = (SOURCES / "mathcirc.jsonl").read_bytes()
    error = refused(
        lambda: formats.convert(
            "mathcirc-v1",
            data,
            set_name="o/v1",
            created="2026-01-01T00:00:00Z",
            corpus=None,
            source_file=None,
        )
    )
    assert "corpus" in error.detail


def test_an_unknown_shape_lists_the_known_ones() -> None:
    error = refused(
        lambda: formats.convert(
            "csv",
            b"x",
            set_name="o/v1",
            created="2026-01-01T00:00:00Z",
            corpus="c",
            source_file=None,
        )
    )
    assert "mathcirc-v1" in error.detail


def test_a_row_that_does_not_fit_names_itself() -> None:
    error = refused(
        lambda: formats.convert(
            "mathcirc-v1",
            b'{"id": "MC-X"}\n',
            set_name="o/v1",
            created="2026-01-01T00:00:00Z",
            corpus="c",
            source_file=None,
        )
    )
    assert "MC-X" in error.detail


def test_the_shapes_are_described() -> None:
    names = {s.name: s for s in formats.shapes()}
    assert set(names) == {
        "mathcirc-v1",
        "circuit-tasks-v1",
        "circuit-claims-v1",
        "qubitqa-pilot",
        "qasper",
    }
    assert names["mathcirc-v1"].needs_corpus
    assert not names["qubitqa-pilot"].needs_corpus
    assert names["circuit-claims-v1"].suffix == ".tsv"


# --- reviews -----------------------------------------------------------------------------------


def review(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "item_id": "EX-001",
        "target": "item",
        "reviewer": "ana",
        "role": "human",
        "verdict": "accept",
        "date": "2026-01-04T10:00:00+00:00",
    }
    base.update(changes)
    return base


def test_a_good_review_has_no_problems() -> None:
    assert formats.check_review(review()) == ()
    assert formats.check_review(review(verdict="reject", note="why")) == ()
    assert formats.check_review(review(verdict="edit", note="n", edit={"answer": "x"})) == ()


@pytest.mark.parametrize(
    ("bad", "where"),
    [
        (review(verdict="edit", edit={"answer": "x"}), "/note"),
        (review(verdict="reject"), "/note"),
        (review(verdict="reject", note=""), "/note"),
        (review(verdict="edit", note="n", edit={"id": "other"}), "/edit"),
        (review(verdict="edit", note="n", edit={}), "/edit"),
        (review(verdict="maybe"), "/verdict"),
        (review(date="yesterday"), "/date"),
        (review(reviewer=""), "/reviewer"),
        (review(set_md5="xyz"), "/set_md5"),
    ],
)
def test_a_bad_review_is_refused_with_a_pointer(bad: dict[str, Any], where: str) -> None:
    assert where in {e.path for e in formats.check_review(bad)}


def test_review_bytes_are_one_line_each() -> None:
    assert formats.review_line(review()).endswith(b"\n")
    assert formats.review_line(review()).count(b"\n") == 1
    text = formats.review_header("demo/v2", "2026-01-01T00:00:00+00:00").decode()
    assert json.loads(text) == {
        "format": "reviews/1",
        "set": "demo/v2",
        "created": "2026-01-01T00:00:00+00:00",
    }


def test_applying_reviews_follows_the_latest_decision() -> None:
    items = [item("T-1"), item("T-2"), item("T-3"), item("T-4")]
    lines = [
        review(item_id="T-1", reviewer="ana"),
        review(item_id="T-2", verdict="reject", note="no", reviewer="ana"),
        review(item_id="T-3", verdict="edit", note="n", edit={"answer": "new"}, reviewer="beto"),
        review(item_id="GONE", reviewer="ana"),
    ]
    applied = formats.apply_reviews(items, lines, "0" * 32)
    assert applied.accepted == ("T-1",)
    assert applied.rejected == ("T-2",)
    assert applied.edited == ("T-3",)
    assert applied.unreviewed == ("T-4",)
    assert applied.unknown == ("GONE",)
    assert [i["id"] for i in applied.items] == ["T-1", "T-3", "T-4"]
    assert applied.items[0]["reviewer"] == "ana"
    assert applied.items[1]["answer"] == "new"


def test_a_decision_on_another_state_of_the_set_is_stale_but_applied() -> None:
    applied = formats.apply_reviews(
        [item("T-1")], [review(item_id="T-1", set_md5="1" * 32)], "2" * 32
    )
    assert applied.stale == ("T-1",)
    assert applied.accepted == ("T-1",)


def test_checker_lines_never_decide() -> None:
    applied = formats.apply_reviews(
        [item("T-1")], [review(item_id="T-1", verdict="reject", note="n", role="checker")], "0" * 32
    )
    assert applied.rejected == ()
    assert applied.unreviewed == ("T-1",)


def test_an_edit_that_makes_the_item_invalid_cannot_be_applied() -> None:
    bad = review(item_id="T-1", verdict="edit", note="n", edit={"form": "nope"})
    error = refused(lambda: formats.apply_reviews([item("T-1")], [bad], "0" * 32))
    assert "T-1" in error.detail
