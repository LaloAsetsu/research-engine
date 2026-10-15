"""Equation numbers asked for in a question, and where a paper writes them.

The LaTeX converter writes the number LaTeX gives a display equation into its math as
``\\tag{n}``. A reader who asks "what does Eq. (2) state?" gives no word that a search can weigh:
every paper has an Eq. (2). So the question is read here for the numbers it names
(:func:`equation_references`), the paper that is already among the retrieved ones is searched for
the tags (:func:`find_equations`), and the chunk that holds the equation is picked with
:func:`chunk_for`. Pure functions on text; nothing is stored.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from .chunking import NAME_LENGTH, Chunk

MAX_REFERENCES = 4  # numbers read from a question: the most that MAX_ADDED could serve at one each
LOOK_BACK = 1500  # characters before a tag searched for a \\[ or \\begin{ that starts its display
TITLE_LENGTH = 100  # characters of a paper's title kept in a header
# A paper is searched for the asked equation number only when its title shares at least this many
# content words with the question. Every paper has an Eq. (2), so a paper that the question does
# not seem to name must not give one: a missed match means the answer is as it was without the
# lookup (a refusal), never another paper's equation. Chosen on a small set of questions that
# paraphrase their paper's title.
MIN_TITLE_WORDS = 4

# ---------------------------------------------------------------- the question

# The number: 3, 3a, 2.3, A1, A.1, S5, A.1a. In parentheses a letter before the digits may be
# either case and the suffix is a lowercase letter. Without parentheses it must be an upper-case
# prefix and a suffix from a to e, and it may not run into more letters or digits, so "2D" and
# "2019" are not equation numbers.
_NUMBER = r"(?:[A-Za-z]\.?)?\d{1,3}(?:\.\d{1,3})?(?-i:[a-z])?"
_BARE = r"(?:(?-i:[A-Z])\.?)?\d{1,3}(?:\.\d{1,3})?(?-i:[a-e])?(?![A-Za-z0-9_%/]|\.\d)"
_ITEM_PAREN = re.compile(rf"\(\s*(?P<n>{_NUMBER})\s*\)", re.IGNORECASE)
_ITEM_BARE = re.compile(rf"(?P<n>{_BARE})", re.IGNORECASE)
# A bare number followed by a unit or a counted noun is a measure or a count ("equation 3 times",
# "eq. 5 MHz", "equation 10 photons"), not an equation number. English and Spanish.
_COUNTED = re.compile(
    r"\s*(?:%|(?:times|percent|per|veces|por\s+ciento|qubits?|photons?|fotones|modes?|modos|"
    r"spins?|espines|sites?|sitios|levels?|niveles|terms?|t[ée]rminos?|variables?|unknowns?|"
    r"inc[óo]gnitas?|parameters?|par[áa]metros?|steps?|pasos?|[kMGT]?Hz|[nuµμm]s|m?K|dB|m?eV|"
    r"[nuµμm]m)\b)",
    re.IGNORECASE,
)

_WORD = (
    r"(?:(?:equations?|ecuaci(?:ones|[óo]n))\b|(?:eqns?|eqs?|ecs?)\b\.?)"
    r"(?:\s*(?:number|n[úu]mero|n[úu]m\.|no\.|n[º°]))?"
)
_KEYWORD = re.compile(rf"\b{_WORD}", re.IGNORECASE)
_AGAIN = re.compile(rf"(?:{_WORD})?\s*", re.IGNORECASE)
# What joins two numbers of one mention: "(2) and (3)", "(2), (3)", "(2)-(4)", "(2)--(4)" (the two
# ends only).
_JOIN = re.compile(
    r"\s*(?:(?:,\s*)?\b(?:and|or|y|e|o|to|through)\b|,|;|&|-{1,2}|[\u2013\u2014])\s*",
    re.IGNORECASE,
)


def _item(text: str, pos: int, parenthesized: bool | None) -> tuple[str, int, bool] | None:
    """The number written at ``pos``: (number, end, whether in parentheses). ``parenthesized``
    None accepts either form, True or False only that one."""
    if parenthesized is not False and (m := _ITEM_PAREN.match(text, pos)):
        return m["n"], m.end(), True
    if parenthesized is not True and (m := _ITEM_BARE.match(text, pos)):
        if _COUNTED.match(text, m.end()):
            return None
        return m["n"], m.end(), False
    return None


def canonical(number: str) -> str:
    """The form in which two spellings of one number are equal: lower case, and no dot or dash
    between a leading letter and the digits (``A.1`` is ``A1``; ``2.3`` stays ``2.3``)."""
    return re.sub(r"^([a-z])[.\-](?=\d)", r"\1", number.strip().lower())


def equation_references(question: str) -> list[str]:
    """The equation numbers a question names, in order and without repeats, at most
    :data:`MAX_REFERENCES`: ``Eq. (2)``, ``Eqs. (2) and (3)``, ``equation (A.1)``, ``Equation 7``,
    ``ecuación (4)``, ``Ec. 3a``. A number must follow the word, in parentheses or bare; a
    number that runs into more letters or digits (``2D``) or is followed by a counted noun
    (``3 times``) is not read. A range names its two ends only. Numbers are returned as written,
    compare them through :func:`canonical`."""
    found: dict[str, str] = {}
    for word in _KEYWORD.finditer(question):
        if re.fullmatch(r"ECS?\.?", word.group()):  # the acronym EC, not "Ec." (ecuación)
            continue
        first = _item(question, _skip_spaces(question, word.end()), None)
        if first is None:
            continue
        number, end, parenthesized = first
        found.setdefault(canonical(number), number)
        while len(found) < MAX_REFERENCES and (join := _JOIN.match(question, end)):
            again = _AGAIN.match(question, join.end())
            assert again is not None  # every part of the pattern is optional
            nxt = _item(question, again.end(), parenthesized)
            if nxt is None:
                break
            number, end, _ = nxt
            found.setdefault(canonical(number), number)
        if len(found) >= MAX_REFERENCES:
            break
    return list(found.values())[:MAX_REFERENCES]


def _skip_spaces(text: str, pos: int) -> int:
    while pos < len(text) and text[pos] in " \t\u00a0~":  # a LaTeX tie and a no-break space
        pos += 1
    return pos


# ---------------------------------------------------------------- the paper named by the question

_STOP_TEXT = """
    the a an of in on for and to with by from at as is are was were be been it its this that these
    those their there what which who whom whose how why when where does do did can could would
    should will may might has have had not no or but if than then so such into onto about between
    via per out over under up down more most less least each both all any some also only own
    paper papers article articles work study using use used based new tutorial introduction
    equation equations eq eqs state states give gives define defines describe describes
    el la los las un una unos unas de del al en y o u e a con por para sin sobre entre es son fue
    ser que cual cuales quien como cuando donde cuyo este esta estos estas ese esa eso aquel
    se su sus lo le les mas menos muy pero si ni segun hacia desde hasta tambien solo articulo
    articulos trabajo estudio ecuacion ecuaciones establece define describe indica
