"""Checks on OCR output that the model cannot be trusted to make itself. Pure functions.

A vision-language model asked to transcribe a page sometimes writes something else: a
plausible page that is not there (blank or faint pages, the same text on page after
page), a loop that fills the token budget, or a drift far past what a page can hold.
The checks here name the problem; what to do about it (retry, fall back to the text
layer, leave the page empty) is decided by the OCR adapters' retry ladder and by
``IngestService``.

The thresholds are measured, not guessed: on the 123 reference pages the densest real
page holds 88 non-space characters per square inch (a 27x27 table), prose pages 40 to
75; the failures were 221 and 270 (see ``config/README.md``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class OutputProblem(StrEnum):
    """Why an OCR output is not accepted as it is, most serious first."""

    TOO_LONG = "too_long"  # more text than the page could hold
    LOOP = "loop"  # the same words over and over
    TRUNCATED = "truncated"  # stopped at max_tokens
    EMPTY = "empty"  # no letter or digit left


class OcrGuards(BaseModel):
    """Thresholds of the checks. Part of the cache key of every kind that uses OCR."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Length: non-space characters per square inch of page, with a floor for tiny images.
    max_chars_per_sq_in: float = Field(default=150.0, gt=0)
    min_capacity_chars: int = Field(default=300, ge=0)
    # Loops: a run of words (1 to loop_max_period of them) that repeats back to back.
    loop_max_period: int = Field(default=30, ge=1)
    loop_min_repeats: int = Field(default=6, ge=2)  # for a unit of loop_min_unit_chars or more
    loop_min_unit_chars: int = Field(default=12, ge=1)
    loop_short_unit_repeats: int = Field(default=40, ge=2)  # any shorter unit ("loop loop ...")
    loop_min_share: float = Field(default=0.5, gt=0, le=1)  # of the words, if the reply stopped
    # A reply cut at the token limit that ends in this many non-space characters without a
    # letter or digit is a runaway dot leader or rule (". . . .", "____", "- - -").
    loop_min_punct_run: int = Field(default=200, ge=1)
    # Retries: up to max_attempts tries, stricter each time (the third rescued 3 of the 6
    # corpus pages that reached it); a dense page that only ran out of tokens gets more room
    # instead, if that is at least retry_max_tokens_gain times the limit it had.
    max_attempts: int = Field(default=3, ge=1)
    retry_max_tokens_factor: int = Field(default=2, ge=1)
    retry_max_tokens_cap: int = Field(default=8192, ge=1)
    retry_max_tokens_gain: float = Field(default=1.5, ge=1)
    # Pages of one document that transcribe to the same text.
    duplicate_min_chars: int = Field(default=80, ge=1)
    duplicate_similarity: float = Field(default=0.9, gt=0, le=1)
    duplicate_shingle_words: int = Field(default=4, ge=1)


_WORD = re.compile(r"[^\W_]+")


def page_area_sq_in(width_px: int, height_px: int, dpi: int) -> float:
    return width_px * height_px / (dpi * dpi)


def image_area_sq_in(width_px: int, height_px: int, dpi: object) -> float | None:
    """The physical area of an image from its own resolution (``Image.info["dpi"]``: a
    number or an (x, y) pair), or None when it has none that can be believed.

    Images arrive without a page size: a screenshot or a photo says nothing about how
    much text it can hold, so no length bound applies to it. A resolution below 20 is a
    placeholder some encoders write (1 dpi), not a measurement.
    """
    pair = dpi if isinstance(dpi, tuple | list) and len(dpi) == 2 else (dpi, dpi)
    if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in pair):
        return None
    x, y = float(pair[0]), float(pair[1])  # type: ignore[arg-type]
    if min(x, y) < 20:
        return None
    return width_px * height_px / (x * y)


def count_chars(text: str) -> int:
    """Non-space characters: what ``PageSignals.n_chars`` counts for a text layer."""
    return len("".join(text.split()))


_TAG = re.compile(r"<[^>\n]{1,200}>")
_TABLE_RULE = re.compile(r"^[ \t]*\|?[ \t]*:?-{2,}:?[ \t|:\-]*$", re.MULTILINE)


def content_chars(text: str) -> int:
    """Non-space characters of the content: without HTML tags, table pipes and rules.

    The length bound was measured on Markdown tables; a model asked for HTML tables
    (olmOCR, Nanonets profiles) writes about three times the characters for the same
    page, all of them markup.
    """
    text = _TABLE_RULE.sub("", _TAG.sub("", text)).replace("|", "")
    return count_chars(text)


def capacity_chars(area_sq_in: float, guards: OcrGuards) -> int:
    """The most non-space characters a page of this size can plausibly hold."""
    return max(guards.min_capacity_chars, round(guards.max_chars_per_sq_in * area_sq_in))


