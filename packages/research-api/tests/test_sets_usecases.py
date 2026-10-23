"""The use cases of the question sets over a real SQLite file, real files and a clock the test moves."""

from __future__ import annotations

import gzip
import hashlib
import json
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from helpers import MovableClock
from qa_bench.formats import check_against_set, read_qset, read_reviews
from sets_helpers import SOURCES, example_bytes, example_item, header, item, qset_bytes

from research_api.adapters.bench import QaBenchSetFormats
from research_api.adapters.files import SetFileStore
from research_api.adapters.persistence import Database
from research_api.application.sets import SetServices, build_set_services
from research_api.domain import sets as rules
from research_api.domain.errors import (
    ConflictError,
    CorruptStoreError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    PreconditionRequiredError,
    TooLargeError,
)
from research_api.domain.sets import Actor, ItemFilter, SetRef, etag_of

ANA = Actor("ana")  # an owner
BETO = Actor("beto")  # another member
ROOT = Actor("root", admin=True)
V1 = SetRef("demo", "v1")
V2 = SetRef("demo", "v2")


@dataclass
class Stack:
    services: SetServices
    database: Database
    files: SetFileStore
    clock: MovableClock
    root: Path

    @property
    def manage(self):
        return self.services.manage

    @property
    def review(self):
        return self.services.review

    @property
    def freeze(self):
        return self.services.freeze

    def path(self, ref: SetRef, suffix: str = ".qset.jsonl") -> Path:
        return self.root / "sets" / ref.name / f"{ref.version}{suffix}"


@pytest.fixture
def stack(tmp_path: Path) -> Iterator[Stack]:
    database = Database.open(tmp_path / "t.sqlite3")
    database.upgrade()
    clock = MovableClock()
    files = SetFileStore(tmp_path / "data" / "sets")
    services = build_set_services(
        catalog=database.sets, files=files, formats=QaBenchSetFormats(), clock=clock
    )
    yield Stack(services, database, files, clock, tmp_path / "data")
    database.close()


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def draft(stack: Stack, items: list[dict[str, Any]] | None = None, owner: Actor = ANA) -> SetRef:
    """A draft ``demo/v1`` made by import."""
    stack.manage.import_set(owner, qset_bytes(items), name="demo", version="v1")
    return V1


def etag(stack: Stack, ref: SetRef = V1) -> str:
    return etag_of(stack.manage.get_version(ref).version.md5)


def ids(stack: Stack, ref: SetRef = V1) -> list[str]:
    return [i.id for i in stack.manage.get_version(ref, limit=200).items]


# --- creating a draft ---------------------------------------------------------------------------


def test_a_new_empty_draft(stack: Stack) -> None:
    row = stack.manage.create(ANA, name="demo", corpus="c1")
    assert (row.ref, row.status, row.owner, row.items, row.corpus) == (V1, "draft", "ana", 0, "c1")
    assert row.created_at == row.updated_at == stack.clock.now()
    assert row.frozen_at is None
    file = read_qset(stack.path(V1))
    assert file.header["set"] == "demo/v1"
    assert file.md5 == row.md5
    assert file.records == ()


def test_the_name_of_an_existing_set_is_taken(stack: Stack) -> None:
    stack.manage.create(ANA, name="demo")
    with pytest.raises(ConflictError, match="Copy one of its versions"):
        stack.manage.create(BETO, name="demo")


@pytest.mark.parametrize("name", ["", "a/b", "..", "has space", "x" * 65])
def test_a_bad_name_is_refused_with_its_place(stack: Stack, name: str) -> None:
    with pytest.raises(InvalidInputError) as caught:
        stack.manage.create(ANA, name=name)
    assert caught.value.errors[0].path == "/name"
    assert stack.database.sets.all() == []


def test_a_bad_copy_source_is_refused(stack: Stack) -> None:
    with pytest.raises(InvalidInputError) as caught:
        stack.manage.create(ANA, name="demo", copy_from="1")
    assert caught.value.errors[0].path == "/copy_from"


def test_a_copy_is_the_next_version_with_the_same_items(stack: Stack) -> None:
    draft(stack, [item("T-1"), item("T-2")])
    stack.manage.create(ANA, name="other")  # an unrelated set must not disturb the numbering
    copy = stack.manage.create(BETO, name="demo", copy_from="v1")
    assert copy.ref == V2
    assert copy.owner == "beto"
    assert copy.status == "draft"
    assert ids(stack, V2) == ["T-1", "T-2"]
    file = read_qset(stack.path(V2))
    assert file.header["set"] == "demo/v2"
    assert "md5" in file.header["notes"]
    assert copy.corpus == "test-corpus"


def test_copying_the_second_version_makes_the_third(stack: Stack) -> None:
    draft(stack)
    stack.manage.create(ANA, name="demo", copy_from="v1")
    assert stack.manage.create(ANA, name="demo", copy_from="v2").ref == SetRef("demo", "v3")
    assert stack.manage.create(ANA, name="demo", copy_from="v1").ref == SetRef("demo", "v4")


