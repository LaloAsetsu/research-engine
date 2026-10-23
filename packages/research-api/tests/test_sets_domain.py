"""The rules of the question sets that need no files, no database and no web framework."""

from __future__ import annotations

import gzip
import os
import zlib
from datetime import UTC, datetime
from typing import Any

import pytest

from research_api.domain.auth import Role, User
from research_api.domain.errors import ForbiddenError, InvalidInputError, TooLargeError
from research_api.domain.sets import (
    MAX_GZIP_MEMBERS,
    Actor,
    ItemFilter,
    ItemReview,
    SetRef,
    SetVersion,
    apply_edit,
    dominant_corpus,
    etag_of,
    has_wildcard,
    if_match_allows,
    item_matches,
    next_version,
    parse_header_name,
    plan_token,
    require_draft,
    require_editable,
    review_progress,
    review_states,
    summarize,
    unzip_upload,
    valid_item_id,
    valid_name,
    valid_version,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
MD5 = "0123456789abcdef0123456789abcdef"


def version(status: str = "draft", owner: str = "ana") -> SetVersion:
    return SetVersion(
        ref=SetRef("demo", "v1"),
        status=status,  # type: ignore[arg-type]
        owner=owner,
        md5=MD5,
        items=1,
        corpus="c",
        created_at=T0,
        updated_at=T0,
    )


def line(item: str, who: str, verdict: str, day: int = 1, **extra: Any) -> dict[str, Any]:
    return {
        "item_id": item,
        "target": "item",
        "reviewer": who,
        "role": "human",
        "verdict": verdict,
        "date": f"2026-01-{day:02d}T10:00:00+00:00",
        **extra,
    }


# --- names ----------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["a", "demo-papers", "demo-set", "A.b_c-1", "x" * 64])
def test_good_names(name: str) -> None:
    assert valid_name(name)


@pytest.mark.parametrize(
    "name", ["", "-a", ".a", "a/b", "a b", "a\n", "..", "x" * 65, "é", "a\\b", "a:b"]
)
def test_bad_names(name: str) -> None:
    assert not valid_name(name)


@pytest.mark.parametrize("v", ["v1", "v2", "v10", "v999999"])
def test_good_versions(v: str) -> None:
    assert valid_version(v)


@pytest.mark.parametrize("v", ["", "v0", "v01", "1", "V1", "v1.2", "v1\n", "v1000000", "v-1"])
def test_bad_versions(v: str) -> None:
    assert not valid_version(v)


@pytest.mark.parametrize("item_id", ["EX-001", "a", "CL-a1a1a1a1a1a1a1a1-b2", "x.y_z"])
def test_good_item_ids(item_id: str) -> None:
    assert valid_item_id(item_id)


@pytest.mark.parametrize("item_id", ["", "-x", "a b", "a/b", "a\n"])
def test_bad_item_ids(item_id: str) -> None:
    assert not valid_item_id(item_id)


def test_the_header_name_is_name_slash_version() -> None:
    ref = SetRef("demo-set", "v1")
    assert ref.header_name == "demo-set/v1"
    assert ref.file_stem == "demo-set-v1"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("demo-set/v1", SetRef("demo-set", "v1")),
        ("a/v12", SetRef("a", "v12")),
        ("plain", None),
        ("a/b/v1", None),  # a slash cannot be in a name
        ("a/1", None),
        ("/v1", None),
        ("a/", None),
        ("", None),
    ],
)
def test_reading_a_header_name(text: str, expected: SetRef | None) -> None:
    assert parse_header_name(text) == expected


def test_the_next_version_follows_the_highest_not_the_count() -> None:
    assert next_version([]) == "v1"
    assert next_version(["v1"]) == "v2"
    assert next_version(["v1", "v3"]) == "v4"
    assert next_version(["v9", "v10"]) == "v11"  # numbers, not text


# --- ETags ----------------------------------------------------------------------------------


def test_the_etag_is_the_quoted_md5() -> None:
    assert etag_of(MD5) == f'"{MD5}"'


