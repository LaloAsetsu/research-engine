"""Section-by-section conversion and the reference-loss guard of the LaTeX converter.

pandoc is replaced by a rule (``StubPandoc``): a document that holds the word BROKEN is a
parse error (exit 64), anything else becomes Markdown the crude way (sections to headings,
list items to numbered items). That lets the tests count the runs and read the documents
the converter hands to pandoc. ``tests/integration/test_pandoc_latex_sections.py`` runs the
same cases through the real pandoc. Every fixture is invented.
"""

import re

import pytest
from timing import assert_linear

from docingest.adapters.converters import pandoc_latex
from docingest.adapters.converters.latex_source import (
    bibliography_blocks,
    count_bibliography_entries,
    macro_context,
    prepare_for_pandoc,
    section_units,
    split_document,
)
from docingest.adapters.converters.pandoc_latex import (
    PandocLatexConverter,
    _Attempt,
    _Failure,
    _numbered_list,
    assemble,
    markdown_metadata,
    split_markdown,
)
from docingest.config import LatexConfig
from docingest.domain.models import PageMethod

DOC = "\\documentclass{article}\n\\title{Lantern Drift}\n\\begin{document}\n%s\n\\end{document}\n"
LONG = "These sentences carry enough words for the length check of the converter to be met. "
BIB = (
    "\\begin{thebibliography}{4}\n"
    + "".join(f"\\bibitem{{k{i}}} Author {i}, Title {i} ({2000 + i}).\n" for i in range(1, 5))
    + "\\end{thebibliography}"
)


def sections(*names: str, broken: str = "", bib: str = BIB) -> str:
    """A body with one section per name; the section named ``broken`` is a parse error."""
    parts = [
        f"\\section{{{n}}}\n{LONG}{'BROKEN ' if n == broken else ''}\\cite{{k1}}\n" for n in names
    ]
    return DOC % ("\\maketitle\n" + "".join(parts) + bib)


# ------------------------------------------------------------ the stand-in
class StubPandoc(PandocLatexConverter):
    def __init__(self, cfg: LatexConfig | None = None, *, drops_lists: bool = False):
        super().__init__(cfg or LatexConfig(pandoc_path="/stub/pandoc"))
        self.drops_lists = drops_lists  # the list vanishes from any document with other sections
        self.calls: list[str] = []
        self.timeouts: list[float | None] = []
        self.failures: list[_Failure | None] = []  # in order, instead of converting; None: convert
        self.seconds = 0.0  # advance the clock (see the `clock` fixture) on each run

    def _pandoc(self, latex, work, timeout=None):
        self.calls.append(latex)
        self.timeouts.append(timeout)
        pandoc_latex.time.advance(self.seconds)  # type: ignore[attr-defined]
        if self.failures and (failure := self.failures.pop(0)) is not None:
            return None, failure
        if "BROKEN" in latex:
            return None, _Failure("pandoc exited with code 64: stub parse error")
        drop = self.drops_lists and re.search(r"\\section\{", latex) is not None
        md = self.markdown(latex, drop)
        title = re.search(r"\\title\{([^{}]*)\}", latex)
        meta = markdown_metadata({"title": title[1]}) if title else None
        return _Attempt(assemble(split_markdown(md, self.cfg.split_level)), meta, [], ""), None

    @staticmethod
    def markdown(latex: str, drop_list: bool) -> str:
        body = latex.partition("\\begin{document}")[2].replace("\\item", "\n\\item")
        out: list[str] = []
        n = 0
        for line in body.splitlines():
            if m := re.match(r"\\section\*?\{([^{}]*)\}", line):
                out.append(f"\n# {m[1]}\n")
                n = 0
            elif re.match(r"\\begin\{enumerate\}", line):
                n = 0
            elif m := re.match(r"\\item (?!\\cite)(.*)", line):
                n += 1
                out.append(f"{n}.  {m[1]}\n")
            elif m := re.match(r"\\item \\cite\{([^{}]*)\}(.*)", line):
                n += 1
                if not drop_list:
                    out.append(f"{n}.  [@{m[1]}]{m[2]}\n")
            elif not re.match(r"\\(?:newcommand|providecommand|def|begin|end|maketitle)", line):
                out.append(re.sub(r"\\[a-zA-Z]+\*?|[{}]", "", line))
        return "\n".join(out)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """A clock the stub advances, so a time budget can be tested without waiting."""

    class Clock:
        now = 0.0

        def monotonic(self):
            return self.now

        def advance(self, seconds):
            self.now += seconds

    fake = Clock()
    monkeypatch.setattr(pandoc_latex, "time", fake)
    return fake


def run(conv: StubPandoc, text: str, tmp_path):
    return conv._convert_latex(prepare_for_pandoc(text)[0], tmp_path, [], "paper.tex")


def methods(conv):
    return {s.title: s.method for s in conv.segments}


# --------------------------------------------------------- source helpers
def test_section_units_cut_the_body_losslessly():
    body = "Front \\maketitle\n\\section{One} a\n\\subsection{Sub} b\n\\section*{Two} c\n"
    front, units = section_units(body)
    assert front == "Front \\maketitle\n"
    assert units == ["\\section{One} a\n\\subsection{Sub} b\n", "\\section*{Two} c\n"]
    assert front + "".join(units) == body


def test_section_units_follow_chapters_and_parts():
    body = "\\part{P} \\chapter{A} x \\section{S} y \\chapter[Short]{B} z"
    assert section_units(body)[1] == [
        "\\part{P} ",
        "\\chapter{A} x \\section{S} y ",
        "\\chapter[Short]{B} z",
    ]


def test_section_units_without_a_section_or_with_one_at_the_start():
    assert section_units("Just words.") == ("Just words.", [])
    assert section_units("\\section{A} x") == ("", ["\\section{A} x"])
    assert section_units("x \\section{A unclosed y") == ("x \\section{A unclosed y", [])
    assert section_units("") == ("", [])


def test_split_document():
    pre, body = split_document("\\documentclass{a}\n\\begin{document}\nBody\n\\end{document}\nTail")
    assert pre == "\\documentclass{a}\n\\begin{document}" and body == "\nBody\n"
    assert split_document("A fragment") == ("", "A fragment")


