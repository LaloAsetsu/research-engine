"""Page furniture of a text layer: running headers, running footers and page numbers.

A running line is recognized by coming back: the same text (or nearly: OCR noise, a changing
page number) near the top or bottom of several pages. A bare page number is recognized by
its form and by standing alone on the first or last row of the page. Pure functions.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import pairwise

from .layout import GAP, PageLayout, line_pitch, size_bucket
from .text import HYPHEN_MARK

ZONE = 0.15  # share of the page height, at the top and at the bottom, where furniture lives
SIMILAR = 0.85  # similarity of two running lines read as the same line
MIN_PAGES = 3  # pages a running line must appear on (2 for documents of up to SHORT_DOCUMENT)
SHORT_DOCUMENT = 5
DENSITY = 0.3  # share of the pages between its first and last one that a running line is on
TEMPLATE_SHARE = 0.3  # share of the pages whose outermost line must start or end alike
SAME_SIZE = 0.12  # relative size difference that still counts as the same type size

Cell = tuple[int, int]  # (page, line index)

_DIGITS = re.compile(r"\d+")
_PLAIN_NUMBER = re.compile(r"\d{1,4}")
_WORD = re.compile(r"[^\W\d_]+")
_NOT_A_HEADER_WORD = frozenset({"figure", "table", "algorithm", "listing", "scheme"})


def _roman(n: int) -> str:
    out = ""
    for value, symbol in (
        (100, "c"),
        (90, "xc"),
        (50, "l"),
        (40, "xl"),
        (10, "x"),
        (9, "ix"),
        (5, "v"),
        (4, "iv"),
        (1, "i"),
    ):
        while n >= value:
            out, n = out + symbol, n - value
    return out


_ROMAN = {_roman(n) for n in range(1, 100)}


def in_zone(baseline: float, layout: PageLayout) -> bool:
    h = layout.height
    return h > 0 and (baseline <= ZONE * h or baseline >= (1 - ZONE) * h)


def _key(text: str) -> str:
    """The text of a possible running line with the page number taken out of it."""
    t = _DIGITS.sub("#", text.replace(HYPHEN_MARK, "").lower())
    return re.sub(r"[^\w#]|_", "", t).strip("#")


def is_page_number(text: str) -> bool:
    t = text.strip().strip("-\u2013\u2014\u2022\u00b7[]() \t").strip()
    t = re.sub(r"^(?:page|pp?\.?)\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*(?:of|/)\s*\d{1,4}$", "", t, flags=re.IGNORECASE)
    return bool(_PLAIN_NUMBER.fullmatch(t)) or t.lower() in _ROMAN


class _Clusters:
    """Keys grouped by similarity; candidates share their start, middle or end, not all pairs."""

    LOOKED_AT = 30  # the most popular clusters sharing an anchor are compared, not all of them

    def __init__(self) -> None:
        self._reps: list[str] = []
        self._size: list[int] = []
        self._by_anchor: dict[str, list[int]] = {}

    @staticmethod
    def _anchors(key: str) -> list[str]:
        mid = len(key) // 2
        return [f"s{key[:5]}", f"m{key[mid - 2 : mid + 3]}", f"e{key[-5:]}"]

    def find(self, key: str) -> int:
        """The cluster ``key`` belongs to (a new one when no cluster is similar)."""
        anchors = self._anchors(key)
        known = dict.fromkeys(c for a in anchors for c in self._by_anchor.get(a, ()))
        for cid in sorted(known, key=lambda c: -self._size[c])[: self.LOOKED_AT]:
            rep = self._reps[cid]
            if abs(len(rep) - len(key)) <= 0.2 * len(rep) and (
                SequenceMatcher(None, rep, key, autojunk=False).ratio() >= SIMILAR
            ):
                self._size[cid] += 1
                return cid
        self._reps.append(key)
        self._size.append(1)
        for a in anchors:
            self._by_anchor.setdefault(a, []).append(len(self._reps) - 1)
        return len(self._reps) - 1


@dataclass(frozen=True)
class Margin:
    """The rows (top to bottom for the top margin, bottom to top for the bottom one) that
    can be furniture: from the page edge to the first wide gap, or the outermost row only."""

    rows: tuple[tuple[int, ...], ...]
    separated: bool  # a wide gap sets the rows off from the body text


def _rows(layout: PageLayout) -> list[tuple[float, list[int]]]:
    """The upright lines grouped by baseline, top to bottom."""
    rows: list[tuple[float, list[int]]] = []
    ordered = sorted(
        ((i, ln) for i, ln in enumerate(layout.lines) if ln.upright), key=lambda t: t[1].baseline
    )
    for i, ln in ordered:
        if rows and ln.baseline - rows[-1][0] <= 0.6 * ln.size:
            rows[-1][1].append(i)
        else:
            rows.append((ln.baseline, [i]))
    return rows


def margins(layout: PageLayout) -> list[Margin]:
    """The top and the bottom margin of the page, when their outermost row is in the zone.

    Body lines also fall inside the zone (the first and last few of a page), and a short line
    such as ``where`` or ``Proof.`` that recurs there is not furniture. Only the rows between
    the page edge and the first vertical gap wider than the page's line pitch count; a page
    without such a gap inside the zone offers its outermost row only.
    """
    rows = _rows(layout)
    pitch = line_pitch(layout.lines) or 1.2 * statistics.median(ln.size for ln in layout.lines)
    found = []
    for ordered in (rows, rows[::-1]) if rows else ():
        if not in_zone(ordered[0][0], layout):
            continue
        block, separated = [ordered[0]], False
        for prev, row in pairwise(ordered):
            if abs(row[0] - prev[0]) > GAP * pitch:
                separated = True
                break
            if not in_zone(row[0], layout):
                break
            block.append(row)
        keep = block if separated else block[:1]
        found.append(Margin(tuple(tuple(r[1]) for r in keep), separated))
    return found


def outer_rows(layout: PageLayout) -> list[list[int]]:
    """Indexes of the lines on the first and on the last row of the page, when in the zone."""
    return [list(m.rows[0]) for m in margins(layout)]


def _text_groups(pages: Mapping[int, PageLayout]) -> list[list[Cell]]:
    """Margin lines grouped by their text, page numbers and OCR noise aside."""
    clusters = _Clusters()
    members: dict[int, list[Cell]] = {}
    for page, layout in pages.items():
        for margin in margins(layout):
            for row in margin.rows:
                for i in row:
                    key = _key(layout.lines[i].text)
                    if sum(c.isalpha() for c in key) >= 3:
                        members.setdefault(clusters.find(key), []).append((page, i))
    return list(members.values())


def _template_groups(pages: Mapping[int, PageLayout]) -> list[list[Cell]]:
    """Outermost lines that start or end with the same long word at the same type size.

    Catches a header whose title changes with the section but keeps its first or last word
    ("Section 4.1 Lattices 23" on every odd page of a book).
    """
    words: dict[tuple[str, float, bool], list[Cell]] = {}
    for page, layout in pages.items():
        for row in outer_rows(layout):
            for i in row:
                ln = layout.lines[i]
                found = _WORD.findall(ln.text.lower())
                for w in {found[0], found[-1]} if found else ():
                    if len(w) >= 5 and w not in _NOT_A_HEADER_WORD:
                        words.setdefault((w, size_bucket(ln.size), ln.bold), []).append((page, i))
    need = max(4, TEMPLATE_SHARE * len(pages))
    return [cells for cells in words.values() if len({page for page, _ in cells}) >= need]


def _on_the_usual_row(cells: list[Cell], pages: Mapping[int, PageLayout]) -> list[Cell]:
    """The cells set in the group's usual type size on its usual row.

    A title set larger than the running header that repeats it is not furniture, and a
    header sits on the same row on every page.
    """
    lines = [pages[p].lines[i] for p, i in cells]
    counts = Counter(size_bucket(ln.size) for ln in lines)
    usual = min(size for size, n in counts.items() if n == max(counts.values()))
    same = [
        (cell, ln)
        for cell, ln in zip(cells, lines, strict=True)
        if abs(ln.size - usual) <= SAME_SIZE * usual
    ]
    row = statistics.median(ln.baseline for _, ln in same)
    return [cell for cell, ln in same if abs(ln.baseline - row) <= 0.6 * usual + 2]


def repeated_lines(pages: Mapping[int, PageLayout]) -> dict[int, set[int]]:
    """{page: indexes of its lines that are running headers or footers}.

    A group of lines must come back on at least ``MIN_PAGES`` pages and, in a document of more
    than ``SHORT_DOCUMENT`` pages, on at least ``DENSITY`` of the pages between its first and
    its last: a header is on every page or every other page, while ``Chapter 3`` at the top of
    a chapter opener, or a word that opens the body text of a few pages, is on a few.
    """
    groups = _text_groups(pages)
    short = len(pages) <= SHORT_DOCUMENT
    if not short:
        groups += _template_groups(pages)
    need = 2 if short else MIN_PAGES
    position = {page: n for n, page in enumerate(sorted(pages))}
    found: dict[int, set[int]] = {}
    for group in groups:
        cells = _on_the_usual_row(group, pages)
        on = {page for page, _ in cells}
        if len(on) < need:
            continue
        span = max(position[p] for p in on) - min(position[p] for p in on) + 1
        if not short and len(on) < DENSITY * span:
            continue
        for page, i in cells:
            found.setdefault(page, set()).add(i)
    return found


def page_numbers(layout: PageLayout, repeated: set[int]) -> set[int]:
    """Bare page numbers: alone on the first or last row of the page, or next to running text."""
    found: set[int] = set()
    for margin in margins(layout):
        for row in margin.rows:
            numbers = {i for i in row if is_page_number(layout.lines[i].text)}
            if len(numbers) != 1 or not all(i in numbers or i in repeated for i in row):
                continue
            (number,) = numbers
            # A Roman numeral is also a word or a math symbol (``x``, ``i``): only when set off.
            if margin.separated or _PLAIN_NUMBER.fullmatch(layout.lines[number].text.strip()):
                found |= numbers
    return found


__all__ = ["page_numbers", "repeated_lines"]
