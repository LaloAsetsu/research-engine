"""Applying reviews/1 decisions: the latest line holds, accept, edit and reject."""

import pytest
from conftest import FIXTURES, lines_of

from qa_bench.formats import REVIEWS, FormatError, build, check_line, read_qset, read_reviews
from qa_bench.reviews import ReviewError, apply_reviews, apply_to_set, latest_decisions

CREATED = "2026-02-01T00:00:00+00:00"


@pytest.fixture
def qset():
    return read_qset(FIXTURES / "example.qset.jsonl")


@pytest.fixture
def reviews():
    return read_reviews(FIXTURES / "example.reviews.jsonl")


def line(item_id, verdict, date, **more):
    base = {
        "item_id": item_id,
        "target": "item",
        "reviewer": "r1",
        "role": "human",
        "verdict": verdict,
        "date": date,
    }
    if verdict in ("edit", "reject"):
        base["note"] = "because"
    return {**base, **more}


def test_the_latest_line_per_item_holds(reviews):
    decisions = latest_decisions(reviews.records)
    assert decisions["EX-001"]["verdict"] == "accept"
    assert decisions["EX-001"]["reviewer"] == "reviewer-b"


def test_on_equal_dates_the_later_line_holds():
    lines = [
        line("A", "accept", "2026-01-01T00:00:00+00:00", reviewer="first"),
        line("A", "reject", "2026-01-01T00:00:00+00:00", reviewer="second"),
    ]
    assert latest_decisions(lines)["A"]["reviewer"] == "second"
    assert latest_decisions(reversed(lines))["A"]["reviewer"] == "first"


def test_dates_are_compared_as_instants_not_as_text():
    lines = [
        line("A", "accept", "2026-01-01T12:00:00+00:00", reviewer="noon-utc"),
        line("A", "reject", "2026-01-01T11:00:00-02:00", reviewer="later-in-utc"),
    ]
    assert latest_decisions(lines)["A"]["reviewer"] == "later-in-utc"


def test_only_item_decisions_of_the_asked_roles_count(reviews):
    answers = read_reviews(FIXTURES / "example-answers.reviews.jsonl")
    assert latest_decisions(answers.records) == {}
    assert "EX-005" not in latest_decisions(reviews.records)
    both = latest_decisions(reviews.records, roles=("human", "checker"))
    assert both["EX-005"]["role"] == "checker"


def test_roles_given_as_one_string_are_refused_instead_of_matched_by_substring(reviews):
    with pytest.raises(TypeError, match="not one string"):
        latest_decisions(reviews.records, roles="human")
    assert latest_decisions(reviews.records, roles=frozenset({"human"})) == latest_decisions(
        reviews.records
    )


def test_accept_edit_and_reject_change_the_items(qset, reviews):
    applied = apply_reviews(qset.records, reviews.records, set_md5=qset.md5)
    by_id = {i["id"]: i for i in applied.items}
    assert applied.accepted == ("EX-001",)
    assert by_id["EX-001"]["reviewer"] == "reviewer-b"
    assert applied.edited == ("EX-004",)
    assert by_id["EX-004"]["answer"] == "8.6 ms (Hahn echo)"
    assert by_id["EX-004"]["reviewer"] == "reviewer-a"
    assert applied.rejected == ("EX-003",)
    assert "EX-003" not in by_id
    assert "EX-005" in applied.unreviewed
    assert "reviewer" not in by_id["EX-005"]
    assert [i["id"] for i in applied.items] == [
        r["id"] for r in qset.records if r["id"] != "EX-003"
    ]


def test_the_input_items_are_not_changed(qset, reviews):
    before = [dict(r) for r in qset.records]
    apply_reviews(qset.records, reviews.records)
    assert list(qset.records) == before


def test_an_agent_check_can_be_asked_to_count(qset, reviews):
    applied = apply_reviews(qset.records, reviews.records, roles=("human", "checker"))
    assert applied.rejected == ("EX-003", "EX-005")