def test_macro_context_keeps_definitions_only():
    text = (
        "Text \\newcommand{\\ket}[1]{|#1\\rangle} more \\def\\R{\\mathbb{R}} "
        "\\newtheorem{lemma}{Lemma}[section] end"
    )
    assert macro_context(text).splitlines() == [
        "\\newcommand{\\ket}[1]{|#1\\rangle}",
        "\\def\\R{\\mathbb{R}}",
        "\\newtheorem{lemma}{Lemma}[section]",
    ]
    assert macro_context("No definitions here.") == ""


def test_bibliography_blocks_are_the_lists_rewrite_bibliography_wrote():
    prepared = prepare_for_pandoc(DOC % ("Text.\n" + BIB + "\nMore.\n" + BIB))[0]
    blocks = bibliography_blocks(prepared)
    assert len(blocks) == 2 and all(b.startswith("\\section*{References}") for b in blocks)
    assert count_bibliography_entries(prepared) == 8
    assert count_bibliography_entries("\\item one \\itemsep \\item two") == 0  # not a list
    cut = prepared[: prepared.rindex("\\end{enumerate}")]
    assert len(bibliography_blocks(cut)) == 1  # an unclosed list is not counted
    assert bibliography_blocks("Plain text.") == []


def test_helpers_are_linear_on_pathological_input():
    def bad(k: int) -> str:
        return (
            "\\section{ x " * (10 * k)
            + "\\begin{document}" * k
            + "\\section*{References}\n\\begin{enumerate}\n" * k
        )

    def helpers(text: str) -> None:
        section_units(text)
        split_document(text)
        macro_context(text)
        bibliography_blocks(text)

    # Quadrupling the input must cost far less than 16x. Only the ratio of two runs is checked
    # (see tests/timing.py).
    assert_linear(helpers, bad(1000), bad(4000))


# ------------------------------------------------- section by section
def test_only_the_broken_section_is_plain_text(tmp_path):
    conv = run(StubPandoc(), sections("Intro", "Setup", "Results", broken="Setup"), tmp_path)

    assert conv.method == PageMethod.LATEX and not conv.degraded
    assert methods(conv) == {
        "Intro": None,
        "Setup": PageMethod.LATEX_PLAINTEXT,
        "Results": None,
        "References": None,
    }
    setup = next(s for s in conv.segments if s.title == "Setup")
    assert "pandoc exited with code 64" in setup.warnings[0]
    assert setup.warnings[0].endswith("used the pylatexenc plain-text fallback")
    assert all(not s.warnings for s in conv.segments if s.title != "Setup")
    assert conv.engine.startswith("pandoc ") and " + pylatexenc " in conv.engine
    assert conv.title == "Lantern Drift"
    summary = conv.warnings[-1]
    assert summary.startswith("pandoc exited with code 64")
    assert "converted section by section, pandoc took 4 of 5 pieces" in summary
    assert "the pylatexenc plain-text fallback the rest (Setup)" in summary


def test_the_reference_list_of_a_sectioned_paper_is_kept(tmp_path):
    conv = run(StubPandoc(), sections("Intro", "Setup", broken="Setup"), tmp_path)
    refs = next(s for s in conv.segments if s.title == "References")
    assert re.findall(r"^\d+\.  \[@(\w+)\]", refs.text, re.M) == ["k1", "k2", "k3", "k4"]
    assert not conv.warnings[-1].startswith("pandoc kept")  # no recovery needed


def test_a_clean_paper_is_not_sectioned(tmp_path):
    stub = StubPandoc()
    conv = run(stub, sections("Intro", "Setup"), tmp_path)
    assert len(stub.calls) == 1 and conv.warnings == []
    assert conv.engine == stub.engine and all(s.method is None for s in conv.segments)


def test_when_every_piece_fails_the_whole_paper_is_plain_text_as_before(tmp_path):
    stub = StubPandoc()
    text = DOC % "".join(f"\\section{{S{i}}} {LONG} BROKEN\n" for i in range(3))
    conv = run(stub, text, tmp_path)
    assert conv.method == PageMethod.LATEX_PLAINTEXT and conv.engine.startswith("pylatexenc ")
    assert all(s.method is None and not s.warnings for s in conv.segments)
    assert conv.warnings[-1].endswith("; used the pylatexenc plain-text fallback")
    assert len(stub.calls) == 1 + 1 + 3  # whole, preamble, three sections


@pytest.mark.parametrize(
    "text",
    [
        DOC % f"\\section{{Only}} {LONG} BROKEN",  # one piece: nothing to cut
        DOC % f"BROKEN {LONG} but no section at all",
    ],
)
def test_a_document_in_one_piece_is_not_run_again(tmp_path, text):
    stub = StubPandoc()
    conv = run(stub, text, tmp_path)
    assert conv.method == PageMethod.LATEX_PLAINTEXT and len(stub.calls) == 1


def test_a_preamble_pandoc_cannot_read_stops_after_one_check(tmp_path):
    stub = StubPandoc()
    text = sections("A", "B", "C").replace("\\title", "BROKEN \\title")
    conv = run(stub, text, tmp_path)
    assert conv.method == PageMethod.LATEX_PLAINTEXT
    assert len(stub.calls) == 2  # the whole document and the preamble alone


def test_a_machine_failure_is_not_retried_by_section(tmp_path):
    stub = StubPandoc()
    stub.failures = [_Failure("pandoc timed out after 120s", environmental=True)]
    conv = run(stub, sections("A", "B", "C"), tmp_path)
    assert conv.method == PageMethod.LATEX_PLAINTEXT and conv.degraded
    assert len(stub.calls) == 1


def test_no_fallback_means_no_sectioning(tmp_path):
    stub = StubPandoc(LatexConfig(pandoc_path="/stub/pandoc", fallback=False))
    with pytest.raises(pandoc_latex.ConversionError, match="code 64"):
        run(stub, sections("A", "B", "C", broken="B"), tmp_path)
    assert len(stub.calls) == 1