def test_a_copy_does_not_share_items_with_its_source(stack: Stack) -> None:
    draft(stack, [item("T-1")])
    stack.manage.create(ANA, name="demo", copy_from="v1")
    stack.manage.put_item(ANA, V2, "T-1", item("T-1", answer="changed"), etag(stack, V2))
    assert stack.manage.get_item(V1, "T-1").item["answer"] == "Synthetic answer."


def test_a_copy_keeps_the_conversion_source_of_the_original(stack: Stack) -> None:
    stack.manage.import_set(ANA, example_bytes(), name="demo", version="v1")
    stack.manage.create(ANA, name="demo", copy_from="v1")
    assert read_qset(stack.path(V2)).header["source"]["file"] == "example-source.jsonl"


def test_copying_a_version_that_does_not_exist(stack: Stack) -> None:
    with pytest.raises(NotFoundError):
        stack.manage.create(ANA, name="demo", copy_from="v1")


def test_a_copy_may_name_another_default_corpus(stack: Stack) -> None:
    draft(stack)
    assert stack.manage.create(ANA, name="demo", copy_from="v1", corpus="c9").corpus == "c9"


# --- importing -----------------------------------------------------------------------------------


def test_an_import_naming_its_own_target_keeps_its_exact_bytes(stack: Stack) -> None:
    data = example_bytes()
    outcome = stack.manage.import_set(ANA, data)
    assert outcome.version.ref == SetRef("example-set", "v1")
    assert outcome.items_read == 9
    assert outcome.shape is None
    assert outcome.version.md5 == md5(data)
    assert stack.path(outcome.version.ref).read_bytes() == data
    assert stack.manage.export(outcome.version.ref)[1] == data


def test_an_import_under_another_name_rewrites_only_the_header(stack: Stack) -> None:
    outcome = stack.manage.import_set(ANA, example_bytes(), name="mine")
    assert outcome.version.ref == SetRef("mine", "v1")
    file = read_qset(stack.path(outcome.version.ref))
    assert file.header["set"] == "mine/v1"
    original = read_qset(SOURCES.parent / "example.qset.jsonl")
    assert file.records == original.records
    assert outcome.version.md5 != md5(example_bytes())


def test_an_import_into_a_taken_version_goes_to_the_next(stack: Stack) -> None:
    first = stack.manage.import_set(ANA, example_bytes())
    second = stack.manage.import_set(BETO, example_bytes())
    assert (first.version.ref.version, second.version.ref.version) == ("v1", "v2")
    assert read_qset(stack.path(second.version.ref)).header["set"] == "example-set/v2"


def test_an_import_to_a_version_that_exists_is_a_conflict(stack: Stack) -> None:
    stack.manage.import_set(ANA, example_bytes())
    with pytest.raises(ConflictError, match="example-set/v1"):
        stack.manage.import_set(ANA, example_bytes(), version="v1")
    assert len(stack.database.sets.all()) == 1


def test_an_import_may_ask_for_a_version(stack: Stack) -> None:
    outcome = stack.manage.import_set(ANA, example_bytes(), version="v7")
    assert outcome.version.ref == SetRef("example-set", "v7")


def test_an_import_is_a_draft_owned_by_the_importer(stack: Stack) -> None:
    row = stack.manage.import_set(BETO, example_bytes()).version
    assert (row.status, row.owner, row.items) == ("draft", "beto", 9)
    assert row.corpus == "example-corpus"


def test_a_gzip_import_is_stored_as_text(stack: Stack) -> None:
    outcome = stack.manage.import_set(ANA, gzip.compress(example_bytes()))
    assert stack.path(outcome.version.ref).read_bytes() == example_bytes()
    assert outcome.version.md5 == md5(example_bytes())


def test_a_refused_file_leaves_nothing_behind(stack: Stack) -> None:
    bad = qset_bytes([item("T-1", form="nope")])
    with pytest.raises(InvalidInputError) as caught:
        stack.manage.import_set(ANA, bad, name="demo")
    assert any(e.path.startswith("/lines/2") for e in caught.value.errors)
    assert stack.database.sets.all() == []
    assert not (stack.root / "sets").exists()


def test_an_empty_upload(stack: Stack) -> None:
    for blank in (b"", b" \n\n"):
        with pytest.raises(InvalidInputError, match="empty"):
            stack.manage.import_set(ANA, blank, name="demo")


def test_an_upload_over_the_cap(stack: Stack, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rules, "MAX_IMPORT_BYTES", 100)
    with pytest.raises(TooLargeError):
        stack.manage.import_set(ANA, example_bytes())
    assert stack.database.sets.all() == []


def test_a_header_that_is_not_name_slash_version_needs_a_name(stack: Stack) -> None:
    data = qset_bytes([item()], name="not a valid name!")
    with pytest.raises(InvalidInputError) as caught:
        stack.manage.import_set(ANA, data)
    assert caught.value.errors[0].path == "/name"
    ok = stack.manage.import_set(ANA, data, name="fine")
    assert ok.version.ref == SetRef("fine", "v1")


def test_a_plain_header_set_is_the_name(stack: Stack) -> None:
    row = stack.manage.import_set(ANA, qset_bytes([item()], name="plain-name")).version
    assert row.ref == SetRef("plain-name", "v1")


