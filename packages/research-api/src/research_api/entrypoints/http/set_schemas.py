"""HTTP schemas of the question sets. An item is a ``qset/1`` item and a review line a ``reviews/1``
line: both pass through as JSON objects, checked by the format's own JSON Schema, so this API
never keeps a second description of them."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ...application.sets import (
    CatalogEntry,
    ImportOutcome,
    ItemView,
    VersionListing,
    VersionView,
)
from ...domain.errors import FieldError
from ...domain.sets import (
    NAME_PATTERN,
    VERSION_PATTERN,
    FreezePlan,
    ImportShape,
    ItemReview,
    ItemSummary,
    ReviewProgress,
    ReviewState,
    SetStatus,
    SetVersion,
    etag_of,
)

NAME_REGEX = f"^{NAME_PATTERN}$"
VERSION_REGEX = f"^{VERSION_PATTERN}$"

Item = dict[str, Any]


class ReviewProgressOut(BaseModel):
    """How many items are in each review state."""

    items: int
    none: int = Field(description="No human has decided yet.")
    needs_second: int = Field(description="Accepted by one person; a second is needed.")
    accepted: int = Field(description="Accepted by two different people (gold).")
    edited: int = Field(description="The latest decision is an edit.")
    rejected: int = Field(description="The latest decision is a reject.")

    @classmethod
    def from_domain(cls, p: ReviewProgress) -> ReviewProgressOut:
        return cls(
            items=p.items,
            none=p.none,
            needs_second=p.needs_second,
            accepted=p.accepted,
            edited=p.edited,
            rejected=p.rejected,
        )


class SetVersionOut(BaseModel):
    name: str
    version: str
    status: SetStatus
    owner: str
    md5: str = Field(description="The md5 of the file: the identity of a frozen version.")
    etag: str = Field(description="Send this in `If-Match` to change a draft.")
    items: int
    corpus: str | None
    created_at: datetime
    updated_at: datetime
    frozen_at: datetime | None = None
    review: ReviewProgressOut | None = Field(
        default=None, description="Left out when the file could not be read for the count."
    )

    @classmethod
    def from_domain(cls, v: SetVersion, progress: ReviewProgress | None = None) -> SetVersionOut:
        return cls(
            name=v.ref.name,
            version=v.ref.version,
            status=v.status,
            owner=v.owner,
            md5=v.md5,
            etag=etag_of(v.md5),
            items=v.items,
            corpus=v.corpus,
            created_at=v.created_at,
            updated_at=v.updated_at,
            frozen_at=v.frozen_at,
            review=None if progress is None else ReviewProgressOut.from_domain(progress),
        )

    @classmethod
    def from_listing(cls, listing: VersionListing) -> SetVersionOut:
        return cls.from_domain(listing.version, listing.progress)


class SetOut(BaseModel):
    name: str
    corpus: str | None
    versions: list[SetVersionOut] = Field(description="Oldest first.")

    @classmethod
    def from_domain(cls, entry: CatalogEntry) -> SetOut:
        return cls(
            name=entry.name,
            corpus=entry.corpus,
            versions=[SetVersionOut.from_listing(v) for v in entry.versions],
        )


class NewSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=NAME_REGEX, max_length=64)
    corpus: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="The corpus new items name when they leave it out.",
    )
    copy_from: str | None = Field(
        default=None,
        pattern=VERSION_REGEX,
        description="Start the next version of an existing set as a copy of this version.",
    )


class QSetHeader(BaseModel):
    """Line 1 of a `qset/1` file."""

    model_config = ConfigDict(extra="allow")

    format: str
    set: str
    created: str
    source: dict[str, str] | None = None
    notes: str | None = None


class ItemSummaryOut(BaseModel):
    id: str
    lang: str
    form: str
    content: str | None
    corpus: str
    split: str | None
    question: str = Field(description="The question, cut to 240 characters.")
    tags: dict[str, str]
    mirror_of: str | None
    review: ReviewState
    accepted_by: list[str]

    @classmethod
    def from_domain(cls, s: ItemSummary) -> ItemSummaryOut:
        return cls(
            id=s.id,
            lang=s.lang,
            form=s.form,
            content=s.content,
            corpus=s.corpus,
            split=s.split,
            question=s.question,
            tags=s.tags,
            mirror_of=s.mirror_of,
            review=s.review,
            accepted_by=list(s.accepted_by),
        )


class ItemPage(BaseModel):
    version: SetVersionOut
    header: QSetHeader
    review: ReviewProgressOut
    items: list[ItemSummaryOut]
    total: int = Field(description="Items matching the filters, before paging.")
    offset: int
    limit: int

    @classmethod
    def from_domain(cls, view: VersionView) -> ItemPage:
        return cls(
            version=SetVersionOut.from_domain(view.version),
            header=QSetHeader.model_validate(view.header),
            review=ReviewProgressOut.from_domain(view.progress),
            items=[ItemSummaryOut.from_domain(i) for i in view.items],
            total=view.total,
            offset=view.offset,
            limit=view.limit,
        )


class ReviewLine(BaseModel):
    """One `reviews/1` decision on an item, as stored."""

    model_config = ConfigDict(extra="allow")

    item_id: str
    target: Literal["item"]
    reviewer: str
    role: Literal["human", "checker"]
    verdict: Literal["accept", "edit", "reject"]
    date: str
    note: str | None = None
    set_md5: str | None = None
    edit: Item | None = None


class ItemReviewOut(BaseModel):
    state: ReviewState
    gold: bool = Field(description="True when two different people accepted it.")
    accepted_by: list[str]
    deciding: ReviewLine | None = Field(description="The latest decision, which freezing applies.")

    @classmethod
    def from_domain(cls, r: ItemReview) -> ItemReviewOut:
        return cls(
            state=r.state,
            gold=r.gold,
            accepted_by=list(r.accepted_by),
            deciding=None if r.deciding is None else ReviewLine.model_validate(dict(r.deciding)),
        )


class ProblemAt(BaseModel):
    path: str = Field(description="JSON pointer to the value (`/lines/N/...` for a file).")
    message: str

    @classmethod
    def from_error(cls, e: FieldError) -> ProblemAt:
        return cls(path=e.path, message=e.message)


class ItemDetail(BaseModel):
    version: SetVersionOut
    item: Item = Field(description="The `qset/1` item as stored.")
    review: ItemReviewOut
    reviews: list[ReviewLine] = Field(description="Every decision on this item, oldest first.")
    validation: list[ProblemAt] = Field(
        description="What `qset/1` says about the item. Empty when valid."
    )

    @classmethod
    def from_domain(cls, view: ItemView) -> ItemDetail:
        return cls(
            version=SetVersionOut.from_domain(view.version),
            item=view.item,
            review=ItemReviewOut.from_domain(view.review),
            reviews=[ReviewLine.model_validate(line) for line in view.reviews],
            validation=[ProblemAt.from_error(p) for p in view.problems],
        )


class NewReview(BaseModel):
    """The server adds the reviewer (you), the role (`human`), the date and the set md5."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    verdict: Literal["accept", "edit", "reject"]
    note: str | None = Field(
        default=None, max_length=4000, description="Required for edit and reject."
    )
    edit: Item | None = Field(
        default=None, description="For `edit`: the item fields to replace, with their new values."
    )