def test_a_piece_that_keeps_too_little_text_goes_to_plain_text(tmp_path):
    class Terse(StubPandoc):
        def markdown(self, latex, drop_list):
            return (
                "# Tiny"
                if "Setup" in latex and "BROKEN" not in latex
                else super().markdown(latex, drop_list)
            )

    conv = run(Terse(), sections("Intro", "Setup", broken="Intro") + "", tmp_path)
    assert methods(conv)["Setup"] == PageMethod.LATEX_PLAINTEXT
    assert "pandoc kept" in next(s for s in conv.segments if s.title == "Setup").warnings[0]


def test_macros_defined_in_the_body_reach_the_later_pieces(tmp_path):
    stub = StubPandoc()
    text = DOC % (
        "\\section{A} \\newcommand{\\wick}{tallow} \\def\\lamp{oil} "
        + LONG
        + "BROKEN\n\\section{B} "
        + LONG
        + "\\section{C} "
        + LONG
    )
    run(stub, text.replace("\\title", "\\newcommand{\\pre}{x}\\title"), tmp_path)
    b = next(c for c in stub.calls if "\\section{B}" in c and "\\section{C}" not in c)
    c = next(c for c in stub.calls if "\\section{C}" in c and "\\section{B}" not in c)
    a = next(c for c in stub.calls if "\\section{A}" in c and "\\section{B}" not in c)
    assert "\\newcommand{\\wick}{tallow}" in b and "\\def\\lamp{oil}" in b
    assert "\\newcommand{\\wick}{tallow}" in c
    assert a.count("\\newcommand{\\wick}") == 1  # a piece does not get its own definitions twice
    assert "\\newcommand{\\pre}{x}" in b  # the preamble is part of every piece


def test_the_pieces_share_one_time_budget(tmp_path, clock):
    stub = StubPandoc(LatexConfig(pandoc_path="/stub/pandoc", timeout_s=100))
    stub.seconds = 30
    conv = run(stub, sections("A", "B", "C", "D", broken="D"), tmp_path)
    # The whole paper takes 30 s of the clock, the preamble 30 s, then pieces get what is
    # left of 100 s: the front matter, A and B run (to 150 s), C and D have no time left.
    assert len(stub.calls) == 1 + 1 + 3
    assert conv.degraded  # the machine, not the document: not to be cached
    assert [methods(conv)[t] for t in "ABCD"] == [None, None, *[PageMethod.LATEX_PLAINTEXT] * 2]
    assert "no time left" in next(s for s in conv.segments if s.title == "C").warnings[0]


def test_a_piece_pylatexenc_cannot_read_is_dropped_and_said(tmp_path, monkeypatch):
    def boom(self, latex):
        if "Setup" in latex:
            raise RuntimeError("pylatexenc bug")
        return real(self, latex)

    real = PandocLatexConverter._plaintext
    monkeypatch.setattr(PandocLatexConverter, "_plaintext", boom)
    conv = run(StubPandoc(), sections("Intro", "Setup", "Results", broken="Setup"), tmp_path)
    assert [s.title for s in conv.segments] == ["Intro", "Results", "References"]
    assert "no text from section 3" in conv.warnings[-1]


def test_the_plain_text_of_a_piece_has_no_macro_definitions_in_it(tmp_path):
    # pylatexenc prints "#1]" for two of REVTeX's bibliography helpers in a row.
    text = sections("Intro", "Setup", "Results", broken="Setup").replace(
        "\\section{Intro}",
        "\\section{Intro}\\providecommand \\citenamefont [1]{#1}\n\\providecommand{\\Eprint}[2]{\\href{#1}{#2}}",
        1,
    )
    conv = run(StubPandoc(), text, tmp_path)
    setup = next(s for s in conv.segments if s.title == "Setup")
    assert setup.method == PageMethod.LATEX_PLAINTEXT
    assert not any("#1" in s.text for s in conv.segments)
    assert [s.title for s in conv.segments].count(None) == 0  # no untitled junk segment


def test_pandoc_warnings_of_all_pieces_become_one(tmp_path):
    class Noisy(StubPandoc):
        def _pandoc(self, latex, work, timeout=None):
            attempt, failure = super()._pandoc(latex, work, timeout)
            if attempt:
                attempt.stderr = "[WARNING] Could not convert a box at line 1\n"
            return attempt, failure

    conv = run(Noisy(), sections("Intro", "Setup", broken="Setup"), tmp_path)
    assert sum(w.startswith("pandoc: ") for w in conv.warnings) == 1
    assert "pandoc: 3 warning(s)" in conv.warnings[-1]


# ---------------------------------------------------------- reference guard
def refs_text(conv) -> str:
    return "\n".join(s.text for s in conv.segments if s.title == "References")


def test_a_list_pandoc_dropped_is_recovered_by_converting_it_alone(tmp_path):
    stub = StubPandoc(drops_lists=True)
    conv = run(stub, sections("Intro", "Results"), tmp_path)

    assert [s.title for s in conv.segments] == ["Intro", "Results", "References"]
    refs = conv.segments[-1]
    assert re.findall(r"^\d+\.  \[@(\w+)\]", refs.text, re.M) == ["k1", "k2", "k3", "k4"]
    note = "pandoc kept 0 of 4 reference entries; converted the bibliography on its own with pandoc"
    assert refs.warnings == (note,) and conv.warnings == [note]
    assert refs.method is None and conv.method == PageMethod.LATEX and conv.engine == stub.engine
    assert len(stub.calls) == 2
    assert "\\section{Intro}" not in stub.calls[1]  # the lists alone


def test_the_recovered_list_takes_the_place_of_a_cut_short_one(tmp_path):
    class Cut(StubPandoc):
        def markdown(self, latex, drop_list):
            md = super().markdown(latex, drop_list)
            return (
                md.replace("3.  [@k3]", "").replace("4.  [@k4]", "")
                if "\\section{Intro}" in latex
                else md
            )

    conv = run(Cut(), sections("Intro", "Results"), tmp_path)
    assert [s.title for s in conv.segments].count("References") == 1
    assert len(re.findall(r"^\d+\.\s+\[@", refs_text(conv), re.M)) == 4
    assert "pandoc kept 2 of 4" in conv.warnings[-1]


