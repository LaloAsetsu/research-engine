"""Turn a LaTeX source (a ``.tex`` file or an arXiv archive) into one self-contained string.

pandoc is not trusted with the file system. It resolves ``\\input`` against its CWD,
silently drops includes that are not UTF-8, hangs forever on a file that inputs itself
or on a self-referential macro (conference ``.sty`` files do
``\\renewcommand{\\small}{\\@setfontsize\\small...}``), and reads any path it is given
(``\\lstinputlisting{/etc/passwd}`` ends up in the Markdown). So the converter unpacks
archives safely, picks the main file the way arXiv does, and inlines every include
here; pandoc then runs with ``--sandbox`` on a single document.

Pure functions over paths and strings: no pandoc, no pylatexenc.
"""

from __future__ import annotations

import codecs
import functools
import gzip
import io
import json
import re
import tarfile
import zlib
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

from ...domain.errors import ConversionError

TEX_SUFFIXES = frozenset({".tex", ".ltx"})
MAIN_NAMES = ("main", "ms", "paper", "article")  # tie-break between several main files
MAX_DEPTH = 20  # nested \input levels
MAX_MEMBERS = 20_000  # files in one archive
MAX_TAR_HEADER = 2**20  # bytes of headers (pax, GNU long name) for one archive member
MAX_MACRO_BODY = 20_000  # chars; longer "definitions" are unbalanced braces, not macros

