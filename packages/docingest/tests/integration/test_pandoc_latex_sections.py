"""A LaTeX paper that pandoc cannot read whole, converted section by section, and the
guard for a reference list pandoc drops (real pandoc, pylatexenc, invented sources).

``tests/unit/test_latex_sections.py`` covers the same logic with pandoc replaced by a rule.
"""

import math
import os
import re
from pathlib import Path

import pytest
from fakes import FakeOcr
from timing import timeout_notes

from docingest.adapters.converters import latex_source
from docingest.adapters.converters.pandoc_latex import PandocLatexConverter
from docingest.bootstrap import Container
from docingest.config import LatexConfig
from docingest.domain.models import PageMethod

REVTEX = Path(__file__).parents[1] / "fixtures" / "latex" / "revtex"
CONVERTER = PandocLatexConverter()
needs_pandoc = pytest.mark.skipif(CONVERTER.pandoc_version is None, reason="no pandoc binary")
posix = pytest.mark.skipif(os.name != "posix", reason="shell-script stand-in for pandoc")

PROSE = "The lanterns drift with the tide and the wicks burn slowly through the night. "
# pandoc 3.9 stops on an environment inside the argument of a command.
BROKEN = "\\textbf{\\begin{center} wrapped words \\end{center}}"
PAPER = (
    "\\documentclass{article}\n"
    "\\newcommand{\\lamp}{tallow lamp}\n"
    "\\title{Tidal Wicks in Low Marshes}\n"
    "\\author{Ada Quill \\and Bo Marsh}\n"
    "\\begin{document}\n"
    "\\maketitle\n"
    "\\begin{abstract}\nWe light a \\lamp\\ and follow it.\n\\end{abstract}\n"
    "\\section{Introduction}\n\\newcommand{\\wick}{braided wick}\n"
    + PROSE
    + "See \\cite{quill31}.\n"
    "\\section{Setup}\nThe setup uses a " + BROKEN + ". " + PROSE + "\n"
    "\\section{Results}\nThe \\lamp\\ and the \\wick\\ agree. " + PROSE + "\n"
    "\\begin{thebibliography}{1}\n"
    "\\bibitem{quill31} I. Quill, Tidal wicks, Basin Letters \\textbf{4}, 7 (2031).\n"
    "\\end{thebibliography}\n"
    "\\end{document}\n"
)


def text_of(conv) -> str:
    return "\n\n".join(s.text for s in conv.segments)


def by_title(conv) -> dict:
    return {s.title: s for s in conv.segments}


@needs_pandoc
def test_one_broken_section_is_plain_text_and_the_others_are_pandoc(tmp_path):
    (tmp_path / "main.tex").write_text(PAPER)
    conv = CONVERTER.convert(tmp_path / "main.tex")

    assert conv.method == PageMethod.LATEX and not conv.degraded
    segments = by_title(conv)
    assert list(segments) == ["Abstract", "Introduction", "Setup", "Results", "References"]
    assert {t: s.method for t, s in segments.items()} == {
        "Abstract": None,
        "Introduction": None,
        "Setup": PageMethod.LATEX_PLAINTEXT,
        "Results": None,
        "References": None,
    }
    assert "pandoc exited with code 64" in segments["Setup"].warnings[0]
    assert all(not s.warnings for t, s in segments.items() if t != "Setup")
    assert conv.engine.startswith(f"{CONVERTER.engine} + pylatexenc ")
    assert "converted section by section, pandoc took 4 of 5 pieces" in conv.warnings[-1]
    assert "(Setup)" in conv.warnings[-1]

    # What pandoc made of the other sections: the preamble macro and the macro that the
    # Introduction defined in its body are expanded in the Results; the plain-text section
    # has the words and no pandoc markup.
    assert conv.title == "Tidal Wicks in Low Marshes"
    assert conv.metadata is not None and conv.metadata.authors == ["Ada Quill", "Bo Marsh"]
    assert conv.metadata.abstract.replace("\xa0", " ") == "We light a tallow lamp and follow it."
    assert "The tallow lamp and the braided wick agree." in segments["Results"].text.replace(
        "\xa0", " "
    )
    assert "Introduction" in segments["Introduction"].text and "[@quill31]" in text_of(conv)
    assert "wrapped words" in segments["Setup"].text and "\\textbf" not in segments["Setup"].text
    assert re.findall(r"^\d+\.  \[@(\w+)\]", segments["References"].text, re.M) == ["quill31"]