@pytest.mark.parametrize(
    ("header", "allowed"),
    [
        (f'"{MD5}"', True),
        ("*", False),  # it does not say which version was read
        (f'"other", "{MD5}"', True),
        (f'  "{MD5}"  ', True),
        (MD5, False),  # an entity tag is quoted
        (f'W/"{MD5}"', False),  # a weak tag never matches a write
        ('"other"', False),
        ("", False),
        (f'"{MD5}', False),
    ],
)
def test_if_match(header: str, allowed: bool) -> None:
    assert if_match_allows(header, MD5) is allowed


@pytest.mark.parametrize(
    ("header", "wildcard"), [("*", True), (f'"{MD5}", *', True), (" * ", True), (f'"{MD5}"', False)]
)
def test_the_wildcard_is_recognised(header: str, wildcard: bool) -> None:
    assert has_wildcard(header) is wildcard


def test_a_plan_token_changes_with_the_draft_and_with_every_decision() -> None:
    base = plan_token(MD5, b"header\none\n")
    assert plan_token(MD5, b"header\none\n") == base
    assert plan_token(MD5, b"header\none\ntwo\n") != base
    assert plan_token("f" * 32, b"header\none\n") != base
    assert plan_token(MD5, None) != base
    assert plan_token(MD5, None) == plan_token(MD5, b"")
    assert len(base) == 32 and base.isalnum()


# --- who may change what ----------------------------------------------------------------------


def make_user(role: Role) -> User:
    return User(
        id=1,
        username="u",
        display_name="U",
        role=role,
        password_hash="x",
        must_change_password=False,
        active=True,
        language="es",
        created_at=T0,
        password_changed_at=None,
    )


def test_an_actor_knows_whether_the_user_is_an_admin() -> None:
    assert Actor.of(make_user(Role.ADMIN)) == Actor("u", admin=True)
    assert Actor.of(make_user(Role.MEMBER)) == Actor("u", admin=False)


def test_the_owner_edits_their_draft() -> None:
    require_editable(Actor("ana"), version())


def test_an_admin_edits_anyones_draft() -> None:
    require_editable(Actor("root", admin=True), version())


def test_another_member_cannot_edit_a_draft() -> None:
    with pytest.raises(ForbiddenError, match="owner"):
        require_editable(Actor("beto"), version())


def test_nobody_edits_a_frozen_version_not_even_the_owner_or_an_admin() -> None:
    for who in (Actor("ana"), Actor("root", admin=True), Actor("beto")):
        with pytest.raises(ForbiddenError, match="frozen"):
            require_editable(who, version("frozen"))


def test_a_review_needs_a_draft() -> None:
    require_draft(version())
    with pytest.raises(ForbiddenError):
        require_draft(version("frozen"))


# --- the review state of an item ----------------------------------------------------------------


def test_an_item_nobody_decided_has_no_state() -> None:
    states = review_states(["a", "b"], [])
    assert states["a"] == ItemReview("none", None, ())
    assert not states["a"].gold


def test_one_accept_needs_a_second_person() -> None:
    state = review_states(["a"], [line("a", "ana", "accept")])["a"]
    assert state.state == "needs_second"
    assert state.accepted_by == ("ana",)
    assert not state.gold


def test_two_different_people_make_it_gold() -> None:
    state = review_states(["a"], [line("a", "ana", "accept"), line("a", "beto", "accept")])["a"]
    assert state.state == "accepted"
    assert state.gold
    assert state.accepted_by == ("ana", "beto")


def test_the_same_person_twice_is_still_one_person() -> None:
    lines = [line("a", "ana", "accept", 1), line("a", "ana", "accept", 2)]
    assert review_states(["a"], lines)["a"].state == "needs_second"


def test_a_reject_after_two_accepts_wins() -> None:
    lines = [
        line("a", "ana", "accept"),
        line("a", "beto", "accept"),
        line("a", "cleo", "reject", 2),
    ]
    state = review_states(["a"], lines)["a"]
    assert state.state == "rejected"
    assert state.deciding is not None
    assert state.deciding["reviewer"] == "cleo"


