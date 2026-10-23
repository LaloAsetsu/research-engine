"""Use cases of the question sets: browse, create, import, edit, review and freeze.

A set version is a file (``qset/1``); the catalog says who owns it and whether it is still a
draft. Everything that writes takes one lock, so two requests in this process never interleave
their read-modify-write of a file. The service runs one process, as the scope fixes.
"""

from __future__ import annotations

import contextlib
import copy
import logging
import re
import threading
from dataclasses import dataclass, replace
from typing import Any

from ..domain import sets as rules
from ..domain.errors import (
    ConflictError,
    CorruptStoreError,
    FieldError,
    InvalidInputError,
    ItemHasDecisionsError,
    NotFoundError,
    PreconditionRequiredError,
    TooLargeError,
)
from ..domain.sets import (
    DEFAULT_PAGE,
    MAX_PAGE,
    QSET_FORMAT,
    VERDICTS,
    Actor,
    AppliedReviews,
    BuiltSet,
    FreezePlan,
    ImportShape,
    ItemFilter,
    ItemReview,
    ItemSummary,
    ReviewProgress,
    SetFile,
    SetRef,
    SetVersion,
    apply_edit,
    dominant_corpus,
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
    valid_name,
    valid_version,
)
from ..ports.clock import Clock
from ..ports.sets import SetCatalog, SetFiles, SetFormats

log = logging.getLogger("research_api")

NO_SUCH_VERSION = "No such set version."
NO_SUCH_ITEM = "No such item in this version."
NEEDS_IF_MATCH = "Send the version you read in an If-Match header."
NO_WILDCARD = "Send the etag you read in If-Match; * does not say which version you read."
CHANGED = "The draft changed since you read it. Read it again."
NEEDS_PLAN = "Read the freeze plan first and send its etag in an If-Match header."
PLAN_CHANGED = "The draft or its decisions changed since you read the plan. Read it again."
PREFREEZE = "prefreeze"
TEXT_IS_FIXED = (
    "{id} has review decisions, so its text is fixed: they are about the text people read. "
    "Propose the change with an edit decision, or reject it and add a new item."
)
ID_IS_TAKEN = (
    "{id} has review decisions that are kept in this version, so the id cannot be used for "
    "another item. Use a new id."
)


@dataclass(frozen=True)
class VersionListing:
    version: SetVersion
    progress: ReviewProgress | None
    """None when the file could not be read for the count."""


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    corpus: str | None
    versions: tuple[VersionListing, ...]


@dataclass(frozen=True)
class VersionView:
    version: SetVersion
    header: dict[str, Any]
    progress: ReviewProgress
    items: tuple[ItemSummary, ...]
    total: int
    """How many items match the filter, before paging."""
    offset: int
    limit: int


@dataclass(frozen=True)
class ItemView:
    version: SetVersion
    item: dict[str, Any]
    review: ItemReview
    reviews: tuple[dict[str, Any], ...]
    problems: tuple[FieldError, ...]


@dataclass(frozen=True)
class ImportOutcome:
    version: SetVersion
    items_read: int
    shape: str | None


