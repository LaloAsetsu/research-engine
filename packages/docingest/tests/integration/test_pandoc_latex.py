"""PandocLatexConverter with the real pandoc (pypandoc-binary) and pylatexenc.

The fixture paper (tests/fixtures/latex/paper) packs the traps seen in arXiv sources:
a latin-1 include, a file that includes itself, a local .sty whose layout macro makes
pandoc loop forever, a natbib .bbl, a commented-out include and verbatim code.
"""

import gzip
import io
import os
import re
import tarfile
import time
from pathlib import Path

import pytest
from fakes import FakeOcr

from docingest.adapters.converters.pandoc_latex import PandocLatexConverter
from docingest.bootstrap import Container
from docingest.config import LatexConfig
from docingest.domain.errors import ConversionError
from docingest.domain.models import PageMethod, SourceKind
from docingest.ports import DocumentConverter

FIXTURES = Path(__file__).parents[1] / "fixtures" / "latex"
PAPER = FIXTURES / "paper"
REVTEX = FIXTURES / "revtex"
TITLES = [
    "Abstract",
    "Introduction",
    "Method",
    "Method > Complexity",
    "Experiments > Setup",
    "Conclusion",
    "References",
]
DOC = "\\documentclass{article}\n\\begin{document}\n%s\n\\end{document}\n"

CONVERTER = PandocLatexConverter()
needs_pandoc = pytest.mark.skipif(CONVERTER.pandoc_version is None, reason="no pandoc binary")


def pack(dest: Path, files: dict[str, bytes], mode: str = "w:gz") -> Path:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=mode) as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    dest.write_bytes(buf.getvalue())
    return dest


def paper_files() -> dict[str, bytes]:
    return {
        p.relative_to(PAPER).as_posix(): p.read_bytes() for p in PAPER.rglob("*") if p.is_file()
    }


def text_of(conv) -> str:
    return "\n\n".join(s.text for s in conv.segments)


def test_fingerprint_names_every_setting():
    assert isinstance(CONVERTER, DocumentConverter)
    fp = CONVERTER.fingerprint
    assert f"pandoc {CONVERTER.pandoc_version or 'unavailable'}" in fp
    assert "--sandbox" in fp and "split_level=2" in fp and "pylatexenc 2." in fp
    assert PandocLatexConverter(LatexConfig(split_level=1)).fingerprint != fp
    assert "no fallback" in PandocLatexConverter(LatexConfig(fallback=False)).fingerprint


@needs_pandoc
@pytest.mark.parametrize("form", ["tex", "tar.gz", "tar"])
def test_fixture_paper(tmp_path, form):
    if form == "tex":
        src = PAPER / "main.tex"
    else:  # arXiv archives: no extension, plus figures and a decoy full document
        extra = {"figs/plot.png": b"\x89PNG....", "supplement.tex": (DOC % "Decoy").encode()}
        src = pack(
            tmp_path / "2401.00001", {**paper_files(), **extra}, "w:gz" if form == "tar.gz" else "w"
        )
    conv = CONVERTER.convert(src)

    assert conv.method == PageMethod.LATEX and conv.engine == CONVERTER.engine
    assert not conv.degraded
    assert [s.title for s in conv.segments] == TITLES
    assert conv.title == "Sparse Attention for Tiny Transformers"
    assert conv.metadata is not None
    assert conv.metadata.authors == ["Ada Lovelace", "Alan Turing"]
    assert (
        conv.metadata.abstract
        == "We study attention over $\\mathbf{x}\\in \\mathbb{R}^d$ and report a 2x speed-up."
    )
    assert conv.warnings == ["skipped \\input{sections/setup} (recursive include)"]
    text = text_of(conv)
    for expected in [
        "naïve attention is quadratic; see the café benchmark",  # latin-1 include
        "The score is SPARSE-SCORE computed as $$\\begin{equation}",  # .sty macro, display math
        "\\mathop{\\mathrm{softmax}}_j",  # \DeclareMathOperator from the .sty
        "It costs $O(n \\log n)$ instead of $O(n^2)$ [@vaswani2017; @child2019], i.e. 50% fewer",
        "    % this line is code, not a comment\n    \\input{not-an-include}",  # verbatim
        "| Sparse | 27.1 |  40M   |",
        "1.  [@child2019] Rewon Child, Scott Gray, Alec Radford, and Ilya Sutskever.",
        "In *Advances in Neural Information Processing Systems*, 2017.",  # {\em venue} kept
    ]:
        assert expected in text
    for absent in ["hidden remark", "Decoy", "urlstyle"]:
        assert absent not in text