def test_an_accept_after_a_reject_brings_the_item_back() -> None:
    lines = [line("a", "ana", "reject", 1, note="x"), line("a", "ana", "accept", 2)]
    assert review_states(["a"], lines)["a"].state == "needs_second"


def test_a_person_who_changes_their_mind_counts_by_their_last_word() -> None:
    lines = [
        line("a", "ana", "accept", 1),
        line("a", "beto", "accept", 2),
        line("a", "beto", "reject", 3, note="no"),
        line("a", "cleo", "accept", 4),
    ]
    state = review_states(["a"], lines)["a"]
    assert state.accepted_by == ("ana", "cleo")
    assert state.state == "accepted"


def test_an_edit_is_the_state_while_it_is_the_latest_word() -> None:
    lines = [line("a", "ana", "accept", 1), line("a", "beto", "edit", 2, note="n", edit={"x": 1})]
    assert review_states(["a"], lines)["a"].state == "edited"


def test_the_latest_date_holds_whatever_the_order_in_the_file() -> None:
    lines = [line("a", "beto", "reject", 5, note="n"), line("a", "ana", "accept", 2)]
    assert review_states(["a"], lines)["a"].state == "rejected"


def test_equal_dates_go_to_the_line_written_last() -> None:
    lines = [line("a", "ana", "reject", 3, note="n"), line("a", "beto", "accept", 3)]
    assert review_states(["a"], lines)["a"].state == "needs_second"


def test_dates_compare_as_instants_not_as_text() -> None:
    early = line("a", "ana", "accept")
    early["date"] = "2026-01-01T10:00:00+05:00"  # 05:00 UTC
    late = line("a", "beto", "reject", note="n")
    late["date"] = "2026-01-01T06:00:00+00:00"  # later in UTC, earlier as text
    assert review_states(["a"], [early, late])["a"].state == "rejected"


def test_only_human_lines_about_items_count() -> None:
    checker = line("a", "bot", "reject", note="n")
    checker["role"] = "checker"
    answer = {**line("a", "ana", "accept"), "target": "answer"}
    other_item = line("zzz", "ana", "accept")
    assert review_states(["a"], [checker, answer, other_item])["a"].state == "none"


def test_progress_counts_each_state() -> None:
    lines = [
        line("a", "ana", "accept"),
        line("a", "beto", "accept"),
        line("b", "ana", "accept"),
        line("c", "ana", "reject", note="n"),
        line("d", "ana", "edit", note="n", edit={"x": 1}),
    ]
    progress = review_progress(review_states(list("abcde"), lines))
    assert (progress.items, progress.none, progress.needs_second) == (5, 1, 1)
    assert (progress.accepted, progress.edited, progress.rejected) == (1, 1, 1)


# --- filters and summaries --------------------------------------------------------------------


ITEM = {
    "id": "EX-001",
    "lang": "es",
    "form": "single",
    "content": "math",
    "corpus": "c1",
    "split": "dev",
    "question": "Cual es el Acoplamiento?",
    "answer": "J",
    "notes": "revisar la nota",
    "tags": {"category": "a", "survives": "yes"},
}
NONE = ItemReview("none", None, ())


def test_no_filter_keeps_everything() -> None:
    assert item_matches(ITEM, NONE, ItemFilter())


@pytest.mark.parametrize(
    ("flt", "kept"),
    [
        (ItemFilter(form="single"), True),
        (ItemFilter(form="multi_chain"), False),
        (ItemFilter(content="math"), True),
        (ItemFilter(content="other"), False),
        (ItemFilter(lang="es"), True),
        (ItemFilter(lang="en"), False),
        (ItemFilter(split="dev"), True),
        (ItemFilter(split="test"), False),
        (ItemFilter(tag="category=a"), True),
        (ItemFilter(tag="category=b"), False),
        (ItemFilter(tag="category"), True),
        (ItemFilter(tag="missing"), False),
        (ItemFilter(tag="missing="), False),
        (ItemFilter(review="none"), True),
        (ItemFilter(review="accepted"), False),
        (ItemFilter(q="acoplamiento"), True),  # ignoring case
        (ItemFilter(q="EX-00"), True),
        (ItemFilter(q="nota"), True),
        (ItemFilter(q="J"), True),
        (ItemFilter(q="inexistente"), False),
        (ItemFilter(form="single", lang="en"), False),  # every field must match
    ],
)
def test_filters(flt: ItemFilter, kept: bool) -> None:
    assert item_matches(ITEM, NONE, flt) is kept