class ValidateRequest(BaseModel):
    """Give `item` (one item) or `text` (the text of a whole file)."""

    model_config = ConfigDict(extra="forbid")

    item: Item | None = None
    text: str | None = None


class Validation(BaseModel):
    ok: bool
    errors: list[ProblemAt]


class ImportResult(BaseModel):
    version: SetVersionOut
    items_read: int
    shape: str | None = Field(description="The older shape that was converted, if any.")

    @classmethod
    def from_domain(cls, outcome: ImportOutcome) -> ImportResult:
        return cls(
            version=SetVersionOut.from_domain(outcome.version),
            items_read=outcome.items_read,
            shape=outcome.shape,
        )


class ImportShapeOut(BaseModel):
    name: str
    description: str
    suffix: str
    needs_corpus: bool

    @classmethod
    def from_domain(cls, s: ImportShape) -> ImportShapeOut:
        return cls(
            name=s.name, description=s.description, suffix=s.suffix, needs_corpus=s.needs_corpus
        )


class FreezePlanOut(BaseModel):
    """What freezing does, by item id."""

    items_before: int
    items_after: int
    accepted: list[str]
    edited: list[str]
    rejected: list[str]
    unreviewed: list[str]
    needs_second: list[str] = Field(description="Accepted by one person only; still kept.")
    stale: list[str] = Field(description="Decided on an earlier state of the draft.")
    unknown: list[str] = Field(description="Decisions about items the draft no longer has.")

    @classmethod
    def from_domain(cls, p: FreezePlan) -> FreezePlanOut:
        return cls(
            items_before=p.items_before,
            items_after=p.items_after,
            accepted=list(p.accepted),
            edited=list(p.edited),
            rejected=list(p.rejected),
            unreviewed=list(p.unreviewed),
            needs_second=list(p.needs_second),
            stale=list(p.stale),
            unknown=list(p.unknown),
        )


class FreezePreview(BaseModel):
    version: SetVersionOut
    plan: FreezePlanOut


class FreezeResult(BaseModel):
    version: SetVersionOut
    plan: FreezePlanOut