class _Storage:
    """What the three use cases share: the ports, the lock and the loading of a version."""

    def __init__(
        self, *, catalog: SetCatalog, files: SetFiles, formats: SetFormats, clock: Clock
    ) -> None:
        self.catalog = catalog
        self.files = files
        self.formats = formats
        self.clock = clock
        self.lock = threading.RLock()

    def now(self) -> str:
        return self.clock.now().isoformat()

    def row(self, ref: SetRef) -> SetVersion:
        row = self.catalog.get(ref)
        if row is None:
            raise NotFoundError(NO_SUCH_VERSION)
        return row

    def load(self, ref: SetRef, *, repair: bool = False) -> tuple[SetVersion, SetFile]:
        """The catalog entry and the file.

        For a draft the file is the truth: if the md5 differs (a crash between the two writes) the
        entry is corrected, and with ``repair`` (under the lock, for a request that is about to
        write) the correction is saved. A frozen version is the opposite: its md5 is its identity,
        runs name it, so a file that no longer has it is a server problem, never a correction.
        """
        row = self.row(ref)
        data = self.files.read(ref)
        if data is None:
            raise CorruptStoreError(f"the file of {ref.header_name} is missing")
        file = self.formats.peek(data)
        if file.md5 != row.md5 or len(file.items) != row.items:
            if row.frozen:
                log.error("the frozen file of %s no longer has its md5", ref.header_name)
                raise CorruptStoreError(
                    f"the frozen file of {ref.header_name} no longer has the md5 it was frozen with"
                )
            if (started := self._interrupted_freeze(row)) is not None:
                log.warning("a freeze of %s did not finish; its draft is back", ref.header_name)
                if repair:
                    self.files.write(ref, started.data)
                return row, started
            log.warning("catalog and file of %s disagree; the file wins", ref.header_name)
            fixed = replace(row, md5=file.md5, items=len(file.items))
            if repair:
                self.catalog.save(fixed, expected_md5=row.md5)
            row = fixed
        return row, file

    def _interrupted_freeze(self, row: SetVersion) -> SetFile | None:
        """The draft a freeze started from, when the process stopped after the frozen file was
        written and before the catalog said so. The row still describes the draft, and the copy
        kept for the freeze is that draft."""
        data = self.files.read_copy(row.ref, PREFREEZE)
        if data is None:
            return None
        try:
            kept = self.formats.peek(data)
        except CorruptStoreError:
            return None
        return kept if kept.md5 == row.md5 and len(kept.items) == row.items else None

    def reviews(self, ref: SetRef) -> list[dict[str, Any]]:
        return self.reviews_and_bytes(ref)[0]

    def reviews_and_bytes(self, ref: SetRef) -> tuple[list[dict[str, Any]], bytes | None]:
        """The decisions and the bytes they were read from (one read, so they agree)."""
        data = self.files.read_reviews(ref)
        return ([] if data is None else self.formats.peek_reviews(data)), data

    def commit(self, row: SetVersion, built: BuiltSet, *, expected_md5: str) -> SetVersion:
        """Store a changed draft: the file first, then the catalog."""
        self.files.write(row.ref, built.data)
        saved = replace(
            row,
            md5=built.md5,
            items=len(built.items),
            corpus=row.corpus or dominant_corpus(built.items),
            updated_at=self.clock.now(),
        )
        self.catalog.save(saved, expected_md5=expected_md5)
        return saved

    def check_if_match(
        self,
        if_match: str | None,
        current: str,
        *,
        needs: str = NEEDS_IF_MATCH,
        changed: str = CHANGED,
    ) -> None:
        """``current`` is the md5 of the draft, or the token of the plan, that the caller read."""
        if not if_match:
            raise PreconditionRequiredError(needs)
        if has_wildcard(if_match):
            raise PreconditionRequiredError(NO_WILDCARD)
        if not if_match_allows(if_match, current):
            raise ConflictError(changed)


def _long_id(path: str) -> FieldError:
    return FieldError(path, f"The id is over {rules.MAX_ITEM_ID_LENGTH} characters.")


def _refuse_long_ids(items: tuple[dict[str, Any], ...]) -> None:
    """An id the routes could not name would be stored and never reachable. The file's line is
    the item's position plus two (the header is line one)."""
    long = [
        _long_id(f"/lines/{n + 2}/id")
        for n, item in enumerate(items)
        if len(item["id"]) > rules.MAX_ITEM_ID_LENGTH
    ]
    if long:
        raise InvalidInputError(
            "The file is not a valid qset/1 file.", tuple(long[: rules.MAX_REPORTED_PROBLEMS])
        )


def _find(items: tuple[dict[str, Any], ...], item_id: str) -> int:
    for position, item in enumerate(items):
        if item["id"] == item_id:
            return position
    return -1


def _page(offset: int, limit: int) -> tuple[int, int]:
    return max(offset, 0), min(max(limit, 1), MAX_PAGE)