def test_an_item_without_the_field_is_not_matched_by_it() -> None:
    assert not item_matches({"id": "x"}, NONE, ItemFilter(split="dev"))
    assert not item_matches({"id": "x"}, NONE, ItemFilter(tag="a"))


def test_a_summary_cuts_a_long_question() -> None:
    summary = summarize({**ITEM, "question": "q" * 1000}, NONE)
    assert len(summary.question) == 240
    assert summary.question.endswith("...")
    assert summary.review == "none"
    assert summary.tags == {"category": "a", "survives": "yes"}


def test_a_summary_of_a_bare_item() -> None:
    summary = summarize({"id": "x", "corpus": "none"}, NONE)
    assert (summary.id, summary.question, summary.content, summary.mirror_of) == (
        "x",
        "",
        None,
        None,
    )


def test_the_dominant_corpus_ignores_none_and_breaks_ties_by_first() -> None:
    assert dominant_corpus([]) is None
    assert dominant_corpus([{"corpus": "none"}]) is None
    assert dominant_corpus([{"corpus": "a"}, {"corpus": "b"}, {"corpus": "b"}]) == "b"
    assert dominant_corpus([{"corpus": "a"}, {"corpus": "b"}]) == "a"
    assert dominant_corpus([{"corpus": "none"}, {"corpus": "a"}]) == "a"


def test_an_edit_replaces_the_named_fields_and_leaves_the_original() -> None:
    original = {"id": "x", "answer": "a", "notes": "n"}
    changed = apply_edit(original, {"answer": "b"})
    assert changed == {"id": "x", "answer": "b", "notes": "n"}
    assert original["answer"] == "a"


# --- unzipping an upload -----------------------------------------------------------------------


def test_plain_bytes_pass_through_untouched() -> None:
    assert unzip_upload(b'{"a": 1}\n', 10) == b'{"a": 1}\n'
    assert unzip_upload(b"", 10) == b""


def test_gzip_is_unzipped_up_to_the_limit_and_not_a_byte_more() -> None:
    assert unzip_upload(gzip.compress(b"x" * 100), 100) == b"x" * 100
    with pytest.raises(TooLargeError, match="once it is unzipped"):
        unzip_upload(gzip.compress(b"x" * 101), 100)


def test_a_small_gzip_that_holds_a_great_deal_is_refused_without_unzipping_it_all() -> None:
    bomb = gzip.compress(b"\0" * (64 * 1024 * 1024))
    assert len(bomb) < 100_000  # the upload cap alone would let this in
    with pytest.raises(TooLargeError):
        unzip_upload(bomb, 1024 * 1024)


def test_gzip_members_one_after_another_count_together() -> None:
    two = gzip.compress(b"a" * 60) + gzip.compress(b"b" * 60)
    assert unzip_upload(two, 120) == b"a" * 60 + b"b" * 60
    with pytest.raises(TooLargeError):
        unzip_upload(two, 119)


class _Fed:
    """Counts what ``unzip_upload`` hands to zlib, so the cost is checked in bytes, not seconds."""

    def __init__(self) -> None:
        self.objects = 0
        self.fed = 0
        self.largest = 0


