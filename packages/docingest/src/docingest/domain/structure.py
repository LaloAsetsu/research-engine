"""Markdown for the pages of a PDF text layer: furniture out, paragraphs joined, headings marked.

``structure_pages`` looks at all the text-layer pages of a document at once, because a running
header is only recognizable as a line that comes back on other pages. Everything is
conservative: when a rule is not sure, the text stays as the text layer wrote it (a line
break instead of a joined paragraph, plain text instead of a heading).
Pure functions.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from . import furniture, headings
from .layout import (
    GAP,
    PageLayout,
    StructuredPage,
    TextLine,
    line_pitch,
    n_chars,
    prose_share,
    size_bucket,
)
from .text import HYPHEN_MARK, clean_text_layer

JOIN, LINE, PARAGRAPH = " ", "\n", "\n\n"  # what goes between two lines of a page

SAME_ROW = 0.6  # baseline distance, in font sizes, within which lines are pieces of one line
SHORT = 0.25  # a line this much shorter than its column is kept as a line
SHORT_END = 0.10  # ... or ends the paragraph when it also ends a sentence
INDENT = 0.7  # a first-line indent, in font sizes
PROSE = 0.65  # share of letters below which lines (tables, formulas) are not joined
SMALL_PRINT = 0.9  # footnotes are set this much smaller than the body, or less
FOOT = 0.7  # ... in the lower part of the page

_FOOTNOTE_MARKER = re.compile(r"^(?:[*†‡§¶✩☆]|\d{1,2}(?=\s)|[⁰¹²³⁴⁵⁶⁷⁸⁹]+|\(\d{1,2}\))")
_ENDS_SENTENCE = re.compile(r"[.!?:;][\"'”’)\]]*$")


class _Geometry:
    """Columns and line pitch of one page, to tell a paragraph break from a line break."""

    def __init__(self, lines: Sequence[TextLine], body: float):
        upright = [ln for ln in lines if ln.upright]
        self.pitch = line_pitch(upright) or 1.2 * body
        self.lefts = self._lefts(upright, body)
        self.rights = self._rights(upright)

    @staticmethod
    def _lefts(lines: Sequence[TextLine], body: float) -> list[float]:
        if not lines:
            return [0.0]
        counts = Counter(round(ln.x0 / 4) for ln in lines)
        floor = max(3.0, 0.12 * len(lines))
        lefts: list[float] = []
        for x in sorted(b * 4.0 for b, c in counts.items() if c >= floor):
            if not lefts or x - lefts[-1] > 3 * body:  # an indent is not another column
                lefts.append(x)
        return lefts or [min(ln.x0 for ln in lines)]

    def left_of(self, ln: TextLine) -> float:
        fits = [x for x in self.lefts if x <= ln.x0 + 0.5 * ln.size]
        return max(fits) if fits else self.lefts[0]

    def _rights(self, lines: Sequence[TextLine]) -> dict[float, float]:
        every: dict[float, list[float]] = {x: [] for x in self.lefts}
        long_ones: dict[float, list[float]] = {x: [] for x in self.lefts}
        for ln in lines:
            column = self.left_of(ln)
            every[column].append(ln.x1)
            if n_chars(ln.text) >= 25:
                long_ones[column].append(ln.x1)
        out = {}
        for column, xs in every.items():
            use = sorted(long_ones[column] or xs)
            out[column] = use[int(0.9 * (len(use) - 1))] if use else column
        return out

    def right_of(self, ln: TextLine) -> float:
        return self.rights[self.left_of(ln)]


def _starts_paragraph(p: TextLine, ln: TextLine, d: float, geo: _Geometry) -> bool:
    """A gap, a jump up into the next column, another type size, or a list item."""
    wide_gap = d < 0 or d > GAP * geo.pitch
    return wide_gap or abs(ln.size - p.size) > 0.12 * p.size or headings.is_marker(ln.text)


def _ends_paragraph(p: TextLine, ln: TextLine, first: TextLine, geo: _Geometry) -> bool:
    """The previous line ends a sentence short of the margin, or the next one starts an indent."""
    left, right = geo.left_of(p), geo.right_of(p)
    if p.x1 < right - SHORT_END * (right - left) and _ENDS_SENTENCE.search(p.text.rstrip()):
        return True
    indented = ln.x0 - geo.left_of(ln) > INDENT * ln.size
    # a hanging indent (the lines of a list item) is not a new paragraph
    return indented and p.x0 - left <= INDENT * p.size and not headings.is_marker(first.text)


def _keeps_lines(p: TextLine, ln: TextLine, geo: _Geometry) -> bool:
    """Tables, formulas, numbers and short lines (a title, an address) keep their line breaks."""
    if prose_share(p.text) < PROSE:
        return True
    if prose_share(ln.text) < PROSE and n_chars(ln.text) > 20:
        return True
    left, right = geo.left_of(p), geo.right_of(p)
    return p.x1 < right - SHORT * (right - left)


def _separator(p: TextLine, ln: TextLine, first: TextLine, geo: _Geometry) -> str:
    """What goes between ``p`` and the next line ``ln``: a space (same paragraph), a line
    break (same block, kept as it is) or a blank line (a new block)."""
    if not (p.upright and ln.upright):
        return PARAGRAPH
    if p.text.endswith(HYPHEN_MARK):
        return JOIN  # the word goes on in the next line of the stream, even in the next column
    d = ln.baseline - p.baseline
    if abs(d) < SAME_ROW * max(p.size, ln.size):
        return LINE  # same row, or a sub/superscript: pieces of one line
    if _starts_paragraph(p, ln, d, geo):
        return PARAGRAPH
    if prose_share(p.text) >= PROSE and _ends_paragraph(p, ln, first, geo):
        return PARAGRAPH
    return LINE if _keeps_lines(p, ln, geo) else JOIN


@dataclass
class _Block:
    lines: list[TextLine]
    heading: headings.Style | None = None
    footnote: bool = False
    seps: list[str] = field(default_factory=list)  # seps[i] sits between lines[i] and lines[i + 1]

    def add(self, ln: TextLine, sep: str) -> None:
        self.lines.append(ln)
        self.seps.append(sep)


def _blocks(
    lines: Sequence[TextLine],
    dropped: set[int],
    styles: Sequence[headings.Style | None],
    geo: _Geometry,
) -> list[_Block]:
    blocks: list[_Block] = []
    prev: TextLine | None = None
    for i, ln in enumerate(lines):
        if i in dropped:
            continue
        cur, style = (blocks[-1] if blocks else None), styles[i]
        sep = PARAGRAPH
        if cur is not None and prev is not None:
            if style is not None:  # the next line of a heading that wraps
                wraps = cur.heading == style and len(cur.lines) < headings.MAX_LINES
                if wraps and headings.adjacent(prev, ln):
                    sep = JOIN
            elif cur.heading is None:
                sep = _separator(prev, ln, cur.lines[0], geo)
        if sep == PARAGRAPH or cur is None:
            blocks.append(_Block([ln], heading=style))
        else:
            cur.add(ln, sep)
        prev = ln
    return blocks


def _small_print(block: _Block, layout: PageLayout, body: float) -> bool:
    first = block.lines[0]
    return (
        block.heading is None
        and first.upright
        and first.size <= SMALL_PRINT * body
        and layout.height > 0
        and first.baseline >= FOOT * layout.height
    )


def _starts_footnote(block: _Block) -> bool:
    text = block.lines[0].text.strip()
    return (
        bool(_FOOTNOTE_MARKER.match(text))
        and not headings.is_caption(text)
        and prose_share(text) >= 0.6
    )


def _footnotes_last(blocks: list[_Block], layout: PageLayout, body: float) -> list[_Block]:
    """Move the footnotes behind the body text that follows them in the stream.

    A footnote starts with a mark (``*``, ``†``, a number) in small print low on the page; the
    small print blocks right after it are its other paragraphs or its continuation.
    """
    in_note = False
    for b in blocks:
        b.footnote = in_note = _small_print(b, layout, body) and (_starts_footnote(b) or in_note)
    first = next((i for i, b in enumerate(blocks) if b.footnote), len(blocks))
    if all(b.footnote for b in blocks[first:]):
        return blocks  # the footnotes are already behind the body text
    return [b for b in blocks if not b.footnote] + [b for b in blocks if b.footnote]


def _is_cjk(c: str) -> bool:
    """Chinese, Japanese and Korean text is written without spaces between its lines."""
    return bool(c) and ("\u2e80" <= c <= "\u9fff" or "\uac00" <= c <= "\ud7af")


def _join(block: _Block) -> str:
    """The block as text; a hyphen at a line end becomes the marker the cleaner resolves."""
    out = block.lines[0].text.strip()
    for ln, sep in zip(block.lines[1:], block.seps, strict=True):
        nxt = ln.text.strip()
        if sep == LINE:
            out += LINE + nxt
        elif out.endswith(HYPHEN_MARK):
            out += nxt
        elif re.search(r"[^\W\d_]-$", out) and nxt[:1].islower():
            out = out[:-1] + HYPHEN_MARK + nxt  # may be hyphenation: the vocabulary decides
        elif re.search(r"\w-$", out) and nxt[:1].isalnum():
            out += nxt  # 2019-/2020, Qwen-2.5-/VL: an identifier keeps its hyphen
        else:
            out += ("" if _is_cjk(out[-1:]) and _is_cjk(nxt[:1]) else JOIN) + nxt
    return out


def _body_size(layouts: Iterable[PageLayout]) -> float:
    """The type size most characters are set in, across the whole document."""
    weight: Counter[float] = Counter()
    for layout in layouts:
        for ln in layout.lines:
            if ln.upright:
                weight[size_bucket(ln.size)] += n_chars(ln.text)
    return weight.most_common(1)[0][0] if weight else 0.0


def structure_pages(
    layouts: Mapping[int, PageLayout], vocab: set[str]
) -> dict[int, StructuredPage]:
    """Markdown for every page that has a layout; ``vocab`` decides which hyphens go.

    Pages without lines are left out of the result: the caller keeps their flat text.
    """
    pages = {k: lay for k, lay in layouts.items() if lay.lines}
    body = _body_size(pages.values())
    if body <= 0:
        return {}
    repeated = furniture.repeated_lines(pages)

    out: dict[int, StructuredPage] = {}
    for k, lay in pages.items():
        rep = repeated.get(k, set())
        dropped = rep | furniture.page_numbers(lay, rep)
        styles = headings.page_styles(lay.lines, dropped, body)
        geo = _Geometry([ln for i, ln in enumerate(lay.lines) if i not in dropped], body)
        parts = []
        for b in _footnotes_last(_blocks(lay.lines, dropped, styles, geo), lay, body):
            text = _join(b)
            if b.heading:
                text = f"{'#' * headings.level(text, b.lines[0].size / body)} {text}"
            parts.append(text)
        removed = tuple(
            " ".join(lay.lines[i].text.replace(HYPHEN_MARK, "-").split()) for i in sorted(dropped)
        )
        out[k] = StructuredPage(clean_text_layer("\n\n".join(parts), vocab), removed)
    return out
