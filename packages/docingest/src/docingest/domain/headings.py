"""Headings of a text layer, from font size and weight relative to the body text.

A heading is a line set larger than the body text, or bold and a little larger. A line of
about the body size needs a second sign: a section number or a standard section title when
bold, a section number or capital letters when regular. Pure functions.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence

from .layout import TextLine, n_chars, prose_share, size_bucket

BIG = 1.2  # a line this much larger than the body text is a heading
BOLD = 1.05  # ... or this much larger and bold
BOLD_BODY = 0.85  # a bold or numbered line of about the body size can be one too
TITLE = 1.5  # this much larger: the title (``#``)
MAX_CHARS = 160
MAX_BODY_CHARS = 100
MAX_LINES = 3  # a heading wraps over at most this many lines; a longer run is a paragraph
MAX_LEVEL = 4
DOMINANT = 0.4  # a style on more of the page than this is the page's body text

Style = tuple[float, bool]  # (size bucket, bold)

_MARKER = re.compile(
    r"^(?:[•◦▪▫‣●■□·\u2013\u2014*†‡§¶✩☆-]\s|\(?\d{1,3}[.)]\s|\(?[a-z]\)\s|\[\d{1,3}\])"
)
_NUMBERED = re.compile(r"^(?:\d+(?:\.\d+)*\.?|[IVXLC]+\.|[A-Z]\.)\s+\S")
# regular-weight section numbers: II. / 2.2. / 7.6.4 / A. Title (not "1. item" or "A. Einstein")
_SECTION_NUMBER = re.compile(r"^(?:[IVX]+\.\s+\S|\d+(?:\.\d+)+\.?\s+\S|[A-Z]\.\s+\S+(?:\s+\S+){2})")
_SINGLE_NUMBER = re.compile(r"^(?:\d{1,2}|§\d+)\.?\s+[A-Z]")
_LEADER = re.compile(r"(?:\.\s?){3,}")  # dot leaders of a table of contents
_CONTENTS_LINE = re.compile(r"^(?:\d+(?:\.\d+)*|[IVX]+)\.?\s.*\s\d{1,4}$")
_NUMBER_PREFIX = re.compile(r"^(?:\d+(?:\.\d+)*|[IVXLC]+|§\d+)\.?\s+")
_LEADING_NUMBER = re.compile(r"^(?P<n>\d+(?:\.\d+)*)\.?\s")
_CAPTION = re.compile(r"^(?:fig(?:ure)?|table|tab|scheme|chart|algorithm|listing)\b", re.IGNORECASE)
# a running head with its page number: ``64 3. Chapter title``, ``Chapter title 65``
_RUNNING_HEAD = re.compile(r"^\d{1,4}\s+\S+(?:\s+\S+)+$|^\S+(?:\s+\S+)+\s+\d{1,4}$")
# an author line: ``Firstname X. Lastname``
_INITIALS = re.compile(r"\b[A-Z][a-z]+\s[A-Z]\.\s[A-Z][a-z]")
_NOT_AN_AUTHOR = frozenset({"appendix", "section", "chapter", "part"})
MAX_BODY_WORDS = 12
RUNNING_HEAD_SIZE = 1.1  # a running head is not larger than this times the body text
_SECTION_TITLES = frozenset(
    [
        "abstract",
        "summary",
        "introduction",
        "background",
        "conclusion",
        "conclusions",
        "discussion",
        "results",
        "methods",
        "references",
        "bibliography",
        "acknowledgments",
        "acknowledgements",
        "appendix",
        "contents",
        "preface",
        "foreword",
        "overview",
    ]
)


def is_marker(text: str) -> bool:
    """True when the text starts like a list item or a reference entry."""
    return bool(_MARKER.match(text))


def is_caption(text: str) -> bool:
    return bool(_CAPTION.match(text))


def _is_caps(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 4 and sum(c.isupper() for c in letters) >= 0.9 * len(letters)


def _is_title(text: str) -> bool:
    """Words of four letters or more start with a capital: "8. Conclusions", not a list item."""
    words = [w for w in text.split() if sum(c.isalpha() for c in w) >= 4]
    return 1 <= len(words) <= 8 and all(w[0].isupper() or not w[0].isalpha() for w in words)


def _plausible(ln: TextLine, text: str) -> bool:
    """Text that could be a heading at all: words, no formula, no list item, no contents entry."""
    words = [w for w in text.split() if sum(c.isalpha() for c in w) >= 4]
    if not ln.upright or not words or len(text) > MAX_CHARS or "=" in text:
        return False
    if is_caption(text) or prose_share(text) < 0.5:
        return False
    if is_marker(text) and not _NUMBERED.match(text):
        return False
    return not (_LEADER.search(text) or _CONTENTS_LINE.match(text))


def _body_size_heading(ln: TextLine, text: str) -> bool:
    too_long = len(text) > MAX_BODY_CHARS or len(text.split()) > MAX_BODY_WORDS
    if too_long or text.endswith((",", ";", ":")):
        return False
    known = text.rstrip(".:").lower() in _SECTION_TITLES
    if ln.bold:
        return bool(_NUMBERED.match(text)) or known
    if prose_share(_NUMBER_PREFIX.sub("", text)) < 0.75:  # formulas
        return False
    if known or text.lower().startswith("appendix "):
        return True
    if text.endswith("."):
        return False
    return (
        bool(_SECTION_NUMBER.match(text))
        or _is_caps(text)
        or (bool(_SINGLE_NUMBER.match(text)) and _is_title(text))
    )


def line_style(ln: TextLine, body: float) -> Style | None:
    """(size bucket, bold) when the line is set like a heading, else None."""
    text = ln.text.strip()
    if not _plausible(ln, text):
        return None
    if _INITIALS.search(text) and text.split()[0].lower() not in _NOT_AN_AUTHOR:
        return None
    ratio = ln.size / body
    style = (size_bucket(ln.size), ln.bold)
    if ratio >= BIG or (ratio >= BOLD and ln.bold and "," not in text):
        return style
    return style if ratio >= BOLD_BODY and _body_size_heading(ln, text) else None


def adjacent(a: TextLine, b: TextLine) -> bool:
    """True when ``b`` is the line right under ``a`` (the next line of a wrapped heading)."""
    return 0.3 * b.size < b.baseline - a.baseline <= 1.6 * b.size


def _outermost(lines: Sequence[TextLine], dropped: set[int]) -> list[int]:
    """The lines on the first and on the last row of what is left of the page."""
    kept = [(i, ln) for i, ln in enumerate(lines) if i not in dropped and ln.upright]
    if not kept:
        return []
    edges = (min(ln.baseline for _, ln in kept), max(ln.baseline for _, ln in kept))
    return [i for i, ln in kept if any(abs(ln.baseline - e) <= 0.6 * ln.size for e in edges)]


def page_styles(lines: Sequence[TextLine], dropped: set[int], body: float) -> list[Style | None]:
    """Heading style of each line, or None; a style that dominates the page is body text."""
    styles = [None if i in dropped else line_style(ln, body) for i, ln in enumerate(lines)]
    for i in _outermost(lines, dropped):  # a running head with its page number
        ln = lines[i]
        # set like the body text: a section heading at the top of a page is bold or larger
        if (
            not ln.bold
            and ln.size <= RUNNING_HEAD_SIZE * body
            and _RUNNING_HEAD.match(ln.text.strip())
        ):
            styles[i] = None
    total = sum(n_chars(ln.text) for i, ln in enumerate(lines) if i not in dropped) or 1
    share: Counter[Style] = Counter()
    for ln, st in zip(lines, styles, strict=True):
        if st:
            share[st] += n_chars(ln.text)
    dominant = {st for st, n in share.items() if n / total > DOMINANT}
    styles = [None if st in dominant else st for st in styles]
    i = 0
    while i < len(styles):  # a long run in one style is a bold or large paragraph, not a heading
        j = i + 1
        while j < len(styles) and styles[j] == styles[i] and adjacent(lines[j - 1], lines[j]):
            j += 1
        if styles[i] is not None and j - i > MAX_LINES:
            styles[i:j] = [None] * (j - i)
        i = j
    if sum(st is not None for st in styles) > max(6, 0.4 * len(lines)):
        return [None] * len(lines)
    return styles


def level(text: str, ratio: float) -> int:
    """1 for a title-sized line, 2 for a section, deeper by the depth of its section number."""
    if ratio >= TITLE:
        return 1
    if m := _LEADING_NUMBER.match(text):
        return min(2 + m["n"].count("."), MAX_LEVEL)
    return 3 if re.match(r"[A-Z]\.\s", text) else 2


__all__ = ["adjacent", "is_caption", "is_marker", "level", "line_style", "page_styles"]