def test_a_bad_name_or_version_parameter(stack: Stack) -> None:
    with pytest.raises(InvalidInputError) as caught:
        stack.manage.import_set(ANA, example_bytes(), name="a/b")
    assert caught.value.errors[0].path == "/name"
    with pytest.raises(InvalidInputError) as caught:
        stack.manage.import_set(ANA, example_bytes(), version="1")
    assert caught.value.errors[0].path == "/version"


@pytest.mark.parametrize(
    ("shape", "file", "corpus"),
    [
        ("mathcirc-v1", "mathcirc.jsonl", "demo-corpus"),
        ("circuit-tasks-v1", "circuit-tasks.jsonl", "demo-corpus"),
        ("circuit-claims-v1", "circuit-claims.tsv", "demo-corpus"),
        ("qubitqa-pilot", "qubitqa.jsonl", None),
        ("qasper", "qasper.jsonl", "qasper-demo"),
    ],
)
def test_an_older_shape_becomes_a_draft(
    stack: Stack, shape: str, file: str, corpus: str | None
) -> None:
    outcome = stack.manage.import_set(
        ANA, (SOURCES / file).read_bytes(), name="old", shape=shape, corpus=corpus, source_file=file
    )
    assert outcome.shape == shape
    assert outcome.version.ref == SetRef("old", "v1")
    assert outcome.items_read == outcome.version.items > 0
    stored = read_qset(stack.path(outcome.version.ref))
    assert stored.header["set"] == "old/v1"
    assert stored.header["source"]["file"] == file
    assert stored.md5 == outcome.version.md5


def test_a_converted_file_needs_a_name(stack: Stack) -> None:
    with pytest.raises(InvalidInputError) as caught:
        stack.manage.import_set(
            ANA, (SOURCES / "mathcirc.jsonl").read_bytes(), shape="mathcirc-v1", corpus="c"
        )
    assert caught.value.errors[0].path == "/name"


def test_a_refused_conversion_stores_nothing(stack: Stack) -> None:
    with pytest.raises(InvalidInputError):
        stack.manage.import_set(
            ANA, b'{"id": "MC-X"}\n', name="old", shape="mathcirc-v1", corpus="c"
        )
    with pytest.raises(InvalidInputError, match="unknown source shape"):
        stack.manage.import_set(ANA, b"x\n", name="old", shape="csv")
    assert stack.database.sets.all() == []


def test_the_source_file_name_is_recorded_cut_to_a_sane_length(stack: Stack) -> None:
    outcome = stack.manage.import_set(
        ANA,
        (SOURCES / "qasper.jsonl").read_bytes(),
        name="q",
        shape="qasper",
        corpus="c",
        source_file="f" * 500,
    )
    assert len(read_qset(stack.path(outcome.version.ref)).header["source"]["file"]) == 200