@pytest.mark.parametrize(("lost", "recovered"), [(2, False), (3, True)])
def test_the_threshold_is_ninety_percent_of_the_entries(tmp_path, lost, recovered):
    bib = (
        "\\begin{thebibliography}{20}\n"
        + "".join(f"\\bibitem{{k{i}}} Author {i}.\n" for i in range(1, 21))
        + "\\end{thebibliography}"
    )

    class Short(StubPandoc):
        def markdown(self, latex, drop_list):
            md = super().markdown(latex, drop_list)
            if "\\section{Intro}" in latex:
                for i in range(21 - lost, 21):
                    md = md.replace(f"{i}.  [@k{i}]", "")
            return md

    conv = run(Short(), sections("Intro", "Results", bib=bib), tmp_path)
    assert any(w.startswith("pandoc kept") for w in conv.warnings) is recovered


def test_a_list_that_is_complete_costs_nothing(tmp_path):
    stub = StubPandoc()
    run(stub, sections("Intro", "Results"), tmp_path)
    assert len(stub.calls) == 1


def test_a_paper_without_a_bibliography_is_left_alone(tmp_path):
    stub = StubPandoc(drops_lists=True)
    conv = run(stub, sections("Intro", "Results", bib=""), tmp_path)
    assert len(stub.calls) == 1 and conv.warnings == []


def test_when_the_heading_is_gone_too_the_list_is_appended(tmp_path):
    class Gone(StubPandoc):
        def markdown(self, latex, drop_list):
            md = super().markdown(latex, True if "\\section{Intro}" in latex else drop_list)
            return md.replace("# References", "") if "\\section{Intro}" in latex else md

    conv = run(Gone(), sections("Intro", "Results"), tmp_path)
    assert [s.title for s in conv.segments][-1] == "References"
    assert len(re.findall(r"^\d+\.\s+\[@", refs_text(conv), re.M)) == 4


class Stubborn(StubPandoc):
    """pandoc drops every reference list, alone or not."""

    def markdown(self, latex, drop_list):
        return super().markdown(latex, True)


def test_a_list_pandoc_drops_even_alone_is_plain_text(tmp_path):
    stub = Stubborn()
    conv = run(stub, sections("Intro", "Results"), tmp_path)

    refs = conv.segments[-1]
    assert refs.title == "References" and refs.method == PageMethod.LATEX_PLAINTEXT
    for i in range(1, 5):
        assert f"k{i}" in refs.text and f"Author {i}, Title {i}" in refs.text
    assert "converted the bibliography on its own as plain text" in refs.warnings[0]
    assert conv.method == PageMethod.LATEX and " + pylatexenc " in conv.engine
    assert not conv.degraded


def test_without_the_fallback_a_lost_list_is_reported_not_replaced(tmp_path):
    stub = Stubborn(LatexConfig(pandoc_path="/stub/pandoc", fallback=False))
    conv = run(stub, sections("Intro", "Results"), tmp_path)
    assert conv.warnings == [
        "pandoc kept 0 of 4 reference entries; the list could not be recovered"
    ]
    assert refs_text(conv).strip() == "# References"
    assert all(s.method is None for s in conv.segments)


def test_a_machine_failure_while_recovering_the_list_is_degraded(tmp_path):
    stub = Stubborn()

    def timeout_on_the_lists_alone(latex, work, timeout=None):
        if "\\section{Intro}" not in latex:
            stub.calls.append(latex)
            return None, _Failure("pandoc timed out after 120s", environmental=True)
        return StubPandoc._pandoc(stub, latex, work, timeout)

    stub._pandoc = timeout_on_the_lists_alone  # type: ignore[method-assign]
    conv = run(stub, sections("Intro", "Results"), tmp_path)
    assert conv.degraded  # the next run tries pandoc again
    assert conv.segments[-1].method == PageMethod.LATEX_PLAINTEXT


def test_entries_in_a_plain_text_piece_are_not_lost_entries(tmp_path):
    bib = BIB.replace("Title 2", "BROKEN Title 2")
    stub = StubPandoc()
    conv = run(stub, sections("Intro", "Results", bib=bib), tmp_path)
    refs = conv.segments[-1]
    assert refs.title == "References" and refs.method == PageMethod.LATEX_PLAINTEXT
    assert not any("reference entries" in w for w in conv.warnings)  # nothing to recover
    assert len(stub.calls) == 1 + 1 + 4  # whole, preamble, front matter, three sections


def test_the_little_text_pandoc_made_is_kept_when_plain_text_is_no_better(tmp_path, monkeypatch):
    class Terse(StubPandoc):
        def markdown(self, latex, drop_list):
            if "\\section{Setup}" in latex and "\\section{Intro}" not in latex:
                return "# Setup\n\nTiny."
            return super().markdown(latex, drop_list)

    monkeypatch.setattr(
        PandocLatexConverter, "_plaintext", lambda self, latex: _Attempt([], None, [])
    )
    conv = run(Terse(), sections("Intro", "Setup", "Results", broken="Intro"), tmp_path)
    setup = next(s for s in conv.segments if s.title == "Setup")
    assert setup.method is None and setup.text.endswith("Tiny.")
    assert setup.warnings[0].endswith("kept what it made")
    assert "no text from section 2" in conv.warnings[-1]  # nothing from either converter


@pytest.mark.parametrize("plain", ["raises", "empty"])
def test_a_list_that_cannot_be_recovered_is_reported(tmp_path, monkeypatch, plain):
    def fake(self, latex):
        if "BROKEN" not in latex and "\\section{Intro}" in latex:
            raise RuntimeError("never reached")
        if plain == "raises":
            raise RuntimeError("pylatexenc bug")
        return _Attempt([], None, [])

    monkeypatch.setattr(PandocLatexConverter, "_plaintext", fake)
    conv = run(Stubborn(), sections("Intro", "Results"), tmp_path)
    assert conv.warnings == [
        "pandoc kept 0 of 4 reference entries; the list could not be recovered"
    ]
    assert all(s.method is None for s in conv.segments)


