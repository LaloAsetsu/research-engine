"""DocumentConverter adapter: LaTeX sources -> Markdown segments via pandoc.

Accepts a ``.tex`` file, an arXiv source archive (tar / tar.gz) or a single gzipped
``.tex`` (old arXiv e-prints). It unpacks safely, picks the main file, inlines every
include in Python (see ``latex_source``: pandoc alone hangs, drops latin-1 files and
reads arbitrary paths), runs pandoc ``--sandbox`` under a wall-clock timeout and a
heap cap, then splits the Markdown into one segment per section (``split_level``).

When pandoc fails, times out or returns suspiciously little text, pylatexenc produces
plain text instead (``PageMethod.LATEX_PLAINTEXT``) and the Conversion says so in its
warnings; it is also marked ``degraded`` when the cause was the machine (a timeout, a
crash, no pandoc binary) rather than the document, so it is not cached as the result.
When the document itself is the cause, it is first converted section by section, and only
the sections pandoc still cannot read are plain text (a segment then has its own method).
A reference list that pandoc drops without an error is converted on its own and put back.
Title, authors and abstract come from pandoc's metadata (``$meta-json$``).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from functools import cache, cached_property
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from ...config import LatexConfig
from ...domain.errors import ConversionError
from ...domain.models import PageMethod, SourceMetadata
from ...ports import Conversion, Segment
from .latex_source import (
    CIRCUIT_LANGUAGE,
    FI,
    IFFALSE,
    approx_text_length,
    bibliography_blocks,
    count_bibliography_entries,
    document_body,
    find_main,
    flatten,
    macro_context,
    match_brace,
    prepare_for_pandoc,
    regions,
    section_units,
    split_document,
    split_sections,
    sub_regions,
    unpack,
    verbatim_parts,
)

REVISION = 7  # bump when this adapter's output changes for the same pandoc / pylatexenc
# Markdown that keeps $math$, $$display$$, pipe tables and [@citations], without raw
# HTML/TeX or attribute syntax (gfm would drop the citations).
_DISABLED = (
    *("raw_html", "raw_attribute", "raw_tex", "native_divs", "native_spans", "fenced_divs"),
    *("bracketed_spans", "header_attributes", "link_attributes", "inline_code_attributes"),
    *("grid_tables", "multiline_tables", "simple_tables", "implicit_figures", "smart"),
)
MARKDOWN = "markdown" + "".join(f"-{ext}" for ext in _DISABLED)
# --sandbox: pandoc reads nothing but stdin (includes are inlined beforehand).
# --reference-location=block: footnotes stay next to their paragraph, i.e. in its segment.
PANDOC_ARGS = (
    "-f", "latex", "-t", MARKDOWN, "--wrap=none", "--reference-location=block",
    "--sandbox", "-s",
)  # fmt: skip
PANDOC_HEAP = "+RTS -M2g -RTS"  # pandoc's manual: cap the heap for untrusted input
MIN_TEXT_RATIO = 0.2  # pandoc output under 20% of the source's text -> try the fallback
# A reference list with fewer entries than this share of the source's was cut short: pandoc
# drops a whole list without an error when one entry has a construct it cannot read. On an
# arXiv crawl of about 680 papers with a bibliography, 625 keep every entry, 17 none and four
# 6% to 51%, none in between, so any share from 52% to 99% decides alike.
MIN_REFERENCE_SHARE = 0.9
_BODY_MARK = "@@docingest-body@@"
_TEMPLATE = f"$meta-json$\n{_BODY_MARK}\n$body$\n"


def _pylatexenc_version() -> str:
    try:
        return version("pylatexenc")
    except PackageNotFoundError:
        return "missing"


@dataclass
class _Attempt:
    segments: list[Segment]
    metadata: SourceMetadata | None
    warnings: list[str] = field(default_factory=list)
    stderr: str = ""

    @property
    def n_chars(self) -> int:
        return sum(len(s.text) for s in self.segments)


@dataclass
class _Piece:
    """One part of a document converted on its own: the document that holds just that part
    (``document``, with the macros the earlier parts defined; ``bare``, without them, for
    pylatexenc, which does not expand them), what pandoc made of it, and why it was not taken."""

    document: str
    bare: str
    attempt: _Attempt | None = None
    failure: _Failure | None = None


@dataclass
class _Sectioned:
    conv: Conversion
    plain: list[str]  # the (bare) documents of the pieces that went to plain text
    deadline: float  # the time budget the pieces shared, for whatever runs after them


@dataclass(frozen=True)
class _Failure:
    reason: str
    environmental: bool = False  # the machine, not the document: a later run may succeed


def _environmental(returncode: int) -> bool:
    """pandoc's own error exits (1-99; 64 is a parse error, 91 a macro loop) are about
    the document and recur on every run. A signal (negative: the OOM killer, a kill) or
    a GHC runtime abort (251: heap exhausted under ``PANDOC_HEAP``) is the machine's."""
    return not 0 < returncode < 100


