"""Question sets: names, versions, who may change them, and what the reviews say about an item.

A set version is one ``qset/1`` file. It is a *draft* while people edit and review it and
*frozen* after ``freeze``, when the reviews are applied and the file never changes again. Its
identity is the md5 of the file (the draft's ETag is the md5 too). The rules here work on plain
dicts, so this module needs neither the format library nor a web framework.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from .auth import Role, User
from .errors import ForbiddenError

QSET_FORMAT = "qset/1"
REVIEWS_FORMAT = "reviews/1"

NAME_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
VERSION_PATTERN = r"v[1-9][0-9]{0,5}"
ITEM_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_NAME = re.compile(NAME_PATTERN)
_VERSION = re.compile(VERSION_PATTERN)
_ITEM_ID = re.compile(ITEM_ID_PATTERN)

# Caps on what a request may carry (the import cap is the scope's proposal; tests lower it).
MAX_IMPORT_BYTES = 20 * 1024 * 1024
MAX_ITEM_BYTES = 1024 * 1024
MAX_REVIEW_BYTES = 256 * 1024
DEFAULT_PAGE = 50
MAX_PAGE = 200
MAX_REPORTED_PROBLEMS = 100
QUESTION_PREVIEW = 240

SetStatus = Literal["draft", "frozen"]
ReviewState = Literal["none", "needs_second", "accepted", "edited", "rejected"]
REVIEW_STATES: tuple[ReviewState, ...] = ("none", "needs_second", "accepted", "edited", "rejected")
VERDICTS = ("accept", "edit", "reject")

# Two different people must accept an item before it counts as gold (REQ-GS-5).
ACCEPTS_FOR_GOLD = 2


def valid_name(text: str) -> bool:
    return _NAME.fullmatch(text) is not None


def valid_version(text: str) -> bool:
    return _VERSION.fullmatch(text) is not None


def valid_item_id(text: str) -> bool:
    return _ITEM_ID.fullmatch(text) is not None


@dataclass(frozen=True)
class Actor:
    """Who is acting: a username and whether they are an admin."""

    username: str
    admin: bool = False

    @classmethod
    def of(cls, user: User) -> Actor:
        return cls(user.username, user.role is Role.ADMIN)


@dataclass(frozen=True, order=True)
class SetRef:
    name: str
    version: str

    @property
    def header_name(self) -> str:
        """The ``set`` field of the file's header, which runs and scores repeat."""
        return f"{self.name}/{self.version}"

    @property
    def file_stem(self) -> str:
        return f"{self.name}-{self.version}"


def parse_header_name(text: str) -> SetRef | None:
    """``demo-set/v1`` as a reference, or None when it does not have that shape."""
    name, slash, version = text.rpartition("/")
    if slash and valid_name(name) and valid_version(version):
        return SetRef(name, version)
    return None


def version_number(version: str) -> int:
    return int(version[1:])


def next_version(existing: Iterable[str]) -> str:
    return "v" + str(max((version_number(v) for v in existing), default=0) + 1)


@dataclass(frozen=True)
class SetVersion:
    """The catalog entry of one set version."""

    ref: SetRef
    status: SetStatus
    owner: str
    md5: str
    items: int
    corpus: str | None
    created_at: datetime
    updated_at: datetime
    frozen_at: datetime | None = None

    @property
    def frozen(self) -> bool:
        return self.status == "frozen"


@dataclass(frozen=True)
class SetFile:
    """A ``qset/1`` file read: its header, its items in order, its bytes and their md5."""

    header: dict[str, Any]
    items: tuple[dict[str, Any], ...]
    data: bytes
    md5: str


@dataclass(frozen=True)
class BuiltSet:
    """A file written from a header and items: the bytes to store and their md5."""

    header: dict[str, Any]
    items: tuple[dict[str, Any], ...]
    data: bytes
    md5: str


@dataclass(frozen=True)
class ImportShape:
    name: str
    description: str
    suffix: str
    needs_corpus: bool