def test_a_failed_file_write_forgets_the_catalog_entry(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(ref: SetRef, data: bytes) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(stack.files, "write", boom)
    with pytest.raises(OSError, match="disk full"):
        stack.manage.create(ANA, name="demo")
    assert stack.database.sets.all() == []
    monkeypatch.undo()
    assert stack.manage.create(ANA, name="demo").ref == V1  # the name is free again


# --- reading --------------------------------------------------------------------------------------


def test_the_catalog_groups_versions_under_their_set(stack: Stack) -> None:
    draft(stack, [item("T-1"), item("T-2")])
    stack.manage.create(BETO, name="demo", copy_from="v1")
    stack.manage.create(ANA, name="aaa", corpus="zzz")
    catalog = stack.manage.list_sets()
    assert [e.name for e in catalog] == ["aaa", "demo"]
    demo = catalog[1]
    assert [v.version.ref.version for v in demo.versions] == ["v1", "v2"]
    assert demo.corpus == "test-corpus"
    assert demo.versions[0].progress is not None
    assert demo.versions[0].progress.items == 2
    assert catalog[0].corpus == "zzz"


def test_the_catalog_shows_review_progress(stack: Stack) -> None:
    draft(stack, [item("T-1"), item("T-2"), item("T-3")])
    stack.review.add(ANA, V1, item_id="T-1", verdict="accept")
    stack.review.add(BETO, V1, item_id="T-1", verdict="accept")
    stack.review.add(ANA, V1, item_id="T-2", verdict="accept")
    progress = stack.manage.list_sets()[0].versions[0].progress
    assert progress is not None
    assert (progress.accepted, progress.needs_second, progress.none) == (1, 1, 1)


def test_the_catalog_survives_a_version_whose_file_is_gone(stack: Stack) -> None:
    draft(stack)
    stack.path(V1).unlink()
    (entry,) = stack.manage.list_sets()
    assert entry.versions[0].progress is None


def test_an_unknown_version_is_not_found(stack: Stack) -> None:
    for call in (
        lambda: stack.manage.get_version(V1),
        lambda: stack.manage.get_item(V1, "T-1"),
        lambda: stack.manage.export(V1),
        lambda: stack.review.list(V1),
        lambda: stack.freeze.plan(V1),
    ):
        with pytest.raises(NotFoundError):
            call()


def test_paging_and_filtering(stack: Stack) -> None:
    items = [
        item(f"T-{n:02d}", lang="es" if n % 2 else "en", split="dev" if n < 5 else "test")
        for n in range(1, 11)
    ]
    draft(stack, items)
    page = stack.manage.get_version(V1, offset=3, limit=4)
    assert [i.id for i in page.items] == ["T-04", "T-05", "T-06", "T-07"]
    assert (page.total, page.offset, page.limit) == (10, 3, 4)
    only = stack.manage.get_version(V1, ItemFilter(lang="es", split="test"))
    assert [i.id for i in only.items] == ["T-05", "T-07", "T-09"]
    assert only.total == 3


def test_paging_is_kept_inside_its_bounds(stack: Stack) -> None:
    draft(stack, [item("T-1"), item("T-2")])
    page = stack.manage.get_version(V1, offset=-5, limit=0)
    assert (page.offset, page.limit, len(page.items)) == (0, 1, 1)
    assert stack.manage.get_version(V1, limit=10_000).limit == rules.MAX_PAGE
    assert stack.manage.get_version(V1, offset=99).items == ()


def test_the_review_filter_uses_the_state_of_each_item(stack: Stack) -> None:
    draft(stack, [item("T-1"), item("T-2"), item("T-3")])
    stack.review.add(ANA, V1, item_id="T-2", verdict="accept")
    needs = stack.manage.get_version(V1, ItemFilter(review="needs_second"))
    assert [i.id for i in needs.items] == ["T-2"]
    none = stack.manage.get_version(V1, ItemFilter(review="none"))
    assert [i.id for i in none.items] == ["T-1", "T-3"]


def test_an_item_view_carries_its_reviews_and_what_the_schema_says(stack: Stack) -> None:
    draft(stack, [item("T-1"), item("T-2")])
    stack.review.add(ANA, V1, item_id="T-1", verdict="accept")
    stack.review.add(BETO, V1, item_id="T-2", verdict="accept")
    view = stack.manage.get_item(V1, "T-1")
    assert [line["reviewer"] for line in view.reviews] == ["ana"]
    assert view.review.state == "needs_second"
    assert view.problems == ()
    with pytest.raises(NotFoundError):
        stack.manage.get_item(V1, "T-9")


def test_validate_needs_exactly_one_of_text_or_item(stack: Stack) -> None:
    with pytest.raises(InvalidInputError):
        stack.manage.validate()
    with pytest.raises(InvalidInputError):
        stack.manage.validate(text="x", item={})
    assert stack.manage.validate(item=item()) == ()
    assert stack.manage.validate(item={"id": "x"})
    assert stack.manage.validate(text=example_bytes().decode()) == ()
    assert stack.manage.validate(text="{}")


# --- editing items ----------------------------------------------------------------------------


def test_replacing_an_item_keeps_its_place_and_changes_the_md5(stack: Stack) -> None:
    draft(stack, [item("T-1"), item("T-2"), item("T-3")])
    before = etag(stack)
    stack.clock.advance(hours=1)
    view = stack.manage.put_item(ANA, V1, "T-2", item("T-2", answer="new"), before)
    assert ids(stack) == ["T-1", "T-2", "T-3"]
    assert view.item["answer"] == "new"
    assert view.version.md5 != before.strip('"')
    assert view.version.updated_at == stack.clock.now()
    stored = read_qset(stack.path(V1))
    assert stored.md5 == view.version.md5
    assert stack.database.sets.get(V1).md5 == stored.md5  # type: ignore[union-attr]


def test_an_item_body_may_leave_out_its_id(stack: Stack) -> None:
    draft(stack, [item("T-1")])
    body = item("T-1", answer="x")
    del body["id"]
    view = stack.manage.put_item(ANA, V1, "T-1", body, etag(stack))
    assert view.item["id"] == "T-1"


def test_the_id_in_the_body_must_be_the_id_in_the_path(stack: Stack) -> None:
    draft(stack, [item("T-1"), item("T-2")])
    with pytest.raises(InvalidInputError) as caught:
        stack.manage.put_item(ANA, V1, "T-1", item("T-2"), etag(stack))
    assert caught.value.errors[0].path == "/id"


def test_replacing_an_item_that_is_not_there(stack: Stack) -> None:
    draft(stack)
    with pytest.raises(NotFoundError):
        stack.manage.put_item(ANA, V1, "T-9", item("T-9"), etag(stack))


def test_an_invalid_item_is_refused_with_each_problem_and_nothing_changes(stack: Stack) -> None:
    draft(stack, [item("T-1")])
    before = stack.path(V1).read_bytes()
    bad = item("T-1", form="multi_chain", lang="fr")
    with pytest.raises(InvalidInputError) as caught:
        stack.manage.put_item(ANA, V1, "T-1", bad, etag(stack))
    found = {e.path for e in caught.value.errors}
    assert {"/lang", "/synthesis", "/necessity"} <= found
    assert stack.path(V1).read_bytes() == before


def test_a_rule_between_items_is_refused(stack: Stack) -> None:
    draft(stack, [item("T-1"), item("T-2")])
    mirror = item("T-2", lang="es", lang_origin="mirror", mirror_of="T-404")
    with pytest.raises(InvalidInputError) as caught:
        stack.manage.put_item(ANA, V1, "T-2", mirror, etag(stack))
    assert any("not in the file" in e.message for e in caught.value.errors)


def test_adding_an_item_appends_it_and_fills_the_set_corpus(stack: Stack) -> None:
    draft(stack, [item("T-1")])
    body = item("T-2")
    del body["corpus"]
    view = stack.manage.add_item(ANA, V1, body, etag(stack))
    assert ids(stack) == ["T-1", "T-2"]
    assert view.item["corpus"] == "test-corpus"
    assert view.version.items == 2


def test_adding_an_item_whose_id_exists(stack: Stack) -> None:
    draft(stack, [item("T-1")])
    with pytest.raises(ConflictError, match="exists"):
        stack.manage.add_item(ANA, V1, item("T-1"), etag(stack))


def test_adding_an_invalid_item(stack: Stack) -> None:
    draft(stack)
    with pytest.raises(InvalidInputError):
        stack.manage.add_item(ANA, V1, {"id": "T-2"}, etag(stack))
    with pytest.raises(InvalidInputError):
        stack.manage.add_item(ANA, V1, item("bad id"), etag(stack))


def test_an_item_with_no_id_at_all(stack: Stack) -> None:
    draft(stack)
    body = item("x")
    del body["id"]
    with pytest.raises(InvalidInputError):
        stack.manage.add_item(ANA, V1, body, etag(stack))


def test_adding_to_an_empty_set_sets_its_corpus(stack: Stack) -> None:
    stack.manage.create(ANA, name="demo")
    view = stack.manage.add_item(ANA, V1, item("T-1"), etag(stack))
    assert view.version.corpus == "test-corpus"


def test_deleting_an_item(stack: Stack) -> None:
    draft(stack, [item("T-1"), item("T-2")])
    row = stack.manage.delete_item(ANA, V1, "T-1", etag(stack))
    assert ids(stack) == ["T-2"]
    assert row.items == 1
    assert read_qset(stack.path(V1)).md5 == row.md5


def test_deleting_an_item_that_is_not_there(stack: Stack) -> None:
    draft(stack)
    with pytest.raises(NotFoundError):
        stack.manage.delete_item(ANA, V1, "T-9", etag(stack))


def test_an_item_with_a_mirror_cannot_be_deleted_but_the_mirror_can(stack: Stack) -> None:
    mirror = item("T-1-es", lang="es", lang_origin="mirror", mirror_of="T-1")
    draft(stack, [item("T-1"), mirror])
    with pytest.raises(ConflictError, match="T-1-es"):
        stack.manage.delete_item(ANA, V1, "T-1", etag(stack))
    stack.manage.delete_item(ANA, V1, "T-1-es", etag(stack))
    stack.manage.delete_item(ANA, V1, "T-1", etag(stack))
    assert ids(stack) == []


def test_deleting_an_item_keeps_its_reviews(stack: Stack) -> None:
    draft(stack, [item("T-1"), item("T-2")])
    stack.review.add(BETO, V1, item_id="T-1", verdict="accept")
    stack.manage.delete_item(ANA, V1, "T-1", etag(stack))
    assert [line["item_id"] for line in stack.review.list(V1)] == ["T-1"]


# --- who may change a draft, and the If-Match rule ------------------------------------------------


def test_a_write_needs_if_match(stack: Stack) -> None:
    draft(stack)
    for call in (
        lambda tag: stack.manage.put_item(ANA, V1, "T-001", item(), tag),
        lambda tag: stack.manage.add_item(ANA, V1, item("T-2"), tag),
        lambda tag: stack.manage.delete_item(ANA, V1, "T-001", tag),
    ):
        for missing in (None, ""):
            with pytest.raises(PreconditionRequiredError):
                call(missing)


def test_a_stale_if_match_is_a_conflict_and_changes_nothing(stack: Stack) -> None:
    draft(stack, [item("T-1")])
    old = etag(stack)
    stack.manage.put_item(ANA, V1, "T-1", item("T-1", answer="first"), old)
    before = stack.path(V1).read_bytes()
    with pytest.raises(ConflictError, match="Read it again"):
        stack.manage.put_item(ANA, V1, "T-1", item("T-1", answer="second"), old)
    assert stack.path(V1).read_bytes() == before


def test_a_wildcard_if_match_matches_and_a_weak_one_does_not(stack: Stack) -> None:
    draft(stack, [item("T-1")])
    stack.manage.put_item(ANA, V1, "T-1", item("T-1", answer="a"), "*")
    with pytest.raises(ConflictError):
        stack.manage.put_item(ANA, V1, "T-1", item("T-1", answer="b"), "W/" + etag(stack))


def test_the_owner_and_an_admin_may_edit_but_another_member_may_not(stack: Stack) -> None:
    draft(stack, [item("T-1")])
    with pytest.raises(ForbiddenError, match="owner"):
        stack.manage.put_item(BETO, V1, "T-1", item("T-1", answer="x"), etag(stack))
    with pytest.raises(ForbiddenError):
        stack.manage.add_item(BETO, V1, item("T-2"), etag(stack))
    with pytest.raises(ForbiddenError):
        stack.manage.delete_item(BETO, V1, "T-1", etag(stack))
    stack.manage.put_item(ROOT, V1, "T-1", item("T-1", answer="by admin"), etag(stack))
    stack.manage.add_item(ANA, V1, item("T-2"), etag(stack))


def test_permission_is_checked_before_the_precondition(stack: Stack) -> None:
    draft(stack)
    with pytest.raises(ForbiddenError):
        stack.manage.add_item(BETO, V1, item("T-2"), None)


def test_a_frozen_version_takes_no_edit_from_anyone(stack: Stack) -> None:
    draft(stack, [item("T-1")])
    stack.review.add(ANA, V1, item_id="T-1", verdict="accept")
    stack.freeze.freeze(ANA, V1)
    tag = etag(stack)
    for who in (ANA, ROOT, BETO):
        with pytest.raises(ForbiddenError, match="frozen"):
            stack.manage.put_item(who, V1, "T-1", item("T-1"), tag)
        with pytest.raises(ForbiddenError, match="frozen"):
            stack.manage.add_item(who, V1, item("T-2"), tag)
        with pytest.raises(ForbiddenError, match="frozen"):
            stack.manage.delete_item(who, V1, "T-1", tag)


# --- reviews ----------------------------------------------------------------------------------------


def test_a_review_line_is_filled_in_by_the_server(stack: Stack) -> None:
    draft(stack, [item("T-1")])
    md5_now = stack.manage.get_version(V1).version.md5
    line = stack.review.add(BETO, V1, item_id="T-1", verdict="reject", note="unclear")
    assert line == {
        "item_id": "T-1",
        "target": "item",
        "reviewer": "beto",
        "role": "human",
        "verdict": "reject",
        "date": "2026-01-01T00:00:00+00:00",
        "set_md5": md5_now,
        "note": "unclear",
    }


def test_the_reviews_file_is_a_valid_reviews_file_for_its_set(stack: Stack) -> None:
    draft(stack, [item("T-1"), item("T-2")])
    stack.review.add(ANA, V1, item_id="T-1", verdict="accept")
    stack.clock.advance(minutes=5)
    stack.review.add(BETO, V1, item_id="T-2", verdict="edit", note="n", edit={"answer": "z"})
    reviews = read_reviews(stack.path(V1, ".reviews.jsonl"))
    assert reviews.header["set"] == "demo/v1"
    assert len(reviews.records) == 2
    assert check_against_set(reviews, read_qset(stack.path(V1))) == []


def test_reviews_are_listed_oldest_first_and_by_item(stack: Stack) -> None:
    draft(stack, [item("T-1"), item("T-2")])
    stack.review.add(ANA, V1, item_id="T-1", verdict="accept")
    stack.review.add(BETO, V1, item_id="T-2", verdict="accept")
    stack.review.add(BETO, V1, item_id="T-1", verdict="accept")
    assert [(r["item_id"], r["reviewer"]) for r in stack.review.list(V1)] == [
        ("T-1", "ana"),
        ("T-2", "beto"),
        ("T-1", "beto"),
    ]
    assert [r["reviewer"] for r in stack.review.list(V1, "T-1")] == ["ana", "beto"]
    assert stack.review.list(V1, "T-404") == []


def test_listing_the_reviews_of_a_version_with_none(stack: Stack) -> None:
    draft(stack)
    assert stack.review.list(V1) == []


def test_an_empty_note_on_an_accept_is_left_out(stack: Stack) -> None:
    draft(stack)
    assert "note" not in stack.review.add(ANA, V1, item_id="T-001", verdict="accept", note="")


@pytest.mark.parametrize(
    ("verdict", "note", "edit", "path"),
    [
        ("reject", None, None, "/note"),
        ("reject", "", None, "/note"),
        ("edit", None, {"answer": "x"}, "/note"),
        ("edit", "n", None, "/edit"),
        ("edit", "n", {}, "/edit"),
        ("edit", "n", {"id": "other"}, "/edit"),
        ("accept", None, {"answer": "x"}, "/edit"),
        ("reject", "n", {"answer": "x"}, "/edit"),
        ("maybe", None, None, "/verdict"),
        ("edit", "n", {"form": "nope"}, "/edit/form"),
        ("edit", "n", {"lang": "fr", "answer": "ok"}, "/edit/lang"),
    ],
)
def test_a_bad_review_is_refused_at_its_field_and_nothing_is_written(
    stack: Stack, verdict: str, note: str | None, edit: dict[str, Any] | None, path: str
) -> None:
    draft(stack, [item("T-1")])
    with pytest.raises(InvalidInputError) as caught:
        stack.review.add(ANA, V1, item_id="T-1", verdict=verdict, note=note, edit=edit)
    assert path in {e.path for e in caught.value.errors}
    assert not stack.path(V1, ".reviews.jsonl").exists()


def test_reviewing_an_item_that_is_not_there(stack: Stack) -> None:
    draft(stack)
    with pytest.raises(NotFoundError):
        stack.review.add(ANA, V1, item_id="T-9", verdict="accept")


def test_any_member_may_review_a_draft_that_is_not_theirs(stack: Stack) -> None:
    draft(stack, [item("T-1")])
    assert stack.review.add(BETO, V1, item_id="T-1", verdict="accept")["reviewer"] == "beto"


def test_a_frozen_version_takes_no_review(stack: Stack) -> None:
    draft(stack, [item("T-1")])
    stack.freeze.freeze(ANA, V1)
    with pytest.raises(ForbiddenError, match="frozen"):
        stack.review.add(BETO, V1, item_id="T-1", verdict="accept")


def test_an_edit_review_does_not_change_the_draft_until_it_is_frozen(stack: Stack) -> None:
    draft(stack, [item("T-1")])
    before = stack.path(V1).read_bytes()
    stack.review.add(BETO, V1, item_id="T-1", verdict="edit", note="n", edit={"answer": "better"})
    assert stack.path(V1).read_bytes() == before
    assert stack.manage.get_item(V1, "T-1").item["answer"] == "Synthetic answer."
    stack.freeze.freeze(ANA, V1)
    assert stack.manage.get_item(V1, "T-1").item["answer"] == "better"


# --- freezing ---------------------------------------------------------------------------------------


def reviewed(stack: Stack) -> SetRef:
    draft(stack, [item("T-1"), item("T-2"), item("T-3"), item("T-4")])
    stack.review.add(ANA, V1, item_id="T-1", verdict="accept")
    stack.review.add(BETO, V1, item_id="T-1", verdict="accept")
    stack.review.add(BETO, V1, item_id="T-2", verdict="reject", note="duplicate")
    stack.review.add(BETO, V1, item_id="T-3", verdict="edit", note="fix", edit={"answer": "fixed"})
    return V1


def test_the_plan_says_what_freezing_would_do_and_changes_nothing(stack: Stack) -> None:
    reviewed(stack)
    before = stack.path(V1).read_bytes()
    row, plan = stack.freeze.plan(V1)
    assert row.status == "draft"
    assert (plan.items_before, plan.items_after) == (4, 3)
    assert plan.accepted == ("T-1",)
    assert plan.rejected == ("T-2",)
    assert plan.edited == ("T-3",)
    assert plan.unreviewed == ("T-4",)
    assert plan.stale == ()
    assert stack.path(V1).read_bytes() == before


def test_freezing_applies_the_reviews_and_closes_the_version(stack: Stack) -> None:
    reviewed(stack)
    draft_bytes = stack.path(V1).read_bytes()
    stack.clock.advance(days=1)
    row, plan = stack.freeze.freeze(ANA, V1)
    assert row.status == "frozen"
    assert row.frozen_at == stack.clock.now()
    assert row.items == 3
    assert plan.rejected == ("T-2",)
    frozen = read_qset(stack.path(V1))
    assert frozen.md5 == row.md5
    assert [r["id"] for r in frozen.records] == ["T-1", "T-3", "T-4"]
    assert frozen.records[1]["answer"] == "fixed"
    assert frozen.records[0]["reviewer"] == "beto"
    assert "reviewer" not in frozen.records[2]
    assert frozen.header["set"] == "demo/v1"
    assert "1 rejected" in frozen.header["notes"]
    assert stack.path(V1, ".prefreeze.qset.jsonl").read_bytes() == draft_bytes
    assert stack.database.sets.get(V1) == row


def test_freezing_keeps_the_conversion_source_in_the_header(stack: Stack) -> None:
    stack.manage.import_set(ANA, example_bytes(), name="demo", version="v1")
    stack.freeze.freeze(ANA, V1)
    assert read_qset(stack.path(V1)).header["source"]["file"] == "example-source.jsonl"


def test_a_frozen_version_is_still_readable_and_exportable(stack: Stack) -> None:
    reviewed(stack)
    row, _ = stack.freeze.freeze(ANA, V1)
    assert md5(stack.manage.export(V1)[1]) == row.md5
    assert stack.manage.get_version(V1).total == 3
    assert len(stack.review.list(V1)) == 4


def test_a_version_freezes_once(stack: Stack) -> None:
    reviewed(stack)
    stack.freeze.freeze(ANA, V1)
    with pytest.raises(ForbiddenError, match="frozen"):
        stack.freeze.freeze(ANA, V1)
    with pytest.raises(ForbiddenError):
        stack.freeze.plan(V1)


def test_only_the_owner_or_an_admin_freezes(stack: Stack) -> None:
    reviewed(stack)
    with pytest.raises(ForbiddenError, match="owner"):
        stack.freeze.freeze(BETO, V1)
    assert stack.database.sets.get(V1).status == "draft"  # type: ignore[union-attr]
    stack.freeze.freeze(ROOT, V1)


def test_nothing_left_to_freeze_is_refused(stack: Stack) -> None:
    draft(stack, [item("T-1")])
    stack.review.add(ANA, V1, item_id="T-1", verdict="reject", note="no")
    with pytest.raises(InvalidInputError, match="at least one item"):
        stack.freeze.freeze(ANA, V1)
    stack.manage.create(ANA, name="empty")
    with pytest.raises(InvalidInputError):
        stack.freeze.freeze(ANA, SetRef("empty", "v1"))
    assert stack.database.sets.get(V1).status == "draft"  # type: ignore[union-attr]


def test_a_change_after_a_review_makes_the_review_stale_but_still_applies(stack: Stack) -> None:
    draft(stack, [item("T-1"), item("T-2")])
    stack.review.add(ANA, V1, item_id="T-1", verdict="accept")
    stack.manage.put_item(ANA, V1, "T-2", item("T-2", answer="later"), etag(stack))
    _, plan = stack.freeze.plan(V1)
    assert plan.stale == ("T-1",)
    assert plan.accepted == ("T-1",)


def test_a_decision_about_a_deleted_item_is_listed_as_unknown(stack: Stack) -> None:
    draft(stack, [item("T-1"), item("T-2")])
    stack.review.add(BETO, V1, item_id="T-2", verdict="accept")
    stack.manage.delete_item(ANA, V1, "T-2", etag(stack))
    _, plan = stack.freeze.plan(V1)
    assert plan.unknown == ("T-2",)
    assert stack.freeze.freeze(ANA, V1)[0].items == 1


def test_needs_second_is_listed_but_the_item_is_kept(stack: Stack) -> None:
    draft(stack, [item("T-1"), item("T-2")])
    stack.review.add(ANA, V1, item_id="T-1", verdict="accept")
    _, plan = stack.freeze.plan(V1)
    assert plan.needs_second == ("T-1",)
    assert plan.items_after == 2


def test_a_frozen_set_can_be_the_start_of_the_next_version(stack: Stack) -> None:
    reviewed(stack)
    stack.freeze.freeze(ANA, V1)
    nxt = stack.manage.create(BETO, name="demo", copy_from="v1")
    assert nxt.ref == V2 and nxt.status == "draft"
    assert ids(stack, V2) == ["T-1", "T-3", "T-4"]
    assert stack.review.list(V2) == []


# --- the catalog and the files disagree --------------------------------------------------------------


def test_a_file_that_moved_under_the_catalog_wins(stack: Stack) -> None:
    draft(stack, [item("T-1")])
    other = QaBenchSetFormats().build(header(), [item("T-1"), item("T-2")])
    stack.files.write(V1, other.data)  # a crash between the file write and the catalog write
    view = stack.manage.get_version(V1)
    assert view.version.md5 == other.md5
    assert view.version.items == 2
    stack.manage.add_item(ANA, V1, item("T-3"), etag_of(other.md5))  # the file's etag works
    assert stack.database.sets.get(V1).items == 3  # type: ignore[union-attr]


def test_a_catalog_entry_without_a_file_is_a_server_problem(stack: Stack) -> None:
    draft(stack)
    stack.path(V1).unlink()
    with pytest.raises(CorruptStoreError):
        stack.manage.get_version(V1)


# --- parallel requests ----------------------------------------------------------------------------


def test_two_writers_with_the_same_etag_let_exactly_one_through(stack: Stack) -> None:
    draft(stack, [item("T-1")])
    tag = etag(stack)
    start = threading.Barrier(8)

    def add(n: int) -> bool:
        start.wait()
        try:
            stack.manage.add_item(ANA, V1, item(f"N-{n}"), tag)
        except ConflictError:
            return False
        return True

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(add, range(8)))
    assert results.count(True) == 1
    assert len(ids(stack)) == 2
    assert read_qset(stack.path(V1)).md5 == stack.database.sets.get(V1).md5  # type: ignore[union-attr]


