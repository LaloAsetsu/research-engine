"""Apply ``reviews/1`` decisions to a question set, giving the items of a new set version.

The rule is the one of the format: for each item the line with the latest ``date`` holds, and on
equal dates the later line in the file. ``accept`` sets ``reviewer``, ``edit`` replaces the named
fields and sets ``reviewer``, ``reject`` drops the item. Only lines of the roles asked for count
(``human`` by default: an independent agent's check never makes an item gold nor drops it).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from qa_bench.formats import QSET, REVIEWS, FormatError, FormatFile, build, check_line
from qa_bench.formats.problems import shorten

HUMAN = ("human",)


class ReviewError(ValueError):
    """A decision that cannot be applied, for example an edit that makes the item invalid."""


@dataclass(frozen=True)
class Applied:
    """The result of applying the decisions: the items that stay, and what happened to each id."""

    items: tuple[dict[str, Any], ...]
    accepted: tuple[str, ...]
    edited: tuple[str, ...]
    rejected: tuple[str, ...]
    unreviewed: tuple[str, ...]
    stale: tuple[str, ...]
    unknown: tuple[str, ...]


def latest_decisions(
    lines: Iterable[Mapping[str, Any]], roles: Collection[str] = HUMAN
) -> dict[str, Mapping[str, Any]]:
    """The deciding line of each item: the item decisions of ``roles`` with the latest date.

    ``roles`` is a collection of role names; a bare string is refused (``"human"`` would match
    any role that is a part of it).
    """
    if isinstance(roles, str):
        raise TypeError("roles is a collection of role names, not one string")
    wanted = frozenset(roles)
    best: dict[str, tuple[datetime, int, Mapping[str, Any]]] = {}
    for position, line in enumerate(lines):
        if line.get("target") != "item" or line.get("role") not in wanted:
            continue
        stamp = datetime.fromisoformat(line["date"])
        held = best.get(line["item_id"])
        if held is None or (stamp, position) >= (held[0], held[1]):
            best[line["item_id"]] = (stamp, position, line)
    return {item_id: entry[2] for item_id, entry in best.items()}


def apply_reviews(
    items: Sequence[Mapping[str, Any]],
    lines: Iterable[Mapping[str, Any]],
    *,
    set_md5: str | None = None,
    roles: Collection[str] = HUMAN,
) -> Applied:
    """The items after the latest decision of each: accepted and edited stay, rejected go.

    ``set_md5`` is the md5 of the set file the items come from; a deciding line that names
    another md5 is listed in ``stale`` (it is still applied: the caller decides what to do).
    Decisions about ids that are not in ``items`` are listed in ``unknown``. Raises
    ``ReviewError`` when an edit names no fields or leaves an item that ``qset/1`` refuses.
    """
    decisions = latest_decisions(lines, roles)
    known = {item["id"] for item in items}
    kept: list[dict[str, Any]] = []
    accepted: list[str] = []
    edited: list[str] = []
    rejected: list[str] = []
    unreviewed: list[str] = []
    stale: list[str] = []
    for item in items:
        item_id = item["id"]
        line = decisions.get(item_id)
        if line is None:
            unreviewed.append(item_id)
            kept.append(dict(item))
            continue
        if set_md5 is not None and line.get("set_md5") not in (None, set_md5):
            stale.append(item_id)
        verdict = line["verdict"]
        if verdict == "reject":
            rejected.append(item_id)
            continue
        new = dict(item)
        if verdict == "edit":
            if not line.get("edit"):
                raise ReviewError(f"the edit of {item_id} names no fields to change")
            new.update(line["edit"])
            edited.append(item_id)
        else:
            accepted.append(item_id)
        new["reviewer"] = line["reviewer"]
        problems = check_line(QSET, new, header=False)
        if problems:
            raise ReviewError(
                f"the {verdict} of {item_id} leaves an invalid item: "
                + "; ".join(f"{p.path or '/'}: {shorten(p.message, 100)}" for p in problems[:3])
            )
        kept.append(new)
    unknown = sorted(set(decisions) - known)
    return Applied(
        tuple(kept),
        tuple(accepted),
        tuple(edited),
        tuple(rejected),
        tuple(unreviewed),
        tuple(stale),
        tuple(unknown),
    )


def apply_to_set(
    qset: FormatFile,
    reviews: FormatFile,
    *,
    created: str,
    set_name: str | None = None,
    roles: Collection[str] = HUMAN,
) -> tuple[FormatFile, Applied]:
    """A new ``qset/1`` file: the set with the reviews applied. Its md5 is the new version's.

    The header keeps the name unless ``set_name`` gives the new version's, and its notes say which
    set (by md5) the file came from and the counts of the decisions, among them those made on
    another version of the set (``stale``). ``created`` is the time of the new version, from the
    caller's clock.
    """
    if qset.format != QSET or reviews.format != REVIEWS:
        raise ValueError(f"expected a {QSET} file and a {REVIEWS} file")
    if reviews.header["set"] != qset.header["set"]:
        raise ReviewError(
            f"the reviews are for set {reviews.header['set']!r}, not {qset.header['set']!r}"
        )
    applied = apply_reviews(qset.records, reviews.records, set_md5=qset.md5, roles=roles)
    header: dict[str, Any] = {
        "format": QSET,
        "set": set_name or qset.header["set"],
        "created": created,
        "notes": (
            f"{qset.header['set']} (md5 {qset.md5}) with the reviews of the {'/'.join(roles)} "
            f"role applied: {len(applied.accepted)} accepted, {len(applied.edited)} edited, "
            f"{len(applied.rejected)} rejected, {len(applied.unreviewed)} not reviewed; "
            f"{len(applied.stale)} decided on another version of the set."
        ),
    }
    try:
        built = build(QSET, header, applied.items)
    except FormatError as error:
        raise ReviewError(f"the reviewed set is invalid: {error}") from None
    return built, applied