@dataclass(frozen=True)
class AppliedReviews:
    """What applying the latest decision of each item gives (the rule of ``reviews/1``)."""

    items: tuple[dict[str, Any], ...]
    accepted: tuple[str, ...]
    edited: tuple[str, ...]
    rejected: tuple[str, ...]
    unreviewed: tuple[str, ...]
    stale: tuple[str, ...]
    unknown: tuple[str, ...]


def etag_of(md5: str) -> str:
    """The strong ETag of a set version."""
    return f'"{md5}"'


def if_match_allows(header: str, md5: str) -> bool:
    """Whether an ``If-Match`` header names the current version (or is ``*``)."""
    for part in header.split(","):
        tag = part.strip()
        if tag == "*":
            return True
        if tag.startswith("W/"):  # a weak tag never matches a write (RFC 9110, section 13.1.1)
            continue
        if tag.strip('"') == md5 and tag.startswith('"') and tag.endswith('"'):
            return True
    return False


def require_editable(actor: Actor, version: SetVersion) -> None:
    """A draft is changed by its owner or an admin; a frozen version by nobody."""
    if version.frozen:
        raise ForbiddenError("A frozen version never changes. Start a new version from it.")
    if actor.username != version.owner and not actor.admin:
        raise ForbiddenError("Only the owner of this draft or an admin can change it.")


def require_draft(version: SetVersion) -> None:
    if version.frozen:
        raise ForbiddenError("This version is frozen: it takes no more changes or reviews.")


# --- reviews ---------------------------------------------------------------------------------


def _decided_at(line: Mapping[str, Any]) -> datetime:
    return datetime.fromisoformat(line["date"])


def _latest(lines: Sequence[tuple[int, Mapping[str, Any]]]) -> Mapping[str, Any] | None:
    """The line with the latest date; on equal dates the one written last."""
    best: tuple[datetime, int, Mapping[str, Any]] | None = None
    for position, line in lines:
        key = (_decided_at(line), position)
        if best is None or key >= best[:2]:
            best = (key[0], key[1], line)
    return None if best is None else best[2]


@dataclass(frozen=True)
class ItemReview:
    """What the human decisions say about one item."""

    state: ReviewState
    deciding: Mapping[str, Any] | None
    accepted_by: tuple[str, ...]

    @property
    def gold(self) -> bool:
        return self.state == "accepted"


def review_states(
    item_ids: Iterable[str], lines: Iterable[Mapping[str, Any]]
) -> dict[str, ItemReview]:
    """The review state of each item, from ``reviews/1`` lines.

    Only ``role: human`` lines about an item count. The deciding line of an item is the latest
    one (``reviews/1``). An item is ``accepted`` when its deciding verdict is ``accept`` and at
    least two different people have accept as their own latest verdict on it; with only one
    person it is ``needs_second``. A person who changes their mind counts by their last word.
    """
    wanted = set(item_ids)
    by_item: dict[str, list[tuple[int, Mapping[str, Any]]]] = {}
    for position, line in enumerate(lines):
        if line.get("target") == "item" and line.get("role") == "human":
            item_id = line.get("item_id")
            if item_id in wanted:
                by_item.setdefault(item_id, []).append((position, line))
    states: dict[str, ItemReview] = {}
    for item_id in wanted:
        entries = by_item.get(item_id, [])
        deciding = _latest(entries)
        if deciding is None:
            states[item_id] = ItemReview("none", None, ())
            continue
        by_person: dict[str, list[tuple[int, Mapping[str, Any]]]] = {}
        for entry in entries:
            by_person.setdefault(entry[1]["reviewer"], []).append(entry)
        accepted_by = tuple(
            sorted(
                person
                for person, theirs in by_person.items()
                if (last := _latest(theirs)) is not None and last["verdict"] == "accept"
            )
        )
        verdict = deciding["verdict"]
        state: ReviewState
        if verdict == "reject":
            state = "rejected"
        elif verdict == "edit":
            state = "edited"
        elif len(accepted_by) >= ACCEPTS_FOR_GOLD:
            state = "accepted"
        else:
            state = "needs_second"
        states[item_id] = ItemReview(state, deciding, accepted_by)
    return states