@needs_pandoc
def test_the_page_records_say_which_section_is_plain_text(cfg, tmp_path):
    (tmp_path / "paper.tex").write_text(PAPER)
    svc = Container(cfg, overrides={"ocr": FakeOcr()}).ingest
    doc = svc.ingest(tmp_path / "paper.tex")

    pages = doc.manifest.pages
    assert [(p.title, p.method) for p in pages] == [
        ("Abstract", PageMethod.LATEX),
        ("Introduction", PageMethod.LATEX),
        ("Setup", PageMethod.LATEX_PLAINTEXT),
        ("Results", PageMethod.LATEX),
        ("References", PageMethod.LATEX),
    ]
    assert [bool(p.warnings) for p in pages] == [False, False, True, False, False]
    assert not doc.manifest.pages[0].engine.endswith("unknown")
    markdown = svc.store.markdown(doc)
    assert "<!-- page 3 | method=latex_plaintext -->" in markdown
    assert "<!-- page 4 | method=latex -->" in markdown
    assert doc.canonical  # a failure of the document is cached, as before


@needs_pandoc
def test_a_paper_whose_every_section_is_broken_is_plain_text_as_before(tmp_path):
    body = "".join(f"\\section{{S{i}}}\n{PROSE}{BROKEN}\n" for i in range(4))
    (tmp_path / "main.tex").write_text(
        f"\\documentclass{{article}}\n\\begin{{document}}\n{body}\\end{{document}}\n"
    )
    conv = CONVERTER.convert(tmp_path / "main.tex")
    assert conv.method == PageMethod.LATEX_PLAINTEXT and conv.engine.startswith("pylatexenc ")
    assert all(s.method is None and not s.warnings for s in conv.segments)
    assert [s.title for s in conv.segments] == ["S0", "S1", "S2", "S3"]


@needs_pandoc
def test_malformed_sections_do_not_hang(tmp_path):
    # Twelve sections, every other one with an unclosed group or an unclosed environment,
    # plus a title that never closes: one run per section, all of them bounded.
    sections = [
        f"\\section{{S{i}}}\n{PROSE}"
        + ("{ never closed\n" if i % 2 else "\\begin{itemize}\\item x\n")
        for i in range(12)
    ]
    (tmp_path / "main.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\n"
        + "\\section{Dangling title "
        + "".join(sections)
        + "\\end{document}\n"
    )
    conv = PandocLatexConverter(LatexConfig(timeout_s=20)).convert(tmp_path / "main.tex")
    # a hang ends in the converter's timeout, which marks the result degraded: no run timed out or
    # ran out of the shared budget, so none of the twelve runs (and the first one on the whole paper) hung
    assert not conv.degraded and timeout_notes(conv) == []
    assert conv.method in (PageMethod.LATEX, PageMethod.LATEX_PLAINTEXT)
    assert len(conv.segments) >= 12


@needs_pandoc
def test_a_list_pandoc_drops_silently_is_recovered_as_plain_text(monkeypatch):
    # Without the rewrite of REVTeX's \href@noop, pandoc 3.9 drops the whole list and says
    # nothing: the body converts, the list is gone, and no error says so.
    monkeypatch.setattr(latex_source, "_HREF_NOOP", re.compile(r"(?!)"))
    conv = CONVERTER.convert(REVTEX / "main.tex")

    assert conv.method == PageMethod.LATEX and not conv.degraded
    segments = by_title(conv)
    refs = segments["References"]
    assert refs.method == PageMethod.LATEX_PLAINTEXT
    for entry in ["Quill", "Tallowick", "Marsh", "Lamp", "Basin Letters", "Verdigris Press"]:
        assert entry in refs.text
    note = (
        "pandoc kept 0 of 4 reference entries; converted the bibliography on its own as plain text"
    )
    assert refs.warnings == (note,) and note in conv.warnings
    assert all(s.method is None for t, s in segments.items() if t != "References")
    assert "Lanterns drift with the tide [@quill31; @tallow29]." in text_of(conv)
    assert " + pylatexenc " in conv.engine