def test_the_recovered_list_keeps_the_place_of_the_one_it_replaces(tmp_path):
    # A paper with an appendix after the references: the list stays between them.
    text = sections("Intro", "Results", bib=BIB + "\n\\section{Appendix}\n" + LONG)
    conv = run(StubPandoc(drops_lists=True), text, tmp_path)
    assert [s.title for s in conv.segments] == ["Intro", "Results", "References", "Appendix"]
    assert len(re.findall(r"^\d+\.\s+\[@", conv.segments[2].text, re.M)) == 4
    assert conv.segments[2].warnings and not conv.segments[3].warnings
    assert conv.segments[3].text.startswith("# Appendix")  # not "# References" twice


def test_a_cut_short_list_is_replaced_where_it_stood(tmp_path):
    class Cut(StubPandoc):
        def markdown(self, latex, drop_list):
            md = super().markdown(latex, drop_list)
            return (
                md.replace("3.  [@k3]", "").replace("4.  [@k4]", "")
                if "\\section{Intro}" in latex
                else md
            )

    text = sections("Intro", "Results", bib=BIB + "\n\\section{Appendix}\n" + LONG)
    conv = run(Cut(), text, tmp_path)
    assert [s.title for s in conv.segments] == ["Intro", "Results", "References", "Appendix"]
    assert len(re.findall(r"^\d+\.\s+\[@", conv.segments[2].text, re.M)) == 4


def test_plain_text_that_is_no_better_than_what_pandoc_kept_is_not_used(tmp_path, monkeypatch):
    # An entry neither converter can read cuts the list short for both of them.
    class Cut(StubPandoc):
        def markdown(self, latex, drop_list):
            md = super().markdown(latex, drop_list)
            return (
                md.replace("3.  [@k3]", "").replace("4.  [@k4]", "")
                if "\\section{Intro}" in latex
                else md
            )

    def two_entries(self, latex):
        return _Attempt(assemble([(1, "References", "# References", "@k1 a\n\n  @k2 b")]), None, [])

    monkeypatch.setattr(PandocLatexConverter, "_plaintext", two_entries)
    stub = Cut()
    original = stub._pandoc

    def alone_drops_the_list(latex, work, timeout=None):
        return (
            original(latex, work, timeout)
            if "\\section{Intro}" in latex
            else StubPandoc._pandoc(Stubborn(), latex, work, timeout)
        )

    stub._pandoc = alone_drops_the_list  # type: ignore[method-assign]
    conv = run(stub, sections("Intro", "Results"), tmp_path)
    assert conv.warnings == [
        "pandoc kept 2 of 4 reference entries; the list could not be recovered"
    ]
    assert len(re.findall(r"^\d+\.\s+\[@", refs_text(conv), re.M)) == 2  # what pandoc kept stays
    assert all(s.method is None for s in conv.segments)


# ----------------------------------------------- what follows a list, several lists
AFTER = "End matter words that follow the list and have no section of their own."


def with_end_matter(*names: str, bib: str = BIB) -> str:
    return sections(*names, bib=bib + f"\n\n{AFTER}\n")


def test_text_after_a_lost_list_stays(tmp_path):
    conv = run(StubPandoc(drops_lists=True), with_end_matter("Intro", "Results"), tmp_path)
    assert [s.title for s in conv.segments] == ["Intro", "Results", "References", "References"]
    assert re.findall(r"^\d+\.  \[@(\w+)\]", conv.segments[2].text, re.M) == [
        "k1",
        "k2",
        "k3",
        "k4",
    ]
    assert conv.segments[2].warnings and not conv.segments[3].warnings
    assert conv.segments[3].text == AFTER  # no heading, no entries: the text that followed them


def test_text_after_a_cut_short_list_stays_and_its_entries_go(tmp_path):
    class Cut(StubPandoc):
        def markdown(self, latex, drop_list):
            md = super().markdown(latex, drop_list)
            return (
                md.replace("3.  [@k3]", "").replace("4.  [@k4]", "")
                if "\\section{Intro}" in latex
                else md
            )

    conv = run(Cut(), with_end_matter("Intro", "Results"), tmp_path)
    assert [s.title for s in conv.segments][-2:] == ["References", "References"]
    assert len(re.findall(r"^\d+\.\s+\[@", "\n".join(s.text for s in conv.segments), re.M)) == 4
    assert conv.segments[-1].text == AFTER


def test_text_after_a_list_that_pandoc_dropped_with_its_section_heading(tmp_path):
    # The heading of the vanished list is glued onto the next section; the section stays.
    text = sections("Intro", "Results", bib=BIB + "\n\\section{Appendix}\n" + AFTER)
    conv = run(StubPandoc(drops_lists=True), text, tmp_path)
    assert [s.title for s in conv.segments] == ["Intro", "Results", "References", "Appendix"]
    assert conv.segments[3].text == "# Appendix\n\n" + AFTER


BIB2 = BIB.replace("{4}", "{2}").replace("k3", "m3").replace("k4", "m4")
BIB2 = (
    "".join(line + "\n" for line in BIB2.splitlines() if "m3" not in line and "m4" not in line)
    .replace("k1", "m1")
    .replace("k2", "m2")
)


class DropsTheSecondList(StubPandoc):
    def markdown(self, latex, drop_list):
        md = super().markdown(latex, drop_list)
        if "\\section{" in latex:  # the lists alone are not dropped
            md = re.sub(r"^\d+\.  \[@m\d\].*\n?", "", md, flags=re.M)
        return md


def two_lists(after: str = "") -> str:
    return sections("Intro", "Results", bib=BIB + "\n\\section{Supplement}\n" + LONG + BIB2 + after)


def test_a_complete_list_is_not_replaced_when_another_one_is_lost(tmp_path):
    control = run(StubPandoc(), two_lists(), tmp_path)
    conv = run(DropsTheSecondList(), two_lists(), tmp_path)

    refs = [s for s in conv.segments if s.title == "References"]
    assert refs[0] == next(s for s in control.segments if s.title == "References")  # untouched
    assert len(refs) == 2 and refs[1].warnings and not refs[0].warnings
    assert len(re.findall(r"^\d+\.\s+\[@m", refs[1].text, re.M)) == 2
    assert conv.warnings == [
        "pandoc kept 0 of 2 reference entries in list 2 of 2; converted the bibliography on its "
        "own with pandoc"
    ]