class ManageSets:
    """The catalog, drafts, items, import and export."""

    def __init__(self, storage: _Storage) -> None:
        self._s = storage

    # --- reading ----------------------------------------------------------------------------

    def list_sets(self) -> list[CatalogEntry]:
        by_name: dict[str, list[VersionListing]] = {}
        for row in self._s.catalog.all():
            by_name.setdefault(row.ref.name, []).append(VersionListing(row, self._progress(row)))
        return [
            CatalogEntry(
                name=name,
                corpus=next(
                    (v.version.corpus for v in reversed(listings) if v.version.corpus), None
                ),
                versions=tuple(listings),
            )
            for name, listings in sorted(by_name.items())
        ]

    def _progress(self, row: SetVersion) -> ReviewProgress | None:
        try:
            _, file = self._s.load(row.ref)
            states = review_states((i["id"] for i in file.items), self._s.reviews(row.ref))
        except (CorruptStoreError, OSError):
            return None
        return review_progress(states)

    def shapes(self) -> tuple[ImportShape, ...]:
        return self._s.formats.shapes()

    def get_version(
        self,
        ref: SetRef,
        flt: ItemFilter | None = None,
        *,
        offset: int = 0,
        limit: int = DEFAULT_PAGE,
    ) -> VersionView:
        row, file = self._s.load(ref)
        states = review_states((i["id"] for i in file.items), self._s.reviews(ref))
        flt = flt or ItemFilter()
        matching = [i for i in file.items if item_matches(i, states[i["id"]], flt)]
        offset, limit = _page(offset, limit)
        shown = tuple(summarize(i, states[i["id"]]) for i in matching[offset : offset + limit])
        return VersionView(
            row, file.header, review_progress(states), shown, len(matching), offset, limit
        )

    def get_item(self, ref: SetRef, item_id: str) -> ItemView:
        row, file = self._s.load(ref)
        position = _find(file.items, item_id)
        if position < 0:
            raise NotFoundError(NO_SUCH_ITEM)
        return self._item_view(row, file, file.items[position])

    def _item_view(self, row: SetVersion, file: SetFile, item: dict[str, Any]) -> ItemView:
        lines = self._s.reviews(row.ref)
        states = review_states([item["id"]], lines)
        mine = tuple(
            line
            for line in lines
            if line.get("target") == "item" and line.get("item_id") == item["id"]
        )
        return ItemView(row, item, states[item["id"]], mine, self._s.formats.check_item(item))

    def export(self, ref: SetRef) -> tuple[SetVersion, bytes]:
        row, file = self._s.load(ref)
        return row, file.data

    def validate(
        self, *, text: str | None = None, item: dict[str, Any] | None = None
    ) -> tuple[FieldError, ...]:
        """A dry run: what is wrong with a file's text or one item. Nothing is stored."""
        if (text is None) == (item is None):
            raise InvalidInputError("Give either text or item.")
        if text is not None:
            return self._s.formats.check_text(text)
        assert item is not None
        return self._s.formats.check_item(item)

    # --- creating ---------------------------------------------------------------------------

    def create(
        self,
        actor: Actor,
        *,
        name: str,
        corpus: str | None = None,
        copy_from: str | None = None,
    ) -> SetVersion:
        """A new draft: empty under a new name, or a copy of a version as the next version."""
        if not valid_name(name):
            raise InvalidInputError("The name is not valid.", (FieldError("/name", NAME_RULE),))
        if copy_from is not None and not valid_version(copy_from):
            raise InvalidInputError(
                "The version is not valid.", (FieldError("/copy_from", "Use a version like v1."),)
            )
        with self._s.lock:
            existing = self._s.catalog.versions_of(name)
            now = self._s.now()
            if copy_from is None:
                if existing:
                    raise ConflictError(
                        f"A set named {name} exists. Copy one of its versions to start the next."
                    )
                ref = SetRef(name, "v1")
                header: dict[str, Any] = {
                    "format": QSET_FORMAT,
                    "set": ref.header_name,
                    "created": now,
                }
                items: list[dict[str, Any]] = []
            else:
                source_ref = SetRef(name, copy_from)
                source, file = self._s.load(source_ref, repair=True)
                ref = SetRef(name, next_version(v.ref.version for v in existing))
                header = {
                    "format": QSET_FORMAT,
                    "set": ref.header_name,
                    "created": now,
                    "notes": f"Copy of {source_ref.header_name} (md5 {file.md5}).",
                }
                if "source" in file.header:
                    header["source"] = file.header["source"]
                items = copy.deepcopy(list(file.items))
                corpus = corpus or source.corpus
            built = self._s.formats.build(header, items)
            return self._store_new(actor, ref, built, corpus or dominant_corpus(built.items))

    def _store_new(
        self, actor: Actor, ref: SetRef, built: BuiltSet, corpus: str | None
    ) -> SetVersion:
        now = self._s.clock.now()
        row = SetVersion(
            ref=ref,
            status="draft",
            owner=actor.username,
            md5=built.md5,
            items=len(built.items),
            corpus=corpus,
            created_at=now,
            updated_at=now,
        )
        self._s.catalog.add(row)  # refuses a version that exists, before any file is written
        try:
            self._s.files.write(ref, built.data)
        except BaseException:
            self._s.catalog.delete(ref)  # no entry without a file
            raise
        return row

    def import_set(
        self,
        actor: Actor,
        data: bytes,
        *,
        name: str | None = None,
        version: str | None = None,
        shape: str | None = None,
        corpus: str | None = None,
        source_file: str | None = None,
    ) -> ImportOutcome:
        """A new draft from an uploaded ``qset/1`` file, or from an older shape (converted).

        Nothing is stored unless the whole file is accepted. A ``qset/1`` file whose header
        already names the target keeps its exact bytes, so its md5 is the one it came with.
        """
        if len(data) > rules.MAX_IMPORT_BYTES:
            raise TooLargeError(f"The file is over {rules.MAX_IMPORT_BYTES} bytes.")
        data = rules.unzip_upload(data, rules.MAX_IMPORT_BYTES)
        if not data.strip():
            raise InvalidInputError("The file is empty.")
        if name is not None and not valid_name(name):
            raise InvalidInputError("The name is not valid.", (FieldError("/name", NAME_RULE),))
        if version is not None and not valid_version(version):
            raise InvalidInputError(
                "The version is not valid.", (FieldError("/version", "Use a version like v1."),)
            )
        with self._s.lock:
            if shape is None:
                file = self._s.formats.read(data)
                ref = self._target(name, version, file.header["set"])
                if file.header["set"] == ref.header_name:
                    built = BuiltSet(file.header, file.items, file.data, file.md5)
                else:
                    built = self._s.formats.build(
                        {**file.header, "set": ref.header_name}, file.items
                    )
            else:
                if name is None:
                    raise InvalidInputError(
                        "Give the name of the new set.",
                        (FieldError("/name", "A converted file does not carry a set name."),),
                    )
                ref = self._target(name, version, name)
                built = self._s.formats.convert(
                    shape,
                    data,
                    set_name=ref.header_name,
                    created=self._s.now(),
                    corpus=corpus,
                    source_file=source_file[:200] if source_file else None,
                )
            _refuse_long_ids(built.items)
            row = self._store_new(actor, ref, built, corpus or dominant_corpus(built.items))
        return ImportOutcome(row, len(built.items), shape)

    def _target(self, name: str | None, version: str | None, header_set: str) -> SetRef:
        """Where an import goes: the name asked for or the file's, and a version that is free."""
        header_ref = parse_header_name(header_set)
        if name is None:
            if header_ref is not None:
                name = header_ref.name
            elif valid_name(header_set):
                name = header_set
            else:
                raise InvalidInputError(
                    "Give a name for the new set.",
                    (FieldError("/name", f"The file's set {header_set!r} is not name/vN."),),
                )
        taken = [v.ref.version for v in self._s.catalog.versions_of(name)]
        if version is not None:
            if version in taken:
                raise ConflictError(f"{name}/{version} already exists.")
            return SetRef(name, version)
        if header_ref is not None and header_ref.name == name and header_ref.version not in taken:
            return header_ref
        return SetRef(name, next_version(taken))

    # --- items ------------------------------------------------------------------------------

    def put_item(
        self, actor: Actor, ref: SetRef, item_id: str, item: dict[str, Any], if_match: str | None
    ) -> ItemView:
        """Replace one item of a draft."""
        with self._s.lock:
            row, file = self._editable(actor, ref, if_match)
            position = _find(file.items, item_id)
            if position < 0:
                raise NotFoundError(NO_SUCH_ITEM)
            item = self._checked(item, expect_id=item_id)
            if item == file.items[position]:
                return self._item_view(row, file, file.items[position])  # nothing to change
            self._refuse_if_decided(ref, item_id, TEXT_IS_FIXED)
            items = list(file.items)
            items[position] = item
            return self._save(row, file, items, item)

    def add_item(
        self, actor: Actor, ref: SetRef, item: dict[str, Any], if_match: str | None
    ) -> ItemView:
        """Add an item to the end of a draft. A missing ``corpus`` is the set's own."""
        with self._s.lock:
            row, file = self._editable(actor, ref, if_match)
            if "corpus" not in item and row.corpus:
                item = {**item, "corpus": row.corpus}
            item = self._checked(item, expect_id=None)
            if _find(file.items, item["id"]) >= 0:
                raise ConflictError(f"The item {item['id']} exists. Replace it instead.")
            self._refuse_if_decided(ref, item["id"], ID_IS_TAKEN)
            return self._save(row, file, [*file.items, item], item)

    def delete_item(
        self, actor: Actor, ref: SetRef, item_id: str, if_match: str | None
    ) -> SetVersion:
        with self._s.lock:
            row, file = self._editable(actor, ref, if_match)
            if _find(file.items, item_id) < 0:
                raise NotFoundError(NO_SUCH_ITEM)
            mirrors = [i["id"] for i in file.items if i.get("mirror_of") == item_id]
            if mirrors:
                raise ConflictError(
                    f"{item_id} is the original of {', '.join(mirrors)}. Delete the mirror first."
                )
            items = [i for i in file.items if i["id"] != item_id]
            built = self._s.formats.build(file.header, items)
            return self._s.commit(row, built, expected_md5=row.md5)

    def _refuse_if_decided(self, ref: SetRef, item_id: str, message: str) -> None:
        """People accept, edit or reject the text they read. If that text could be replaced, an
        accept given before the change would count towards the two people that make an item gold,
        and nobody would have read what is now there. So the text of an item with decisions is
        fixed (a change goes through an ``edit`` decision, applied at the freeze), and so is the
        id of an item that was deleted after it was decided on."""
        if any(
            line.get("target") == "item" and line.get("item_id") == item_id
            for line in self._s.reviews(ref)
        ):
            raise ItemHasDecisionsError(message.format(id=item_id))

    def _editable(
        self, actor: Actor, ref: SetRef, if_match: str | None
    ) -> tuple[SetVersion, SetFile]:
        require_editable(actor, self._s.row(ref))  # before anything is read or repaired
        row, file = self._s.load(ref, repair=True)
        self._s.check_if_match(if_match, row.md5)
        return row, file

    def _checked(self, item: dict[str, Any], *, expect_id: str | None) -> dict[str, Any]:
        if isinstance(item.get("id"), str) and len(item["id"]) > rules.MAX_ITEM_ID_LENGTH:
            raise InvalidInputError("The item is not a valid qset/1 item.", (_long_id("/id"),))
        if expect_id is not None:
            if "id" not in item:
                item = {"id": expect_id, **item}
            elif item["id"] != expect_id:
                raise InvalidInputError(
                    "The item's id is not the id in the path.",
                    (FieldError("/id", f"must be {expect_id}, the id in the path"),),
                )
        problems = self._s.formats.check_item(item)
        if problems:
            raise InvalidInputError("The item is not a valid qset/1 item.", problems)
        return item

    def _save(
        self, row: SetVersion, file: SetFile, items: list[dict[str, Any]], item: dict[str, Any]
    ) -> ItemView:
        built = self._s.formats.build(file.header, items)
        saved = self._s.commit(row, built, expected_md5=row.md5)
        changed = SetFile(built.header, built.items, built.data, built.md5)
        stored = next(i for i in built.items if i["id"] == item["id"])
        return self._item_view(saved, changed, stored)