def revtex_with_end_matter(tmp_path: Path) -> Path:
    """The REVTeX fixture with text after the bibliography that has no section of its own."""
    (tmp_path / "main.bbl").write_text((REVTEX / "main.bbl").read_text())
    tex = (
        (REVTEX / "main.tex")
        .read_text()
        .replace(
            "\\bibliography{main}",
            "\\bibliography{main}\n\\clearpage\n\\textbf{End Matter}\n\n"
            "\\textit{Related work.} --- Ospreyglass harbours drift slowly past the wicks.\n\n"
            "\\textit{Derivation.} The Kelpwright bound follows from the tide equation.\n",
        )
    )
    (tmp_path / "main.tex").write_text(tex)
    return tmp_path / "main.tex"


@needs_pandoc
def test_text_after_the_reference_list_survives_a_recovery(tmp_path, monkeypatch):
    src = revtex_with_end_matter(tmp_path)
    control = CONVERTER.convert(src)  # the list is kept
    monkeypatch.setattr(latex_source, "_HREF_NOOP", re.compile(r"(?!)"))
    conv = CONVERTER.convert(src)

    for text in (text_of(control), text_of(conv)):
        for word in ["End Matter", "Related work.", "Ospreyglass", "Derivation.", "Kelpwright"]:
            assert word in text
    refs = [s for s in conv.segments if s.title == "References"]
    assert [s.method for s in refs] == [PageMethod.LATEX_PLAINTEXT, None]
    assert "Quill" in refs[0].text and "Ospreyglass" not in refs[0].text
    assert refs[1].text.startswith("**End Matter**") and "Kelpwright" in refs[1].text
    assert "# References" not in refs[1].text


@needs_pandoc
def test_a_list_of_the_papers_own_after_a_dropped_list_stays(tmp_path, monkeypatch):
    (tmp_path / "main.bbl").write_text((REVTEX / "main.bbl").read_text())
    tex = (
        (REVTEX / "main.tex")
        .read_text()
        .replace(
            "\\bibliography{main}",
            "\\bibliography{main}\n\\begin{enumerate}\n\\item Light the wick.\n"
            "\\item Wait for the tide.\n\\item Count the lanterns.\n\\end{enumerate}\n",
        )
    )
    (tmp_path / "main.tex").write_text(tex)
    monkeypatch.setattr(latex_source, "_HREF_NOOP", re.compile(r"(?!)"))
    conv = CONVERTER.convert(tmp_path / "main.tex")

    text = text_of(conv)
    for step in ["Light the wick.", "Wait for the tide.", "Count the lanterns."]:
        assert step in text
    refs = next(s for s in conv.segments if s.warnings)
    assert refs.method == PageMethod.LATEX_PLAINTEXT and "Tallowick" in refs.text
    assert "pandoc kept 0 of 4 reference entries" in conv.warnings[0]


@needs_pandoc
@pytest.mark.parametrize("keyless", [(1,), (1, 2), (5,), (10,)])
def test_a_keyless_entry_does_not_make_a_complete_list_look_cut_short(tmp_path, keyless):
    entries = "".join(
        f"\\bibitem{{{'' if i in keyless else f'quill{i}'}}} Author {i}, Tidal wicks {i}, "
        f"Basin Letters \\textbf{{{i}}}, 7 (20{i:02d}).\n"
        for i in range(1, 11)
    )
    paper = re.sub(r"\\bibitem\{quill31\}.*\n", lambda _: entries, PAPER)
    (tmp_path / "main.tex").write_text(paper.replace(BROKEN, "wrapped words"))
    conv = CONVERTER.convert(tmp_path / "main.tex")

    assert conv.method == PageMethod.LATEX and conv.warnings == []
    assert all(s.method is None for s in conv.segments)
    for i in range(1, 11):
        assert text_of(conv).count(f"Tidal wicks {i},") == 1  # none of them twice