# Never needed to produce text: not extracted (faster, and a smaller zip-bomb surface).
_MEDIA_SUFFIXES = frozenset(
    {
        *(".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".svg"),
        *(".eps", ".ps", ".pdf", ".ai", ".psd", ".mp4", ".mov", ".avi"),
        *(".zip", ".gz", ".tgz", ".tar", ".bz2", ".xz", ".7z"),
        *(".npy", ".npz", ".pkl", ".pt", ".pth", ".ckpt", ".h5", ".hdf5", ".bin", ".mat"),
        *(".xlsx", ".docx", ".pptx"),
    }
)
_GZIP_MAGIC = b"\x1f\x8b"

# Environments whose content LaTeX reads verbatim: no comments, no includes inside.
_VERBATIM_ENVS = r"verbatim\*?|Verbatim\*?|BVerbatim|LVerbatim|lstlisting|minted|comment"
_NOT_LETTER = r"(?![a-zA-Z@])"

# Commands pandoc must see as themselves; a redefinition (a conference style's
# \renewcommand{\section}{\@startsection...}) hides headings, titles or authors.
STRUCTURAL = frozenset(
    {
        *("part", "chapter", "section", "subsection", "subsubsection", "paragraph"),
        *("subparagraph", "maketitle", "title", "author", "and", "And", "AND", "thanks"),
        *("date", "abstract", "footnote", "caption", "label", "ref", "cite", "item"),
        *("begin", "end", "emph", "textbf", "textit", "input", "include", "document"),
        *("appendix", "bibliography"),
    }
)


# ------------------------------------------------------------------ decoding
def _legacy_char(byte: int) -> str:
    try:
        return bytes([byte]).decode("cp1252")
    except UnicodeDecodeError:  # 0x81, 0x8d, 0x8f, 0x90, 0x9d: undefined in cp1252
        return chr(byte)  # latin-1


_LEGACY = [_legacy_char(b) for b in range(256)]


def _legacy_bytes(err: UnicodeError) -> tuple[str, int]:
    """Decode error handler: the bytes that are not UTF-8 are cp1252 (else latin-1)."""
    if not isinstance(err, UnicodeDecodeError):
        raise err
    return "".join(_LEGACY[b] for b in err.object[err.start : err.end]), err.end


codecs.register_error("docingest-legacy", _legacy_bytes)


def decode_tex(data: bytes) -> str:
    """UTF-8, cp1252 or latin-1, decided per byte where needed (never fails); normalized
    newlines.

    A UTF-8 file with one pasted latin-1 byte (a no-break space) must not have every
    accented letter garbled, so only the bytes that are not UTF-8 are read as cp1252.
    A file without a single multi-byte UTF-8 sequence is a legacy file: cp1252, or
    latin-1 if it uses a byte that cp1252 leaves undefined.
    """
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        if not data.decode("utf-8", errors="ignore").isascii():  # some real UTF-8
            text = data.decode("utf-8", errors="docingest-legacy")
        else:
            try:
                text = data.decode("cp1252")
            except UnicodeDecodeError:
                text = data.decode("latin-1")
    return text.replace("\r\n", "\n").replace("\r", "\n")


# Inline commands whose argument LaTeX reads verbatim, like \verb|...|: a % or an
# \endinput in there is text. \verb's delimiter can be any character, even a brace;
# the others also accept a balanced {...}. TikZ's \path[...] (...) is not url's \path.
_INLINE_VERB = (
    r"\\(?:(?P<verb>verb)\*?(?=[^a-zA-Z\s*])"
    r"|(?:lstinline|Verb\*?)(?![a-zA-Z@])[ \t]*(?:\[[^\]\n]*\][ \t]*)?(?=[^a-zA-Z\s])"
    r"|mintinline(?![a-zA-Z@])[ \t]*(?:\[[^\]\n]*\][ \t]*)?\{[^{}\n]*\}[ \t]*(?=[^a-zA-Z\s])"
    r"|path(?![a-zA-Z@])[ \t]*(?=[^a-zA-Z\s(\[]))"
)
_INLINE_VERB_MAX = 1000  # chars; bounds the scan, so a line of stray heads stays linear
_VERB_GROUP = re.compile(r"\{(?:[^{}\n]|\{(?:[^{}\n]|\{[^{}\n]*\})*\})*\}")  # 3 levels deep


def _inline_verb_end(text: str, m: re.Match[str]) -> int | None:
    """Index just past the verbatim argument whose command ``m`` matched (an
    ``_INLINE_VERB`` head); None when it does not close on its line, as TeX requires."""
    i = m.end()
    stop = min(len(text), i + _INLINE_VERB_MAX)
    if (nl := text.find("\n", i, stop)) >= 0:
        stop = nl
    if text[i] == "{" and m.group("verb") is None:
        group = _VERB_GROUP.match(text, i, stop)
        return group.end() if group else None
    close = text.find(text[i], i + 1, stop)
    return close + 1 if close >= 0 else None


_SCAN = re.compile(
    rf"(?P<inline>{_INLINE_VERB})"  # \verb|...|, \lstinline!...!, ...
    rf"|\\begin\s*\{{(?P<env>{_VERBATIM_ENVS})\}}"
    r"|\\(?:url|href)\s*\{[^{}\n]*\}"  # a % inside a URL is not a comment
    r"|\\."  # escaped char: \%, \\, \{ ...
    r"|%"
)


def _end_env(env: str) -> re.Pattern[str]:
    return re.compile(rf"\\end\s*\{{{re.escape(env)}\}}")


@dataclass(frozen=True)
class Verbatim:
    start: int
    end: int
    body: str  # what LaTeX reads verbatim; an environment's [options]{args} included
    env: str | None  # None for a \verb-like inline command


# filecontents is written out verbatim, never typeset: nothing in it is a command either.
_VERBATIM_START = re.compile(
    rf"\\begin\s*\{{(?P<env>{_VERBATIM_ENVS}|filecontents\*?)\}}|(?P<inline>{_INLINE_VERB})"
)


def verbatim_parts(text: str, start: int = 0) -> Iterator[Verbatim]:
    """The verbatim regions of ``text[start:]``, in order: verbatim-like environments
    (an unclosed one runs to the end, as in TeX) and ``\\verb``-like arguments.

    Linear: the regex ``\\begin{verbatim}.*?\\end{verbatim}`` rescans the rest of the
    text for every unclosed begin, which takes minutes on a few hundred KB of them.
    """
    pos = start
    while m := _VERBATIM_START.search(text, pos):
        if env := m.group("env"):
            end = _end_env(env).search(text, m.end())
            if end is None:
                yield Verbatim(m.start(), len(text), text[m.end() :], env)
                return
            yield Verbatim(m.start(), end.end(), text[m.end() : end.start()], env)
            pos = end.end()
        elif (end := _inline_verb_end(text, m)) is not None:
            yield Verbatim(m.start(), end, text[m.end() + 1 : end - 1], None)
            pos = end
        else:
            pos = m.end()


def _outside_verbatim(text: str, start: int = 0) -> Iterator[tuple[int, int]]:
    """``(start, end)`` of the stretches between the ``verbatim_parts``, in order."""
    pos = start
    for v in verbatim_parts(text, start):
        yield pos, v.start
        pos = v.end
    yield pos, len(text)


def regions(
    text: str,
    begin: re.Pattern[str],
    end: re.Pattern[str] | Callable[[re.Match[str]], re.Pattern[str]],
) -> Iterator[tuple[re.Match[str], re.Match[str]]]:
    """Non-overlapping ``(begin, end)`` match pairs, the ones ``begin.*?end`` (DOTALL)
    would find, in linear time: that regex rescans the rest of the text for every
    begin without an end. ``end`` may depend on the begin match (an environment's
    name); an end that is missing after one begin is missing after every later one.
    """
    pos = 0
    unclosed: set[str] = set()
    for b in begin.finditer(text):
        if b.start() < pos:
            continue
        stop = end(b) if callable(end) else end
        if stop.pattern in unclosed:
            continue
        if (e := stop.search(text, b.end())) is None:
            unclosed.add(stop.pattern)
            continue
        yield b, e
        pos = e.end()


def sub_regions(
    text: str,
    begin: re.Pattern[str],
    end: re.Pattern[str] | Callable[[re.Match[str]], re.Pattern[str]],
    repl: Callable[[str], str],
) -> str:
    """``text`` with every ``regions`` pair, and what is between, replaced by
    ``repl(what is between)``."""
    parts: list[str] = []
    pos = 0
    for b, e in regions(text, begin, end):
        parts += (text[pos : b.start()], repl(text[b.end() : e.start()]))
        pos = e.end()
    parts.append(text[pos:])
    return "".join(parts)


def strip_comments(text: str) -> str:
    """Drop ``%`` comments like TeX does, except inside verbatim-like environments.

    A comment-only line disappears entirely; a trailing comment keeps its ``%`` so the
    "no space at end of line" meaning survives. ``\\%`` is text, and so is a ``%`` in
    ``\\verb|%|``, ``\\lstinline!%!``, ``\\Verb``, ``\\mintinline`` or ``\\path``.
    """
    out: list[str] = []
    env: str | None = None
    for line in text.splitlines(keepends=True):
        if env is not None:
            out.append(line)
            if _end_env(env).search(line):
                env = None
            continue
        kept, env = _strip_line(line)
        if kept is not None:
            out.append(kept)
    return "".join(out)


def _strip_line(line: str) -> tuple[str | None, str | None]:
    """(line without its comment, or None to drop it; verbatim env left open)."""
    pos = 0
    while m := _SCAN.search(line, pos):
        if m.group("inline") is not None:
            end = _inline_verb_end(line, m)
            if end is None:
                return line, None
            pos = end
        elif env := m.group("env"):
            end = _end_env(env).search(line, m.end())
            if end is None:
                return line, env
            pos = end.end()
        elif m.group() == "%":
            head = line[: m.start()]
            if not head.strip():
                return None, None
            return head + "%" + ("\n" if line.endswith("\n") else ""), None
        else:
            pos = m.end()
    return line, None


def document_body(text: str) -> str:
    """What is between ``\\begin{document}`` and ``\\end{document}`` (all if absent)."""
    begin = re.search(r"\\begin\s*\{document\}", text)
    if begin is None:
        return text
    ends = list(re.finditer(r"\\end\s*\{document\}", text))
    end = ends[-1].start() if ends and ends[-1].start() > begin.end() else len(text)
    return text[begin.end() : end]


def match_brace(text: str, i: int, limit: int = MAX_MACRO_BODY) -> int | None:
    """Index just past the ``}`` closing the ``{`` at ``text[i]``; None if unbalanced."""
    if i >= len(text) or text[i] != "{":
        return None
    depth, j, stop = 0, i, min(len(text), i + limit)
    while j < stop:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return None


def brace_pairs(text: str) -> dict[int, int]:
    """Map each ``{`` index to the index just past its matching ``}``, in one linear pass.

    Same matching as :func:`match_brace` (``\\`` escapes the next char), but computed once
    per text: scanning a window per candidate went quadratic on unbalanced sources.
    """
    pairs: dict[int, int] = {}
    stack: list[int] = []
    j, n = 0, len(text)
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            stack.append(j)
        elif c == "}" and stack:
            pairs[stack.pop()] = j + 1
        j += 1
    return pairs


def _matched(pairs: dict[int, int], i: int, limit: int = MAX_MACRO_BODY) -> int | None:
    end = pairs.get(i)
    return end if end is not None and end - i <= limit else None


# ------------------------------------------------------------ macro hygiene
_DEF_HEAD = re.compile(
    r"\\(?P<kind>(?:re)?newcommand|providecommand|DeclareRobustCommand|DeclareMathOperator)"
    r"\*?\s*(?:\{\s*\\(?P<n1>[a-zA-Z@]+)\s*\}|\\(?P<n2>[a-zA-Z@]+))"
    r"(?:\s*\[[^\]\n]*\]){0,2}\s*(?=\{)"
    r"|\\(?P<dkind>[gex]?def)\s*\\(?P<n3>[a-zA-Z@]+)(?:\s*#\d)*\s*(?=\{)"
)
_NEWTHEOREM = re.compile(
    r"\\newtheorem\*?\s*\{[^{}]*\}\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}\s*(?:\[[^\]]*\])?"
)


@dataclass(frozen=True)
class MacroDef:
    start: int
    end: int
    kind: str  # newcommand, renewcommand, def, DeclareMathOperator, ...
    name: str  # without the backslash
    body: str

    @property
    def recursive(self) -> bool:
        return re.search(rf"\\{re.escape(self.name)}{_NOT_LETTER}", self.body) is not None


def macro_definitions(text: str) -> Iterator[MacroDef]:
    """Top-level ``\\newcommand``-style and ``\\def`` definitions with a braced body."""
    pos = 0
    pairs = brace_pairs(text)
    while m := _DEF_HEAD.search(text, pos):
        end = _matched(pairs, m.end())
        if end is None:
            pos = m.end()
            continue
        name = m.group("n1") or m.group("n2") or m.group("n3")
        kind = m.group("kind") or m.group("dkind")
        yield MacroDef(m.start(), end, kind, name, text[m.end() + 1 : end - 1])
        pos = end


def drop_unsafe_macros(text: str) -> tuple[str, list[str]]:
    """Remove definitions pandoc would loop on (self-referential) or that hide structure.

    TeX never expands ``\\small`` inside ``\\@setfontsize\\small``; pandoc does, forever.
    """
    parts: list[str] = []
    dropped: list[str] = []
    pos = 0
    for d in macro_definitions(text):
        if d.recursive or d.name in STRUCTURAL:
            parts.append(text[pos : d.start])
            dropped.append(d.name)
            pos = d.end
    parts.append(text[pos:])
    return "".join(parts), dropped


_EQUATION_WITHIN = re.compile(r"\\(?:numberwithin|counterwithin)\*?\s*\{equation\}\s*\{[a-z]+\}")


def package_macros(sty: str) -> str:
    """The notation macros of a local ``.sty``: what pandoc needs, nothing it chokes on.

    Layout machinery (``@`` internals, ``\\renewcommand`` of LaTeX commands) is skipped:
    pandoc cannot typeset anyway, and those definitions are the ones that hang it.
    """
    keep = [
        sty[d.start : d.end]
        for d in macro_definitions(sty)
        if d.kind != "renewcommand"
        and "@" not in d.name
        and "@" not in sty[d.start : d.end]
        and d.name not in STRUCTURAL
        and not d.recursive
    ]
    keep += [m.group() for m in _NEWTHEOREM.finditer(sty)]
    keep += [m.group() for m in _EQUATION_WITHIN.finditer(sty)]  # their numbers, for the count
    return "\n".join(keep)


# pandoc 3.9 reads a citation key of letters (any script), digits and ``_:.+/-``; an empty key
# (``\cite{a,,b}``) or a ``$``, a curly quote, a non-breaking hyphen or a brace in one stops
# the run.
_KEY_OTHER = re.compile(r"[^\w:.+/\-]")
_CITE_COMMAND = re.compile(
    r"(\\(?:[cC]ite[a-zA-Z]*|[pP]arencite|[tT]extcite|[aA]utocite|[fF]ootcite|nocite)\*?"
    r"\s*(?:\[[^\]]{0,200}\]\s*){0,2})\{"
)
# What precedes a citation command that is being defined, as in ``\edef\citenum{...}``.
_DEFINING = re.compile(r"(?:\\[a-z]*def|\\let|command\*?\s*\{?|\\Declare[A-Za-z]*\s*\{?)\s*$")
_CITE_LOOKBEHIND = 30  # chars before a command in which to look for its definition


_KEY_COMMENT = re.compile(r"(?<!\\)%[^\n]*")  # a comment line inside the argument


def clean_citation_keys(keys: str) -> str:
    """A comma-separated key list that pandoc reads: empty keys dropped, braces removed,
    each other character it does not accept written as ``_x<hex code>_`` (``a$b`` is
    ``a_x24_b``), so two keys that differ in such a character stay different. A ``%``
    comment inside the list is dropped, as TeX and pandoc do."""
    keys = _KEY_COMMENT.sub("", keys)
    cleaned = (
        _KEY_OTHER.sub(_escape_key_char, k.replace("{", "").replace("}", "").strip())
        for k in keys.split(",")
    )
    return ",".join(k for k in cleaned if k)


def _escape_key_char(match: re.Match[str]) -> str:
    return f"_x{ord(match.group()):x}_"


def sanitize_citation_keys(text: str) -> str:
    """Apply :func:`clean_citation_keys` to the argument of every citation command.

    A command with no key left disappears with its options. The definition of a citation
    macro (``\\edef\\citenum{...}``), a key that is a macro parameter (``\\cite{#1}``) and
    ``\\nocite{*}`` are not citations of one key and stay as they are.
    """
    parts: list[str] = []
    pos = 0
    pairs = brace_pairs(text)
    for m in _CITE_COMMAND.finditer(text):
        if m.start() < pos:
            continue
        start = max(0, m.start() - _CITE_LOOKBEHIND)
        if _DEFINING.search(text, start, m.start()):
            continue
        end = _matched(pairs, m.end() - 1)
        if end is None or "#" in text[m.end() : end] or text[m.end() : end - 1].strip() == "*":
            continue
        keys = clean_citation_keys(text[m.end() : end - 1])
        parts.append(text[pos : m.start()])
        if keys:
            parts.append(f"{m.group(1)}{{{keys}}}")
        pos = end
    parts.append(text[pos:])
    return "".join(parts)


_THEBIB_BEGIN = re.compile(r"\\begin\s*\{thebibliography\}\s*(?:\{[^{}]*\})?")
_THEBIB_END = re.compile(r"\\end\s*\{thebibliography\}")
_BIBITEM = re.compile(r"\\bibitem\s*(?:\[[^\]]*\])?\s*\{(?P<key>[^{}]*)\}")
# REVTeX writes its reference entries with \href {url}<newline>{text}, and \href@noop {} for
# an entry without URL; pandoc 3.9 then drops the whole list without an error.
_HREF_BREAK = re.compile(r"\\href\s*(\{[^{}]*\})\s*(?=\{)")
_HREF_NOOP = re.compile(r"\\href@noop\s*\{\}\s*")
# REVTeX's \providecommand \Eprint [0]{\href }: the arguments of \href come after the call.
_EPRINT = re.compile(r"\\providecommand\s*\{?\\Eprint\}?\s*(?:\[0\])?\s*\{\\href\s*\}")


# What rewrite_bibliography writes around the entries of a list; bibliography_blocks finds it again.
_BIB_OPEN = "\\section*{References}\n\\begin{enumerate}\n"
_BIB_CLOSE = "\n\\end{enumerate}\n"
_ITEM = re.compile(r"\\item \\cite\{")  # what _bibitem writes for an entry with a key


def _bibitem(match: re.Match[str]) -> str:
    keys = clean_citation_keys(match.group("key"))
    return f"\\item \\cite{{{keys}}} " if keys else "\\item "


def rewrite_bibliography(text: str) -> str:
    """``thebibliography`` -> a "References" section with one list item per entry.

    pandoc prints the widest-label argument as text, drops ``\\newblock {\\em ...}``
    groups (the venue) and emits no heading; a plain enumerate avoids all three. Each
    item starts with its key as a citation, so ``[@key]`` in the text can be matched; the
    key goes through the same clean-up as the citations in the text.

    REVTeX's ``\\href`` forms that make pandoc drop the whole list are rewritten in the
    entries and in the preamble's ``\\Eprint``.
    """

    def _rewrite(body: str) -> str:
        body = body.replace("\\newblock", " ")
        first = _BIBITEM.search(body)
        if first is None:
            return ""
        # natbib's preamble (\providecommand{\url}..., \expandafter\ifx\csname urlstyle...)
        # only styles the list; keep its helper macros, not \url/\doi overrides or junk.
        preamble = body[: first.start()]
        head = "\n".join(
            preamble[d.start : d.end]
            for d in macro_definitions(preamble)
            if d.name not in ("url", "doi", "href", "urlprefix")
        )
        head = shim_at_macros(head, opaque=True)
        head = _EPRINT.sub(r"\\providecommand{\\Eprint}[2]{\\href{#1}{#2}}", head)
        items = _BIBITEM.sub(_bibitem, body[first.start() :])
        items = _HREF_BREAK.sub(r"\\href\1", _HREF_NOOP.sub("", items))
        return f"{head}\n{_BIB_OPEN}{items}{_BIB_CLOSE}"

    return sub_regions(text, _THEBIB_BEGIN, _THEBIB_END, _rewrite)


def bibliography_blocks(text: str) -> list[str]:
    """The reference lists that :func:`rewrite_bibliography` wrote into ``text``."""
    blocks: list[str] = []
    pos = 0
    while (begin := text.find(_BIB_OPEN, pos)) != -1:
        end = text.find(_BIB_CLOSE, begin + len(_BIB_OPEN))
        if end == -1:
            break
        pos = end + len(_BIB_CLOSE)
        blocks.append(text[begin:pos])
    return blocks


def count_bibliography_entries(text: str) -> int:
    """How many reference entries with a key the lists in ``text`` hold (see
    :func:`bibliography_blocks`); an entry without a key is not counted."""
    return sum(len(_ITEM.findall(b)) for b in bibliography_blocks(text))


# What an @-internal stands for in a macro whose whole body it is: (arguments, body).
_AT_PLAIN = {
    "\\@firstofone": (1, "#1"),
    "\\@gobble": (1, ""),
    "\\@gobbletwo": (2, ""),
    "\\@firstoftwo": (2, "#1"),
    "\\@secondoftwo": (2, "#2"),
}
_AT_OPAQUE = re.compile(rf"\\(?:csname|catcode|spacefactor){_NOT_LETTER}")
_CSNAME = re.compile(rf"\\csname{_NOT_LETTER}")
_ENDCSNAME = re.compile(rf"\\endcsname{_NOT_LETTER}")
_TYPEOUT = re.compile(rf"\\typeout{_NOT_LETTER}\s*(?=\{{)")
_PARAMETER = re.compile(r"#\d")


def shim_at_macros(text: str, *, opaque: bool = False) -> str:
    """Make the ``@`` helper macros of a REVTeX or natbib ``.bbl`` preamble harmless.

    pandoc cannot read a definition of an ``@`` name (``\\providecommand \\@ifxundefined
    [1]{...}``): it reads ``\\@`` and stops at ``#1``. Such definitions are dropped. A
    macro whose whole body is one ``@`` internal (``\\bibinfo [0]{\\@secondoftwo}``) gets the
    plain definition that does the same. Any other body that uses an ``@`` stays: dropping
    those lost tables and headings.

    With ``opaque`` (the preamble of a bibliography, where :func:`rewrite_bibliography` uses
    it) a macro built on ``\\csname``, ``\\catcode`` or ``\\spacefactor``
    (``\\BibitemShut``) also gets a body that pandoc can read: empty, or just the arguments
    it passes on outside ``\\csname`` and ``\\typeout`` (IEEEtran's ``\\BIBforeignlanguage``
    prints its second argument, the title).
    """
    parts: list[str] = []
    pos = 0
    for d in macro_definitions(text):
        body = " ".join(d.body.split())
        if "@" in d.name:
            replacement = ""
        elif body in _AT_PLAIN:
            replacement = _plain_definition(text, d, *_AT_PLAIN[body])
        elif opaque and _AT_OPAQUE.search(body):
            replacement = _plain_definition(text, d, _arguments(text, d), _passed_on(d.body))
        else:
            continue
        parts += (text[pos : d.start], replacement)
        pos = d.end
    parts.append(text[pos:])
    return "".join(parts)


def _passed_on(body: str) -> str:
    """The macro parameters of ``body`` that are used outside ``\\csname...\\endcsname`` and
    ``\\typeout{...}``, in order of first use, as the body that keeps them."""
    body = sub_regions(body, _CSNAME, _ENDCSNAME, lambda _: " ")
    pairs = brace_pairs(body)
    parts: list[str] = []
    pos = 0
    for m in _TYPEOUT.finditer(body):
        end = _matched(pairs, m.end())
        if m.start() < pos or end is None:
            continue
        parts.append(body[pos : m.start()])
        pos = end
    parts.append(body[pos:])
    return "".join(dict.fromkeys(_PARAMETER.findall("".join(parts))))


def _arguments(text: str, d: MacroDef) -> int:
    """How many arguments the definition ``d`` declares (``[2]`` or ``#1#2``)."""
    head = text[d.start : d.end - len(d.body) - 2]  # up to the brace that opens the body
    if declared := re.search(r"\[(\d)\]", head):
        return int(declared[1])
    return len(_PARAMETER.findall(head))


def _plain_definition(text: str, d: MacroDef, arguments: int, body: str) -> str:
    count = f"[{arguments}]" if arguments else ""
    return f"\\providecommand{{\\{d.name}}}{count}{{{body}}}"


_NEWCOLUMNTYPE = re.compile(r"\\newcolumntype\s*\{[^{}]*\}\s*(?:\[\d\]\s*)?(?=\{)")


def _drop_column_types(text: str) -> str:
    """``\\newcolumntype{X}[n]{spec}`` removed: pandoc reads a tabular without it."""
    parts: list[str] = []
    pos = 0
    pairs = brace_pairs(text)
    for m in _NEWCOLUMNTYPE.finditer(text):
        if m.start() < pos:
            continue
        end = _matched(pairs, m.end())
        if end is None:
            continue
        parts.append(text[pos : m.start()])
        pos = end
    parts.append(text[pos:])
    return "".join(parts)


_LABEL_OPTION = re.compile(r"\\label\s*\[[^\]]{0,100}\]\s*\{")  # cleveref's \label[type]{key}
# LyX writes \global\long\def\ket#1{...}; \def does the same for pandoc.
_DEF_PREFIX = re.compile(
    rf"(?:\\(?:global|long|protected){_NOT_LETTER}\s*){{1,4}}\\def{_NOT_LETTER}"
)
# pandoc reads \gdef, and \edef or \xdef without parameters, with the right meaning; it stops
# at \edef\cs#1{...}, where \def is the closest form.
_DEF_PARAMETERS = re.compile(rf"\\[ex]def{_NOT_LETTER}(?=\s*\\[a-zA-Z@]+\s*#)")
_GLUE = r"\s*plus\s*[\d.]+\s*\w+"
_VSKIP = re.compile(
    rf"\\vskip\s*(?:\\[a-zA-Z]+|-?[\d.]+\s*(?:pt|mm|cm|in|em|ex|bp|pc|sp|dd|cc)(?![a-zA-Z]))(?:{_GLUE})?(?:\s*minus\s*[\d.]+\s*\w+)?"
)


def simplify_layout(text: str) -> str:
    """The layout and definition forms pandoc stops at, in their plain equivalent.

    cleveref's ``\\label[type]{key}`` loses its option; LyX's ``\\global\\long\\def`` (up to
    four prefixes) and an ``\\edef``/``\\xdef`` with parameters become ``\\def``;
    ``\\newcolumntype`` definitions,
    ``\\raggedright`` and ``\\vskip <glue>`` go; IEEEtran's ``\\linebreakand`` is ``\\and``.
    """
    text = _LABEL_OPTION.sub(r"\\label{", text)
    text = _DEF_PARAMETERS.sub(r"\\def", _DEF_PREFIX.sub(r"\\def", text))
    text = _drop_column_types(text)
    text = re.sub(rf"\\linebreakand{_NOT_LETTER}", r"\\and", text)
    text = re.sub(rf"\\raggedright{_NOT_LETTER}", " ", text)
    return _VSKIP.sub(" ", text)


# --------------------------------------------------------- quantum circuits
# pandoc has no reader for the environments that draw a quantum circuit. With raw TeX off it
# drops the commands inside them (``\gate{H}``, ``\ctrl{1}``, ``\qw``) and keeps the column
# separators, so a circuit came out as ``& & &\`` or as nothing, and inside ``\resizebox``,
# ``\scalebox`` or a TikZ node it vanished. A circuit is source code, and its gates and wires
# are what a reader needs, so each one is handed to pandoc as a ``minted`` listing in LaTeX
# (a fenced code block in the Markdown) holding its original text. A circuit in math is left
# alone: pandoc copies math verbatim.
CIRCUIT_MAX = 200_000  # chars; a longer "circuit" is an environment or group that never closed
CIRCUIT_LANGUAGE = "latex"  # the language of the code block, so of the ``minted`` listing


def _env_pattern(*names: str) -> re.Pattern[str]:
    return re.compile(rf"\\(?P<kind>begin|end)\s*\{{(?P<env>{'|'.join(names)})\}}")


_ANY_CIRCUIT = re.compile(r"quantikz|\\Qcircuit|yquant|tikzcd")  # the cheap test before the scans
_DRAWN_ENVS = _env_pattern("quantikz", r"yquant\*?")
_TIKZCD = _env_pattern("tikzcd")
_LISTING_HEAD = re.compile(
    rf"\\begin\s*\{{minted\}}\{{{CIRCUIT_LANGUAGE}\}}\n"
)  # as protect_circuits writes it
_LISTING_TAIL = re.compile(r"\n\\end\s*\{minted\}\Z")
_BLANK_LINE = re.compile(r"\n[ \t]*\n")  # an argument TeX reads cannot hold one: a brace is missing
_QCIRCUIT = re.compile(r"\\Qcircuit(?![a-zA-Z])[^{}$&\\]{0,200}\{")  # \Qcircuit @C=1em { ... }
# What only a circuit has. A tikzcd with none of them is a commutative diagram, not touched.
_CIRCUIT_COMMAND = re.compile(
    r"\\(?:gate|ctrl|octrl|targ|qw|qwbundle|meter|measuretab|lstick|rstick|control|ocontrol"
    r"|swap|targX|phase|slice|wireoverride|setwiretype|multigate|ghost|measureD|cw)"
    r"(?![a-zA-Z@])"
)
_TIKZPICTURE = _env_pattern("tikzpicture")
_ADJUSTBOX = _env_pattern("adjustbox")
_TABULAR = _env_pattern(r"tabular\*?", "tabularx", "tabulary", "longtable", "tabu", "supertabular")
_MATH_ENV = (
    r"(?:equation|align|gather|multline|eqnarray|flalign|alignat|displaymath|math|dmath"
    r"|IEEEeqnarray)\*?"
)
_MATH_TOKEN = re.compile(
    r"(?P<blank>\n[ \t]*\n)"  # TeX forbids a blank line in math: whatever opened it is over
    rf"|\\begin\s*\{{(?P<begin>{_MATH_ENV})\}}"
    rf"|\\end\s*\{{(?P<end>{_MATH_ENV})\}}"
    r"|(?P<open>\\\[|\\\()"
    r"|(?P<close>\\\]|\\\))"
    r"|\\."  # an escaped character: \$, \\, \%
    r"|(?P<brace>[{}])"
    r"|(?P<dollars>\$\$?)"
)
_MATH_CLOSER = {"\\[": "\\]", "\\(": "\\)"}
# A block (the listing) in the argument of a command stops pandoc 3.9 (``\\centerline{...}``,
# ``\\mbox{...}``), and some commands make it drop their argument whole (the text in a
# ``\\resizebox``) or print their options. So the commands around a circuit are taken off it,
# keeping their content, unless the content would lose its meaning there (a caption).
_KEPT = frozenset(
    {
        *("caption", "captionof", "footnote", "footnotetext", "thanks", "title", "author", "date"),
        *("part", "chapter", "section", "subsection", "subsubsection", "paragraph", "item"),
    }
)
# What pandoc empties: a circuit in math, which it would copy, vanishes with these.
_DROPPING = frozenset(
    {
        *("resizebox", "scalebox", "raisebox", "rotatebox", "adjustbox", "makebox", "framebox"),
        *("fbox", "subfloat", "subfigure", "subcaptionbox"),
    }
)
_CAPTIONED = frozenset({"subfloat", "subfigure", "subcaptionbox"})  # their caption stays
_LINKED = "href"  # its URL stays too, after the block
_HEAD_MAX = 400  # chars before a brace group in which the command it belongs to is looked for
_DEPTH = 8  # groups around a circuit that are looked at
_CONTROL_WORD = re.compile(r"\\([a-zA-Z]+)\*?\Z")


@dataclass(frozen=True)
class _Env:
    start: int  # of ``\begin``
    inner: int  # just past ``\begin{name}``
    close: int  # of ``\end``
    end: int  # just past ``\end{name}``


def _environments(text: str, pattern: re.Pattern[str]) -> list[_Env]:
    """Every ``\\begin{name} ... \\end{name}`` pair that ``pattern`` finds (groups ``kind``
    and ``env``), nested ones too, by start. One pass: a ``\\begin`` without its ``\\end``
    is not a pair and costs nothing."""
    opened: dict[str, list[re.Match[str]]] = {}
    found: list[tuple[re.Match[str], re.Match[str]]] = []
    for m in pattern.finditer(text):
        if m["kind"] == "begin":
            opened.setdefault(m["env"], []).append(m)
        elif opened.get(m["env"]):
            found.append((opened[m["env"]].pop(), m))
    found.sort(key=lambda pair: pair[0].start())
    return [_Env(b.start(), b.end(), e.start(), e.end()) for b, e in found]


class _Covered:
    """Spans of a text, asked whether a position or a span lies in one of them. Spans that
    overlap (nested ones) count as the outermost."""

    def __init__(self, spans: Iterable[tuple[int, int]]):
        self.starts: list[int] = []
        self.ends: list[int] = []
        for a, b in sorted(spans):
            if self.ends and a < self.ends[-1]:
                self.ends[-1] = max(self.ends[-1], b)
            else:
                self.starts.append(a)
                self.ends.append(b)

    def containing(self, a: int, b: int) -> tuple[int, int] | None:
        """The span that holds ``[a, b)``, if one does."""
        i = bisect_right(self.starts, a) - 1
        return (self.starts[i], self.ends[i]) if i >= 0 and b <= self.ends[i] else None

    def __contains__(self, pos: int) -> bool:
        i = bisect_right(self.starts, pos) - 1
        return i >= 0 and pos < self.ends[i]


def _math_spans(text: str, start: int) -> Iterator[tuple[int, int]]:
    """The spans of ``text[start:]`` that are math: ``$..$``, ``$$..$$``, ``\\[..\\]``,
    ``\\(..\\)`` and the display environments. Verbatim text is not scanned. A blank line ends
    what is open, as in TeX, so a stray ``$`` does not turn the rest of the paper into math. A
    ``$`` inside braces within ``$..$`` is a box of its own (``\\lstick{$q_0$}`` in a circuit)."""
    for a, b in _outside_verbatim(text, start):
        mode: str | None = None  # what is open: "$", "$$", "\\[", "\\(" or an environment's name
        opened = depth = 0
        for m in _MATH_TOKEN.finditer(text, a, b):
            if mode is None:
                if m.lastgroup in ("dollars", "open"):
                    mode, opened, depth = m[0], m.start(), 0
                elif m.lastgroup == "begin":
                    mode, opened, depth = m["begin"], m.start(), 0
            elif m.lastgroup == "blank":
                yield opened, m.start()
                mode = None
            elif m.lastgroup == "brace":
                depth = max(depth + (1 if m[0] == "{" else -1), 0)
            elif (depth == 0 or m.lastgroup != "dollars") and _closes(mode, m):
                reopen = mode == "$" and m[0] == "$$"  # adjacent inline maths: $a$$b$
                end = m.start() + 1 if reopen else m.end()
                yield opened, end
                mode, opened = ("$", end) if reopen else (None, opened)
        if mode is not None:
            yield opened, b


def _closes(mode: str, m: re.Match[str]) -> bool:
    if m.lastgroup == "dollars":
        return mode == "$" or (mode == "$$" and m[0] == "$$")
    if m.lastgroup == "close":
        return m[0] == _MATH_CLOSER.get(mode)
    return m.lastgroup == "end" and m["end"] == mode


def _skipped(text: str) -> tuple[_Covered, _Covered]:
    """Where a circuit is left as it is: the preamble, verbatim text, a macro definition and
    an ``\\iffalse`` block (nothing of it is typeset), and, apart, math."""
    begin = re.search(r"\\begin\s*\{document\}", text)
    body = begin.end() if begin else 0
    spans = [(0, body)] if body else []
    spans += ((v.start, v.end) for v in verbatim_parts(text))
    spans += ((d.start, d.end) for d in macro_definitions(text))
    spans += ((b.start(), e.end()) for b, e in regions(text, IFFALSE, FI))
    return _Covered(spans), _Covered(_math_spans(text, body))


def _any_in(positions: list[int], start: int, end: int) -> bool:
    """Whether the sorted ``positions`` have one in ``[start, end)``."""
    i = bisect_left(positions, start)
    return i < len(positions) and positions[i] < end


def circuit_spans(text: str) -> list[tuple[int, int]]:
    """``(start, end)`` of each quantum circuit in ``text`` that :func:`protect_circuits`
    rewrites, in order and not overlapping: a ``quantikz`` or ``yquant`` environment, a
    ``\\Qcircuit`` group, a ``tikzcd`` with circuit commands in it. Where a circuit sits inside a
    ``tikzpicture`` the span is the whole picture: that is what holds it together, and what
    pandoc drops. Nothing in math, verbatim text, a macro definition or the preamble
    (:func:`_skipped`)."""
    return _circuits(text)[0]


def _circuits(text: str) -> tuple[list[tuple[int, int]], list[int], _Covered]:
    """The spans of :func:`circuit_spans`, the starts of the circuits that sit in math (left
    alone), and the math of ``text``."""
    if not _ANY_CIRCUIT.search(text):
        return [], [], _Covered([])
    pairs = brace_pairs(text)
    commands = [m.start() for m in _CIRCUIT_COMMAND.finditer(text)]
    found = [(e.start, e.end) for e in _environments(text, _DRAWN_ENVS)]
    found += (
        (e.start, e.end) for e in _environments(text, _TIKZCD) if _any_in(commands, e.start, e.end)
    )
    found += (
        (m.start(), end)
        for m in _QCIRCUIT.finditer(text)
        if (end := pairs.get(m.end() - 1)) and not _BLANK_LINE.search(text, m.start(), end)
    )
    skipped, math = _skipped(text)
    pictures = _Covered(
        (e.start, e.end)
        for e in _environments(text, _TIKZPICTURE)
        if e.end - e.start <= CIRCUIT_MAX
    )
    spans: list[tuple[int, int]] = []
    in_math: list[int] = []
    last = 0
    for a, b in sorted(found):
        if a < last or b - a > CIRCUIT_MAX or a in skipped:
            continue
        last = b
        if a in math:
            in_math.append(a)
            continue
        span = pictures.containing(a, b) or (a, b)
        if spans and span[0] < spans[-1][1]:  # crossed environments, or a picture already taken
            continue
        spans.append(span)
        last = span[1]
    kept = _Covered(spans)
    return spans, [a for a in in_math if a not in kept], math


def _enclosing(pairs: dict[int, int], positions: list[int]) -> dict[int, list[int]]:
    """For each of ``positions``, the opening braces of the groups around it, innermost first
    (``_DEPTH`` of them), found in one sweep."""
    opens = sorted(pairs)
    found: dict[int, list[int]] = {}
    stack: list[int] = []
    i = 0
    for pos in sorted(set(positions)):
        while i < len(opens) and opens[i] < pos:
            while stack and pairs[stack[-1]] <= opens[i]:  # closed before this one opens
                stack.pop()
            stack.append(opens[i])
            i += 1
        while stack and pairs[stack[-1]] <= pos:
            stack.pop()
        found[pos] = stack[: -_DEPTH - 1 : -1]
    return found


def _head(text: str, i: int) -> tuple[str, int, str, list[tuple[str, str]]]:
    """What the brace group that opens at ``text[i]`` belongs to: ``("command", start, name,
    arguments)`` when it follows a control word and the ``[options]`` and ``{arguments}`` that
    come before it (``\\resizebox{a}{b}{``), ``("bare", ...)`` when it follows plain text, and
    ``("unknown", ...)`` when those arguments hold groups too deep to read. Spaces are skipped
    right after a control word only (``\\centerline {``): a group after a space that follows an
    argument or text (``\\textbf{Note.} {``), a line break or ``\\begin{env}`` is a group of its
    own."""
    limit = max(0, i - _HEAD_MAX)
    j = i
    arguments: list[tuple[str, str]] = []
    while True:
        if j > limit and text[j - 1] in "}]":
            opener = "{" if text[j - 1] == "}" else "["
            o = text.rfind(opener, limit, j - 1)
            inner = text[o + 1 : j - 1] if o >= 0 else "{"
            if any(c in inner for c in "{}[]"):
                return "unknown", 0, "", []
            arguments.append((opener, inner))
            j = o
            continue
        k = j
        while k > limit and text[k - 1] in " \t":  # TeX skips the spaces after a control word
            k -= 1
        if (word := _CONTROL_WORD.search(text, limit, k)) is None:
            return ("unknown" if arguments else "bare"), 0, "", []
        if word[1] in ("begin", "end"):
            return "bare", 0, "", []
        return "command", word.start(), word[1], arguments[::-1]


def _circuit_block(source: str) -> str:
    return f"\n\\begin{{minted}}{{{CIRCUIT_LANGUAGE}}}\n{source}\n\\end{{minted}}\n"


def protect_circuits(text: str) -> str:
    """``text`` with each quantum circuit (see :func:`circuit_spans`) written as a ``minted``
    listing in LaTeX, which pandoc turns into a fenced code block with the circuit's own
    source, where the circuit was. A circuit in the cell of a table, which cannot hold a code
    block, goes after the table. The commands around a circuit are taken off it (their name and
    arguments go, their content stays): ``\\resizebox``, ``\\scalebox``, ``\\mbox``,
    ``\\centerline`` and the like, which pandoc empties or stops at. A subfigure's caption stays
    as a paragraph after its circuit. A circuit whose surroundings cannot be read (inside a
    caption, or in an argument too deep to parse) is left as it is."""
    spans, in_math, math = _circuits(text)
    if not spans and not in_math:
        return text
    pairs = brace_pairs(text)
    tables = _Covered((e.start, e.end) for e in _environments(text, _TABULAR))
    anchors = [(t[1] - 1 if (t := tables.containing(a, b)) else a) for a, b in spans]
    around = _enclosing(pairs, [*anchors, *in_math])
    edits: list[tuple[int, int, str]] = []
    behind: dict[int, list[str]] = {}  # table end -> the circuits that were in its cells
    taken: dict[int, tuple[int, str, list[tuple[str, str]]]] = {}  # brace -> its command
    kept: list[int] = []  # the starts of the circuits that are rewritten
    for (a, b), anchor in zip(spans, anchors, strict=True):
        heads = [(o, *_head(text, o)) for o in around[anchor]]
        if any(kind == "unknown" or name in _KEPT for _, kind, _, name, _ in heads):
            continue
        taken.update(
            {o: (start, name, args) for o, kind, start, name, args in heads if kind == "command"}
        )
        kept.append(a)
        block = _circuit_block(text[a:b])
        if table := tables.containing(a, b):  # in a cell pandoc would write "[TABLE]" for the table
            edits.append((a, b, ""))
            behind.setdefault(table[1], []).append(block)
        else:
            edits.append((a, b, block))
    edits += ((end, end, "".join(blocks)) for end, blocks in behind.items())
    for a in in_math:  # math that sits in a box pandoc empties: the box goes, the math stays
        for o in around[a]:
            kind, start, name, args = _head(text, o)
            if kind == "command" and name in _DROPPING and o not in taken and start not in math:
                taken[o] = (start, name, args)
    for o, (start, name, args) in taken.items():
        edits += [(start, o + 1, ""), (pairs[o] - 1, pairs[o], _caption(name, args))]
    starts = sorted([*kept, *in_math])
    for env in _environments(text, _ADJUSTBOX):
        if _any_in(starts, env.inner, env.close):
            edits += [
                (env.start, _skip_arguments(text, env.inner, pairs), ""),
                (env.close, env.end, ""),
            ]
    parts: list[str] = []
    pos = 0
    for a, b, replacement in sorted(edits):
        parts += (text[pos:a], replacement)
        pos = b
    parts.append(text[pos:])
    return "".join(parts)


def _caption(name: str, arguments: list[tuple[str, str]]) -> str:
    """What follows a command that is taken off and has a text of its own: a subfigure's
    caption, or the URL of a link, as a paragraph."""
    if name == _LINKED:
        url = next((c for opener, c in arguments if opener == "{"), "").strip()
        return f"\n\n\\url{{{url}}}\n\n" if url else ""
    if name not in _CAPTIONED:
        return ""
    chosen = [c for opener, c in arguments if opener == ("{" if name == "subcaptionbox" else "[")]
    caption = (chosen[0] if name == "subcaptionbox" else chosen[-1]).strip() if chosen else ""
    return f"\n\n{caption}\n\n" if caption else ""


def _skip_arguments(text: str, i: int, pairs: dict[int, int]) -> int:
    """The index after the ``{options}`` of ``\\begin{adjustbox}`` that ends at ``i``."""
    while i < len(text) and text[i] in " \t\n":
        i += 1
    return pairs.get(i, i) if i < len(text) and text[i] == "{" else i


def _hide_listings(text: str) -> tuple[str, Callable[[str], str]]:
    """``text`` with the body of each circuit listing (see :func:`protect_circuits`) replaced
    by a word, and the function that puts the bodies back. The rewrites that follow
    (``\\vskip``, ``\\today``, citation keys, ``\\label[type]``, ...) read the whole text and
    must not change a circuit: its block is the author's source."""
    if "minted" not in text:
        return text, lambda out: out
    word = "DOCINGESTLISTING"
    while word in text:
        word += "X"
    bodies: list[str] = []
    parts: list[str] = []
    pos = 0
    for v in verbatim_parts(text):
        listing = text[v.start : v.end]
        head = _LISTING_HEAD.match(listing) if v.env == "minted" else None
        tail = _LISTING_TAIL.search(listing, head.end()) if head else None
        if head is None or tail is None:
            continue
        parts += (text[pos : v.start + head.end()], f"{word}{len(bodies)}END")
        bodies.append(listing[head.end() : tail.start()])
        pos = v.start + tail.start()
    parts.append(text[pos:])

    def restore(out: str) -> str:
        return re.sub(rf"{word}(\d+)END", lambda m: bodies[int(m[1])], out)

    return "".join(parts), restore


# ---------------------------------------------------------- equation numbers
# pandoc keeps a display equation as math but not the number LaTeX gives it, and it writes a
# reference to one (``\eqref{x}``) as a link whose text is the label. A reader asking what
# "Eq. (7)" states then finds neither the equation nor the number. So the numbers are counted
# here the way LaTeX counts them and written into the math as ``\tag{n}``, which pandoc (and
# pylatexenc, with the math verbatim) copy as they copy the rest of the math, and a reference
# to a numbered equation becomes its number. Whatever makes the count uncertain ends the
# numbering at that point: an equation is left without a number rather than given a wrong one.
_NUMBERED = frozenset(
    {"equation", "multline", "align", "alignat", "xalignat", "flalign", "gather", "eqnarray"}
)
_ONE_NUMBER = frozenset({"equation", "multline"})  # one number for the whole environment
# Numbered environments of packages whose rules are not modelled here.
_UNMODELLED = frozenset(
    {"dmath", "dgroup", "dseries", "IEEEeqnarray", "empheq", "numcases", "subnumcases"}
)
_HARD_ENVS = _NUMBERED | _UNMODELLED | {"subequations", "appendix", "appendices"}
_BELOW = {"chapter": ("section", "subsection"), "section": ("subsection",), "subsection": ()}
_BOOK_CLASSES = frozenset({"book", "report", "amsbook", "scrbook", "scrreprt"})
_ARTICLE_CLASSES = frozenset(
    {
        "article",
        "amsart",
        "amsproc",
        "scrartcl",
        "quantumarticle",
        "iopjournal",
        "ieeeaccess",
        "wlscirep",
    }
)
_CLASS = re.compile(
    r"\\documentclass\s*(?:\[(?P<options>[^\]]{0,300})\])?\s*\{(?P<name>[^{}]{0,100})\}"
)
_CHAPTER = re.compile(r"\\chapter\*?(?![A-Za-z@])(?=\s*(?:\[[^\]]{0,300}\]\s*)?\{)")
_FORMATTED = frozenset({"theequation", "thesection", "thesubsection", "thechapter"})

# How a counter is shown: ``\theequation`` and its relatives, as a list of ("text", s),
# ("counter", name, style) and ("the", name).
_Format = list[tuple[str, ...]]
_FORMAT_TOKEN = re.compile(
    r"\\(?P<style>arabic|Alph|alph|roman|Roman)\s*\{(?P<counter>[A-Za-z]+)\}"
    r"|\\@(?P<atstyle>arabic|Alph|alph|roman|Roman)\s*\\c@(?P<atcounter>[A-Za-z]+)"
    r"|\\the(?P<the>equation|section|subsection|chapter)(?![A-Za-z@])"
    r"|(?P<text>[A-Za-z0-9.:()\- ]+)"
    r"|(?P<bad>[\s\S])"
)
_ROMAN = (
    (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
    (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
)  # fmt: skip
_SIMPLE_TAG = re.compile(r"[A-Za-z0-9.:+*'\-]+")  # a ``\tag`` text that is a number as it stands
_NUMBER_SHOWN = re.compile(r"[A-Za-z0-9.\-]+")  # what is written into a ``\tag``
_EQ_INTERESTING = re.compile(
    r"\\(?:begin|end)\s*\{(?P<env>[A-Za-z@]+\*?)\}|\\(?P<word>nonumber|notag|tag)(?![A-Za-z@])"
    r"|(?P<rowend>\\\\)"
)
_LET = re.compile(r"\\let\s*\\[A-Za-z@]+\s*=?\s*\\[A-Za-z@]+")
_LET_ALIAS = re.compile(
    r"\\let\s*\\(?P<name>[A-Za-z@]+)\s*=?\s*\\(?P<end>end)?(?P<env>[A-Za-z]+)(?![A-Za-z@*])"
)
_NEWENVIRONMENT = re.compile(
    r"\\(?P<kind>re)?newenvironment\*?\s*\{(?P<name>[^{}]*)\}\s*(?:\[\d\]\s*(?:\[[^\]]*\]\s*)?)?"
)
_BEGIN_ONLY = re.compile(r"\\begin\s*\{(?P<env>[A-Za-z@]+\*?)\}")
_END_ONLY = re.compile(r"\\end\s*\{(?P<env>[A-Za-z@]+\*?)\}")
_SHARED_THEOREM = re.compile(r"\\newtheorem\s*\{(?P<name>[^{}]*)\}\s*\[(?P<counter>[^\]]{0,100})\]")
_TWO_ARGUMENTS = re.compile(r"\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_ONE_ARGUMENT = re.compile(r"\s*\{([^{}]*)\}")
_INTEGER = re.compile(r"\s*-?\d+\s*\Z")
# What a macro does to the counters when it is used: sections, the appendix, counter commands and a
# new form of the number of an equation or a section.
_EFFECT = re.compile(
    r"\\(?:chapter|section|subsection|appendix)(?![A-Za-z@])"
    r"|\\(?:setcounter|addtocounter|stepcounter|refstepcounter|numberwithin|counterwithin"
    r"|@addtoreset)(?![A-Za-z@])"
    r"|\\(?:(?:re)?newcommand\*?\s*\{?|[gex]?def)\\the(?:equation|section|subsection|chapter)"
)
# Packages and options that number only the equations that are referred to, or number lines
# in a way that is not modelled.
_NUMBERING_PACKAGES = re.compile(
    r"showonlyrefs(?!\s*=\s*false)"
    r"|\\usepackage\s*(?:\[[^\]]{0,300}\])?\s*\{[^{}]{0,300}\b(?:autonum|numcases)\b"
)
# A conditional: \ifx ... \fi, or a command that takes the branches as arguments. What one holds may
# or may not be typeset, which the count cannot know, so a counter that changes in one stops the
# numbering. A main document takes the branch B of ``\ifSubfilesClassLoaded{A}{B}``.
_CONDITIONAL = re.compile(
    r"(?P<newif>\\newif\s*\\if[A-Za-z@]+)"
    r"|(?P<fi>\\fi(?![A-Za-z@]))"
    r"|\\(?P<name>(?:@?if|If)[A-Za-z@]*)(?![A-Za-z@])"
)
_BRANCHES = {  # commands that take their branches as arguments: how many groups they read
    "ifthenelse": 3, "iftoggle": 3, "ifbool": 3, "ifdef": 3, "ifundef": 3, "ifcsdef": 3,
    "ifcsundef": 3, "ifstrequal": 4, "ifstrempty": 3, "ifdefempty": 3, "ifdefstring": 4,
    "ifdefequal": 4, "ifSubfilesClassLoaded": 2, "IfFileExists": 3,
}  # fmt: skip
_NOT_CONDITIONAL = frozenset({"iff"})  # the arrow of math
_PRIMITIVE_CONDITIONALS = frozenset(
    {
        *("if", "ifcat", "ifnum", "ifdim", "ifodd", "ifvmode", "ifhmode", "ifmmode", "ifinner"),
        *("ifvoid", "ifhbox", "ifvbox", "ifx", "ifeof", "iftrue", "iffalse", "ifcase"),
        *("ifdefined", "ifcsname", "iffontchar"),
    }
)  # the others are declared with \newif (a name of the paper's own, or one of a package)
_ELSE_OR_FI = re.compile(r"\\(?:else|fi)(?![A-Za-z@])")
_COUNTERS_OF_THE_NUMBERING = frozenset({"equation", "chapter", "section", "subsection"})
_COUNTER_COMMAND = re.compile(r"\s*\{([^{}]*)\}")
_ROW_TAIL = re.compile(
    r"\A\s*\*?\s*(?:\[[^\]]{0,100}\])?"
)  # what follows ``\\`` before the next row
_COUNTING = frozenset({"begin", "section", "appendix", "counter", "macro", "matter"})
_ROWS = frozenset({"nonumber", "tag", "rowend"})  # what a conditional in an equation may change
_CLOSE_ROUNDS = 50  # rounds that look for the macros that use a macro that steps a counter
_NESTING_MAX = 4  # ``\thesubsection`` names ``\thesection``, which names ``\thechapter``


def _parse_format(body: str) -> _Format | None:
    """The parts of a ``\\theequation``-like definition, or None when it uses anything but
    counters, ``\\the<counter>`` and plain text."""
    parts: _Format = []
    for m in _FORMAT_TOKEN.finditer(body):
        if m["bad"] is not None:
            return None
        if style := m["style"] or m["atstyle"]:
            parts.append(("counter", m["counter"] or m["atcounter"], style))
        elif m["the"]:
            parts.append(("the", m["the"]))
        else:
            parts.append(("text", m["text"]))
    return parts or None


def _style(value: int, style: str) -> str | None:
    """A counter in one of the styles of ``\\arabic`` and the like. LaTeX prints nothing for
    ``\\Alph{0}`` or ``\\Alph{27}`` (it reports an error and goes on), and so do we."""
    if style == "arabic":
        return str(value)
    if style in ("Alph", "alph"):
        letter = chr(96 + value) if 1 <= value <= 26 else ""
        return letter.upper() if style == "Alph" else letter
    if not 1 <= value < 4000:
        return None
    out = ""
    for size, glyphs in _ROMAN:
        count, value = divmod(value, size)
        out += glyphs * count
    return out.upper() if style == "Roman" else out


@dataclass(frozen=True)
class EquationNumbers:
    """What :func:`number_equations` did: the new text, the numbers it wrote as ``\\tag`` in
    order (``empty``: the numbers of rows that have nothing to show, which it counted and did not
    write), why the numbering stopped (None: it did not), and how many ``\\ref`` and ``\\eqref``
    it saw and how many it replaced by a number."""

    text: str
    written: tuple[str, ...]
    stopped: str | None
    refs: int
    resolved: int
    empty: tuple[str, ...] = ()  # the numbers of empty rows: counted, nothing written


@dataclass
class _Row:
    start: int
    labels: list[str] = field(default_factory=list)
    tagged: bool = False  # a ``\tag`` of the author's
    tag: str | None = None  # its text when that is a number as it stands
    unnumbered: bool = False  # ``\nonumber`` or ``\notag``


@dataclass
class _Open:
    """The numbered environment being read: its rows (a row ends at a ``\\\\`` that is neither
    in braces nor in an environment of its own), its brace depth and its nested environments."""

    name: str
    row: _Row
    rows: list[tuple[_Row, int]] = field(default_factory=list)
    depth: int = 0
    inner: int = 0


@dataclass
class _Sub:
    prefix: str  # the number of the group: ``3`` in ``3a``
    parent: int  # the value of the equation counter to go back to
    at: int  # just past ``\begin{subequations}``
    seen: bool = False  # a label was met: a later one has text before it


@functools.cache
def _eq_token(names: tuple[str, ...]) -> re.Pattern[str]:
    """The tokens the numbering reads; ``names`` are the macros of the paper that stand for
    them (``\\be``) or hide them."""
    macros = (
        rf"|(?P<macro>\\(?:{'|'.join(re.escape(n) for n in names)})(?![A-Za-z@]))" if names else ""
    )
    return re.compile(
        r"(?P<begin>\\begin\s*\{(?P<benv>[A-Za-z@]+\*?)\})"
        r"|(?P<end>\\end\s*\{(?P<eenv>[A-Za-z@]+\*?)\})"
        r"|(?P<section>\\(?P<level>chapter|section|subsection)(?P<starred>\*?)(?![A-Za-z@])"
        r"(?=\s*(?:\[[^\]]{0,300}\]\s*)?\{))"  # a heading, not ``\titleformat{\section}``
        r"|(?P<appendix>\\appendix(?P<astar>\*?)(?![A-Za-z@]))"
        r"|(?P<matter>\\(?:front|main|back)matter(?![A-Za-z@]))"
        r"|(?P<counter>\\(?P<cmd>setcounter|addtocounter|stepcounter|refstepcounter|numberwithin"
        r"|counterwithin\*?|@addtoreset)(?![A-Za-z@]))"
        r"|(?P<label>\\label\s*\{(?P<key>[^{}]*)\})"
        r"|(?P<tag>\\tag(?P<tstar>\*?)\s*\{)"
        r"|(?P<nonumber>\\(?:nonumber|notag)(?![A-Za-z@]))"
        r"|(?P<ref>\\(?P<refcmd>eqref|ref)(?![A-Za-z@])\s*\{(?P<refkey>[^{}]*)\})"
        r"|(?P<rowend>\\\\)"
        rf"{macros}"
        r"|(?P<brace>[{}])"
        r"|\\[A-Za-z@]+|\\."
    )


class _Numberer:
    """One pass over a document, in order, with the counters LaTeX keeps."""

    def __init__(self, text: str):
        self.text = text
        self.stop: str | None = None
        self.n = {"chapter": 0, "section": 0, "subsection": 0, "equation": 0}
        self.parent: str | None = None  # the counter whose step resets the equation counter
        self.fmt: dict[str, _Format | None] = {
            "equation": [("counter", "equation", "arabic")],
            "section": [("counter", "section", "arabic")],
            "subsection": [("the", "section"), ("text", "."), ("counter", "subsection", "arabic")],
            "chapter": [("counter", "chapter", "arabic")],
        }
        self.family: str | None = None
        self.prl = False  # the REVTeX option of Physical Review Letters
        self.own_format = False  # the paper redefines \\theequation itself
        self.appendix_seen = False
        self.cur: _Open | None = None
        self.sub: _Sub | None = None
        self.aliases: dict[str, tuple[tuple[str, str], ...]] = {}  # macro -> what it stands for
        self.env_aliases: dict[str, str] = {}  # environment -> the one it begins
        self.hard: set[str] = set()  # macros that hide an environment of the numbering
        self.soft: set[str] = set()  # macros that hide a row end or a ``\nonumber``
        self.effects: dict[str, str] = {}  # macros that step a section or set a counter: their text
        self.nested = 0
        self.budget = len(text) + 10_000  # macro uses that are read, over the whole document
        self.effect_names: tuple[str, ...] = ()
        self.matter = True  # a book is in its main matter (chapters are numbered)
        self.conditional = _Covered([])
        self.hard_envs: set[str] = set()
        self.redefined: set[str] = set()  # ``\eqref`` and ``\ref`` when the paper defines them
        self.sharing: set[str] = set()  # theorem environments counted with the equations
        self.labels: dict[str, str] = {}
        self.label_count: Counter[str] = Counter()
        self.refs: list[tuple[int, int, str, str]] = []
        self.edits: list[tuple[int, int, str]] = []
        self.written: list[str] = []
        self.empty: list[str] = []
        self._pairs: dict[int, int] | None = None

    # -- the result
    def result(self) -> EquationNumbers:
        trusted = {k: v for k, v in self.labels.items() if self.label_count[k] == 1}
        replaced = 0
        for a, b, command, key in self.refs:
            if (number := trusted.get(key)) is not None:
                self.edits.append((a, b, f"({number})" if command == "eqref" else number))
                replaced += 1
        parts: list[str] = []
        pos = 0
        for a, b, new in sorted(self.edits):
            parts += (self.text[pos:a], new)
            pos = b
        parts.append(self.text[pos:])
        return EquationNumbers(
            "".join(parts),
            tuple(self.written),
            self.stop,
            len(self.refs),
            replaced,
            tuple(self.empty),
        )

    def poison(self, why: str) -> None:
        if self.stop is None:
            self.stop = why

    @property
    def pairs(self) -> dict[int, int]:
        if self._pairs is None:
            self._pairs = brace_pairs(self.text)
        return self._pairs

    # -- the document and its macros
    def prepare(self) -> tuple[_Covered, list[tuple[int, MacroDef]]]:
        """The class, the macros that matter and the stretches that are not read."""
        text = self.text
        self.read_class(text)
        defs = list(macro_definitions(text))
        spans = [(v.start, v.end) for v in verbatim_parts(text)]
        spans += ((b.start(), e.end()) for b, e in regions(text, IFFALSE, FI))
        spans += ((d.start, d.end) for d in defs)
        extras: list[tuple[int, MacroDef]] = []
        for d in defs:
            if d.name in ("eqref", "ref"):
                self.redefined.add(d.name)
            if d.name in _FORMATTED:
                extras.append((d.start, d))
            else:
                self.classify(d, _arguments(text, d))
        self.close_effects(defs)
        # ``\let\old\appendix`` saves a command, it does not use it
        spans += ((m.start(), m.end()) for m in _LET.finditer(text))
        for m in _LET_ALIAS.finditer(text):
            if (env := m["env"]) in _NUMBERED | {"subequations"}:
                self.aliases[m["name"]] = (("end" if m["end"] else "begin", env),)
        for m in _SHARED_THEOREM.finditer(text):
            if m["counter"] == "equation" or m["counter"] in self.sharing:
                self.sharing.add(m["name"])
        spans += self.environment_definitions()
        self.effect_names = tuple(sorted(self.effects))
        skipped = _Covered(spans)
        branches = self.conditionals(skipped, {d.name for d in defs})
        return _Covered([*spans, *branches]), extras

    def conditionals(self, skipped: _Covered, defined: set[str]) -> list[tuple[int, int]]:
        """Fills ``self.conditional`` with the stretches that are in a conditional, and returns the
        spans to skip: the branch of ``\\ifSubfilesClassLoaded`` that a main document skips.
        A name that the paper defines (``\\newcommand{\\ifo}``) is not a conditional, and an
        ``\\if...`` that is not a TeX primitive, is not declared by ``\\newif`` and is followed
        by a brace group takes its branches as arguments (``\\ifdraft{A}{B}``)."""
        held: list[tuple[int, int]] = []
        unused: list[tuple[int, int]] = []
        opened: list[int] = []
        found = [m for m in _CONDITIONAL.finditer(self.text) if m.start() not in skipped]
        declared = {m["newif"].rsplit("\\", 1)[1] for m in found if m["newif"]}
        for m in found:
            if m["newif"]:
                continue
            if m["fi"]:
                if opened and (start := opened.pop()) is not None and not opened:
                    held.append((start, m.end()))
            elif (name := m["name"]) in _NOT_CONDITIONAL or name in defined:
                continue
            elif name in _BRANCHES:
                end, groups = self.groups(m.end(), _BRANCHES[name])
                if name == "ifSubfilesClassLoaded" and len(groups) == 2:
                    unused.append(groups[0])
                else:
                    held.append((m.start(), end))
            elif name.startswith(("@if", "If")) or (
                name not in _PRIMITIVE_CONDITIONALS
                and name not in declared
                and self.takes_branches(m.end())
            ):
                held.append((m.start(), self.groups(m.end(), 3)[0]))
            else:
                opened.append(m.start())
        if opened:
            held.append((opened[0], len(self.text)))  # a conditional that never closes
        self.conditional = _Covered(held)
        return unused

    def takes_branches(self, i: int) -> bool:
        """Whether an ``\\if...`` of unknown kind, whose name ends at ``i``, takes its branches as
        brace groups (``\\ifdraft{A}{B}``). A group that is followed by ``\\else`` or ``\\fi`` is
        the text of a TeX conditional that a package declared (``\\ifpdf{x}\\else..\\fi``)."""
        groups = self.groups(i, 3)[1]
        if not groups:
            return False
        after = [self.text[g[1] :].lstrip() for g in groups]
        return not any(_ELSE_OR_FI.match(rest) for rest in after)

    def groups(self, i: int, count: int) -> tuple[int, list[tuple[int, int]]]:
        """The brace groups that follow position ``i`` (up to ``count``): where the last one ends
        and the span of each."""
        found: list[tuple[int, int]] = []
        for _ in range(count):
            while i < len(self.text) and self.text[i] in " \t\n":
                i += 1
            end = self.pairs.get(i)
            if end is None:
                break
            found.append((i, end))
            i = end
        return i, found

    def read_class(self, text: str) -> None:
        found = _CLASS.search(text)
        name = found["name"].strip() if found else ""
        options = (found["options"] or "") if found else ""
        chapters = _CHAPTER.search(text) is not None
        if name.startswith("revtex4"):
            self.family = "revtex"
        elif name in _ARTICLE_CLASSES:
            self.family = "article"
        elif name in _BOOK_CLASSES:
            self.family = "book"
        self.prl = re.search(r"(?<![A-Za-z])prl(?![A-Za-z])", options) is not None
        if self.family == "revtex" or name == "IEEEtran":  # sections are I, II, III
            self.fmt["section"] = [("counter", "section", "Roman")]
            self.fmt["subsection"] = None
        elif name == "ieeeaccess":  # the style of IEEEtran: its section numbers are not modelled
            self.fmt["section"] = self.fmt["subsection"] = None
        if "eqsecnum" in options:
            if self.family == "revtex":  # revtex4: equations numbered within the section, 2.3
                self.parent = "section"
                self.fmt["equation"] = [
                    ("counter", "section", "arabic"),
                    ("text", "."),
                    ("counter", "equation", "arabic"),
                ]
            else:
                self.poison("class option eqsecnum")
        if self.family == "book":
            if chapters:
                self.parent = "chapter"
                # no chapter, no prefix: ``\ifnum\c@chapter>\z@ \thechapter.\fi\arabic{equation}``
                self.fmt["equation"] = [("chapter_prefix",), ("counter", "equation", "arabic")]
                self.fmt["section"] = _within("chapter", "section")
            else:
                self.poison("a book class without chapters")
        elif chapters:
            self.poison(f"chapters in the class {name or 'unknown'}")

    def classify(self, d: MacroDef, arguments: int) -> None:
        """What a macro of the paper does to the count: it stands for some tokens of it
        (``\\newcommand{\\be}{\\begin{equation}}``), or hides them in more text."""
        found = list(_EQ_INTERESTING.finditer(d.body))
        effect = _EFFECT.search(d.body) is not None
        if not found and not effect:
            return
        if not found:
            self.effects[d.name] = d.body
            return
        rest = _EQ_INTERESTING.sub("", d.body).replace("&", "")
        ops = tuple(_operation(m) for m in found)
        if arguments == 0 and not rest.strip() and not effect:
            self.aliases[d.name] = ops
        elif effect or _hides(d.body):
            self.hard.add(d.name)
        else:
            self.soft.add(d.name)

    def close_effects(self, defs: list[MacroDef]) -> None:
        """A macro that only uses a macro that steps a counter does so too. Each round adds the
        macros that use one found before; ``apply`` stops a chain at ``_NESTING_MAX`` levels."""
        for _ in range(_CLOSE_ROUNDS):
            if not self.effects:
                return
            uses = re.compile(
                "|".join(rf"\\{re.escape(n)}(?![A-Za-z@])" for n in sorted(self.effects))
            )
            known = {*self.effects, *self.aliases, *self.hard, *self.soft}
            more = {d.name: d.body for d in defs if d.name not in known and uses.search(d.body)}
            if not more:
                return
            self.effects.update(more)
        self.poison("macros that use each other too deeply")

    def environment_definitions(self) -> list[tuple[int, int]]:
        """The spans of ``\\newenvironment``; an environment that begins and ends another one
        is an alias of it, and one that hides a numbered environment is not modelled."""
        spans: list[tuple[int, int]] = []
        for m in _NEWENVIRONMENT.finditer(self.text):
            begin = _matched(self.pairs, m.end())
            if begin is None:
                continue
            after = begin
            while after < len(self.text) and self.text[after] in " \t\n":
                after += 1
            end = _matched(self.pairs, after)
            if end is None:
                continue
            spans.append((m.start(), end))
            name = m["name"]
            first = self.text[m.end() + 1 : begin - 1].strip()
            last = self.text[after + 1 : end - 1].strip()
            if name in _HARD_ENVS:
                self.poison(f"\\{'re' if m['kind'] else ''}newenvironment of {name}")
            elif (b := _BEGIN_ONLY.fullmatch(first)) and (e := _END_ONLY.fullmatch(last)):
                if b["env"] == e["env"]:
                    self.env_aliases[name] = b["env"]
            elif _hides(first + last):
                self.hard_envs.add(name)
        return spans

    # -- the pass
    def run(self) -> EquationNumbers:
        if _NUMBERING_PACKAGES.search(self.text):
            self.poison("a package or option that numbers only some equations")
        skipped, extras = self.prepare()
        names = tuple(sorted({*self.aliases, *self.hard, *self.soft, *self.effects}))
        ei = 0
        for m in _eq_token(names).finditer(self.text):
            if m.start() in skipped:
                continue
            while ei < len(extras) and extras[ei][0] < m.start():
                self.formats(extras[ei][1], at=extras[ei][0])
                ei += 1
            kind = m.lastgroup
            if kind is not None:
                if m.start() in self.conditional and (
                    (kind in _COUNTING and self.counts(m))
                    or (self.cur and (kind in _ROWS or self.hides_row(m)))
                ):
                    self.poison("a counter or an equation inside a conditional")
                getattr(self, f"on_{kind}")(m)
        for pos, d in extras[ei:]:
            self.formats(d, at=pos)
        if self.cur is not None:
            self.poison("an environment that never closes")
        return self.result()

    def counts(self, m: re.Match[str]) -> bool:
        """Whether the token changes a counter or is a numbered environment (a ``\\label``, a
        ``\\tag`` and the like do not count)."""
        kind = m.lastgroup
        if kind == "section":
            return not m["starred"]
        if kind == "counter":  # only the counters of the numbering
            first = _COUNTER_COMMAND.match(self.text, m.end())
            return first is None or first[1] in _COUNTERS_OF_THE_NUMBERING
        if kind == "begin":
            return m["benv"].rstrip("*") in _HARD_ENVS and not m["benv"].endswith("*")
        if kind == "macro":
            name = m[0][1:]
            ops = self.aliases.get(name, ())
            return name in self.effects or any(
                op == "begin" and arg.rstrip("*") in _HARD_ENVS for op, arg in ops
            )
        return True

    def hides_row(self, m: re.Match[str]) -> bool:
        """Whether the token is a macro of the paper that stands for a ``\\nonumber``, a ``\\tag``
        or a row end (``\\newcommand{\\nn}{\\nonumber}``)."""
        if m.lastgroup != "macro":
            return False
        ops = self.aliases.get(m[0][1:], ())
        return any(op in ("word", "rowend") for op, _ in ops)

    def formats(self, d: MacroDef, at: int | None = None) -> None:
        """A redefinition of ``\\theequation`` or of the number of a section."""
        if at is not None and at in self.conditional:
            self.poison("a counter or an equation inside a conditional")
        if self.stop is None:
            parts = _parse_format(d.body)
            if parts is None and d.name == "theequation":
                self.poison("\\theequation is not a counter")
            if d.name == "theequation":
                self.own_format = True
                if self.family == "revtex" and self.appendix_seen:
                    self.poison("\\theequation set after the appendix of a REVTeX paper")
            self.fmt[d.name.removeprefix("the")] = parts

    def on_label(self, m: re.Match[str]) -> None:
        key = m["key"]
        self.label_count[key] += 1
        if self.stop is not None:
            return
        if self.cur is not None:
            self.cur.row.labels.append(key)
        elif self.sub is not None and not self.sub.seen:
            self.sub.seen = True  # a later label has this one before it
            if not self.text[self.sub.at : m.start()].strip():
                self.labels[key] = self.sub.prefix  # the label of the group

    def on_ref(self, m: re.Match[str]) -> None:
        if m["refcmd"] not in self.redefined:  # the paper's own \eqref may print more than (n)
            self.refs.append((m.start(), m.end(), m["refcmd"], m["refkey"]))

    def on_brace(self, m: re.Match[str]) -> None:
        if self.cur is not None:
            self.cur.depth += 1 if m[0] == "{" else -1
            if self.cur.depth < 0:
                self.poison("unbalanced braces in an equation")

    def on_nonumber(self, _: re.Match[str]) -> None:
        if self.cur is not None:
            self.cur.row.unnumbered = True

    def on_tag(self, m: re.Match[str]) -> None:
        if self.cur is None:
            return
        close = self.pairs.get(m.end() - 1)
        text = self.text[m.end() : close - 1].strip() if close else ""
        self.cur.depth += 1  # the brace that opens the text is part of the token
        self.cur.row.tagged = True
        self.cur.row.tag = text if _SIMPLE_TAG.fullmatch(text) else None

    def on_rowend(self, m: re.Match[str]) -> None:
        cur = self.cur
        if cur is not None and cur.name not in _ONE_NUMBER and cur.depth == 0 and cur.inner == 0:
            cur.rows.append((cur.row, m.start()))
            cur.row = _Row(m.end())

    def on_macro(self, m: re.Match[str]) -> None:
        name = m[0][1:]
        if name in self.aliases:
            for op, arg in self.aliases[name]:
                self.operate(op, arg, m)
        elif name in self.effects:
            self.apply(self.effects[name])
        elif name in self.hard or (name in self.soft and self.cur is not None):
            self.poison(f"the macro \\{name} hides part of an equation")

    def apply(self, body: str) -> None:
        """What a macro does to the counters, read from its text where it is used. The macro uses
        that are read in the whole document are bounded (``self.budget``): a macro that uses a
        macro that uses a macro, many times each, would grow with the depth of the nesting."""
        if self.nested >= _NESTING_MAX:
            self.poison("macros that use each other too deeply")
            return
        self.budget -= 1 + len(body) // 64
        if self.budget < 0:
            self.poison("macros that expand too much")
            return
        saved, self.text, self.nested = self.text, body, self.nested + 1
        try:
            events: list[tuple[int, str, object]] = [
                (m.start(), "token", m)
                for m in _eq_token(self.effect_names).finditer(body)
                if m.lastgroup in ("section", "appendix", "counter", "macro", "matter")
            ]
            events += ((d.start, "format", d) for d in _format_definitions(body))
            for _, kind, item in sorted(events, key=lambda e: e[0]):
                self.budget -= 1
                if kind == "format":
                    self.formats(item)  # type: ignore[arg-type]
                else:
                    getattr(self, f"on_{item.lastgroup}")(item)  # type: ignore[attr-defined]
        finally:
            self.text, self.nested = saved, self.nested - 1

    def operate(self, op: str, arg: str, m: re.Match[str]) -> None:
        if op == "begin":
            self.begin(arg, m)
        elif op == "end":
            self.end(arg, m)
        elif op == "word":
            if arg == "tag":
                self.poison("a macro that holds a \\tag")
            else:
                self.on_nonumber(m)
        else:
            self.on_rowend(m)

    def on_begin(self, m: re.Match[str]) -> None:
        self.begin(m["benv"], m)

    def on_end(self, m: re.Match[str]) -> None:
        self.end(m["eenv"], m)

    def begin(self, name: str, m: re.Match[str]) -> None:
        name = self.env_aliases.get(name, name)
        base = name.removesuffix("*")
        starred = name.endswith("*")
        if name in self.hard_envs:
            self.poison(f"the environment {name} hides part of an equation")
        elif self.cur is not None:
            if not starred and base in _HARD_ENVS:
                self.poison(f"{base} inside {self.cur.name}")
            self.cur.inner += 1
        elif starred:
            return
        elif base in _NUMBERED:
            self.cur = _Open(base, _Row(m.end()))
        elif base in _UNMODELLED:
            self.poison(f"the environment {base}")
        elif base == "subequations":
            self.begin_group(m)
        elif base in ("appendix", "appendices"):
            self.appendix(starred=False)
        elif base in self.sharing:
            self.n["equation"] += 1  # a theorem numbered like an equation
            if self.sub is not None:
                self.poison("a theorem counted with the equations inside subequations")

    def end(self, name: str, m: re.Match[str]) -> None:
        name = self.env_aliases.get(name, name)
        cur = self.cur
        if cur is None:
            if name == "subequations" and self.sub is not None:
                self.n["equation"] = self.sub.parent
                self.sub = None
            return
        if name == cur.name and cur.inner == 0:
            self.finish(cur, m.start())
        elif name != cur.name and cur.inner > 0:
            cur.inner -= 1
        else:
            self.poison(f"\\end{{{name}}} inside {cur.name}")
            self.cur = None

    # -- counters
    def current(self) -> str | None:
        """What ``\\theequation`` shows now."""
        if self.sub is not None:
            letter = _style(self.n["equation"], "alph")
            return self.sub.prefix + letter if letter else None
        return self.render(self.fmt["equation"], 0)

    def render(self, parts: _Format | None, depth: int) -> str | None:
        if parts is None or depth > _NESTING_MAX:
            return None
        out = ""
        for part in parts:
            if part[0] == "text":
                out += part[1]
            elif part[0] == "chapter_prefix":
                if self.n["chapter"] > 0:
                    shown = self.render(self.fmt["chapter"], depth + 1)
                    if shown is None:
                        return None
                    out += shown + "."
            elif part[0] == "the":
                shown = self.render(self.fmt[part[1]], depth + 1)
                if shown is None:
                    return None
                out += shown
            else:
                value = self.n.get(part[1])
                shown = None if value is None else _style(value, part[2])
                if shown is None:
                    return None
                out += shown
        return out

    def begin_group(self, m: re.Match[str]) -> None:
        """``subequations``: the group takes one number, its equations are 3a, 3b, ..."""
        if self.sub is not None:
            self.poison("nested subequations")
            return
        self.n["equation"] += 1
        prefix = self.current()
        if prefix is None:
            self.poison("a number that cannot be shown")
            return
        self.sub = _Sub(prefix, self.n["equation"], m.end())
        self.n["equation"] = 0

    def step(self, counter: str) -> None:
        self.n[counter] += 1
        for below in _BELOW[counter]:
            self.n[below] = 0
        if self.parent == counter or self.parent in _BELOW[counter]:
            self.n["equation"] = 0

    def on_section(self, m: re.Match[str]) -> None:
        if m["starred"]:
            return
        if self.family == "book" and not self.matter:  # the front and back matter of a book
            if m["level"] != "chapter":
                self.poison("a section in the front or back matter of a book")
            return  # its chapters have no number
        self.step(m["level"])

    def on_matter(self, m: re.Match[str]) -> None:
        self.matter = m[0] == "\\mainmatter"

    def on_appendix(self, m: re.Match[str]) -> None:
        self.appendix(starred=bool(m["astar"]))

    def appendix(self, *, starred: bool) -> None:
        """Sections are lettered from here on. REVTeX also resets the equations at each section
        and numbers them A1, A2, ... (A.1 after ``\\appendix*``) unless the paper has its own
        ``\\theequation``."""
        if self.family is None or (starred and self.family != "revtex"):
            self.poison("an appendix in a class whose numbering is not modelled")
            return
        self.appendix_seen = True
        top = "chapter" if self.family == "book" else "section"
        self.n[top] = 0
        for below in _BELOW[top]:
            self.n[below] = 0
        self.fmt[top] = [("counter", top, "Alph")]
        if self.family == "revtex":
            if self.prl:  # the prl style does not do what \appendix does in the others (seen)
                self.poison("an appendix in a REVTeX paper with the prl option")
                return
            if self.own_format:
                self.poison("an appendix in a REVTeX paper that sets \\theequation itself")
                return
            self.parent = "section"
            self.fmt["equation"] = (
                [("text", "A."), ("counter", "equation", "arabic")]
                if starred
                else [("the", "section"), ("counter", "equation", "arabic")]
            )

    def on_counter(self, m: re.Match[str]) -> None:
        cmd = m["cmd"]
        two = cmd not in ("stepcounter", "refstepcounter")
        args = (_TWO_ARGUMENTS if two else _ONE_ARGUMENT).match(self.text, m.end())
        head = _ONE_ARGUMENT.match(self.text, m.end()) if args is None and two else None
        if head is not None and head[1] in self.n and cmd in ("setcounter", "addtocounter"):
            self.poison(f"\\{cmd}{{{head[1]}}} set to something that is not a number")
        if args is None or self.stop is not None:
            return
        name = args[1]
        if cmd in ("setcounter", "addtocounter"):
            if name not in self.n:
                return
            if not _INTEGER.match(args[2]):
                self.poison(f"\\{cmd}{{{name}}} set to something that is not a number")
                return
            value = int(args[2])
            self.n[name] = value if cmd == "setcounter" else self.n[name] + value
        elif not two:
            if name == "equation":
                self.n["equation"] += 1
            elif name in _BELOW:
                self.step(name)
        elif name == "equation":
            self.within(cmd, args[2])

    def within(self, cmd: str, parent: str) -> None:
        if parent not in _BELOW:
            self.poison(f"equations numbered within {parent}")
            return
        self.parent = parent
        if cmd in ("numberwithin", "counterwithin"):
            self.fmt["equation"] = _within(parent, "equation")

    # -- the numbers
    def finish(self, cur: _Open, end: int) -> None:
        """``\\end{name}`` of ``cur`` at ``end``: number its rows."""
        self.cur = None
        if cur.depth != 0:
            self.poison("unbalanced braces in an equation")
            return
        rows = [*cur.rows, (cur.row, end)]
        for i, (row, stop) in enumerate(rows):
            text = self.text[row.start : stop]
            if i:  # the options of the row end before it: ``\\[2pt]``, ``\\*``
                text = _ROW_TAIL.sub("", text, count=1)
            self.number(row, stop, write=bool(text.replace("%", "").strip()))

    def number(self, row: _Row, at: int, *, write: bool) -> None:
        if self.stop is not None:
            return
        if row.tagged:  # a ``\tag`` takes the place of the number and does not count
            shown = row.tag
        elif row.unnumbered:
            shown = None
        else:
            self.n["equation"] += 1
            shown = self.current()
            if shown is None or not _NUMBER_SHOWN.fullmatch(shown):
                self.poison("a number that cannot be shown")
                return
            if write:
                own_line = self.text[at - 1 : at] == "\n"  # keep the line break before \end
                self.edits.append(
                    (at, at, f"\\tag{{{shown}}}\n" if own_line else f" \\tag{{{shown}}} ")
                )
                self.written.append(shown)
            else:
                self.empty.append(shown)
        if shown is not None:
            for key in row.labels:
                self.labels[key] = shown


@functools.lru_cache(maxsize=512)
def _format_definitions(body: str) -> tuple[MacroDef, ...]:
    """The redefinitions of ``\\theequation`` and its kin in the text of a macro."""
    return tuple(d for d in macro_definitions(body) if d.name in _FORMATTED)


def _within(top: str, counter: str) -> _Format:
    """``\\thechapter.\\arabic{equation}``: what ``\\numberwithin{equation}{chapter}`` shows."""
    return [("the", top), ("text", "."), ("counter", counter, "arabic")]


def _hides(body: str) -> bool:
    """Whether ``body`` begins or ends an environment of the numbering."""
    return any(
        m["env"] and not m["env"].endswith("*") and m["env"] in _HARD_ENVS
        for m in _EQ_INTERESTING.finditer(body)
    )


def _operation(m: re.Match[str]) -> tuple[str, str]:
    if m["rowend"]:
        return ("rowend", "")
    if m["word"]:
        return ("word", m["word"])
    kind = "begin" if m[0].startswith("\\begin") else "end"
    return (kind, m["env"])


_EQ_ANY = re.compile(rf"\\begin\s*\{{(?:{'|'.join(sorted(_NUMBERED))})\}}")


def number_equations(text: str) -> EquationNumbers:
    """Write the number LaTeX gives each display equation into it as ``\\tag{n}`` and replace
    ``\\eqref{key}`` by ``(n)`` and ``\\ref{key}`` by ``n`` when ``key`` labels one.

    Counted: ``equation`` and ``multline`` once, ``align``, ``alignat``, ``xalignat``,
    ``flalign``, ``gather`` and ``eqnarray`` per row, unless the environment is starred or the
    row has ``\\nonumber`` or ``\\notag``; a ``\\tag`` is the number of its row and does not
    count. ``subequations`` gives 3a, 3b; ``\\numberwithin``, ``\\setcounter``, ``\\appendix``,
    a ``\\theequation`` made of counters and text, and the macros of the paper that stand for
    ``\\begin{equation}`` and ``\\nonumber`` are followed. Where the count cannot be known (an
    environment of a package that is not modelled, a macro that hides part of an equation, a
    class whose rules are not modelled, a counter set to something that is not a number) the
    numbering stops there, the equations before it keep their numbers, and no later equation
    gets one. Verbatim text, macro definitions and ``\\iffalse`` blocks are not read.
    """
    if not _EQ_ANY.search(text):
        return EquationNumbers(text, (), None, 0, 0)
    return _Numberer(text).run()


def prepare_for_pandoc(text: str) -> tuple[str, list[str]]:
    """Source rewrites that make pandoc's output complete and its run bounded."""
    text, restore = _hide_listings(protect_circuits(text))
    text, dropped = drop_unsafe_macros(text)
    text = rewrite_bibliography(text)
    # NeurIPS/ICLR styles separate authors with \And / \AND: pandoc only knows \and.
    text = re.sub(rf"\\(?:And|AND){_NOT_LETTER}", r"\\and", text)
    text = re.sub(rf"\\today{_NOT_LETTER}", "", text)  # sandboxed pandoc prints 1970-01-01
    text = simplify_layout(sanitize_citation_keys(shim_at_macros(text)))
    text = restore(number_equations(text).text)
    warnings = []
    if dropped:
        names = ", ".join(sorted({f"\\{n}" for n in dropped}))
        warnings.append(f"ignored {len(dropped)} self-referential/structural macro(s): {names}")
    return text, warnings


# ------------------------------------------------------------------ flatten
@dataclass
class Flattened:
    text: str
    files: list[str] = field(default_factory=list)  # inlined files, relative to the root
    warnings: list[str] = field(default_factory=list)


# Applied outside verbatim regions only (see _Flattener._expand).
_FLATTEN = re.compile(
    rf"\\(?P<input>input|include|subfile|expandableinput){_NOT_LETTER}\s*"
    r"(?:\{(?P<arg>[^{}]*)\}|(?P<bare>[^\s{}\\%]+))"
    r"|\\(?P<imp>(?:sub)?(?:import|inputfrom|includefrom))\*?\s*"
    r"\{(?P<dir>[^{}]*)\}\s*\{(?P<file>[^{}]*)\}"
    rf"|\\(?:usepackage|RequirePackage){_NOT_LETTER}\s*(?P<opts>\[[^\]]*\])?\s*"
    r"\{(?P<pkgs>[^{}]*)\}"
    rf"|(?P<bib>\\bibliography{_NOT_LETTER}\s*\{{[^{{}}]*\}})"
)
_ENDINPUT = re.compile(rf"\\endinput{_NOT_LETTER}")
_ENDINPUT_TOKEN = re.compile(rf"(?P<endinput>\\endinput{_NOT_LETTER})|\\.|(?P<brace>[{{}}])")
_END_DOCUMENT = re.compile(r"\\end\s*\{document\}")
# An \endinput TeX does not execute on a first read: an include guard closed on the same
# line (\ifx\loaded\undefined\else\endinput\fi) or the operand of \let, \ifx, ...
_GUARDED = re.compile(rf"[^\n]{{0,200}}?\\fi{_NOT_LETTER}")
_OPERAND = re.compile(
    r"\\(?:(?:future)?let\s*\\[a-zA-Z@]+\s*=?|ifx(?:\s*\\[a-zA-Z@]+)?|noexpand|string|meaning|show)"
    r"\s*\Z"
)


def _executed(text: str, m: re.Match[str]) -> bool:
    return not (
        _GUARDED.match(text, m.end()) or _OPERAND.search(text, max(0, m.start() - 80), m.start())
    )


def cut_at_endinput(text: str, *, main: bool = False) -> str:
    """``text`` as far as TeX reads it: to the end of the line holding the first
    ``\\endinput`` TeX executes (the rest of that line is still read). The command
    itself is dropped: pandoc would stop reading the whole flattened document there.

    Only an ``\\endinput`` at brace depth 0 counts, outside verbatim and filecontents
    environments and ``\\verb``-like arguments: one in a macro body, as a ``\\let``
    operand or in an include guard is not executed. LaTeX has to reach
    ``\\end{document}`` in the main file, so there only one after it counts.
    """
    start = 0
    if main and (ends := [m.end() for m in _END_DOCUMENT.finditer(text)]):
        start = ends[-1]
    if not _ENDINPUT.search(text, start):
        return text
    depth = 0
    for a, b in _outside_verbatim(text, start):
        for m in _ENDINPUT_TOKEN.finditer(text, a, b):
            if brace := m.group("brace"):
                depth = depth + 1 if brace == "{" else max(depth - 1, 0)
            elif m.group("endinput") and depth == 0 and _executed(text, m):
                eol = text.find("\n", m.end())
                eol = len(text) if eol < 0 else eol + 1
                return text[: m.start()] + text[m.end() : eol]
    return text


class _Flattener:
    def __init__(self, main: Path, boundary: Path, max_chars: int, max_depth: int):
        self.main = main.resolve()
        self.boundary = boundary.resolve()
        self.max_chars = max_chars
        self.max_depth = max_depth
        self.size = 0
        self.files: list[str] = []
        self.warnings: list[str] = []
        self.packages: set[Path] = set()
        bbl = self.main.with_suffix(".bbl")  # what \bibliography reads: <jobname>.bbl
        self.bbl: Path | None = bbl if bbl.is_file() else None

    def run(self) -> Flattened:
        text = self._load(self.main)
        text = self._expand(text, self.main, self.main.parent, (self.main,))
        return Flattened(text, self.files, self.warnings)

    # -- files
    def _rel(self, path: Path) -> str:
        return path.relative_to(self.boundary).as_posix()

    def _load(self, path: Path, *, endinput: bool = True) -> str:
        size = path.stat().st_size
        self.size += size
        if self.size > self.max_chars:
            raise ConversionError(f"LaTeX source exceeds {_size(self.max_chars)} (max_archive_mb)")
        text = strip_comments(decode_tex(path.read_bytes()))
        return cut_at_endinput(text, main=path == self.main) if endinput else text

    def _resolve(self, name: str, dirs: list[Path], suffix: str, always: bool) -> Path | str:
        """The file for ``name``, or why there is none."""
        name = name.strip().strip('"').strip()
        if not name or "#" in name or "\\" in name:
            return "unresolvable"
        if name.endswith(suffix):
            names = [name]
        else:
            names = [name + suffix] if always else [name + suffix, name]
        outside = False
        for d in dict.fromkeys(dirs):
            for n in names:
                try:
                    p = (d / n).resolve()
                except (OSError, RuntimeError):
                    continue
                if not p.is_relative_to(self.boundary):
                    outside = True
                elif p.is_file():
                    return p
        return "outside the source tree" if outside else "missing"

    # -- expansion
    def _expand(self, text: str, current: Path, base: Path, stack: tuple[Path, ...]) -> str:
        """Inline the includes of ``text``; verbatim regions are copied untouched."""
        parts: list[str] = []
        pos = 0
        for a, b in _outside_verbatim(text):
            chunk = _FLATTEN.sub(lambda m: self._replace(m, current, base, stack), text[a:b])
            parts += (text[pos:a], chunk)
            pos = b
        return "".join(parts)

    def _replace(self, m: re.Match[str], current: Path, base: Path, stack: tuple[Path, ...]) -> str:
        nl = m.string[m.end() : m.end() + 1] == "\n"
        if cmd := m.group("input"):
            name = m.group("arg") if m.group("arg") is not None else m.group("bare")
            dirs = [base, current.parent, self.main.parent]
            path = self._resolve(name, dirs, ".tex", always=cmd == "include")
            return self._inline(m.group(), path, base, stack, nl=nl, page_break=cmd == "include")
        if imp := m.group("imp"):
            anchor = base if imp.startswith("sub") else self.main.parent
            d = anchor / m.group("dir").strip()
            path = self._resolve(m.group("file"), [d], ".tex", always=False)
            return self._inline(m.group(), path, d, stack, nl=nl, page_break="include" in imp)
        if m.group("pkgs") is not None:
            return self._packages(m, current, base, stack)
        return self._bibliography(m.group())

    def _skip(self, command: str, why: str, nl: bool) -> str:
        self.warnings.append(f"skipped {command} ({why})")
        return f"%docingest: skipped ({why})" + ("" if nl else "\n")

    def _inline(
        self,
        command: str,
        path: Path | str,
        base: Path,
        stack: tuple[Path, ...],
        *,
        nl: bool,
        page_break: bool,
    ) -> str:
        if isinstance(path, str):
            return self._skip(command, path, nl)
        if path in stack:
            return self._skip(command, "recursive include", nl)
        if len(stack) > self.max_depth:
            return self._skip(command, f"nesting deeper than {self.max_depth}", nl)
        text = self._load(path)
        if re.search(r"\\begin\s*\{document\}", text):  # \subfile, standalone figures
            text = document_body(text)
        self.files.append(self._rel(path))
        text = self._expand(text, path, base, (*stack, path)).rstrip("\n")
        if page_break:  # \include always starts a new page, hence a new paragraph
            return f"\n{text}\n\n"
        return text + ("" if nl else "\n")

    def _packages(
        self, m: re.Match[str], current: Path, base: Path, stack: tuple[Path, ...]
    ) -> str:
        names = [n.strip() for n in m.group("pkgs").split(",") if n.strip()]
        dirs = [base, current.parent, self.main.parent]
        system, macros = [], []
        for name in names:
            path = self._resolve(name, dirs, ".sty", always=True)
            if isinstance(path, str):
                system.append(name)  # amsmath & co.: pandoc knows them or ignores them
            elif path not in self.packages and path not in stack:  # LaTeX loads a package once
                self.packages.add(path)
                self.files.append(self._rel(path))
                sty = self._expand(self._load(path, endinput=False), path, base, (*stack, path))
                macros.append(package_macros(sty))
        if len(system) == len(names):
            return m.group()
        keep = f"\\usepackage{m.group('opts') or ''}{{{','.join(system)}}}" if system else ""
        return "\n".join(x for x in (keep, *macros) if x) + "\n"

    def _bibliography(self, command: str) -> str:
        if self.bbl is None:
            return command
        bbl, self.bbl = self._load(self.bbl), None  # inline once
        if not re.search(r"\\begin\s*\{thebibliography\}", bbl):  # biblatex: leave it
            return command
        self.files.append(self.main.with_suffix(".bbl").relative_to(self.boundary).as_posix())
        return f"\n{bbl.strip()}\n"


def flatten(main: Path, boundary: Path, *, max_chars: int, max_depth: int = MAX_DEPTH) -> Flattened:
    """Inline ``\\input``/``\\include``/``\\subfile``/``\\import``, local ``.sty`` macros
    and the ``<main>.bbl`` bibliography, recursively, into one string.

    Includes resolve like TeX (relative to the main file's directory) with the including
    file's directory as a fallback; nothing outside ``boundary`` is ever read. Missing,
    recursive or too-deep includes become a ``%docingest:`` comment plus a warning.
    Raises ``ConversionError`` when the total exceeds ``max_chars``.
    """
    return _Flattener(main, boundary, max_chars, max_depth).run()


# ---------------------------------------------------------------- main file
_DOCCLASS = re.compile(r"^[^%\n]*\\document(?:class|style)\s*(?:\[[^\]]*\])?\s*\{?([\w-]*)", re.M)
_BEGIN_DOC = re.compile(r"\\begin\s*\{document\}")
_INCLUDED = re.compile(
    rf"\\(?:input|include|subfile|(?:sub)?import\s*\{{[^{{}}]*\}}){_NOT_LETTER}\s*\{{([^{{}}]+)\}}"
)


def _readme_main(root: Path) -> Path | None:
    """arXiv's 00README.json (``usage: toplevel``) or legacy ``00README.XXX``."""
    readme = root / "00README.json"
    if readme.is_file():
        try:
            data = json.loads(decode_tex(readme.read_bytes()))
        except ValueError:
            data = {}
        sources = data.get("sources", []) if isinstance(data, dict) else []
        for src in sources if isinstance(sources, list) else []:
            if (
                isinstance(src, dict)
                and src.get("usage") == "toplevel"
                and (p := _child(root, str(src.get("filename", ""))))
            ):
                return p
    for legacy in sorted(root.glob("00README*")):
        if legacy.suffix.lower() == ".json" or not legacy.is_file():
            continue
        for line in decode_tex(legacy.read_bytes()).splitlines():
            parts = line.split()
            if (
                len(parts) >= 2
                and parts[1].lower() == "toplevelfile"
                and (p := _child(root, parts[0]) or _child(root, parts[0] + ".tex"))
            ):
                return p
    return None


def _child(root: Path, name: str) -> Path | None:
    if not name:
        return None
    p = (root / name).resolve()
    return p if p.is_relative_to(root.resolve()) and p.is_file() else None


def find_main(root: Path) -> tuple[Path, list[str]]:
    """The file to compile: README hint, else ``\\documentclass`` + ``\\begin{document}``,
    else the conventional name, else the file nobody includes, else the largest."""
    if (hinted := _readme_main(root)) is not None:
        return hinted, []
    files = [
        p
        for p in sorted(root.rglob("*"))
        if p.suffix.lower() in TEX_SUFFIXES
        and p.is_file()
        and not any(part.startswith((".", "__MACOSX")) for part in p.relative_to(root).parts)
    ]
    if not files:
        raise ConversionError("no .tex file in the LaTeX source")
    texts = {p: strip_comments(decode_tex(p.read_bytes())) for p in files}

    def doc_class(p: Path) -> str | None:
        m = _DOCCLASS.search(texts[p])
        return m.group(1) if m else None

    full = [p for p in files if doc_class(p) is not None and _BEGIN_DOC.search(texts[p])]
    real = [p for p in full if doc_class(p) not in ("standalone", "subfiles")]
    pool = real or full or [p for p in files if doc_class(p) is not None] or files
    if len(pool) == 1:
        return pool[0], []

    named = [p for p in pool if p.stem.lower() in MAIN_NAMES]
    if named:
        best = min(
            named, key=lambda p: (len(p.relative_to(root).parts), MAIN_NAMES.index(p.stem.lower()))
        )
        return best, []
    included = set()
    for p, text in texts.items():
        for m in _INCLUDED.finditer(text):
            target = m.group(1).strip()
            for d in (root, p.parent):
                included.add((d / target).resolve())
                included.add((d / (target + ".tex")).resolve())
    top = [p for p in pool if p.resolve() not in included] or pool
    best = max(top, key=lambda p: p.stat().st_size)
    if len(top) > 1:
        others = ", ".join(p.relative_to(root).as_posix() for p in top if p != best)
        return best, [
            f"several main-file candidates; picked the largest ({best.name}) over {others}"
        ]
    return best, []


# ------------------------------------------------------------------- unpack
@dataclass(frozen=True)
class Unpacked:
    root: Path  # nothing outside it is read
    main: Path | None  # None: an archive, call find_main(root)
    warnings: list[str] = field(default_factory=list)


def _is_tar(block: bytes) -> bool:
    """A valid first tar header: ustar or pre-POSIX (v7) without the magic.

    Checks the header checksum on this one block, like the detector does, so every
    archive the detector routes here is unpacked as an archive.
    """
    try:
        tarfile.TarInfo.frombuf(block[: tarfile.BLOCKSIZE], tarfile.ENCODING, "surrogateescape")
    except tarfile.HeaderError:
        return False
    return True


def unpack(path: Path, dest: Path, *, max_bytes: int) -> Unpacked:
    """Plain ``.tex`` -> itself; tar / tar.gz -> extracted into ``dest``; a single
    gzipped ``.tex`` (old arXiv e-prints) -> ``dest/main.tex``.

    Archives are capped at ``max_bytes`` unpacked (checked while reading headers, so a
    zip bomb is refused early) and extracted with tarfile's ``data`` filter.
    """
    with path.open("rb") as f:
        head = f.read(512)
    if head.startswith(_GZIP_MAGIC):
        try:
            with gzip.open(path) as g:
                block = g.read(512)
        except (OSError, EOFError, zlib.error) as e:
            raise ConversionError(f"corrupt gzip file {path.name}: {e}") from e
        if _is_tar(block):
            return _untar(path, dest, max_bytes, gzipped=True)
        dest.mkdir(parents=True, exist_ok=True)
        main = dest / "main.tex"
        _gunzip(path, main, max_bytes)
        return Unpacked(dest, main)
    if _is_tar(head):
        return _untar(path, dest, max_bytes, gzipped=False)
    return Unpacked(path.parent, path)


def _size(n: int) -> str:
    return f"{n / 2**20:.0f} MB" if n >= 2**20 else f"{n} bytes"


def _too_big(name: str, max_bytes: int) -> ConversionError:
    return ConversionError(f"{name} unpacks to more than {_size(max_bytes)} (max_archive_mb)")


def _gunzip(src: Path, dst: Path, max_bytes: int) -> None:
    written = 0
    try:
        with gzip.open(src) as g, dst.open("wb") as out:
            while chunk := g.read(1 << 20):
                written += len(chunk)
                if written > max_bytes:
                    raise _too_big(src.name, max_bytes)
                out.write(chunk)
    except (OSError, EOFError, zlib.error) as e:
        raise ConversionError(f"corrupt gzip file {src.name}: {e}") from e


class _CappedReader:
    """The archive stream tarfile reads through, handing out at most ``room`` more bytes.

    tarfile reads a pax or GNU long-name header into memory whole, inside ``next()``,
    before the loop in ``_untar`` sees the member it belongs to: 150 KB of gzip make a
    150 MB "header" and several times that in memory, so ``max_archive_mb`` alone bounds
    nothing. A read that would exceed the room is refused before it decompresses or
    allocates anything. Seeking (skipping member data) is not reading: the loop caps it.
    """

    def __init__(self, raw: io.BufferedIOBase, label: str):
        self.raw = raw
        self.label = label
        self.room = MAX_TAR_HEADER

    def read(self, size: int = -1, /) -> bytes:
        if size < 0 or size > self.room:
            what = _size(size) if size >= 0 else "the rest"
            raise ConversionError(
                f"cannot unpack {self.label}: refusing to read {what} of tar headers "
                f"at once (limit {_size(MAX_TAR_HEADER)}; a pax or GNU long-name bomb?)"
            )
        data = self.raw.read(size)
        self.room -= len(data)
        return data

    def seek(self, pos: int, whence: int = io.SEEK_SET, /) -> int:
        return self.raw.seek(pos, whence)

    def tell(self) -> int:
        return self.raw.tell()

    def write(self, b: bytes, /) -> int:
        raise io.UnsupportedOperation("read-only archive stream")

    def close(self) -> None:
        self.raw.close()


def _untar(path: Path, dest: Path, max_bytes: int, *, gzipped: bool) -> Unpacked:
    dest.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    wanted: list[tarfile.TarInfo] = []
    total = 0
    try:
        with gzip.open(path) if gzipped else path.open("rb") as raw:
            reader = _CappedReader(raw, path.name)
            with tarfile.open(fileobj=reader, mode="r:") as tar:
                for i, member in enumerate(tar):  # headers are read lazily: abort early
                    reader.room = MAX_TAR_HEADER  # for the headers of the next member
                    if i >= MAX_MEMBERS:
                        raise ConversionError(f"{path.name} has more than {MAX_MEMBERS} files")
                    total += max(member.size, 0)
                    if total > max_bytes:
                        raise _too_big(path.name, max_bytes)
                    if not member.isfile() or Path(member.name).suffix.lower() in _MEDIA_SUFFIXES:
                        continue  # links, devices, figures: never needed for text
                    try:
                        tarfile.data_filter(member, str(dest))
                    except tarfile.FilterError as e:
                        warnings.append(f"skipped unsafe archive member {member.name!r}: {e}")
                        continue
                    wanted.append(member)
                reader.room = sum(m.size for m in wanted)  # extracting reads exactly that
                tar.extractall(dest, members=wanted, filter="data")
    except MemoryError as e:  # a last resort: any allocation above must not kill the run
        raise ConversionError(f"cannot unpack {path.name}: out of memory") from e
    except (tarfile.TarError, OSError, EOFError, zlib.error) as e:
        raise ConversionError(f"cannot unpack {path.name}: {e}") from e
    return Unpacked(dest, None, warnings)


# ----------------------------------------------------------------- measures
_UNTEXT_ENVS = r"tikzpicture|pgfpicture|axis|filecontents\*?|comment"
_UNTEXT_BEGIN = re.compile(rf"\\begin\{{({_UNTEXT_ENVS})\}}")
IFFALSE = re.compile(r"\\iffalse\b")
FI = re.compile(r"\\fi\b")


def approx_text_length(latex: str) -> int:
    """Rough count of the readable characters in a document, to sanity-check output."""
    body = document_body(latex)
    body = sub_regions(body, _UNTEXT_BEGIN, lambda b: _end_env(b.group(1)), lambda _: " ")
    body = sub_regions(body, IFFALSE, FI, lambda _: " ")
    body = re.sub(r"\\[a-zA-Z@]+\*?", " ", body)
    body = re.sub(r"[{}\[\]\\$&^_~#%]", " ", body)
    return len(" ".join(body.split()))


_SECTION = re.compile(
    r"\\(?P<cmd>part|chapter|section|subsection|subsubsection)\*?\s*(?:\[[^\]]*\])?\s*(?=\{)"
)


def split_sections(body: str, max_level: int) -> list[tuple[int, str | None, str]]:
    """Split LaTeX at sectioning commands of level <= ``max_level``.

    Returns ``(level, title LaTeX, content LaTeX)``; content before the first heading
    comes first with level 0 and no title. Levels follow pandoc: ``\\section`` is 1,
    unless the document has chapters, which then take level 1.
    """
    cuts = _section_cuts(body, max_level)
    starts = [c[0] for c in cuts] + [len(body)]
    parts: list[tuple[int, str | None, str]] = [(0, None, body[: starts[0]])]
    for (_, end, level, title), nxt in zip(cuts, starts[1:], strict=True):
        parts.append((level, title, body[end:nxt]))
    return parts


def _section_cuts(body: str, max_level: int) -> list[tuple[int, int, int, str]]:
    """``(start, end of the title, level, title)`` of each sectioning command to ``max_level``."""
    chapters = re.search(r"\\chapter\*?\s*[\[{]", body) is not None
    levels = {"part": 0, "chapter": 1, "section": 1 + chapters}
    levels["subsection"] = levels["section"] + 1
    levels["subsubsection"] = levels["section"] + 2
    cuts: list[tuple[int, int, int, str]] = []
    pairs = brace_pairs(body)
    for m in _SECTION.finditer(body):
        level = levels[m.group("cmd")]
        end = _matched(pairs, m.end())
        if level <= max_level and end is not None:
            cuts.append((m.start(), end, max(level, 1), body[m.end() + 1 : end - 1]))
    return cuts


def split_document(text: str) -> tuple[str, str]:
    """``(preamble, body)``: the text up to and including ``\\begin{document}`` (empty when
    there is none), and :func:`document_body`."""
    begin = re.search(r"\\begin\s*\{document\}", text)
    return (text[: begin.end()] if begin else ""), document_body(text)


def section_units(body: str) -> tuple[str, list[str]]:
    """The body cut into what comes before the first top-level section and one piece per
    top-level section (``\\chapter`` when the body has chapters, else ``\\section``; a
    ``\\part`` is a cut too), each piece starting with its sectioning command."""
    starts = [c[0] for c in _section_cuts(body, 1)]
    bounds = [*starts, len(body)]
    return body[: bounds[0]], [body[a:b] for a, b in pairwise(bounds)]


def macro_context(text: str) -> str:
    """The macro and theorem definitions in ``text``, to hand on to a later piece of the body
    that is converted on its own."""
    keep = [text[d.start : d.end] for d in macro_definitions(text)]
    keep += [m.group() for m in _NEWTHEOREM.finditer(text)]
    return "\n".join(keep)
