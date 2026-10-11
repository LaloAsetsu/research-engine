"""What a PDF adapter reports about the lines of a text-layer page, and the helpers on it.

A page is a list of :class:`TextLine` (text, font size, weight, position) in reading order.
The structure of a whole document is derived from these by ``structure.structure_pages``:
running headers (``furniture``), headings (``headings``) and paragraphs.
Pure domain: no I/O.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import pairwise


@dataclass(frozen=True, slots=True)
class TextLine:
    """One visual line of a page. Coordinates in points; ``baseline`` counts from the top."""

    text: str
    size: float  # font size of most characters
    bold: bool  # most characters are bold
    x0: float
    x1: float
    baseline: float
    upright: bool = True  # False for rotated text (margin stamps, vertical labels)


@dataclass(frozen=True)
class PageLayout:
    """The lines of a page in reading order; no lines means the adapter has no layout."""

    lines: Sequence[TextLine] = ()
    width: float = 0.0
    height: float = 0.0


@dataclass(frozen=True)
class StructuredPage:
    text: str  # Markdown: paragraphs, headings marked with ``#``
    removed: tuple[str, ...] = field(default=())  # running headers, footers and page numbers


GAP = 1.4  # a vertical gap this many line pitches wide separates blocks


def line_pitch(lines: Sequence[TextLine]) -> float | None:
    """The usual distance between two consecutive baselines, or None when no pair tells."""
    upright = [ln for ln in lines if ln.upright]
    gaps = [
        b.baseline - a.baseline
        for a, b in pairwise(upright)
        if 0.5 * b.size < b.baseline - a.baseline < 2.5 * b.size
        and abs(a.size - b.size) <= 0.12 * a.size
    ]
    return statistics.median(gaps) if gaps else None


def size_bucket(size: float) -> float:
    """Font sizes jitter by a few tenths of a point: compare them in half-point steps."""
    return round(size * 2) / 2


def n_chars(text: str) -> int:
    return sum(not c.isspace() for c in text)


def prose_share(text: str) -> float:
    """Share of letters among the non-space characters (numbers and symbols read as 0)."""
    n = n_chars(text)
    return sum(c.isalpha() for c in text) / n if n else 0.0