"""
_STOP_WORDS = frozenset(_STOP_TEXT.split())
_SUFFIXES = ("ation", "ition", "ing", "ion", "ed", "ly", "er", "al", "ic", "ive")


def _fold(word: str) -> str:
    """A word as it is compared: plural and a few endings dropped (``resonators`` and
    ``resonator``, ``comparing`` and ``comparison`` are not told apart from their stems)."""
    if len(word) > 4 and word.endswith("ies"):
        word = word[:-3] + "y"
    elif len(word) > 5 and word.endswith(("ches", "shes", "sses", "xes", "ces", "ses")):
        word = word[:-2] if not word.endswith(("ces", "ses")) else word[:-1]
    elif len(word) > 5 and word.endswith("es"):
        word = word[:-2]
    elif len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def content_words(text: str) -> set[str]:
    """The words of ``text`` that say what it is about: lower case, accents removed, English and
    Spanish stop words and words of one or two letters dropped, plurals and common endings
    folded."""
    plain = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    words = re.findall(r"[a-z0-9]+", plain)
    return {_fold(w) for w in words if len(w) > 2 and w not in _STOP_WORDS}


def title_overlap(question: str, title: str | None) -> int:
    """How many content words the question and a paper's title share."""
    return len(content_words(question) & content_words(title or ""))


# ---------------------------------------------------------------- the paper

# ``\tag{n}`` or ``\tag*{n}``; the text is a number as the converter writes it.
_TAG = re.compile(r"\\tag\*?\s*\{\s*(?P<n>[A-Za-z0-9.\-]{1,12})\s*\}")


@dataclass(frozen=True)
class EquationSpan:
    """Where a paper writes one equation number, as offsets in its text (the page texts joined
    as the chunker joins them)."""

    number: str  # canonical
    tag: str  # as the paper writes it
    start: int  # of the display equation; the start of the tag when the equation is not found
    tag_start: int
    end: int  # just past the tag


