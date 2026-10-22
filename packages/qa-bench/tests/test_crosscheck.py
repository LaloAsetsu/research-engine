"""Rules that need two files: a run, a score or a reviews file against its set."""

import json

import pytest
from conftest import FIXTURES, jsonl_text, lines_of

from qa_bench.formats import (
    QA_RUN,
    QSET,
    build,
    check_against_set,
    read_qset,
    read_reviews,
    read_run,
    read_score,
)


@pytest.fixture
def qset():
    return read_qset(FIXTURES / "example.qset.jsonl")


@pytest.mark.parametrize(
    "name",
    ["example-bare.qa-run.jsonl", "example-harness.qa-run.jsonl", "example-oracle.qa-run.jsonl"],
)
def test_the_example_runs_belong_to_the_example_set(qset, name):
    assert check_against_set(read_run(FIXTURES / name), qset) == []


def test_the_example_score_and_reviews_belong_to_the_example_set(qset):
    assert check_against_set(read_score(FIXTURES / "example.qa-score.jsonl"), qset) == []
    assert check_against_set(read_reviews(FIXTURES / "example.reviews.jsonl"), qset) == []


def run_with(**header):
    lines = lines_of("example-bare.qa-run.jsonl")
    lines[0].update(header)
    return build(QA_RUN, lines[0], lines[1:])


def test_a_run_for_another_md5_or_name_is_refused(qset):
    other_md5 = run_with(set={"name": "example-set/v1", "md5": "9" * 32})
    (problem,) = check_against_set(other_md5, qset)
    assert problem.path == "/set/md5"
    other_name = run_with(set={"name": "other/v1", "md5": qset.md5})
    (problem,) = check_against_set(other_name, qset)
    assert problem.path == "/set/name"


def test_an_answer_for_an_unknown_item_is_refused_at_its_line(qset):
    lines = lines_of("example-bare.qa-run.jsonl")
    lines[2]["id"] = "EX-404"
    run = build(QA_RUN, lines[0], lines[1:])
    (problem,) = check_against_set(run, qset)
    assert (problem.line, problem.path) == (3, "/id")


def test_oracle_contexts_are_the_gold_passages_in_set_order(qset):
    lines = lines_of("example-oracle.qa-run.jsonl")
    lines[1]["contexts"] = [{"doc_id": "a1" * 32, "text": "some other text"}]
    run = build(QA_RUN, lines[0], lines[1:])
    (problem,) = check_against_set(run, qset)
    assert problem.path == "/contexts"


def test_a_score_or_reviews_file_for_another_set_is_refused(qset):
    score = lines_of("example.qa-score.jsonl")
    score[0]["set"]["md5"] = "8" * 32
    built = build("qa-score/1", score[0], score[1:])
    assert [p.path for p in check_against_set(built, qset)] == ["/set/md5"]
    reviews = lines_of("example.reviews.jsonl")
    reviews[0]["set"] = "other/v1"
    built = build("reviews/1", reviews[0], reviews[1:])
    assert [p.path for p in check_against_set(built, qset)] == ["/set/name"]


def test_a_decision_about_an_unknown_item_is_refused(qset):
    reviews = lines_of("example.reviews.jsonl")
    reviews[1]["item_id"] = "EX-404"
    built = build("reviews/1", reviews[0], reviews[1:])
    (problem,) = check_against_set(built, qset)
    assert (problem.line, problem.path) == (2, "/item_id")


def test_only_a_set_can_be_the_set_and_a_set_has_nothing_to_check(qset):
    run = read_run(FIXTURES / "example-bare.qa-run.jsonl")
    with pytest.raises(ValueError, match="must be a qset/1 file"):
        check_against_set(run, run)
    with pytest.raises(ValueError, match="does not belong to a set"):
        check_against_set(qset, qset)


def test_the_text_of_a_set_is_the_identity_a_run_points_at(qset):
    text = (FIXTURES / "example.qset.jsonl").read_text(encoding="utf-8")
    changed = json.loads(text.splitlines()[1])
    changed["notes"] = "changed"
    lines = lines_of("example.qset.jsonl")
    lines[1] = changed
    other = build(QSET, lines[0], lines[1:])
    assert other.md5 != qset.md5
    run = read_run(FIXTURES / "example-bare.qa-run.jsonl")
    assert [p.path for p in check_against_set(run, other)] == ["/set/md5"]
    assert jsonl_text(lines)