def test_writers_that_retry_after_a_conflict_all_get_in(stack: Stack) -> None:
    draft(stack, [item("T-1")])

    def add(n: int) -> None:
        while True:
            try:
                stack.manage.add_item(ANA, V1, item(f"N-{n:02d}"), etag(stack))
                return
            except ConflictError:
                continue

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(add, range(20)))
    assert sorted(ids(stack)) == sorted(["T-1", *[f"N-{n:02d}" for n in range(20)]])
    assert read_qset(stack.path(V1)).md5 == stack.database.sets.get(V1).md5  # type: ignore[union-attr]


def test_parallel_reviews_all_land_as_whole_lines(stack: Stack) -> None:
    draft(stack, [item("T-1")])
    people = [Actor(f"user{n}") for n in range(12)]
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda p: stack.review.add(p, V1, item_id="T-1", verdict="accept"), people))
    lines = stack.review.list(V1)
    assert sorted(line["reviewer"] for line in lines) == sorted(p.username for p in people)
    assert len(read_reviews(stack.path(V1, ".reviews.jsonl")).records) == 12


def test_the_files_on_disk_are_what_a_reader_of_the_formats_expects(stack: Stack) -> None:
    draft(stack, [example_item("EX-001")])
    stack.manage.add_item(ANA, V1, example_item("EX-004"), etag(stack))
    raw = stack.path(V1).read_text(encoding="utf-8").splitlines()
    assert json.loads(raw[0])["set"] == "demo/v1"
    assert len(raw) == 3