@needs_pandoc
def test_with_the_rewrite_in_place_the_guard_does_nothing():
    conv = CONVERTER.convert(REVTEX / "main.tex")
    assert conv.warnings == [] and all(s.method is None for s in conv.segments)


@needs_pandoc
def test_a_reference_list_in_a_plain_text_section_is_not_called_lost(tmp_path):
    paper = PAPER.replace(
        "Basin Letters \\textbf{4}, 7 (2031).", "Basin Letters " + BROKEN + ", 7 (2031)."
    )
    (tmp_path / "main.tex").write_text(paper)
    conv = CONVERTER.convert(tmp_path / "main.tex")
    refs = by_title(conv)["References"]
    assert refs.method == PageMethod.LATEX_PLAINTEXT and "Tidal wicks" in refs.text
    assert not any("reference entries" in w for w in conv.warnings)


def fake_pandoc(tmp_path: Path, script: str) -> str:
    exe = tmp_path / "pandoc"
    exe.write_text(
        f'#!/bin/sh\nif [ "$1" = "--version" ]; then echo "pandoc 9.9"; exit 0; fi\n{script}\n'
    )
    exe.chmod(0o755)
    return str(exe)


@posix
def test_a_slow_pandoc_cannot_take_longer_than_the_timeout_in_total(tmp_path):
    # The whole paper and the preamble fail or pass at once; each section takes 0.6 s. The
    # sections share one budget of 2 s, so at most 4 of them start (the fourth is cut off) and
    # the others never run. Without the shared budget all 12 would start: 12 x 0.6 s = 7.2 s.
    # The test counts the runs the converter starts instead of timing them: a slower machine
    # starts fewer, so it cannot fail for being slow.
    log = tmp_path / "runs.log"
    script = (
        "input=$(cat)\n"
        'case "$input" in *"\\section{S0}"*"\\section{S1}"*)'
        f' echo whole >> {log}; echo "Error at (line 1, column 1): stub" >&2; exit 64;; esac\n'
        f'case "$input" in *"\\section"*) echo section >> {log}; sleep 0.6;'
        " printf '{}\\n@@docingest-body@@\\n# Piece\\n\\n%s\\n' \"$input\" | head -c 400; exit 0;; esac\n"
        f"echo preamble >> {log}\n"
        "printf '{}\\n@@docingest-body@@\\n'"
    )
    cfg = LatexConfig(timeout_s=2, pandoc_path=fake_pandoc(tmp_path, script))
    body = "".join(f"\\section{{S{i}}}\n{PROSE}\n" for i in range(12))
    (tmp_path / "main.tex").write_text(
        f"\\documentclass{{article}}\n\\begin{{document}}\n{body}\\end{{document}}\n"
    )
    conv = PandocLatexConverter(cfg).convert(tmp_path / "main.tex")
    runs = log.read_text().split()
    started = runs.count("section")
    assert runs[:2] == ["whole", "preamble"] and started + 2 == len(runs)
    assert started <= math.ceil(2 / 0.6) + 1  # 12 without the shared budget
    assert conv.degraded  # the sections left over are plain text for want of time
    assert len(conv.segments) == 12
    left_over = [s for s in conv.segments if s.method == PageMethod.LATEX_PLAINTEXT]
    assert len(left_over) >= 12 - started  # every section that never started
    assert any(s.method is None for s in conv.segments)


@posix
def test_a_preamble_pandoc_cannot_read_costs_two_runs(tmp_path):
    log = tmp_path / "runs.log"
    script = f"echo run >> {log}\necho 'Error at (line 2, column 1): stub' >&2\nexit 64"
    cfg = LatexConfig(pandoc_path=fake_pandoc(tmp_path, script))
    body = "".join(f"\\section{{S{i}}}\n{PROSE}\n" for i in range(8))
    (tmp_path / "main.tex").write_text(
        f"\\documentclass{{article}}\n\\begin{{document}}\n{body}\\end{{document}}\n"
    )
    conv = PandocLatexConverter(cfg).convert(tmp_path / "main.tex")
    assert conv.method == PageMethod.LATEX_PLAINTEXT and not conv.degraded
    assert log.read_text().count("run") == 2  # the whole paper and the preamble alone