@dataclass(frozen=True)
class ReviewProgress:
    items: int
    none: int
    needs_second: int
    accepted: int
    edited: int
    rejected: int


def review_progress(states: Mapping[str, ItemReview]) -> ReviewProgress:
    counts = dict.fromkeys(REVIEW_STATES, 0)
    for review in states.values():
        counts[review.state] += 1
    return ReviewProgress(
        items=len(states),
        none=counts["none"],
        needs_second=counts["needs_second"],
        accepted=counts["accepted"],
        edited=counts["edited"],
        rejected=counts["rejected"],
    )


def apply_edit(item: Mapping[str, Any], edit: Mapping[str, Any]) -> dict[str, Any]:
    """The item after an ``edit`` decision: the named fields replaced."""
    changed = dict(item)
    changed.update(edit)
    return changed


# --- listing items ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemFilter:
    """Which items a listing keeps. Every given field must match."""

    form: str | None = None
    content: str | None = None
    lang: str | None = None
    split: str | None = None
    tag: str | None = None
    """``key=value``, or ``key`` for any value."""
    review: ReviewState | None = None
    q: str | None = None
    """Text found (ignoring case) in the id, the question, the answer or the notes."""


def _has_tag(item: Mapping[str, Any], tag: str) -> bool:
    tags = item.get("tags") or {}
    key, equals, value = tag.partition("=")
    if key not in tags:
        return False
    return not equals or tags[key] == value


def item_matches(item: Mapping[str, Any], review: ItemReview, flt: ItemFilter) -> bool:
    for field in ("form", "content", "lang", "split"):
        wanted = getattr(flt, field)
        if wanted is not None and item.get(field) != wanted:
            return False
    if flt.tag is not None and not _has_tag(item, flt.tag):
        return False
    if flt.review is not None and review.state != flt.review:
        return False
    if flt.q:
        needle = flt.q.casefold()
        haystack = " ".join(str(item.get(k, "")) for k in ("id", "question", "answer", "notes"))
        if needle not in haystack.casefold():
            return False
    return True


@dataclass(frozen=True)
class ItemSummary:
    id: str
    lang: str
    form: str
    content: str | None
    corpus: str
    split: str | None
    question: str
    tags: dict[str, str]
    mirror_of: str | None
    review: ReviewState
    accepted_by: tuple[str, ...]


def summarize(item: Mapping[str, Any], review: ItemReview) -> ItemSummary:
    question = str(item.get("question", ""))
    if len(question) > QUESTION_PREVIEW:
        question = question[: QUESTION_PREVIEW - 3] + "..."
    return ItemSummary(
        id=item["id"],
        lang=item.get("lang", ""),
        form=item.get("form", ""),
        content=item.get("content"),
        corpus=item.get("corpus", ""),
        split=item.get("split"),
        question=question,
        tags=dict(item.get("tags") or {}),
        mirror_of=item.get("mirror_of"),
        review=review.state,
        accepted_by=review.accepted_by,
    )


def dominant_corpus(items: Sequence[Mapping[str, Any]]) -> str | None:
    """The corpus most items name (the first one on a tie), ignoring ``none``."""
    counts: dict[str, int] = {}
    for item in items:
        corpus = item.get("corpus")
        if corpus and corpus != "none":
            counts[corpus] = counts.get(corpus, 0) + 1
    return max(counts, key=lambda c: counts[c]) if counts else None


@dataclass(frozen=True)
class FreezePlan:
    """What freezing a draft would do, in the words of the reviews."""

    items_before: int
    items_after: int
    accepted: tuple[str, ...]
    edited: tuple[str, ...]
    rejected: tuple[str, ...]
    unreviewed: tuple[str, ...]
    needs_second: tuple[str, ...]
    stale: tuple[str, ...]
    unknown: tuple[str, ...]