def find_equations(
    document: str, numbers: Sequence[str], *, look_back: int = LOOK_BACK
) -> dict[str, EquationSpan]:
    """For each of ``numbers`` (canonical) that ``document`` tags, the first ``\\tag`` that
    carries it. A paper numbers an equation once, so a second tag with the same text (a restart
    after the numbering stopped) is not looked at. One pass over the text, which ends as soon
    as every number is found."""
    wanted = set(numbers)
    out: dict[str, EquationSpan] = {}
    if not wanted:
        return out
    for m in _TAG.finditer(document):
        key = canonical(m["n"])
        if key in wanted and key not in out:
            out[key] = EquationSpan(
                key, m["n"], _equation_start(document, m.start(), look_back), m.start(), m.end()
            )
            if len(out) == len(wanted):
                break
    return out


def _equation_start(document: str, tag_start: int, look_back: int) -> int:
    """The start of the display equation that holds the tag. The nearest ``$$`` before it, however
    far back, when it opens a block: an odd number of ``$$`` come before the tag, or no blank line
    lies between that ``$$`` and the tag (a stray ``$$`` earlier in a paper turns the count the
    wrong way for all the displays after it, and a display does not hold a blank line). Else the
    nearest ``\\[`` or ``\\begin{`` within ``look_back`` characters, else the tag itself. A start
    that is too early only makes the chunk say that it holds the end of the equation."""
    at = document.rfind("$$", 0, tag_start)
    if at >= 0 and (
        document.count("$$", 0, tag_start) % 2 == 1 or "\n\n" not in document[at:tag_start]
    ):
        return at
    low = max(0, tag_start - look_back)
    at = max(document.rfind("\\[", low, tag_start), document.rfind("\\begin{", low, tag_start))
    return at if at >= 0 else tag_start


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n\s*\n")


def lead_sentence_start(document: str, start: int, limit: int) -> int:
    """Where the sentence before the display equation at ``start`` begins, or ``start`` itself
    when there is none to take: the text before ends in another display (``$$``), the sentence is
    longer than ``limit`` characters (no sentence or paragraph break within it), or nothing
    precedes. The sentence is the last one of the
    paragraph before (a display is a paragraph of its own), so that the words that name the
    quantity the equation gives, which usually end in a colon, go with it."""
    end = len(document[:start].rstrip())
    if end == 0 or document[max(0, end - 2) : end] == "$$":
        return start
    low = max(0, end - limit)
    cut = 0
    for m in _SENTENCE_END.finditer(document, low, end):
        cut = m.end()
    return cut if 0 < cut < end else start


def covers(chunk: Chunk, span: EquationSpan) -> bool:
    """Whether ``chunk`` holds the whole equation, from its start to the end of its tag."""
    return chunk.start <= span.start and span.end <= chunk.start + len(chunk.text)


def chunk_for(chunks: Sequence[Chunk], span: EquationSpan) -> Chunk | None:
    """The chunk of ``chunks`` that holds the tag whole and most of its equation; of two equal
    ones the earlier (the chunks overlap, and the earlier shows more of what leads to the tag).
    None when no chunk holds the tag."""
    best: Chunk | None = None
    best_size = -1
    for chunk in chunks:
        end = chunk.start + len(chunk.text)
        if chunk.start <= span.tag_start and span.end <= end:
            size = min(end, span.end) - max(chunk.start, span.start)
            if size > best_size:
                best, best_size = chunk, size
    return best


# ---------------------------------------------------------------- the header


def equation_header(
    tags: Sequence[str], doc_id: str, title: str | None, partial: Collection[str] = ()
) -> str:
    """The line put before a chunk that was added for the equation numbers ``tags``: it ties the
    numbers to their paper by the name the citations use (``doc_id[:NAME_LENGTH]``) and its title.
    A number in ``partial`` is one whose equation starts before the chunk: the header says that
    the chunk holds its end, so that a fragment is not copied as the equation."""
    whole = [f"({t})" for t in tags if t not in partial]
    cut = [f"({t})" for t in tags if t in partial]
    parts = []
    if whole:
        parts.append(_numbers("Equation", whole))
    if cut:
        parts.append("the end of " + _numbers("Equation", cut))
    head = " and ".join(parts)
    head = head[0].upper() + head[1:]
    name = doc_id[:NAME_LENGTH]
    clean = " ".join((title or "").split())
    if len(clean) > TITLE_LENGTH:
        clean = clean[: TITLE_LENGTH - 3].rstrip() + "..."
    note = " (the start of the cut equation is in the text before this chunk)" if cut else ""
    return f"{head} of paper {name}" + (f", {clean}" if clean else "") + note + ":"


def _numbers(word: str, numbers: Sequence[str]) -> str:
    if len(numbers) == 1:
        return f"{word} {numbers[0]}"
    return f"{word}s {', '.join(numbers[:-1])} and {numbers[-1]}"