def test_the_heading_of_a_lost_list_is_not_left_on_the_next_section(tmp_path):
    text = two_lists("\n\\section{Appendix}\n" + LONG)
    conv = run(DropsTheSecondList(), text, tmp_path)
    titles = [s.title for s in conv.segments]
    assert titles == ["Intro", "Results", "References", "Supplement", "References", "Appendix"]
    assert not conv.segments[-1].text.startswith("# References")
    assert conv.segments[-1].text.startswith("# Appendix")


def test_a_lost_list_next_to_a_lost_one_in_the_same_segment(tmp_path):
    # Two lists in a row, both dropped: their headings are glued together onto the next section.
    text = sections("Intro", "Results", bib=BIB + "\n" + BIB2 + "\n\\section{Appendix}\n" + LONG)
    conv = run(StubPandoc(drops_lists=True), text, tmp_path)
    assert [s.title for s in conv.segments] == [
        "Intro",
        "Results",
        "References",
        "References",
        "Appendix",
    ]
    assert conv.segments[-1].text.startswith("# Appendix")
    assert sum(bool(s.warnings) for s in conv.segments) == 2


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.  [@a] a\n\n2.  [@b] b\n\nAfter.", (2, "1.  [@a] a\n\n2.  [@b] b\n")),
        ("\n1.  [@a] a\n    more of a\n\n2.  [@b] b", (2, None)),
        ("Not a list.\n1.  [@a] a", (0, "")),
        ("1.  [@a] a\n\n1.  [@b] restarts\n", (1, "1.  [@a] a\n")),
        ("2.  [@a] starts at two\n", (0, "")),
        ("1.  a plain item\n\n2.  another\n", (0, "")),
        ("1.  [@a] a\n\n2.  no key\n", (1, "1.  [@a] a\n\n2.  no key\n")),
        (
            "1.  [@a] a\n\n2.  no key\n    wraps\n\n3.  [@c] c\n\nAfter.",
            (2, "1.  [@a] a\n\n2.  no key\n    wraps\n\n3.  [@c] c\n"),
        ),
        ("1.  [@a] a\n\n2.\n\n3.  [@c] c\n", (2, "1.  [@a] a\n\n2.\n\n3.  [@c] c\n")),
        (
            "1.  [@a] a\n\n2.  no key\n\n1.  restarts: the paper's own\n",
            (1, "1.  [@a] a\n\n2.  no key\n"),
        ),
        ("1.  [@a] a\n\n3.  skips two\n", (1, "1.  [@a] a\n")),
        ("1.  no key\n\n2.  [@b] b\n", (1, "1.  no key\n\n2.  [@b] b\n")),
        (
            "1.  no key\n    wraps\n\n2.  no key\n\n3.  [@c] c\n\nAfter.",
            (1, "1.  no key\n    wraps\n\n2.  no key\n\n3.  [@c] c\n"),
        ),
        ("1.  no key\n\n2.  no key either\n", (0, "")),
        ("1.  no key\n    wraps\n\nAfter.", (0, "")),
        ("1.  [@a] a\n2.5 GHz\n", (1, "1.  [@a] a\n")),
        ("", (0, "")),
    ],
)
def test_numbered_list_reads_the_entries_that_open_a_text(text, expected):
    count, end = _numbered_list(text)
    assert count == expected[0]
    if expected[1] is not None:
        assert text[:end] == expected[1]
    else:
        assert text[:end].endswith("more of a\n\n2.  [@b] b")


def test_the_lists_alone_get_the_macros_of_the_body(tmp_path):
    stub = StubPandoc(drops_lists=True)
    text = sections("Intro", "Results").replace(
        "\\section{Intro}", "\\section{Intro}\\newcommand{\\wick}{tallow}", 1
    )
    run(stub, text, tmp_path)
    assert "\\newcommand{\\wick}{tallow}" in stub.calls[1]


# ------------------------------------------------ the guard after sectioning, the budget
class DropsTheListOnce(StubPandoc):
    """pandoc drops a list the first time it converts one, as it does for a real paper."""

    dropped = False

    def markdown(self, latex, drop_list):
        if "\\item" in latex and not self.dropped:
            self.dropped = True
            return super().markdown(latex, True)
        return super().markdown(latex, drop_list)


def test_a_list_lost_in_a_section_is_recovered_after_sectioning(tmp_path):
    stub = DropsTheListOnce()
    stub.seconds = 10
    stub.cfg = LatexConfig(pandoc_path="/stub/pandoc", timeout_s=100)
    text = sections("Intro", "Setup", "Results", broken="Setup", bib=BIB + "\n\n" + LONG * 2)
    conv = run(stub, text, tmp_path)

    assert methods(conv)["Setup"] == PageMethod.LATEX_PLAINTEXT  # it was sectioned
    refs = next(s for s in conv.segments if s.title == "References")
    assert re.findall(r"^\d+\.  \[@(\w+)\]", refs.text, re.M) == ["k1", "k2", "k3", "k4"]
    assert "converted the bibliography on its own with pandoc" in conv.warnings[-1]
    # whole 10 s, deadline 110; preamble 100 left, front matter 90, Intro 80, Setup 70, Results 60,
    # References 50 and the recovery of the list gets the 40 that are left, not timeout_s
    assert stub.timeouts[1:] == [100, 90, 80, 70, 60, 50, 40]


def test_every_run_gets_what_is_left_of_the_budget(tmp_path):
    stub = StubPandoc(LatexConfig(pandoc_path="/stub/pandoc", timeout_s=100))
    stub.seconds = 30
    run(stub, sections("A", "B", "C", "D", broken="D"), tmp_path)
    assert stub.timeouts[1:] == [100, 70, 40, 10]  # the preamble, the front matter, A and B