class ReviewItems:
    """Human decisions on items (``reviews/1``), kept in a file beside the version."""

    def __init__(self, storage: _Storage) -> None:
        self._s = storage

    def add(
        self,
        actor: Actor,
        ref: SetRef,
        *,
        if_match: str | None,
        item_id: str,
        verdict: str,
        note: str | None = None,
        edit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record one decision. The reviewer, role and date are filled in here.

        ``if_match`` is the etag of the draft the reviewer read, and ``set_md5`` is that md5: a
        decision is about the text that was read, so it is refused (409) when the draft has
        changed since, and a decision never lands on a text its author did not see.
        """
        if verdict not in VERDICTS:
            raise InvalidInputError(
                "The verdict is not valid.",
                (FieldError("/verdict", f"must be one of {', '.join(VERDICTS)}"),),
            )
        with self._s.lock:
            require_draft(self._s.row(ref))
            _, file = self._s.load(ref, repair=True)
            self._s.check_if_match(if_match, file.md5)
            position = _find(file.items, item_id)
            if position < 0:
                raise NotFoundError(NO_SUCH_ITEM)
            line: dict[str, Any] = {
                "item_id": item_id,
                "target": "item",
                "reviewer": actor.username,
                "role": "human",
                "verdict": verdict,
                "date": self._s.now(),
                "set_md5": file.md5,
            }
            if note:
                line["note"] = note
            if edit is not None:
                line["edit"] = edit
            problems = list(self._s.formats.check_review(line))
            if edit is not None and verdict != "edit":
                problems = [FieldError("/edit", "Only an edit decision carries an edit.")]
            elif edit is None and verdict == "edit":
                problems = [*problems, FieldError("/edit", "An edit lists the fields to change.")]
            if not problems and verdict == "edit" and edit:
                # an edit that leaves the item invalid would stop the freeze, so refuse it now
                problems = [
                    FieldError("/edit" + (p.path if p.path != "/" else ""), p.message)
                    for p in self._s.formats.check_item(apply_edit(file.items[position], edit))
                ]
            if problems:
                raise InvalidInputError("The review is not valid.", tuple(problems))
            self._append(ref, line)
            return line

    def _append(self, ref: SetRef, line: dict[str, Any]) -> None:
        text = self._s.formats.review_line(line)
        stored = self._s.files.read_reviews(ref)
        if stored is None:
            header = self._s.formats.review_header(ref.header_name, self._s.now())
            self._s.files.write_reviews(ref, header + text)
        elif stored.endswith(b"\n"):
            self._s.files.append_reviews(ref, text)
        else:
            # An earlier append stopped half way (its request got no answer). Appending would glue
            # this line to the torn one, so the file is rewritten without the torn end.
            whole = stored[: stored.rfind(b"\n") + 1]
            log.warning("a half-written review line of %s was dropped", ref.header_name)
            self._s.files.write_reviews(ref, whole + text)

    def list(self, ref: SetRef, item_id: str | None = None) -> list[dict[str, Any]]:
        self._s.row(ref)
        return [
            line
            for line in self._s.reviews(ref)
            if line.get("target") == "item" and (item_id is None or line.get("item_id") == item_id)
        ]


class FreezeSets:
    """Applying the reviews and closing a draft for good."""

    def __init__(self, storage: _Storage) -> None:
        self._s = storage

    def _plan(self, row: SetVersion, file: SetFile) -> tuple[FreezePlan, AppliedReviews]:
        lines, raw = self._s.reviews_and_bytes(row.ref)
        applied = self._s.formats.apply_reviews(file.items, lines, file.md5)
        states = review_states((i["id"] for i in file.items), lines)
        plan = FreezePlan(
            items_before=len(file.items),
            items_after=len(applied.items),
            accepted=applied.accepted,
            edited=applied.edited,
            rejected=applied.rejected,
            unreviewed=applied.unreviewed,
            needs_second=tuple(
                i["id"] for i in file.items if states[i["id"]].state == "needs_second"
            ),
            stale=applied.stale,
            unknown=applied.unknown,
            token=plan_token(file.md5, raw),
        )
        return plan, applied

    def _frozen(
        self, row: SetVersion, file: SetFile, plan: FreezePlan, applied: AppliedReviews
    ) -> BuiltSet:
        """The file a freeze would write. The plan and the freeze both build it, so the plan
        refuses what the freeze would refuse, and names the item by id."""
        if not applied.items:
            raise InvalidInputError(
                "Nothing would be left: a frozen version needs at least one item."
            )
        ref = row.ref
        header: dict[str, Any] = {
            "format": QSET_FORMAT,
            "set": ref.header_name,
            "created": self._s.now(),
        }
        if "source" in file.header:
            header["source"] = file.header["source"]
        header["notes"] = (
            f"{ref.header_name} (md5 {file.md5}) with the human reviews applied: "
            f"{len(plan.accepted)} accepted, {len(plan.edited)} edited, "
            f"{len(plan.rejected)} rejected, {len(plan.unreviewed)} not reviewed."
        )
        try:
            return self._s.formats.build(header, applied.items)
        except InvalidInputError as e:
            ids = [i["id"] for i in applied.items]
            raise InvalidInputError(
                "The reviews would leave a set that is not valid.",
                tuple(FieldError(_item_path(err.path, ids), err.message) for err in e.errors),
            ) from None

    def plan(self, ref: SetRef) -> tuple[SetVersion, FreezePlan]:
        """What freezing would do; nothing changes. It refuses what the freeze would refuse."""
        row, file = self._s.load(ref)
        require_draft(row)
        plan, applied = self._plan(row, file)
        self._frozen(row, file, plan, applied)
        return row, plan

    def freeze(
        self, actor: Actor, ref: SetRef, if_match: str | None
    ) -> tuple[SetVersion, FreezePlan]:
        """Apply the latest human decision of each item and close the version.

        ``if_match`` is the token of the plan that was read (``plan``): a decision added or an
        item changed since then is a 409, so nothing is applied that nobody saw.

        The draft as it was is kept beside the version (``<version>.prefreeze.qset.jsonl``), so
        a rejected item is never lost for good. The frozen file has a new md5.
        """
        with self._s.lock:
            require_editable(actor, self._s.row(ref))
            row, file = self._s.load(ref, repair=True)
            plan, applied = self._plan(row, file)
            built = self._frozen(row, file, plan, applied)  # what the plan refuses, refused first
            self._s.check_if_match(if_match, plan.token, needs=NEEDS_PLAN, changed=PLAN_CHANGED)
            self._s.files.keep_copy(ref, PREFREEZE, file.data)
            self._s.files.write(ref, built.data)
            frozen = replace(
                row,
                status="frozen",
                md5=built.md5,
                items=len(built.items),
                updated_at=self._s.clock.now(),
                frozen_at=self._s.clock.now(),
            )
            try:
                self._s.catalog.save(frozen, expected_md5=row.md5)
            except BaseException:
                # The pair of writes is not atomic: put the reviewed draft back so the version is
                # still the draft it was (if this write fails too, the next load restores it).
                with contextlib.suppress(OSError):
                    self._s.files.write(ref, file.data)
                raise
            return frozen, plan


_ITEM_POINTER = re.compile(r"^/items/(\d+)(.*)$", re.S)


def _item_path(path: str, ids: list[str]) -> str:
    """``/items/<position>/...`` as ``/items/<id>/...``: the person sees ids, not positions in a
    list that the reviews rearranged."""
    found = _ITEM_POINTER.match(path)
    if found and int(found[1]) < len(ids):
        return f"/items/{ids[int(found[1])]}{found[2]}"
    return path


NAME_RULE = "Use letters, digits, '.', '_' and '-', starting with a letter or digit (64 at most)."


@dataclass(frozen=True)
class SetServices:
    manage: ManageSets
    review: ReviewItems
    freeze: FreezeSets


def build_set_services(
    *, catalog: SetCatalog, files: SetFiles, formats: SetFormats, clock: Clock
) -> SetServices:
    storage = _Storage(catalog=catalog, files=files, formats=formats, clock=clock)
    return SetServices(ManageSets(storage), ReviewItems(storage), FreezeSets(storage))


__all__ = [
    "CatalogEntry",
    "FreezeSets",
    "ImportOutcome",
    "ItemView",
    "ManageSets",
    "ReviewItems",
    "SetServices",
    "VersionListing",
    "VersionView",
    "build_set_services",
]