def test_a_decision_on_an_older_set_is_stale_but_applied(qset):
    lines = [line("EX-001", "accept", "2026-01-01T00:00:00+00:00", set_md5="7" * 32)]
    applied = apply_reviews(qset.records, lines, set_md5=qset.md5)
    assert applied.stale == ("EX-001",)
    assert applied.accepted == ("EX-001",)
    assert apply_reviews(qset.records, lines).stale == ()


def test_a_decision_on_an_unknown_item_is_listed(qset):
    lines = [line("EX-404", "accept", "2026-01-01T00:00:00+00:00")]
    assert apply_reviews(qset.records, lines).unknown == ("EX-404",)


def test_an_edit_that_leaves_an_invalid_item_is_refused(qset):
    lines = [line("EX-001", "edit", "2026-01-01T00:00:00+00:00", edit={"form": "nope"})]
    with pytest.raises(ReviewError, match="edit of EX-001 leaves an invalid item"):
        apply_reviews(qset.records, lines)


def test_an_edit_line_without_fields_passes_the_schema_but_cannot_be_applied(qset):
    bare = line("EX-001", "edit", "2026-01-01T00:00:00+00:00")
    assert "edit" not in bare
    assert check_line(REVIEWS, bare, header=False) == []
    for lines in ([bare], [dict(bare, edit={})]):
        with pytest.raises(ReviewError, match="edit of EX-001 names no fields"):
            apply_reviews(qset.records, lines)


def test_an_edit_may_add_a_field(qset):
    lines = [line("EX-006", "edit", "2026-01-01T00:00:00+00:00", edit={"split": "dev"})]
    applied = apply_reviews(qset.records, lines)
    assert {i["id"]: i for i in applied.items}["EX-006"]["split"] == "dev"


def test_apply_to_set_makes_a_new_valid_version_with_a_new_md5(qset, reviews):
    frozen, applied = apply_to_set(qset, reviews, created=CREATED, set_name="example-set/v2")
    assert frozen.header["set"] == "example-set/v2"
    assert frozen.header["created"] == CREATED
    assert qset.md5 in frozen.header["notes"]
    assert "1 accepted, 1 edited, 1 rejected" in frozen.header["notes"]
    assert frozen.md5 != qset.md5
    assert len(frozen.records) == len(qset.records) - 1
    assert applied.rejected == ("EX-003",)


def test_apply_to_set_counts_the_decisions_made_on_another_version(qset, reviews):
    frozen, applied = apply_to_set(qset, reviews, created=CREATED)
    assert applied.stale == ()
    assert frozen.header["notes"].endswith("0 decided on another version of the set.")
    lines = lines_of("example.reviews.jsonl")
    lines[2]["set_md5"] = "7" * 32
    older = build(REVIEWS, lines[0], lines[1:])
    refrozen, applied = apply_to_set(qset, older, created=CREATED)
    assert len(applied.stale) == 1
    assert refrozen.header["notes"].endswith("1 decided on another version of the set.")


def test_apply_to_set_keeps_the_name_by_default(qset, reviews):
    frozen, _ = apply_to_set(qset, reviews, created=CREATED)
    assert frozen.header["set"] == qset.header["set"]


def test_apply_to_set_refuses_reviews_of_another_set(qset, reviews):
    lines = lines_of("example.reviews.jsonl")
    lines[0]["set"] = "other/v1"
    other = build(REVIEWS, lines[0], lines[1:])
    with pytest.raises(ReviewError, match="other/v1"):
        apply_to_set(qset, other, created=CREATED)
    with pytest.raises(ValueError, match="expected a qset/1 file"):
        apply_to_set(reviews, reviews, created=CREATED)


def test_rejecting_an_item_that_a_mirror_names_is_refused(qset):
    lines = [line("EX-002", "reject", "2026-01-01T00:00:00+00:00")]
    reviews = build(
        REVIEWS,
        {"format": REVIEWS, "set": qset.header["set"], "created": CREATED},
        lines,
    )
    with pytest.raises(ReviewError, match="reviewed set is invalid"):
        apply_to_set(qset, reviews, created=CREATED)


def test_a_format_error_is_not_leaked_as_a_type_error(qset):
    with pytest.raises(FormatError):
        build(REVIEWS, {"format": REVIEWS, "set": "x"}, [])