def test_a_preamble_check_that_times_out_leaves_a_result_that_is_not_kept(tmp_path):
    stub = StubPandoc()
    stub.failures = [
        _Failure("exit 64"),
        _Failure("pandoc timed out after 100s", environmental=True),
    ]
    conv = run(stub, sections("A", "B", "C"), tmp_path)
    assert conv.method == PageMethod.LATEX_PLAINTEXT and conv.degraded


def test_every_piece_timing_out_leaves_a_result_that_is_not_kept(tmp_path):
    stub = StubPandoc()
    env = _Failure("pandoc timed out after 100s", environmental=True)
    stub.failures = [_Failure("exit 64"), None, env, env, env, env]
    conv = run(stub, sections("A", "B"), tmp_path)
    assert conv.method == PageMethod.LATEX_PLAINTEXT and conv.degraded


def test_a_complete_list_in_the_segment_of_a_lost_one_stays(tmp_path):
    # The heading of the lost first list is glued onto the heading of the complete second one.
    class DropsTheFirstList(StubPandoc):
        def markdown(self, latex, drop_list):
            md = super().markdown(latex, drop_list)
            if "\\section{" in latex:
                md = re.sub(r"^\d+\.  \[@k\d\].*\n?", "", md, flags=re.M)
            return md

    text = sections("Intro", "Results", bib=BIB + "\n" + BIB2 + "\n\\section{Appendix}\n" + LONG)
    conv = run(DropsTheFirstList(), text, tmp_path)
    refs = [s for s in conv.segments if s.title == "References"]
    assert len(refs) == 2 and refs[0].warnings and not refs[1].warnings
    assert re.findall(r"^\d+\.  \[@(\w+)\]", refs[0].text, re.M) == ["k1", "k2", "k3", "k4"]
    assert re.findall(r"^\d+\.  \[@(\w+)\]", refs[1].text, re.M) == ["m1", "m2"]
    assert refs[1].text.startswith("# References\n\n1.  [@m1]")
    assert "pandoc kept 0 of 4 reference entries in list 1 of 2" in conv.warnings[0]


def test_the_recovery_of_a_whole_paper_gets_the_budget(tmp_path):
    stub = StubPandoc(LatexConfig(pandoc_path="/stub/pandoc", timeout_s=100), drops_lists=True)
    run(stub, sections("Intro", "Results"), tmp_path)
    assert stub.timeouts == [None, 100]


def test_a_list_under_the_papers_own_references_heading_is_found(tmp_path):
    # `\\section{REFERENCES}` and the generated heading follow each other: pandoc glues the
    # first onto the second, and the segment opens with both.
    stub = StubPandoc()
    conv = run(stub, sections("Intro", "Results", bib="\\section{REFERENCES}\n" + BIB), tmp_path)
    assert len(stub.calls) == 1 and conv.warnings == []
    assert conv.segments[-1].text.startswith("# REFERENCES\n\n# References\n\n1.  [@k1]")


def test_the_papers_own_heading_before_a_lost_list_stays(tmp_path):
    stub = StubPandoc(drops_lists=True)
    conv = run(stub, sections("Intro", "Results", bib="\\section{REFERENCES}\n" + BIB), tmp_path)
    recovered = next(s for s in conv.segments if s.warnings)
    assert recovered.text.startswith("# REFERENCES\n\n# References\n\n1.  [@k1]")
    assert len(re.findall(r"^\d+\.\s+\[@", recovered.text, re.M)) == 4


def test_a_list_whose_heading_is_gone_too_is_found_by_its_keys(tmp_path):
    # pandoc drops the second list and its heading: one heading for two lists. The complete
    # first list is not converted again; the second is added at the end.
    class DropsTheSecondListAndHeading(DropsTheSecondList):
        def markdown(self, latex, drop_list):
            md = super().markdown(latex, drop_list)
            if "\\section{" in latex:
                head, _, tail = md.rpartition("# References")
                md = head + tail
            return md

    text = sections("Intro", "Results", bib=BIB + "\n" + BIB2)
    control = run(StubPandoc(), text, tmp_path)
    stub = DropsTheSecondListAndHeading()
    conv = run(stub, text, tmp_path)
    refs = [s for s in conv.segments if s.title == "References"]
    assert len(stub.calls) == 2  # the paper, then the second list alone
    assert refs[0] == next(s for s in control.segments if s.title == "References")
    assert conv.segments[-1].warnings and "in list 2 of 2" in conv.warnings[-1]
    assert len(re.findall(r"^\d+\.\s+\[@", "\n".join(s.text for s in conv.segments), re.M)) == 6


def test_a_list_with_its_entries_but_without_its_heading_is_not_lost(tmp_path):
    class NoHeading(StubPandoc):
        def markdown(self, latex, drop_list):
            md = super().markdown(latex, drop_list)
            return md.replace("# References", "") if "\\section{Intro}" in latex else md

    stub = NoHeading()
    conv = run(stub, sections("Intro", "Results"), tmp_path)
    assert len(stub.calls) == 1 and conv.warnings == []


def test_the_entries_of_a_list_written_on_one_line_are_all_counted(tmp_path):
    # The paper's own "References" heading before the generated one, and entries that follow
    # each other on one line: the keys are still found, nothing is called lost.
    bib = "\\section{References}" + BIB.replace("\n\\bibitem", "\\bibitem")
    stub = StubPandoc()
    conv = run(stub, sections("Intro", "Results", bib=bib), tmp_path)
    assert len(stub.calls) == 1 and conv.warnings == []


def test_a_second_list_with_the_same_keys_is_not_taken_for_the_first(tmp_path):
    # The same entries twice (a list repeated in a supplement): pandoc drops the second one,
    # heading and entries. The keys the first list uses are not the second list's.
    class DropsTheRepeat(StubPandoc):
        def markdown(self, latex, drop_list):
            md = super().markdown(latex, drop_list)
            if "\\section{" not in latex:
                return md
            head, _, _ = md.rpartition("# References")
            return head

    conv = run(DropsTheRepeat(), sections("Intro", "Results", bib=BIB + "\n" + BIB), tmp_path)
    assert "in list 2 of 2" in conv.warnings[-1] and len(conv.warnings) == 1
    assert len(re.findall(r"^\d+\.\s+\[@", "\n".join(s.text for s in conv.segments), re.M)) == 8