def loop_run(text: str, guards: OcrGuards) -> tuple[float, bool]:
    """The longest group of words repeated back to back (``a b c a b c a b c ...``) as a
    share of the text's words (0.0 when there is none), and whether a repeated run goes
    on to the end of the text.

    Words are letters and digits only, so dot leaders, table pipes and rules do not
    count and a contents page or a table does not look like a loop. A unit has to be
    long (``loop_min_unit_chars``) to count after ``loop_min_repeats`` repeats; a short
    one only after ``loop_short_unit_repeats``. A unit without a letter never counts:
    rows of ``0`` in a table or an identity matrix are not a loop.
    """
    words = [w.lower() for w in _WORD.findall(text)]
    n = len(words)
    longest, at_end = 0, False
    for period in range(1, min(guards.loop_max_period, n // 2) + 1):
        run = 0
        for i in range(n - period):
            if words[i] != words[i + period]:
                run = 0
                continue
            run += 1
            repeats = (run + period) // period
            if repeats < guards.loop_min_repeats:
                continue
            unit = words[i + 1 - run : i + 1 - run + period]
            if not any(c.isalpha() for w in unit for c in w):
                continue  # numbers only: a matrix, a table of zeros
            if (
                sum(map(len, unit)) >= guards.loop_min_unit_chars
                or repeats >= guards.loop_short_unit_repeats
            ):
                longest = max(longest, run + period)
                at_end = at_end or i + period >= n - 2
    return (longest / n, at_end) if n else (0.0, False)


def trailing_punct_run(text: str) -> int:
    """Non-space characters at the end of the text with no letter or digit among them: the
    length of a dot leader, rule or row of dashes that the text ends in."""
    run = 0
    for ch in reversed(text):
        if ch.isalnum():
            break
        if not ch.isspace():
            run += 1
    return run


def loop_share(text: str, guards: OcrGuards) -> float:
    """The share of the text's words taken by its longest repeated run (see :func:`loop_run`)."""
    return loop_run(text, guards)[0]


def is_looping(text: str, guards: OcrGuards) -> bool:
    """Whether the text has a repeated run at all (see :func:`loop_share`)."""
    return loop_share(text, guards) > 0


def find_problem(
    text: str, finish_reason: str | None, area_sq_in: float | None, guards: OcrGuards
) -> OutputProblem | None:
    """The first problem of one page's (cleaned) OCR output, or None when it is fine.

    ``area_sq_in`` None: the size of the page is not known (an image file), so there is no
    length bound. A repeated run is a loop only when the reply ran into the token limit
    and the run goes on to the end of the text (a runaway fills the budget, so the cut
    falls inside it; a dense table with a block of "N/A" rows that happens to be cut
    later is not one), or the reply ends in a long run of punctuation only (a dot leader
    that never stops: the words of the loop are not words), or the run is at least
    ``loop_min_share`` of the text. An appendix of "N/A" rows, a check-mark table or a
    form with ten identical lines repeats inside a page of other content and ends by
    itself, and a dot leader inside a contents page that goes on with titles and page
    numbers is no loop.
    """
    if area_sq_in is not None and content_chars(text) > capacity_chars(area_sq_in, guards):
        return OutputProblem.TOO_LONG
    if finish_reason == "length" and trailing_punct_run(text) >= guards.loop_min_punct_run:
        return OutputProblem.LOOP
    share, at_end = loop_run(text, guards)
    if share > 0 and ((finish_reason == "length" and at_end) or share >= guards.loop_min_share):
        return OutputProblem.LOOP
    if finish_reason == "length":
        return OutputProblem.TRUNCATED
    if not any(c.isalnum() for c in text):
        return OutputProblem.EMPTY
    return None


def retry_max_tokens(max_tokens: int, guards: OcrGuards) -> int:
    """Room for a dense page that hit the limit; unchanged when it is already generous."""
    return min(
        max_tokens * guards.retry_max_tokens_factor, max(guards.retry_max_tokens_cap, max_tokens)
    )


def duplicate_pages(texts: Mapping[int, str], guards: OcrGuards) -> dict[int, int]:
    """Pages whose text repeats an earlier page's: ``{page: the first page it repeats}``.

    When a model writes the same text for different pages, at most one of them is
    what it read. The first page of a group is kept: a repeat adds no information, so
    dropping it never loses text, while a hallucinated text is stopped at one page.
    Texts shorter than ``duplicate_min_chars`` are ignored (a part title, "intentionally
    left blank"). Each page is compared, in page order, with the first page of every
    group so far and never with a page that was itself found to be a repeat, so a chain
    of slowly changing pages is not one group. Two pages count as the same when their
    word shingles overlap by at least ``duplicate_similarity`` (Jaccard), so a changed
    word or two does not hide a repeat. Shingle sets ignore how often a shingle occurs,
    so a page that is another page twice over is a match too. A page that only contains
    an earlier one (the next slide of a deck that adds a bullet each time) is not.
    """
    n = guards.duplicate_shingle_words
    sim = guards.duplicate_similarity
    firsts: list[tuple[int, frozenset[tuple[str, ...]]]] = []
    found: dict[int, int] = {}
    for index in sorted(texts):
        text = texts[index]
        if count_chars(text) < guards.duplicate_min_chars:
            continue
        words = [w.lower() for w in _WORD.findall(text)]
        shingles = frozenset(tuple(words[i : i + n]) for i in range(max(len(words) - n + 1, 1)))
        for first, other in firsts:
            small, large = sorted((len(shingles), len(other)))
            if small < sim * large:  # too different in size to overlap that much
                continue
            inter = len(shingles & other)
            if inter >= sim * (len(shingles) + len(other) - inter):
                found[index] = first
                break
        else:
            firsts.append((index, shingles))
    return found