@needs_pandoc
def test_single_gzipped_tex(tmp_path):
    body = "\\title{Old Paper}\\maketitle\n\\section{One} First $a+b$.\n\\section{Two} Second."
    src = tmp_path / "math0211159"
    src.write_bytes(gzip.compress((DOC % body).encode()))
    conv = CONVERTER.convert(src)
    assert [s.title for s in conv.segments] == ["One", "Two"]
    assert conv.title == "Old Paper" and "First $a+b$." in text_of(conv)


@needs_pandoc
def test_never_reads_outside_the_source(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-MARKER")
    paper = tmp_path / "paper"
    paper.mkdir()
    tricks = [
        f"\\lstinputlisting{{{secret}}}",
        "\\verbatiminput{../secret.txt}",
        "\\inputminted{text}{../secret.txt}",
        f"\\input{{{secret}}}",
        "\\include{../secret}",
        "\\newcommand{\\sneaky}{\\lstinputlisting}\\sneaky{../secret.txt}",
    ]
    (paper / "main.tex").write_text(DOC % "\n".join(["Visible text.", *tricks]))
    conv = CONVERTER.convert(paper / "main.tex")
    assert "Visible text." in text_of(conv)
    assert "TOP-SECRET" not in text_of(conv)


@needs_pandoc
def test_self_referential_style_macros_do_not_hang(tmp_path):
    # arXiv 2409.13740's style file does this; pandoc alone expands it forever.
    src = tmp_path / "main.tex"
    src.write_text(
        "\\documentclass{article}\n"
        "\\renewcommand{\\normalsize}{\\@setfontsize\\normalsize\\@xpt\\@xipt}\n"
        "\\begin{document}\n\\normalsize\n\\section{Fine}\nConverted quickly.\n\\end{document}\n"
    )
    t0 = time.perf_counter()
    conv = PandocLatexConverter(LatexConfig(timeout_s=30)).convert(src)
    assert time.perf_counter() - t0 < 10
    assert conv.method == PageMethod.LATEX and "Converted quickly." in text_of(conv)
    assert any("\\normalsize" in w for w in conv.warnings)


@needs_pandoc
def test_revtex_paper_converts_with_pandoc_and_keeps_every_reference():
    # The .bbl of a REVTeX-style bibliography: @ helper macros, an \href with a line break
    # before its arguments, \href@noop and \Eprint. Each of these alone made pandoc stop
    # (the plain-text fallback) or drop the whole list without an error. The text also
    # cites with an empty key and a key with a dollar sign.
    conv = CONVERTER.convert(REVTEX / "main.tex")

    assert conv.method == PageMethod.LATEX and not conv.degraded
    assert conv.warnings == []
    assert [s.title for s in conv.segments] == ["Abstract", "Introduction", "Results", "References"]
    text = text_of(conv)
    assert "Lanterns drift with the tide [@quill31; @tallow29]." in text
    assert "$\\left|0\\right\\rangle$ is used as the reference [@marsh30; @lamp_x24_32]" in text
    refs = text.partition("# References")[2].replace("\xa0", " ")  # ~ in the names
    assert re.findall(r"^\d+\.  \[@(\w+)\]", refs, re.M) == [
        "quill31",
        "tallow29",
        "marsh30",
        "lamp_x24_32",
    ]
    for entry in [
        "I. Quill and T. Marrow, [Journal of Imaginary Hydrology **12**, 101 (2031)]"
        "(https://example.org/lanterns/quill31)",
        "B. Tallowick, Basin Letters **4**, 7 (2029)",  # \href@noop
        "O. Marsh, *Lanterns of the Low Marsh*",
        "L. Lamp, Preprint on tidal wicks, [arXiv:3201.00001](https://example.org/abs/3201.00001)",
    ]:
        assert entry in refs
    for junk in ["bibfield", "bibinfo", "BibitemShut", "href@noop", "\\@"]:
        assert junk not in text


@needs_pandoc
def test_nature_style_bibliography_helpers_still_expand(tmp_path):
    # Helpers without an @ are not shimmed: dropping them left entries as ". . , ()."
    src = tmp_path / "main.tex"
    src.write_text(
        DOC
        % (
            "\\section{Intro}\nSee \\cite{reed30}.\n"
            "\\begin{thebibliography}{1}\n"
            "\\providecommand{\\bibinfo}[2]{#2}\n\\providecommand{\\bibfield}[2]{#2}\n"
            "\\bibitem{reed30}\n\\bibfield{author}{\\bibinfo{author}{Reed, A.}} "
            "\\bibfield{title}{\\bibinfo{title}{Tidal wicks}} (\\bibinfo{year}{2030}).\n"
            "\\end{thebibliography}"
        )
    )
    conv = CONVERTER.convert(src)
    assert conv.method == PageMethod.LATEX
    assert "1.  [@reed30] Reed, A. Tidal wicks (2030)." in text_of(conv)


@needs_pandoc
def test_small_constructs_that_stopped_pandoc_convert(tmp_path):
    src = tmp_path / "main.tex"
    src.write_text(
        "\\documentclass{article}\n"
        "\\global\\long\\def\\ket#1{|#1\\rangle}\n"
        "\\newcolumntype{P}[1]{>{\\centering\\arraybackslash}p{#1}}\n"
        "\\begin{document}\n"
        "\\section{Setup}\\label[app]{sec:setup}\n"
        "The state $\\ket{0}$ is cited as \\cite{a$b,,c}.\\vskip 3pt plus 1fil\n"
        "\\begin{table}\\caption{\\raggedright Counts}\n"
        "\\begin{tabular}{lp{2cm}}x & 1\\\\\\end{tabular}\\end{table}\n"
        "\\end{document}\n"
    )
    conv = CONVERTER.convert(src)
    assert conv.method == PageMethod.LATEX and not conv.degraded
    text = text_of(conv)
    assert "$|0\\rangle$ is cited as [@a_x24_b; @c]." in text
    assert (
        "|:----|:----|\n| x   | 1   |\n\n:  Counts" in text
    )  # pandoc 3.9 drops a table with \\raggedright


@needs_pandoc
def test_ieeetran_style_titles_survive_the_bibliography_helpers(tmp_path):
    # IEEEtran writes the title of an entry through \BIBforeignlanguage, whose body uses
    # \csname; making it empty deleted every title.
    src = tmp_path / "main.tex"
    src.write_text(
        DOC
        % (
            "\\section{Intro}\nSee \\cite{reed30}.\n"
            "\\begin{thebibliography}{1}\n"
            "\\providecommand{\\BIBforeignlanguage}[2]{{\\expandafter\\ifx\\csname l@#1\\endcsname"
            "\\relax \\typeout{** WARNING: unknown language #1}\\else\\language=\\csname l@#1"
            "\\endcsname\\fi #2}}\n"
            "\\bibitem{reed30}\nA. Reed, \\BIBforeignlanguage{en}{``Tidal wicks in shallow basins,''} "
            "\\BIBforeignlanguage{en}{\\emph{Basin Letters}}, vol. 4, 2030.\n"
            "\\end{thebibliography}"
        )
    )
    conv = CONVERTER.convert(src)
    assert conv.method == PageMethod.LATEX
    text = text_of(conv)
    assert "Tidal wicks in shallow basins," in text and "*Basin Letters*" in text
    assert "vol. 4, 2030" in text


@needs_pandoc
def test_saved_and_redefined_macros_keep_their_meaning_and_convert_quickly(tmp_path):
    # \xdef must stay: as \def the save-and-redefine idiom loops until the timeout.
    src = tmp_path / "main.tex"
    src.write_text(
        DOC
        % (
            "\\newcommand{\\X}{base}\n\\xdef\\savedX{\\X}\n\\renewcommand{\\X}{[\\savedX]}\n"
            "\\section{Idiom}\nThe macro prints \\X here.\n"
        )
    )
    t0 = time.perf_counter()
    conv = PandocLatexConverter(LatexConfig(timeout_s=30)).convert(src)
    assert time.perf_counter() - t0 < 10
    assert conv.method == PageMethod.LATEX and not conv.degraded
    assert "The macro prints \\[base\\]here." in text_of(
        conv
    )  # no space before "here" in pandoc 3.9


def fake_pandoc(tmp_path: Path, script: str) -> str:
    exe = tmp_path / "pandoc"
    exe.write_text(
        f'#!/bin/sh\nif [ "$1" = "--version" ]; then echo "pandoc 9.9"; exit 0; fi\n{script}\n'
    )
    exe.chmod(0o755)
    return str(exe)


@pytest.mark.skipif(os.name != "posix", reason="shell-script stand-in for pandoc")
def test_timeout_falls_back_to_plain_text(tmp_path):
    cfg = LatexConfig(timeout_s=1, pandoc_path=fake_pandoc(tmp_path, "exec sleep 20"))
    t0 = time.perf_counter()
    conv = PandocLatexConverter(cfg).convert(PAPER / "main.tex")
    assert time.perf_counter() - t0 < 10
    assert conv.method == PageMethod.LATEX_PLAINTEXT
    assert conv.engine.startswith("pylatexenc ")
    assert "pandoc timed out after 1s; used the pylatexenc plain-text fallback" in conv.warnings
    assert conv.degraded  # a busy machine: must not be cached as the paper's conversion
    assert [s.title for s in conv.segments] == TITLES
    assert conv.metadata is not None and conv.metadata.authors == ["Ada Lovelace", "Alan Turing"]
    text = text_of(conv)
    assert "café" in text and "$O(n \\log n)$" in text and "[@vaswani2017; @child2019]" in text


@pytest.mark.skipif(os.name != "posix", reason="shell-script stand-in for pandoc")
def test_suspiciously_short_output_falls_back(tmp_path):
    stub = "printf '{}\\n@@docingest-body@@\\n# Only a heading\\n'"
    src = tmp_path / "main.tex"  # one section: nothing to convert piece by piece
    src.write_text(DOC % ("\\section{Long}\n" + "A sentence with some words in it. " * 20))
    conv = PandocLatexConverter(LatexConfig(pandoc_path=fake_pandoc(tmp_path, stub))).convert(src)
    assert conv.method == PageMethod.LATEX_PLAINTEXT
    assert any("pandoc kept" in w for w in conv.warnings)
    assert not conv.degraded  # the same document gives the same output next time


@pytest.mark.skipif(os.name != "posix", reason="shell-script stand-in for pandoc")
@pytest.mark.parametrize(
    ("script", "degraded"),
    [
        ("echo 'pandoc: Heap exhausted;' >&2; exit 251", True),  # GHC runtime, +RTS -M cap
        ("kill -9 $$", True),  # a signal: the OOM killer, a user's kill
        ("echo 'Error at (line 3, column 1): unexpected end of input' >&2; exit 64", False),
        ("echo 'Error: macro loop' >&2; exit 91", False),
    ],
)
def test_fallback_is_degraded_only_when_the_machine_failed(tmp_path, script, degraded):
    cfg = LatexConfig(pandoc_path=fake_pandoc(tmp_path, script))
    conv = PandocLatexConverter(cfg).convert(PAPER / "main.tex")
    assert conv.method == PageMethod.LATEX_PLAINTEXT
    assert conv.degraded is degraded


def test_fallback_survives_pylatexenc_crashes(tmp_path, monkeypatch):
    from pylatexenc.latex2text import LatexNodes2Text

    real = LatexNodes2Text.latex_to_text

    def flaky(self, latex, **kw):
        if "BOOM" in latex:
            raise IndexError("pylatexenc spec bug")
        return real(self, latex, **kw)

    monkeypatch.setattr(LatexNodes2Text, "latex_to_text", flaky)
    link = "\\href{https://x.org/a_b%20c}{site}"
    (tmp_path / "main.tex").write_text(
        DOC % f"\\section{{Fine}} ok {link}\n\\section{{Bad}} BOOM \\textbf{{bold}} words {link}"
    )
    no_pandoc = LatexConfig(pandoc_path=str(tmp_path / "missing"))
    conv = PandocLatexConverter(no_pandoc).convert(tmp_path / "main.tex")
    assert conv.method == PageMethod.LATEX_PLAINTEXT
    texts = {s.title: s.text for s in conv.segments}
    assert "ok site (https://x.org/a_b%20c)" in texts["Fine"]
    assert "BOOM bold words site (https://x.org/a_b%20c)" in texts["Bad"]
    assert "pylatexenc failed on 1 fragment(s); kept their bare text" in conv.warnings


@needs_pandoc
def test_malformed_latex(tmp_path):
    conv = CONVERTER.convert(FIXTURES / "malformed.tex")  # unclosed group and itemize
    assert conv.method == PageMethod.LATEX_PLAINTEXT
    assert [s.title for s in conv.segments] == ["Broken"]
    assert "pandoc exited with code 64" in conv.warnings[0]
    assert not conv.degraded  # the input itself: cache the fallback
    with pytest.raises(ConversionError, match="code 64"):
        PandocLatexConverter(LatexConfig(fallback=False)).convert(FIXTURES / "malformed.tex")


def test_missing_pandoc(tmp_path):
    conv_cfg = LatexConfig(pandoc_path=str(tmp_path / "no-such-pandoc"))
    converter = PandocLatexConverter(conv_cfg)
    assert "pandoc unavailable" in converter.fingerprint
    conv = converter.convert(PAPER / "main.tex")
    assert conv.method == PageMethod.LATEX_PLAINTEXT and conv.title is not None
    assert any("pandoc could not run" in w for w in conv.warnings)
    assert conv.degraded
    with pytest.raises(ConversionError, match="could not run"):
        PandocLatexConverter(conv_cfg.model_copy(update={"fallback": False})).convert(
            PAPER / "main.tex"
        )


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs file permissions")
def test_unreadable_include_is_a_conversion_error(tmp_path):
    (tmp_path / "main.tex").write_text(DOC % "\\input{locked}")
    locked = tmp_path / "locked.tex"
    locked.write_text("secret")
    locked.chmod(0)
    try:
        with pytest.raises(ConversionError, match="cannot read LaTeX source"):
            CONVERTER.convert(tmp_path / "main.tex")
    finally:
        locked.chmod(0o644)


@needs_pandoc
@pytest.mark.parametrize(
    "tex",
    [  # each used to lose the document after an \endinput TeX never executes
        "\\begin{filecontents*}{notation.sty}\n\\ProvidesPackage{notation}\n"
        "\\newcommand{\\R}{\\mathbb{R}}\n\\endinput\n\\end{filecontents*}\n"
        "\\documentclass{article}\n\\title{A Real Paper}\n\\begin{document}\n\\maketitle\n"
        "\\section{Intro}\nWe study $\\R^n$.\n\\section{Results}\nImportant results.\n"
        "\\end{document}\n",
        DOC % "\\section{Intro}\nEnd a package with \\verb|\\endinput| always.\n"
        "\\section{Results}\nImportant results.",
        "\\documentclass{article}\n\\newcommand{\\stopreading}{\\endinput}\n\\begin{document}\n"
        "\\section{Intro}\nText.\n\\section{Results}\nImportant results.\n\\end{document}\n",
    ],
)
def test_endinput_tex_does_not_execute_keeps_the_document(tmp_path, tex):
    (tmp_path / "paper.tex").write_text(tex)
    conv = CONVERTER.convert(tmp_path / "paper.tex")
    assert conv.method == PageMethod.LATEX and conv.warnings == []
    assert [s.title for s in conv.segments] == ["Intro", "Results"]
    assert "Important results." in text_of(conv)


@needs_pandoc
@pytest.mark.parametrize(
    "sentence",
    [
        "We use a learning rate of $3$$\\times 10^{-4}$.",
        "Accuracy is $91.2$$\\pm$$0.3$ on the test set, i.e. $x$$^2$.",
    ],
)
def test_adjacent_inline_math_does_not_merge_sections(tmp_path, sentence):
    body = f"\\section{{Intro}}\n{sentence}\n\\section{{Method}}\nM.\n\\section{{Results}}\nR."
    (tmp_path / "main.tex").write_text(DOC % body)
    conv = CONVERTER.convert(tmp_path / "main.tex")
    assert [s.title for s in conv.segments] == ["Intro", "Method", "Results"]


@needs_pandoc
def test_percent_in_inline_code_is_not_a_comment(tmp_path):
    body = (
        "\\section{Parity}\nWe test parity with \\lstinline!n % 2 == 0! in the inner loop.\n"
        "\\section{Results}\nResults text."
    )
    (tmp_path / "main.tex").write_text("\\usepackage{listings}\n" + DOC % body)
    conv = CONVERTER.convert(tmp_path / "main.tex")
    assert conv.method == PageMethod.LATEX and conv.warnings == []
    assert "We test parity with `n % 2 == 0` in the inner loop." in text_of(conv)


@needs_pandoc
def test_empty_document_is_an_error(tmp_path):
    (tmp_path / "main.tex").write_text(DOC % "% nothing but a comment")
    with pytest.raises(ConversionError, match="no text"):
        CONVERTER.convert(tmp_path / "main.tex")


@needs_pandoc
def test_split_level_one():
    conv = PandocLatexConverter(LatexConfig(split_level=1)).convert(PAPER / "main.tex")
    assert [s.title for s in conv.segments] == [
        "Abstract",
        "Introduction",
        "Method",
        "Experiments",
        "Conclusion",
        "References",
    ]


@needs_pandoc
def test_ingest_through_the_container(cfg, tmp_path):
    logs: list[str] = []
    svc = Container(cfg, log=logs.append, overrides={"ocr": FakeOcr()}).ingest
    src = pack(tmp_path / "2401.00001.tar.gz", paper_files())
    doc = svc.ingest(src)
    m = doc.manifest
    assert m.source_kind == SourceKind.LATEX and m.complete
    assert m.title == "Sparse Attention for Tiny Transformers"
    assert m.metadata is not None and m.metadata.authors == ["Ada Lovelace", "Alan Turing"]
    assert [p.title for p in m.pages] == TITLES
    assert {p.method for p in m.pages} == {PageMethod.LATEX}
    assert all(p.engine == CONVERTER.engine for p in m.pages)
    markdown = svc.store.markdown(doc)
    assert "$O(n \\log n)$" in markdown and "<!-- page 7 | method=latex -->" in markdown
    assert any("recursive include" in line for line in logs)
    assert svc.ingest(src).location == doc.location and logs[-1].startswith("[cache]")


# Real arXiv e-prints (not redistributable, so not in the repo): point
# DOCINGEST_LATEX_SAMPLES at a folder of raw https://arxiv.org/src/<id> downloads.
SAMPLES = Path(os.environ.get("DOCINGEST_LATEX_SAMPLES", "/nonexistent"))


@needs_pandoc
@pytest.mark.slow
@pytest.mark.parametrize(
    ("name", "title", "min_segments"),
    [
        ("1706.03762.src", "Attention Is All You Need", 15),
        (
            "2409.13740.src",
            "Language agents achieve superhuman synthesis of scientific knowledge",
            10,
        ),
        ("2303.08774.src", "GPT-4 Technical Report", 20),
        (
            "math_0211159.src",
            "The entropy formula for the Ricci flow and its geometric applications",
            10,
        ),
    ],
)
def test_real_arxiv_sources(name, title, min_segments):
    src = SAMPLES / name
    if not src.is_file():
        pytest.skip(f"set DOCINGEST_LATEX_SAMPLES to a folder with {name}")
    t0 = time.perf_counter()
    conv = CONVERTER.convert(src)
    assert time.perf_counter() - t0 < 30
    assert conv.method == PageMethod.LATEX and conv.title == title and not conv.degraded
    assert len(conv.segments) >= min_segments
    assert text_of(conv).count("$") >= 20  # math survives as $...$