@pytest.fixture
def fed(monkeypatch: pytest.MonkeyPatch) -> _Fed:
    seen = _Fed()
    real = zlib.decompressobj

    class Spy:
        def __init__(self, wbits: int) -> None:
            seen.objects += 1
            self._inner = real(wbits=wbits)

        def decompress(self, data: bytes, max_length: int = 0) -> bytes:
            seen.fed += len(data)
            seen.largest = max(seen.largest, len(data))
            return self._inner.decompress(data, max_length)

        @property
        def eof(self) -> bool:
            return self._inner.eof

        @property
        def unused_data(self) -> bytes:
            return self._inner.unused_data

    monkeypatch.setattr(zlib, "decompressobj", Spy)
    return seen


def test_a_million_empty_gzip_members_are_refused_at_once_not_after_minutes(fed: _Fed) -> None:
    """A 20 MiB upload of 20-byte members. Handing each member the rest of the upload made this
    quadratic (3.5 s at 160 000 members, minutes at a million), a stall for everyone. The check
    counts the work (members opened, bytes handed to zlib) so a loaded runner cannot fail it."""
    member = gzip.compress(b"")
    assert len(member) == 20
    upload = member * (20 * 1024 * 1024 // len(member))
    with pytest.raises(InvalidInputError, match="members"):
        unzip_upload(upload, 20 * 1024 * 1024)
    assert fed.objects == MAX_GZIP_MEMBERS
    assert fed.largest <= 64 * 1024
    assert (
        fed.fed <= MAX_GZIP_MEMBERS * 64 * 1024
    )  # one step per member, not the rest of the upload


def test_the_member_cap_is_a_count_and_the_last_member_under_it_is_read() -> None:
    member = gzip.compress(b"")
    assert unzip_upload(member * (MAX_GZIP_MEMBERS - 1) + gzip.compress(b"end"), 10) == b"end"
    assert unzip_upload(member * MAX_GZIP_MEMBERS, 10) == b""
    with pytest.raises(InvalidInputError, match="members"):
        unzip_upload(member * (MAX_GZIP_MEMBERS + 1), 10)


def test_members_that_cross_the_feed_boundaries_are_joined_exactly() -> None:
    """Members of every size, so a member starts, ends or spans a 64 KiB step of the reader."""
    pieces = [os.urandom(size) for size in (1, 70_000, 5, 200_000, 0, 65_535, 65_536, 3, 150_000)]
    upload = b"".join(gzip.compress(piece) for piece in pieces)
    assert len(upload) > 3 * 64 * 1024
    assert unzip_upload(upload, 10**7) == b"".join(pieces)
    assert unzip_upload(gzip.compress(pieces[3]), 10**7) == pieces[3]


def test_many_small_members_with_data_stay_linear(fed: _Fed) -> None:
    pieces = [b"%d\n" % n for n in range(MAX_GZIP_MEMBERS)]
    upload = b"".join(gzip.compress(piece) for piece in pieces)
    assert unzip_upload(upload, 10**6) == b"".join(pieces)
    assert fed.objects == MAX_GZIP_MEMBERS
    assert fed.largest <= 64 * 1024
    # Not a pass over the rest of the upload per member: at most one 64 KiB step each.
    assert fed.fed <= MAX_GZIP_MEMBERS * 64 * 1024


@pytest.mark.parametrize(
    "data",
    [
        gzip.compress(b"hello")[:-6],  # cut off
        gzip.compress(b"hello") + b"junk",  # something after the data
        gzip.compress(b"hello") + b"\x1f",  # one stray byte after the data
        b"\x1f\x8b" + b"not a real stream" * 3,  # a header that leads nowhere
    ],
    ids=["cut off", "junk after", "one byte after", "broken"],
)
def test_gzip_that_is_not_valid_is_refused(data: bytes) -> None:
    with pytest.raises(InvalidInputError):
        unzip_upload(data, 1000)


def test_gzip_inside_gzip_is_refused_so_nothing_further_down_unzips_it_unbounded() -> None:
    inner = gzip.compress(b"\0" * (32 * 1024 * 1024))
    with pytest.raises(InvalidInputError, match="gzip inside gzip"):
        unzip_upload(gzip.compress(inner), 1024 * 1024)