ENUMERATE = "\n\\begin{enumerate}\n\\item first step\n\\item second step\n\\item third step\n"


def test_a_list_of_the_papers_own_after_a_lost_list_is_neither_entries_nor_deleted(tmp_path):
    text = sections("Intro", "Results", bib=BIB + ENUMERATE + "\\end{enumerate}\n")
    conv = run(StubPandoc(drops_lists=True), text, tmp_path)
    assert conv.warnings == [
        "pandoc kept 0 of 4 reference entries; converted the bibliography on its own with pandoc"
    ]
    everything = "\n".join(s.text for s in conv.segments)
    assert len(re.findall(r"^\d+\.\s+\[@", everything, re.M)) == 4
    for item in ["1.  first step", "2.  second step", "3.  third step"]:
        assert item in everything


def test_four_items_of_the_papers_own_do_not_hide_a_lost_list(tmp_path):
    items = ENUMERATE + "\\item fourth step\n\\end{enumerate}\n"
    stub = StubPandoc(drops_lists=True)
    conv = run(stub, sections("Intro", "Results", bib=BIB + items), tmp_path)
    assert len(stub.calls) == 2 and "kept 0 of 4" in conv.warnings[0]
    assert "4.  fourth step" in "\n".join(s.text for s in conv.segments)


def bib10(keyless: int) -> str:
    """Ten entries; the one numbered ``keyless`` is a ``\\bibitem{}``."""
    return (
        "\\begin{thebibliography}{10}\n"
        + "".join(
            f"\\bibitem{{{'' if i == keyless else f'k{i}'}}} Author {i}, Title {i} ({2000 + i}).\n"
            for i in range(1, 11)
        )
        + "\\end{thebibliography}"
    )


def test_a_keyless_entry_in_the_middle_does_not_end_the_list(tmp_path):
    stub = StubPandoc()
    conv = run(stub, sections("Intro", "Results", bib=bib10(5)), tmp_path)
    assert len(stub.calls) == 1 and conv.warnings == []
    everything = "\n".join(s.text for s in conv.segments)
    for i in range(1, 11):
        assert everything.count(f"Title {i} ") == 1  # no entry twice
    assert re.search(r"^5\.\s+Author 5", everything, re.M)


@pytest.mark.parametrize("keyless", [(1,), (1, 2), (2, 3, 4)])
def test_keyless_entries_at_the_start_do_not_hide_the_list(tmp_path, keyless):
    bib = bib10(keyless[0])
    for i in keyless[1:]:
        bib = bib.replace(f"\\bibitem{{k{i}}}", "\\bibitem{}")
    stub = StubPandoc()
    conv = run(stub, sections("Intro", "Results", bib=bib), tmp_path)
    assert len(stub.calls) == 1 and conv.warnings == []
    everything = "\n".join(s.text for s in conv.segments)
    for i in range(1, 11):
        assert everything.count(f"Title {i} ") == 1  # no entry twice


def test_a_list_that_starts_keyless_is_found_among_two_lists(tmp_path):
    bib = bib10(1) + "\n\\section{Supplement}\n" + LONG + BIB2
    stub = StubPandoc()
    conv = run(stub, sections("Intro", "Results", bib=bib), tmp_path)
    assert len(stub.calls) == 1 and conv.warnings == []
    everything = "\n".join(s.text for s in conv.segments)
    for i in range(1, 11):  # entries 1 and 2 are also the two entries of the second list
        assert everything.count(f"Title {i} ") == (2 if i < 3 else 1)
    assert len(re.findall(r"^\d+\.\s+\[@m", everything, re.M)) == 2


def test_a_keyless_last_entry_is_part_of_the_list(tmp_path):
    stub = StubPandoc()
    conv = run(stub, with_end_matter("Intro", "Results", bib=bib10(10)), tmp_path)
    assert len(stub.calls) == 1 and conv.warnings == []
    refs = conv.segments[-1]
    assert re.search(r"^10\.\s+Author 10", refs.text, re.M) and refs.text.endswith(AFTER)


def test_keyless_entries_do_not_count_as_kept(tmp_path):
    # Twenty entries, two without a key: 18 are expected, 16.2 are needed. pandoc keeps 15
    # of them and both keyless ones, 17 items in all, which is still too few.
    bib = (
        "\\begin{thebibliography}{20}\n"
        + "".join(
            f"\\bibitem{{{'' if i in (3, 4) else f'k{i}'}}} Author {i}.\n" for i in range(1, 21)
        )
        + "\\end{thebibliography}"
    )

    class Short(StubPandoc):
        def markdown(self, latex, drop_list):
            md = super().markdown(latex, drop_list)
            if "\\section{Intro}" in latex:
                for i in range(18, 21):
                    md = md.replace(f"{i}.  [@k{i}]", "")
            return md

    conv = run(Short(), sections("Intro", "Results", bib=bib), tmp_path)
    assert "pandoc kept 15 of 18 reference entries" in conv.warnings[0]


def test_a_keyless_entry_in_a_cut_short_list_does_not_swallow_the_papers_own_list(tmp_path):
    bib = BIB.replace("\\bibitem{k3}", "\\bibitem{}") + ENUMERATE + "\\end{enumerate}\n"

    class Cut(StubPandoc):
        def markdown(self, latex, drop_list):
            md = super().markdown(latex, drop_list)
            return md.replace("4.  [@k4]", "") if "\\section{Intro}" in latex else md

    conv = run(Cut(), sections("Intro", "Results", bib=bib), tmp_path)
    assert "pandoc kept 2 of 3 reference entries" in conv.warnings[0]
    everything = "\n".join(s.text for s in conv.segments)
    for item in ["1.  first step", "2.  second step", "3.  third step"]:
        assert everything.count(item) == 1


def test_an_entry_without_a_key_is_not_expected(tmp_path):
    bib = BIB.replace("\\bibitem{k4}", "\\bibitem{}")
    stub = StubPandoc()
    conv = run(stub, sections("Intro", "Results", bib=bib), tmp_path)
    assert len(stub.calls) == 1 and conv.warnings == []
