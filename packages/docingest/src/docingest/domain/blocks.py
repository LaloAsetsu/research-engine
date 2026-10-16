"""Blocks of a Markdown text that a chunk can cut: display equations, code blocks and tables.

The chunker (``chunking.py``) cuts a text every ``chunk_chars`` characters, so a chunk may end
or start inside a display equation, a fenced code block (a circuit) or a pipe table, and then
means little alone. The chunks are not changed. At answer time this module finds the blocks of
the paper's text and plans the text that completes the cut ones. Everything is a pure function
of the text and offsets, and the scan is one linear pass:

* ``find_blocks`` lists the blocks: display math (``$$..$$``, ``\\[..\\]``, a raw
  ``equation`` or ``align`` environment), fenced code, pipe tables, and inline math
  (``$..$``). They never overlap; what a block holds is not scanned again (a ``$`` in a code
  block is a dollar sign).
* ``attach_captions`` extends a code block, a table or a display circuit over the caption
  that pandoc writes after it, because a figure and its caption are one thing.
* ``whole_blocks`` is the two together without the inline math: each block with its caption.
* ``complete`` plans, for the chunks of one paper, how far each one is extended to hold the
  blocks it cuts, without repeating text that another chunk holds and within a size bound.

The scan is linear: one left to right pass, every closing delimiter is searched in a
bounded window, and a window that found nothing is not searched again (``_Finder``).
Delimiters that never close, or close farther than ``BLOCK_MAX`` characters away, are plain
text, so one stray ``$$`` cannot swallow a paper.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Sequence
from typing import Literal, NamedTuple

BLOCK_MAX = 50_000  # farthest closing delimiter that still makes a block (the longest
# equation seen in 1,249 arXiv papers has 39,797 characters, the longest table 11,019)
BRACKET_MAX = 5_000  # a `\[..\]` is checked before it is believed, and the check reads all of it
INLINE_MAX = 500  # inline math longer than this is two stray dollar signs (longest seen: 495)

Kind = Literal["display", "code", "table", "inline"]


class Block(NamedTuple):
    start: int
    end: int  # exclusive
    kind: Kind


_ENVIRONMENTS = "equation|align|gather|multline|eqnarray|flalign|alignat|displaymath|dmath"
# The delimiter row of a table uses possessive quantifiers: two adjacent `[ \t]*` would backtrack
# in quadratic time on a row of dashes followed by a long run of spaces.
_OPENER = re.compile(
    r"(?P<fence>^[ ]{0,3}(?P<mark>`{3,}|~{3,})(?P<info>[^\n]*))"
    r"|(?P<rule>^[ ]{0,3}\|?[ \t]*:?-+:?[ \t]*+(?:\|[ \t]*:?-+:?[ \t]*+)*+\|?[ \t]*$)"
    r"|(?P<dollars>\$\$)"
    rf"|(?P<env>\\begin\{{(?P<name>(?:{_ENVIRONMENTS})\*?)\}})"
    r"|(?P<bracket>\\\[)"
    r"|\\."  # an escaped character (\$, \\) opens nothing
    r"|(?P<inline>\$(?=\S))",
    re.MULTILINE,
)
# The end of inline math is the first dollar sign after its start that is not escaped, as in
# TeX and pandoc, and it must have a non-space before it. Pandoc also refuses a digit after it,
# but its writer does not obey that ("$\\to$16c"); it escapes every literal dollar, so a bare
# one is a delimiter. A first dollar that cannot close ("$5 and $6", "$a $$E$$") leaves the
# opener a plain dollar sign: skipping to a later one would pair it with another formula.
_INLINE_END = re.compile(r"(?<!\\)\$")
_MATH = re.compile(r"\\[A-Za-z]|[\^_{]")  # what `\[..\]` holds when it is math and not a remark


class _Finder:
    """Closing delimiters, found without ever scanning the same characters twice.

    A search that finds nothing up to its window's end records it: a later opener whose
    window overlaps the first one starts after it. Windows searched for one delimiter are
    therefore disjoint and all the searches together cost O(len(text)) per delimiter.
    """

    def __init__(self, text: str):
        self.text = text
        self.empty_until: dict[object, int] = {}

    def find(self, closer: str, start: int, limit: int) -> int:
        """Offset of ``closer`` in ``text[start:limit]``, or -1."""
        stop = min(limit, len(self.text))
        begin = max(start, self.empty_until.get(closer, 0))
        found = self.text.find(closer, begin, stop)
        if found < 0:
            self.empty_until[closer] = max(begin, stop - len(closer) + 1)
        return found

    def search(self, key: object, pattern: re.Pattern[str], start: int, limit: int):
        stop = min(limit, len(self.text))
        begin = max(start, self.empty_until.get(key, 0))
        found = pattern.search(self.text, begin, stop)
        if found is None:
            self.empty_until[key] = max(begin, stop)
        return found


def find_blocks(text: str) -> list[Block]:
    """The blocks of ``text``, in order, none inside another.

    ``display``: ``$$..$$``, ``\\[..\\]`` (not the literal brackets that pandoc writes for a
    citation or a cross reference) and a raw ``\\begin{equation}`` style environment.
    ``code``: a fenced block, up to its closing fence. ``table``: a pipe table, from its
    header line to its last row. ``inline``: ``$..$`` on one paragraph, at most
    ``INLINE_MAX`` characters.
    """
    blocks: list[Block] = []
    finder = _Finder(text)
    solid = 0  # end of the last block that is not inline math
    pos = 0
    while (m := _OPENER.search(text, pos)) is not None:
        pos = m.end()  # a delimiter that opens nothing is skipped
        handler = _HANDLERS.get(m.lastgroup or "")
        block = handler(finder, m, solid) if handler else None
        if block is None:
            continue
        while block.kind == "table" and blocks and blocks[-1].start >= block.start:
            blocks.pop()  # inline math in the header line
        blocks.append(block)
        pos = block.end
        if block.kind != "inline":
            solid = block.end
    return blocks


def _dollars(finder: _Finder, m: re.Match[str], solid: int) -> Block | None:
    close = finder.find("$$", m.end(), m.start() + BLOCK_MAX)
    return Block(m.start(), close + 2, "display") if close >= 0 else None


def _environment(finder: _Finder, m: re.Match[str], solid: int) -> Block | None:
    closer = "\\end{" + m["name"] + "}"
    close = finder.find(closer, m.end(), m.start() + BLOCK_MAX)
    return Block(m.start(), close + len(closer), "display") if close >= 0 else None


def _brackets(finder: _Finder, m: re.Match[str], solid: int) -> Block | None:
    close = finder.find("\\]", m.end(), m.start() + BRACKET_MAX)
    if close >= 0 and _is_display_brackets(finder.text, m.start(), m.end(), close):
        return Block(m.start(), close + 2, "display")
    return None


def _inline(finder: _Finder, m: re.Match[str], solid: int) -> Block | None:
    found = finder.search("$", _INLINE_END, m.end(), m.end() + INLINE_MAX)
    if found is None or finder.text[found.start() - 1].isspace():
        return None
    if "\n\n" in finder.text[m.end() : found.start()]:
        return None
    return Block(m.start(), found.end(), "inline")


def _fence(finder: _Finder, m: re.Match[str], solid: int) -> Block | None:
    mark = m["mark"]
    if mark[0] == "`" and "`" in m["info"]:
        return None  # `` ```x``` `` is inline code
    closer = re.compile(rf"^[ ]{{0,3}}{re.escape(mark[0])}{{{len(mark)},}}[ \t]*$", re.MULTILINE)
    found = finder.search((mark[0], len(mark)), closer, m.end(), m.start() + BLOCK_MAX)
    return Block(m.start(), found.end(), "code") if found is not None else None


def _rule(finder: _Finder, m: re.Match[str], solid: int) -> Block | None:
    return _table(finder.text, m.start(), m.end(), solid)


_HANDLERS = {
    "dollars": _dollars,
    "env": _environment,
    "bracket": _brackets,
    "inline": _inline,
    "fence": _fence,
    "rule": _rule,
}


def _is_display_brackets(text: str, start: int, opened: int, close: int) -> bool:
    """``\\[..\\]`` is display math unless it is the escaped bracket pair that pandoc writes
    for a literal ``[..]``: a link text (``[\\[eq:1\\]](#eq:1)``), a citation (``\\[12\\]``) or
    a remark (``\\[95% CI $0.1$\\]``, ``\\[Fig. [6](#f)\\]``). Those hold no math, or hold
    math in dollars, a link, or a paragraph break, which display math cannot."""
    if start > 0 and text[start - 1] == "[":
        return False
    if text.startswith("](", close + 2):
        return False
    inner = text[opened:close]
    if "$" in inner or "](" in inner or "\n\n" in inner:
        return False
    return inner[:1].isspace() or _MATH.search(inner) is not None


def _table(text: str, rule_start: int, rule_end: int, floor: int) -> Block | None:
    """The pipe table whose delimiter row is ``text[rule_start:rule_end]``, if it is one:
    it needs a pipe in the delimiter row and a header line above it. ``floor`` is the end of
    the previous block: the header cannot begin before it."""
    if "|" not in text[rule_start:rule_end] or rule_start == 0:
        return None
    head = text.rfind("\n", 0, rule_start - 1) + 1
    header = text[head : rule_start - 1]
    if head < floor or "|" not in header:
        return None
    end = rule_end
    while end < len(text) and text[end] == "\n":  # rows: the following lines with a pipe
        eol = text.find("\n", end + 1)
        eol = len(text) if eol < 0 else eol
        row = text[end + 1 : eol]
        if "|" not in row:
            break
        end = eol
    return Block(head, end, "table")


# ------------------------------------------------------------------------------- captions

# Pandoc writes the caption of a figure after what it holds, as a paragraph of its own, and
# the caption of a pipe table as a paragraph that starts with ":" (or "Table:"). The converter
# keeps a circuit as a fenced code block or as display math, so "Fig. 3. The circuit ..." (or
# only the words of the caption) follows it.
_LABEL = re.compile(
    r"[*_]{0,2}[ \t]*(?:fig(?:ure)?|tab(?:le)?|scheme|algorithm|listing|chart)\.?[ \t\xa0]*"
    r"\(?(?:[A-Z]?\d+|(?-i:[IVXLC]+))\)?[.:]",
    re.IGNORECASE,
)  # the number ends with "." or ":", so "Tables 3 and 4 show ..." and "Figures I think" are prose
_TABLE_CAPTION = re.compile(r"(?::|table:)[ \t]", re.IGNORECASE)
_NOT_A_CAPTION = re.compile(r"#{1,6}[ \t]|[-*+][ \t]|\d+[.)][ \t]|[>|<]")  # a heading, an item...
_BLANK_LINE = re.compile(r"\n[ \t]*+\n")  # a line of white space ends a paragraph too
_AFTER_BLOCK = re.compile(r"[ \t]*+(?:\n[ \t]*+){1,4}+")
_CIRCUIT = re.compile(r"\\begin\{(?:quantikz|yquant|tikzcd)|\\Qcircuit")


def attach_captions(text: str, blocks: list[Block], *, caption_max: int) -> list[Block]:
    """``blocks`` with the caption of every code block, table and display circuit inside it.

    The paragraph right after such a block (nothing but white space between, and no longer
    than ``caption_max`` characters) is its caption. After a table it is a caption when it
    starts with a label whose number ends with "." or ":" ("Figure 3.", "Fig. 3:", "FIG. 3.",
    "TABLE II.", "Table S1.", ...; "Tables 3 and 4 show ..." is prose) or is
    pandoc's form for a table caption (": text", "Table: text"). After code or a display
    circuit it is one whatever it says: the converter writes a circuit as code or as display
    math only inside a figure, and pandoc gives the caption of such a figure no label. The
    block then ends where the caption does, so no chunk boundary falls between them; the inline
    math of the caption is dropped from the list (it is inside the block now). A paragraph that
    holds another block, a heading or an item of a list is not a caption, and a display
    equation that is not a circuit has none.
    """
    result: list[Block] = []
    next_solid = len(text)  # start of the first block after i that is not inline math
    solid_after = [0] * len(blocks)
    for i in range(len(blocks) - 1, -1, -1):
        solid_after[i] = next_solid
        if blocks[i].kind != "inline":
            next_solid = blocks[i].start
    i = 0
    while i < len(blocks):
        block = blocks[i]
        i += 1
        end = (
            None
            if block.kind == "inline"
            else _caption_end(text, block, solid_after[i - 1], caption_max)
        )
        if end is not None:
            block = Block(block.start, end, block.kind)
            while i < len(blocks) and blocks[i].start < end:
                i += 1  # inline math of the caption
        result.append(block)
    return result


def _caption_end(text: str, block: Block, wall: int, caption_max: int) -> int | None:
    """End of the caption of ``block`` (``wall``: where the next block that is not inline math
    starts), or ``None`` when no caption follows."""
    gap = _AFTER_BLOCK.match(text, block.end)
    start = gap.end() if gap else len(text)
    if start >= min(wall, len(text)) or text[start].isspace() or _NOT_A_CAPTION.match(text, start):
        return None  # (white space: more blank lines than a caption is set off by)
    if not _captions(text, block, start):
        return None
    limit = min(wall, start + caption_max + 2)  # (the white space of a blank line counts)
    blank = _BLANK_LINE.search(text, start, limit)
    if blank is not None:
        return blank.start()
    if limit == len(text):  # the caption is the last paragraph of the text
        end = len(text.rstrip())
        return end if end - start <= caption_max else None
    return None


def _captions(text: str, block: Block, start: int) -> bool:
    """Whether a paragraph at ``start``, right after ``block``, may be its caption."""
    if block.kind == "display":
        return _CIRCUIT.search(text, block.start, block.end) is not None  # an equation has none
    if block.kind == "table":  # prose after a table is not its caption
        return bool(_LABEL.match(text, start) or _TABLE_CAPTION.match(text, start))
    return True


# --------------------------------------------------------------------------- whole blocks


class Whole(NamedTuple):
    """A block with the caption that follows it: ``text[start:core]`` is the block,
    ``text[core:end]`` its caption (``core == end`` when it has none)."""

    start: int
    core: int
    end: int
    kind: Kind  # never "inline"


def whole_blocks(text: str, *, caption_max: int) -> list[Whole]:
    """The display equations, code blocks and tables of ``text`` in order, each with its caption
    (see ``attach_captions``). Inline math is not a block here."""
    found = find_blocks(text)
    core = {b.start: b.end for b in found if b.kind != "inline"}
    return [
        Whole(b.start, core[b.start], b.end, b.kind)
        for b in attach_captions(text, found, caption_max=caption_max)
        if b.kind != "inline"
    ]


class Completion(NamedTuple):
    """Where one chunk starts and stops once the blocks it cuts are completed (the chunk's own
    offsets when nothing is added), and the kinds of the blocks it cuts that are too long to be
    completed (the chunk shows only part of them)."""

    start: int
    stop: int
    partial: tuple[Kind, ...] = ()


def complete(
    spans: Sequence[tuple[int, int]], wholes: Sequence[Whole], *, limit: int
) -> list[Completion]:
    """For the chunks ``spans`` (``(start, stop)`` in the text the ``wholes`` come from, best
    ranked first) of one paper, the ``Completion`` of each, in the same order.

    A block is cut when a chunk starts or stops strictly inside it (its caption is part of it:
    a chunk that stops between a figure and its caption cuts it). Its text no chunk holds (the
    holes: the block minus the chunks that touch it) is added, and only that, so no text appears
    twice: a hole is given to the chunk next to it, the best ranked of the two when a chunk lies
    on each side, and a chunk that is not next to a hole gets nothing for that block. A hole
    between the first chunk and the block's end therefore goes to the first chunk, and a block
    that two neighbouring chunks hold between them is left as it is.

    The size bound is ``limit``: a block with its caption of at most ``limit`` characters is
    completed whole; else its caption is dropped and it is completed alone when that is at most
    ``limit``; else nothing is added and the chunks that touch it, if the block has a hole, are
    told that they hold part of it. A chunk is added at most one block on each side.

    Linear in the blocks (a binary search finds the ones a chunk edge falls in) and quadratic
    only in the number of chunks, which is the handful that an answer prompt carries."""
    starts = [w.start for w in wholes]
    cut: dict[int, Whole] = {}
    for a, b in spans:
        for edge in (a, b):
            if (w := _around(wholes, starts, edge)) is not None:
                cut[w.start] = w
    grown = [list(s) for s in spans]
    partial: list[list[Kind]] = [[] for _ in spans]
    for w in sorted(cut.values()):
        hit = [i for i, (a, b) in enumerate(spans) if a < w.end and b > w.start]
        stop = w.end if w.end - w.start <= limit else w.core if w.core - w.start <= limit else 0
        if not stop:  # too long: nothing is added, the chunks say what they hold
            if _holes(w.start, w.end, [spans[i] for i in hit]):
                for i in hit:
                    partial[i].append(w.kind)
            continue
        hit = [i for i in hit if spans[i][0] < stop]
        for low, high in _holes(w.start, stop, [spans[i] for i in hit]):
            before = [i for i in hit if spans[i][1] == low]
            after = [i for i in hit if spans[i][0] == high]
            if before and (not after or before[0] < after[0]):
                grown[before[0]][1] = high
            elif after:
                grown[after[0]][0] = low
    return [Completion(a, b, tuple(dict.fromkeys(partial[i]))) for i, (a, b) in enumerate(grown)]


def _around(wholes: Sequence[Whole], starts: list[int], edge: int) -> Whole | None:
    """The whole block that has ``edge`` strictly inside it (an edge at its border is no cut)."""
    i = bisect_left(starts, edge) - 1
    return wholes[i] if i >= 0 and wholes[i].end > edge else None


def _holes(low: int, high: int, spans: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """The parts of ``[low, high)`` that none of ``spans`` covers, in order."""
    holes: list[tuple[int, int]] = []
    at = low
    for a, b in sorted(spans):
        if a > at:
            holes.append((at, min(a, high)))
        at = max(at, b)
        if at >= high:
            return holes
    if at < high:
        holes.append((at, high))
    return holes


_NAMES: dict[str, str] = {"display": "display equation", "code": "code block", "table": "table"}


def partial_note(kinds: Sequence[Kind]) -> str:
    """The line put before a chunk that holds only part of a block too long to complete."""
    parts = " and of ".join(f"a longer {_NAMES[k]}" for k in dict.fromkeys(kinds))
    return f"Note: this passage shows only part of {parts}; the rest is not in this passage."