class PandocLatexConverter:
    def __init__(self, cfg: LatexConfig | None = None):
        self.cfg = cfg or LatexConfig()

    # ------------------------------------------------------------- identity
    @cached_property
    def pandoc_path(self) -> str | None:
        if self.cfg.pandoc_path:
            return self.cfg.pandoc_path
        try:
            import pypandoc

            return pypandoc.get_pandoc_path()  # the binary bundled by pypandoc-binary
        except (ImportError, OSError):
            return shutil.which("pandoc")

    @cached_property
    def pandoc_version(self) -> str | None:
        if not self.pandoc_path:
            return None
        try:
            out = subprocess.run(
                [self.pandoc_path, "--version"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        m = re.match(r"pandoc(?:\.exe)?\s+(\S+)", out)
        return m.group(1) if m else None

    @cached_property
    def fingerprint(self) -> str:
        fallback = f"pylatexenc {_pylatexenc_version()}" if self.cfg.fallback else "no fallback"
        return (
            f"pandoc-latex r{REVISION} | pandoc {self.pandoc_version or 'unavailable'} | "
            f"{' '.join(PANDOC_ARGS)} | split_level={self.cfg.split_level} | {fallback}"
        )

    @property
    def engine(self) -> str:
        return f"pandoc {self.pandoc_version or 'unknown'}"

    # ------------------------------------------------------------- convert
    def convert(self, path: Path) -> Conversion:
        max_bytes = self.cfg.max_archive_mb * 2**20
        with tempfile.TemporaryDirectory(prefix="docingest-latex-") as tmp:
            work = Path(tmp)
            try:
                src = unpack(path, work / "src", max_bytes=max_bytes)
                warnings = list(src.warnings)
                main = src.main
                if main is None:
                    main, why = find_main(src.root)
                    warnings += why
                flat = flatten(main, src.root, max_chars=max_bytes)
            except OSError as e:  # unreadable file in the source tree, disk full, ...
                raise ConversionError(f"cannot read LaTeX source {path.name}: {e}") from e
            warnings += flat.warnings
            latex, why = prepare_for_pandoc(flat.text)
            warnings += why
            return self._convert_latex(latex, work, warnings, path.name)

    def _convert_latex(self, latex: str, work: Path, warnings: list[str], name: str) -> Conversion:
        attempt, failure = self._pandoc(latex, work)
        if attempt is not None:
            failure = _unacceptable(attempt, latex)
            if failure is None:
                conv = _conversion(attempt, PageMethod.LATEX, self.engine, warnings)
                deadline = time.monotonic() + self.cfg.timeout_s
                return self._guard_references(conv, latex, work, deadline)
        assert failure is not None
        problem = failure.reason
        got = attempt.n_chars if attempt else 0
        if self.cfg.fallback:
            if not failure.environmental:
                sectioned, machine = self._by_section(latex, work, warnings, problem)
                if sectioned is not None:
                    return self._guard_references(
                        sectioned.conv, latex, work, sectioned.deadline, sectioned.plain
                    )
                if machine:  # a timeout while sectioning: the plain text is not the paper's result
                    failure = _Failure(failure.reason, environmental=True)
            try:
                plain = self._plaintext(latex)
            except Exception as e:  # pylatexenc bugs of any kind mean "no fallback"
                plain = None
                problem += f"; pylatexenc failed: {e}"
            if plain is not None and plain.n_chars > got:
                engine = f"pylatexenc {_pylatexenc_version()}"
                note = f"{problem}; used the pylatexenc plain-text fallback"
                return _conversion(
                    plain,
                    PageMethod.LATEX_PLAINTEXT,
                    engine,
                    [*warnings, note],
                    degraded=failure.environmental,
                )
        if attempt is not None and got:
            return _conversion(
                attempt,
                PageMethod.LATEX,
                self.engine,
                [*warnings, problem],
                degraded=failure.environmental,
            )
        context = f" ({'; '.join(warnings)})" if warnings else ""
        raise ConversionError(f"{name}: {problem}{context}")

    def _labelled(self, conv: Conversion) -> Conversion:
        """``conv``, with the engine naming pylatexenc too when it made some of the segments."""
        if any(s.method == PageMethod.LATEX_PLAINTEXT for s in conv.segments):
            return replace(conv, engine=f"{self.engine} + pylatexenc {_pylatexenc_version()}")
        return conv

    # ----------------------------------------------------- section by section
    def _by_section(
        self, latex: str, work: Path, warnings: list[str], problem: str
    ) -> tuple[_Sectioned | None, bool]:
        """Convert the document one top-level section at a time, after pandoc failed on it whole.

        Each piece is a document of its own: the preamble, the macros that the earlier pieces
        defined in the body, and the piece. pandoc stops at the first error, so one construct
        it cannot read used to send the whole paper to plain text; now only the pieces that
        fail, or keep under ``MIN_TEXT_RATIO`` of their text, are plain text. The pieces share
        one budget of ``timeout_s`` seconds, which the reference guard shares. Returns the
        result, or None when sectioning cannot help: fewer than two pieces, a preamble pandoc
        cannot read, or no piece that pandoc converts. The second item says that the machine
        (a timeout) was part of the reason, so the plain text of the whole paper is not final.
        """
        pre, body = split_document(latex)
        front, sections = section_units(body)
        texts = [t for t in (front, *sections) if t.strip()]
        if len(texts) < 2:
            return None, False
        tail = "\n\\end{document}\n" if pre else "\n"
        deadline = time.monotonic() + self.cfg.timeout_s
        base, failure = self._pandoc_within(f"{pre}{tail}", work, deadline)
        if base is None or failure is not None:
            return None, failure is not None and failure.environmental
        pieces = self._convert_pieces(pre, tail, texts, work, deadline)
        if not any(p.attempt and p.attempt.n_chars and p.failure is None for p in pieces):
            return None, any(p.failure is not None and p.failure.environmental for p in pieces)

        segments: list[Segment] = []
        metadata = base.metadata
        plain: list[str] = []
        lost: list[str] = []
        plain_docs: list[str] = []
        degraded = False
        for i, piece in enumerate(pieces):
            chosen, method, note = piece.attempt, None, ""
            if piece.failure is not None:
                degraded = degraded or piece.failure.environmental
                chosen, method = self._settle(piece)
                name = _first_title(chosen) or f"section {i + 1}"
                if chosen is None:
                    lost.append(name)
                    continue
                kind = "used the pylatexenc plain-text fallback" if method else "kept what it made"
                note = f"{piece.failure.reason}; {kind}"
                if method:
                    plain.append(name)
                    plain_docs.append(piece.bare)
            assert chosen is not None
            if note:
                marked = [replace(s, method=method, warnings=(note,)) for s in chosen.segments]
                chosen = replace(chosen, segments=marked)
            if i == 0 and front.strip() and chosen.metadata:
                metadata = chosen.metadata
            segments += chosen.segments
        took = sum(p.failure is None for p in pieces)
        summary = f"{problem}; converted section by section, pandoc took {took} of {len(pieces)}"
        if plain:
            summary += f" pieces, the pylatexenc plain-text fallback the rest ({_listed(plain)})"
        if lost:
            summary += f"; no text from {_listed(lost)}"
        stderr = "\n".join(p.attempt.stderr for p in pieces if p.attempt)
        attempt = _Attempt(segments, metadata, pandoc_warnings(stderr))
        engine = self.engine
        conv = _conversion(
            attempt, PageMethod.LATEX, engine, [*warnings, summary], degraded=degraded
        )
        return _Sectioned(self._labelled(conv), plain_docs, deadline), False

    def _convert_pieces(
        self, pre: str, tail: str, texts: list[str], work: Path, deadline: float
    ) -> list[_Piece]:
        pieces: list[_Piece] = []
        context = ""
        for text in texts:
            piece = _Piece(f"{pre}\n{context}\n{text}{tail}", f"{pre}\n{text}{tail}")
            piece.attempt, piece.failure = self._pandoc_within(piece.document, work, deadline)
            if piece.attempt is not None:
                piece.failure = _unacceptable(piece.attempt, piece.document, empty_ok=True)
            pieces.append(piece)
            context = "\n".join(x for x in (context, macro_context(text)) if x)
        return pieces

    def _settle(self, piece: _Piece) -> tuple[_Attempt | None, PageMethod | None]:
        """What to use for a piece pandoc did not convert: its plain text if that has more
        text than the little pandoc kept, else what pandoc kept, else nothing."""
        got = piece.attempt.n_chars if piece.attempt else 0
        try:
            plain = self._plaintext(piece.bare)
        except Exception:  # pylatexenc bugs of any kind mean "no fallback" for this piece
            plain = None
        if plain is not None and plain.n_chars > got:
            return plain, PageMethod.LATEX_PLAINTEXT
        return (piece.attempt, None) if got else (None, None)

    # --------------------------------------------------------- reference guard
    def _guard_references(
        self,
        conv: Conversion,
        latex: str,
        work: Path,
        deadline: float,
        plain: Sequence[str] = (),
    ) -> Conversion:
        """Recover a reference list that pandoc dropped or cut short, without an error.

        The lists that ``rewrite_bibliography`` wrote are matched, in order, with the
        "References" headings in the segments, and the numbered entries under each heading
        are counted against the entries in the source. A list under ``MIN_REFERENCE_SHARE`` of
        them (one in a plain-text piece, listed in ``plain``, is not lost) is converted on
        its own, with the preamble and the macros of the body: through pandoc if that keeps
        enough entries, else as pylatexenc plain text. Only that heading and its entries are
        replaced; the text that follows the list in the same segment stays, and so do the
        lists that are complete. The new segments and the conversion carry a warning. The
        runs share what is left of ``deadline``.
        """
        blocks = bibliography_blocks(latex)
        if not blocks:
            return conv
        found = _reference_lists(conv.segments)
        # With a heading for each list, the entries under it are the list's; with some heading
        # missing (pandoc dropped it as well), the lists are matched by their citation keys.
        mapped = len(found) == len(blocks)
        kept = [f.items for f in found] if mapped else _kept_by_key(blocks, conv.segments)
        expected = [count_bibliography_entries(b) for b in blocks]
        lost = [
            i
            for i, (k, e) in enumerate(zip(kept, expected, strict=True))
            if e and k < MIN_REFERENCE_SHARE * e and not _in_plain(blocks[i], plain)
        ]
        if not lost:
            return conv
        pre, body = split_document(latex)
        tail = "\n\\end{document}\n" if pre else "\n"
        macros = macro_context(body)
        degraded = conv.degraded
        notes: list[str] = []
        replacements: dict[int, list[Segment]] = {}
        for i in lost:
            documents = (f"{pre}\n{macros}\n{blocks[i]}{tail}", f"{pre}\n{blocks[i]}{tail}")
            seen = f"pandoc kept {kept[i]} of {expected[i]} reference entries"
            if len(blocks) > 1:
                seen += f" in list {i + 1} of {len(blocks)}"
            got, method, machine = self._recover_references(
                documents, expected[i], kept[i], work, deadline
            )
            degraded = degraded or machine
            if got is None:
                notes.append(f"{seen}; the list could not be recovered")
                continue
            how = "as plain text" if method else "with pandoc"
            notes.append(f"{seen}; converted the bibliography on its own {how}")
            replacements[i] = [
                replace(s, method=method, warnings=(notes[-1],)) for s in got.segments
            ]
        if not mapped:  # a list without its heading: no way to tell where it stood, so at the end
            segments = [*conv.segments, *(s for new in replacements.values() for s in new)]
        else:
            segments = _put_back(conv.segments, found, replacements)
        return self._labelled(
            replace(conv, segments=segments, warnings=[*conv.warnings, *notes], degraded=degraded)
        )

    def _recover_references(
        self, documents: tuple[str, str], expected: int, kept: int, work: Path, deadline: float
    ) -> tuple[_Attempt | None, PageMethod | None, bool]:
        """A reference list alone, converted: ``(result, method, the machine failed)``.

        ``documents`` holds the list with the macros of the body (for pandoc, which expands
        them) and without (for pylatexenc, which does not). pandoc's result needs
        ``MIN_REFERENCE_SHARE`` of the entries, else the plain text is used, if it has more
        entries than the ``kept`` ones: an entry that neither converter can read (an unclosed
        brace) cuts the list short for both, and plain text would only be a worse list.
        """
        with_macros, without = documents
        attempt, failure = self._pandoc_within(with_macros, work, deadline)
        degraded = failure is not None and failure.environmental
        if attempt is not None:
            items = sum(f.items for f in _reference_lists(attempt.segments))
            if items >= MIN_REFERENCE_SHARE * expected:
                return attempt, None, degraded
        if self.cfg.fallback:
            try:
                plain = self._plaintext(without)
            except Exception:  # pylatexenc bugs of any kind mean "no fallback"
                return None, None, degraded
            entries = sum(len(_PLAIN_ENTRY.findall(s.text)) for s in plain.segments)
            if entries > kept:
                return plain, PageMethod.LATEX_PLAINTEXT, degraded
        return None, None, degraded

    # --------------------------------------------------------------- pandoc
    def _pandoc_within(
        self, latex: str, work: Path, deadline: float
    ) -> tuple[_Attempt | None, _Failure | None]:
        """:meth:`_pandoc` with what is left of a shared time budget as its timeout."""
        if (remaining := deadline - time.monotonic()) <= 0:
            return None, _Failure("no time left for this run", environmental=True)
        return self._pandoc(latex, work, remaining)

    def _pandoc(
        self, latex: str, work: Path, timeout: float | None = None
    ) -> tuple[_Attempt | None, _Failure | None]:
        if not self.pandoc_path:
            return None, _Failure("pandoc not found", environmental=True)
        template = work / "docingest-template.md"
        template.write_text(_TEMPLATE)
        cmd = [self.pandoc_path, *PANDOC_HEAP.split(), *PANDOC_ARGS, f"--template={template}"]
        limit = timeout or self.cfg.timeout_s
        try:
            proc = subprocess.run(
                cmd,
                input=latex.encode("utf-8"),
                capture_output=True,
                cwd=work,
                timeout=limit,
                check=False,
            )
        except subprocess.TimeoutExpired:  # a loaded machine, or a document pandoc loops on
            return None, _Failure(f"pandoc timed out after {limit:g}s", environmental=True)
        except OSError as e:
            return None, _Failure(f"pandoc could not run: {e}", environmental=True)
        stderr = proc.stderr.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            detail = " ".join(stderr.split())[:300]
            reason = f"pandoc exited with code {proc.returncode}: {detail}"
            return None, _Failure(reason, environmental=_environmental(proc.returncode))
        meta_json, _, body = proc.stdout.decode("utf-8", errors="replace").partition(_BODY_MARK)
        try:
            meta = json.loads(meta_json)
        except ValueError:
            meta = {}
        metadata = markdown_metadata(meta if isinstance(meta, dict) else {})
        parts = split_markdown(body, self.cfg.split_level)
        abstract = metadata.abstract if metadata else None
        return _Attempt(assemble(parts, abstract), metadata, pandoc_warnings(stderr), stderr), None

    # ------------------------------------------------------------- fallback
    def _plaintext(self, latex: str) -> _Attempt:
        from pylatexenc.latex2text import LatexNodes2Text

        l2t = LatexNodes2Text(latex_context=_text_context(), math_mode="verbatim")
        crude: list[str] = []

        def text(fragment: str) -> str:
            fragment, restore = _take_code_blocks(fragment)
            fragment = _plain_friendly(fragment)
            try:
                out = l2t.latex_to_text(fragment)
            except Exception:  # pylatexenc has spec bugs (e.g. \href in a footnote)
                crude.append(fragment)
                out = _crude_text(fragment)
            out = re.sub(r"\n{3,}", "\n\n", "\n".join(ln.rstrip() for ln in out.splitlines()))
            return restore(out)

        title = _without_thanks(_braced_arg(latex, "title"))
        authors = _without_thanks(_braced_arg(latex, "author"))
        body = document_body(latex)
        abstract = None
        if found := next(regions(body, _ABSTRACT_BEGIN, _ABSTRACT_END), None):
            begin, end = found
            abstract = text(body[begin.end() : end.start()]).strip() or None
            body = body[: begin.start()] + body[end.end() :]
        parts = []
        for level, head, content in split_sections(body, self.cfg.split_level):
            heading = inline_text(text(head)) if head is not None else None
            parts.append(
                (level, heading, f"{'#' * level} {heading}" if heading else "", text(content))
            )
        names = []
        if authors:
            names = author_names([text(a) for a in re.split(r"\\and(?![a-zA-Z@])", authors)])
        metadata = _metadata(inline_text(text(title)) if title else None, names, abstract)
        notes = (
            [f"pylatexenc failed on {len(crude)} fragment(s); kept their bare text"]
            if crude
            else []
        )
        return _Attempt(assemble(parts, abstract), metadata, notes)


def _unacceptable(attempt: _Attempt, latex: str, *, empty_ok: bool = False) -> _Failure | None:
    """Why pandoc's output for ``latex`` is not taken, or None when it is. ``empty_ok``: a
    piece of a document with no text in it (a bare ``\\maketitle``) may come out empty."""
    expected = approx_text_length(latex)
    if attempt.n_chars == 0:
        return None if empty_ok and expected == 0 else _Failure("pandoc produced no text")
    if attempt.n_chars < MIN_TEXT_RATIO * expected:
        return _Failure(f"pandoc kept {attempt.n_chars} chars of ~{expected} in the source")
    return None


def _first_title(attempt: _Attempt | None) -> str | None:
    return next((s.title for s in attempt.segments if s.title), None) if attempt else None


@dataclass(frozen=True)
class _ListFound:
    """One "References" heading at the start of a segment and what follows it: the entries
    under it (only under the last of several headings in a row), where it starts, the
    headings of other sections glued before it, and the text of the segment after the entries."""

    segment: int
    position: int  # among the "References" headings that open the segment
    headings: int
    items: int
    start: int
    lead: str
    remainder: str


# A numbered item; group 2 is set when it starts with its citation key.
_ITEM_LINE = re.compile(r"(\d+)\.(?:[ \t]+(\[@)?|[ \t]*$)")
_HEADING_RUN = re.compile(r"(?:#{1,6}[ \t]+\S[^\n]*(?:\n+|\Z))+")
_REFERENCES_LINE = re.compile(r"^#{1,6}[ \t]+References[ \t]*$", re.M)


def _numbered_list(text: str) -> tuple[int, int]:
    """``(entries, end offset)`` of the reference list that opens ``text``: items numbered
    1, 2, ..., their indented lines and the blank lines between them. An item that
    continues the numbering belongs to the list whether it has a key or not (an entry
    written without ``\\cite`` has none), but only the ones that start with ``[@key]`` are
    counted as entries. The end moves only once a keyed item has been seen, so numbered items
    without a single key are the paper's own list, not entries. Any other numbered item ends
    it: it restarts at 1, so it is the paper's own list."""
    items = keyed = end = offset = 0
    for line in text.split("\n"):
        nxt = offset + len(line) + 1
        m = _ITEM_LINE.match(line)
        if m and int(m[1]) == items + 1:
            items, keyed = items + 1, keyed + bool(m[2])
            end = nxt if keyed else end
        elif line.strip() and (items == 0 or not line[0].isspace()):
            break
        elif line.strip() and keyed:
            end = nxt
        offset = nxt
    return keyed, min(end, len(text))


def _reference_lists(segments: Sequence[Segment]) -> list[_ListFound]:
    """The reference lists in ``segments``, in order, one per "References" heading. The
    heading of a list that vanished is glued onto the next section, so a segment can open
    with several headings, the paper's own among them (a "REFERENCES" before the list)."""
    found: list[_ListFound] = []
    for at, s in enumerate(segments):
        run = _HEADING_RUN.match(s.text)
        heads = list(_REFERENCES_LINE.finditer(run[0])) if run else []
        if not heads:
            continue
        plain = s.method == PageMethod.LATEX_PLAINTEXT  # its list has no numbered entries
        after = s.text[heads[-1].end() :].lstrip("\n")
        count, end = (0, 0) if plain else _numbered_list(after)
        remainder = "" if plain else after[end:].strip()
        previous = 0
        for j, m in enumerate(heads):
            lead = s.text[previous : m.start()].strip()
            previous = m.end()
            n = len(heads)
            found.append(
                _ListFound(at, j, n, count if j == n - 1 else 0, m.start(), lead, remainder)
            )
    return found


def _put_back(
    segments: Sequence[Segment], found: Sequence[_ListFound], new: dict[int, list[Segment]]
) -> list[Segment]:
    """``segments`` with the lists numbered in ``new`` replaced by the segments in it: their
    heading and entries go, the text after the entries stays after the new segments, and a
    complete list that shares its segment with a lost one stays as it was."""
    out: list[Segment] = []
    for at, s in enumerate(segments):
        mine = [(i, f) for i, f in enumerate(found) if f.segment == at]
        if not any(i in new for i, _ in mine):
            out.append(s)
            continue
        for i, f in mine:
            last = f.position == f.headings - 1
            lead = f"{f.lead}\n\n" if f.lead else ""
            if i in new:
                first, *others = new[i]
                out += [replace(first, text=lead + first.text), *others]
                if last and f.remainder:
                    out.append(replace(s, text=f.remainder))
            elif last:
                out.append(replace(s, text=lead + s.text[f.start :]))
    return out


_KEYED_ITEM = re.compile(r"^\d+\.[ \t]+\[@([^\]\s;]+)\]", re.M)
_BLOCK_KEY = re.compile(r"\\item \\cite\{([^{},]*)")


def _kept_by_key(blocks: Sequence[str], segments: Sequence[Segment]) -> list[int]:
    """How many entries of each list are in the segments, found by their citation keys (a key
    in two lists is counted for the first that has it)."""
    available = Counter(
        key
        for s in segments
        if s.method != PageMethod.LATEX_PLAINTEXT
        for key in _KEYED_ITEM.findall(s.text)
    )
    kept: list[int] = []
    for block in blocks:
        n = 0
        for key in _BLOCK_KEY.findall(block):
            if available[key] > 0:
                available[key] -= 1
                n += 1
        kept.append(n)
    return kept


def _in_plain(block: str, plain: Sequence[str]) -> bool:
    return any(block in doc for doc in plain)


def _listed(names: list[str]) -> str:
    shown = "; ".join(n[:60] for n in names[:5])
    return shown + ("; ..." if len(names) > 5 else "")


def _conversion(
    attempt: _Attempt,
    method: PageMethod,
    engine: str,
    warnings: list[str],
    *,
    degraded: bool = False,
) -> Conversion:
    meta = attempt.metadata
    return Conversion(
        segments=attempt.segments,
        method=method,
        engine=engine,
        title=meta.title if meta else None,
        metadata=meta,
        warnings=[*warnings, *attempt.warnings],
        degraded=degraded,
    )


# ------------------------------------------------------------ pure helpers
_ATX = re.compile(r"^(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
_FOOTNOTE_REF = re.compile(r"\[\^[^\]]*\]")


def inline_text(md: str) -> str:
    """One-line plain-ish text of a Markdown inline string (titles, headings)."""
    md = _FOOTNOTE_REF.sub("", md)
    md = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", md)  # [text](#label) -> text
    md = md.replace("\\\n", " ")
    return " ".join(md.split())


def split_markdown(md: str, max_level: int) -> list[tuple[int, str | None, str, str]]:
    """Cut Markdown at ATX headings of level <= ``max_level``.

    Returns ``(level, title, heading line, content)``; content before the first heading
    comes first (level 0, no title). As in pandoc's reader (``blank_before_header``), a
    ``#`` line is a heading only at the start or after a blank line, and never inside a
    code fence. pandoc writes every heading that way, and ``$$`` display math cannot
    hold a blank line (TeX forbids it), so a ``#`` line in math is never taken for one.
    Counting ``$$`` per line does not work: pandoc writes adjacent inline maths
    (``$3$$\\times 10^{-4}$``) with a single ``$$``.
    """
    max_level = max(1, max_level)
    parts: list[tuple[int, str | None, str, list[str]]] = [(0, None, "", [])]
    fence: re.Pattern[str] | None = None
    blank = True  # the start of the document counts as a blank line
    for line in md.split("\n"):
        if fence is not None:
            if fence.match(line):
                fence = None
        elif f := _FENCE.match(line):
            char, n = f.group(1)[0], len(f.group(1))
            fence = re.compile(rf"^[ \t]{{0,3}}{re.escape(char)}{{{n},}}[ \t]*$")
        elif blank and (h := _ATX.match(line)) and len(h.group(1)) <= max_level:
            parts.append((len(h.group(1)), inline_text(h.group(2)), line, []))
            blank = False
            continue
        parts[-1][3].append(line)
        blank = not line.strip()
    return [(lvl, title, head, "\n".join(lines)) for lvl, title, head, lines in parts]


def collapse_blank_lines(md: str) -> str:
    """``md`` with each run of blank lines cut to one, except inside a fenced code block,
    whose lines are the source it shows."""
    out: list[str] = []
    fence: re.Pattern[str] | None = None
    blank = False
    for line in md.split("\n"):
        if fence is not None:
            if fence.match(line):
                fence = None
        elif f := _FENCE.match(line):
            char, n = f.group(1)[0], len(f.group(1))
            fence = re.compile(rf"^[ \t]{{0,3}}{re.escape(char)}{{{n},}}[ \t]*$")
        elif not line:
            if blank:
                continue
            blank = True
            out.append(line)
            continue
        blank = False
        out.append(line)
    return "\n".join(out)


def assemble(
    parts: Sequence[tuple[int, str | None, str, str]], abstract: str | None = None
) -> list[Segment]:
    """Segments titled with their heading path ("Model > Attention").

    A heading followed directly by a sub-heading is glued onto the next segment instead
    of becoming a segment of its own; the abstract, if any, comes first.
    """
    segments = [Segment(f"# Abstract\n\n{abstract.strip()}", "Abstract")] if abstract else []
    path: dict[int, str] = {}
    carry: list[str] = []
    carry_title: str | None = None
    for level, title, heading, content in parts:
        full = None
        if title is not None:
            path = {k: v for k, v in path.items() if k < level}
            path[level] = title
            full = " > ".join(path[k] for k in sorted(path) if path[k])
        body = collapse_blank_lines(content.strip())
        if heading and not body:
            carry.append(heading)
            carry_title = full
            continue
        text = "\n\n".join(x for x in (*carry, heading, body) if x)
        carry = []
        if text:
            segments.append(Segment(text, full or None))
    if carry:
        segments.append(Segment("\n\n".join(carry), carry_title))
    return segments


def _meta_text(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        return "\n\n".join(t for v in value if (t := _meta_text(v))) or None
    return None


_ORG = re.compile(
    r"(?i)\b(univ\w*|institut\w*|laborator\w*|labs?|college|school|department|dept|faculty|"
    r"academy|cent(?:er|re)|hospital|inc|ltd|llc|corp\w*|gmbh|research|foundation|google|"
    r"microsoft|deepmind|meta|facebook|nvidia|amazon|ibm|apple|brain|email|correspondence)\b"
)


def _looks_like_name(s: str) -> bool:
    words = s.split()
    return 0 < len(words) <= 6 and len(s) <= 60 and s[0].isupper() and ":" not in s


def author_names(value: object) -> list[str]:
    """Best-effort author names from pandoc's ``author`` metadata (or plain text).

    LaTeX author blocks mix names, affiliation markers, affiliations and emails; keep
    the names: the first line of each entry, plus following lines until one looks
    like an affiliation or contact. Inline math (``$^{1}$``) separates names.
    """
    entries = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    names: list[str] = []
    for entry in entries:
        if not isinstance(entry, str):
            continue
        lines = [ln.strip() for ln in re.split(r"\\\n|\n", entry)]
        for i, line in enumerate(ln for ln in lines if ln):
            if line.startswith(("$", "^")) or any(c in line for c in "@`") or "http" in line:
                break
            if i and _ORG.search(line):
                break
            bare = re.sub(r"\$[^$]*\$", ",", _FOOTNOTE_REF.sub("", line))
            bare = re.sub(r"[*†‡§¶∗⋆\d^{}\\]", "", bare)
            for part in re.split(r",|;|&|\band\b", bare):
                name = " ".join(part.split()).strip(" .")
                if _looks_like_name(name):
                    names.append(name)
    return list(dict.fromkeys(names))


def _metadata(title: str | None, authors: list[str], abstract: str | None) -> SourceMetadata | None:
    if not (title or authors or abstract):
        return None
    return SourceMetadata(title=title or None, authors=authors, abstract=abstract)


def markdown_metadata(meta: dict) -> SourceMetadata | None:
    """SourceMetadata from pandoc's ``$meta-json$`` (values already Markdown)."""
    title = _meta_text(meta.get("title"))
    abstract = _meta_text(meta.get("abstract"))
    authors = author_names(meta.get("author"))
    return _metadata(inline_text(title) if title else None, authors, abstract)


def pandoc_warnings(stderr: str) -> list[str]:
    """pandoc's [WARNING] lines, summarized into one entry (they can number hundreds)."""
    msgs = [
        ln[len("[WARNING]") :].strip() for ln in stderr.splitlines() if ln.startswith("[WARNING]")
    ]
    if not msgs:
        return []
    kinds = list(dict.fromkeys(re.sub(r"\s*at line \d+ column \d+", "", m) for m in msgs))
    sample = "; ".join(kinds[:3])[:300]
    return [f"pandoc: {len(msgs)} warning(s), e.g. {sample}"]


_PLAIN_ENTRY = re.compile(r"^[ \t]*\[?@", re.M)  # an entry in plain text starts with its key
_CITE = re.compile(
    r"\\(?:[cC]ite[a-zA-Z]*|[pP]arencite|[tT]extcite|[aA]utocite)\*?\s*(?:\[[^\]]*\]\s*){0,2}"
    r"\{([^{}]*)\}"
)
_REF = re.compile(r"\\(?:eq|auto|page|name|c|C)?ref\*?\s*\{([^{}]*)\}")


def _escape_url(url: str) -> str:
    return re.sub(r"([%#_&$])", r"\\\1", url.strip())


_ABSTRACT_BEGIN = re.compile(r"\\begin\s*\{abstract\}")
_ABSTRACT_END = re.compile(r"\\end\s*\{abstract\}")
_VERB_MARK = "\ue000"  # private-use character: the delimiter of a rewritten \verb
_MINTED_ARGS = re.compile(r"\A[ \t]*(?:\[[^\]\n]*\])?[ \t]*\{[^{}\n]*\}")  # [opts]{language}
_LISTING_OPTS = re.compile(r"\A[ \t]*\[[^\]\n]*\]")  # lstlisting / fancyvrb [options]


def _verbatim_text(node: Any) -> str:
    """The text of a ``\\verb`` or ``verbatim`` node, exactly as written."""
    return getattr(node.nodeargd, "verbatim_text", "") or ""


@cache
def _text_context() -> Any:
    """pylatexenc's defaults, plus the text they silently drop: the argument of
    ``\\texttt``, ``\\textsf``, ``\\textup``, ``\\textmd``, ``\\mbox`` and ``\\verb`` and
    the body of ``verbatim`` (model, dataset and code names vanished from the text)."""
    from pylatexenc.latex2text import (
        EnvironmentTextSpec,
        MacroTextSpec,
        get_default_latex_context_db,
    )

    db = get_default_latex_context_db()
    db.add_context_category(
        "docingest",
        prepend=True,
        macros=[
            *(
                MacroTextSpec(name, discard=False)  # discard=None drops the argument
                for name in ("texttt", "textsf", "textup", "textmd", "mbox")
            ),
            MacroTextSpec("verb", simplify_repl=_verbatim_text),
        ],
        environments=[EnvironmentTextSpec("verbatim", simplify_repl=_verbatim_text)],
    )
    return db


_MINTED_LANGUAGE = re.compile(r"\A[ \t]*(?:\[[^\]\n]*\])?[ \t]*\{([^{}\n]*)\}")


def _take_code_blocks(latex: str) -> tuple[str, Callable[[str], str]]:
    """``latex`` with each ``minted`` listing in the language of the circuits (see
    ``protect_circuits``) replaced by a word on a line of its own, and the function that puts the
    listings back, as the fenced code blocks pandoc writes for them, into the text made from it.

    pylatexenc would otherwise read a circuit as markup, or drop it: what it printed of
    ``\\gate{H} & \\ctrl{1}`` depended on its macro tables. The words are letters and digits, so
    neither pylatexenc nor the crude fallback changes them."""
    if "minted" not in latex:
        return latex, lambda out: out
    word = "DOCINGESTCODE"
    while word in latex:
        word += "X"
    blocks: list[str] = []
    parts: list[str] = []
    pos = 0
    for v in verbatim_parts(latex):
        found = _MINTED_LANGUAGE.match(v.body) if v.env == "minted" else None
        if found is None or found[1].strip() != CIRCUIT_LANGUAGE:
            continue
        code = v.body[found.end() :].removeprefix("\n").removesuffix("\n").expandtabs(4)
        fence = "`" * max(3, len(max(re.findall(r"`+", code), key=len, default="")) + 1)
        parts += (latex[pos : v.start], f"\n\n{word}{len(blocks)}END\n\n")
        blocks.append(f"{fence} {CIRCUIT_LANGUAGE}\n{code}\n{fence}")
        pos = v.end
    if not blocks:
        return latex, lambda out: out
    parts.append(latex[pos:])

    def restore(out: str) -> str:
        pieces = re.split(rf"{word}(\d+)END", out)
        shown = [blocks[int(p)] if i % 2 else p.strip() for i, p in enumerate(pieces)]
        return "\n\n".join(p for p in shown if p.strip())

    return "".join(parts), restore


def _plain_verbatim(latex: str) -> str:
    """Every verbatim construct as the two pylatexenc reads verbatim, ``\\verb`` and the
    ``verbatim`` environment (it takes the ``%`` in ``\\lstinline!n % 2!`` for a comment
    and prints the language of ``minted``); comment and filecontents bodies dropped."""
    parts: list[str] = []
    pos = 0
    for v in verbatim_parts(latex):
        parts.append(latex[pos : v.start])
        pos = v.end
        if v.env is None:
            parts.append(f"\\verb{_VERB_MARK}{v.body.replace(_VERB_MARK, '')}{_VERB_MARK}")
        elif v.env != "comment" and not v.env.startswith("filecontents"):
            body = v.body
            if v.env == "minted":
                body = _MINTED_ARGS.sub("", body)
            elif not v.env.startswith("verbatim"):
                body = _LISTING_OPTS.sub("", body)
            parts.append(f"\\begin{{verbatim}}{body}\\end{{verbatim}}")
    parts.append(latex[pos:])
    return "".join(parts)


def _plain_friendly(latex: str) -> str:
    """Rewrites so pylatexenc's text keeps citations, references, links and verbatim
    text readable."""
    latex = _plain_verbatim(latex)
    latex = re.sub(
        r"\\href\s*\{([^{}]*)\}\s*\{([^{}]*)\}",
        lambda m: (
            f"{m[2]} ({_escape_url(m[1])})" if m[2].strip() != m[1].strip() else _escape_url(m[1])
        ),
        latex,
    )
    latex = re.sub(r"\\url\s*\{([^{}]*)\}", lambda m: _escape_url(m[1]), latex)
    latex = sub_regions(latex, IFFALSE, FI, lambda _: "")  # linear, unlike \\iffalse.*?\\fi
    latex = re.sub(r"\\(?:maketitle|tableofcontents)\b", "", latex)
    latex = _CITE.sub(
        lambda m: "[" + "; ".join(f"@{k.strip()}" for k in m[1].split(",")) + "]", latex
    )
    return _REF.sub(lambda m: f"[{m[1].strip()}]", latex)


def _crude_text(latex: str) -> str:
    """Last resort when pylatexenc raises: drop control words and braces, keep the words."""
    latex = re.sub(r"\\(?:begin|end)\s*\{[^{}]*\}", "\n", latex)
    latex = re.sub(r"\\[a-zA-Z@]+\*?(?:\[[^\]]*\])?", " ", latex)
    latex = re.sub(r"\\(.)", r"\1", latex)
    return re.sub(r"[ \t]+", " ", latex.replace("{", "").replace("}", ""))


def _without_thanks(latex: str | None) -> str | None:
    """Drop ``\\thanks{...}`` footnotes (pylatexenc would glue them onto the name)."""
    while latex and (m := re.search(r"\\thanks\s*(?=\{)", latex)):
        end = match_brace(latex, m.end())
        latex = latex[: m.start()] + (latex[end:] if end else "")
    return latex


def _braced_arg(latex: str, command: str) -> str | None:
    """The mandatory argument of the first ``\\command[...]{...}``."""
    m = re.search(rf"\\{command}(?![a-zA-Z@])\s*(?:\[[^\]]*\])?\s*(?=\{{)", latex)
    if m is None:
        return None
    end = match_brace(latex, m.end())
    return latex[m.end() + 1 : end - 1] if end else None
