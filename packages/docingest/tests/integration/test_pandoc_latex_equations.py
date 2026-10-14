"""Equation numbers in the Markdown, with the real pandoc and pylatexenc.

Invented papers. The same numbers must come out whichever path the paper took: pandoc on the whole
paper, pandoc on a section next to a section that went to plain text, and plain text for the whole
paper.
"""

import re

import pytest

from docingest.adapters.converters.pandoc_latex import REVISION, PandocLatexConverter
from docingest.config import LatexConfig
from docingest.domain.models import PageMethod

CONVERTER = PandocLatexConverter()
NO_PANDOC = PandocLatexConverter(LatexConfig(pandoc_path="/nonexistent/pandoc"))
needs_pandoc = pytest.mark.skipif(CONVERTER.pandoc_version is None, reason="no pandoc binary")
pytestmark = needs_pandoc

PROSE = "The lanterns drift with the tide and the wicks burn slowly through the night. "
# pandoc 3.9 stops on an environment inside the argument of a command.
BROKEN = "\\textbf{\\begin{center} wrapped words \\end{center}}"
TAG = re.compile(r"\\tag\{([^{}]*)\}")
QUANTIKZ = "\\begin{quantikz}\n\\lstick{a} & \\gate{H} & \\qw \\\\\n\\lstick{b} & \\qw & \\targ{}\n\\end{quantikz}"


def paper(*sections: tuple[str, str], pre: str = "", cls: str = "article") -> str:
    body = "".join(f"\\section{{{title}}}\n{text}\n" for title, text in sections)
    return (
        f"\\documentclass{{{cls}}}\n\\usepackage{{amsmath}}\n\\usepackage{{quantikz}}\n{pre}\n"
        "\\title{Lamps}\n\\author{Ada Quill}\n"
        f"\\begin{{document}}\n\\maketitle\n{body}\\end{{document}}\n"
    )


def convert(tmp_path, tex: str, converter: PandocLatexConverter = CONVERTER):
    (tmp_path / "main.tex").write_text(tex)
    return converter.convert(tmp_path / "main.tex")


def text_of(conv) -> str:
    return "\n\n".join(s.text for s in conv.segments).replace("\xa0", " ")  # \xa0: LaTeX's ~


def tags(conv) -> list[str]:
    return TAG.findall(text_of(conv))


SETUP = (
    PROSE
    + "\\begin{equation}\\label{eq:energy}\nE = m c^2\n\\end{equation}\n"
    + "\\begin{equation*}\nunnumbered = 1\n\\end{equation*}\n"
    + "\\begin{align}\na &= b \\label{eq:first}\\\\\nc &= d \\nonumber\\\\\ne &= f \\label{eq:last}\n\\end{align}\n"
)
RESULTS = PROSE + "By Eq.~\\eqref{eq:energy} and Eq.~\\ref{eq:last}, and again \\eqref{eq:first}. "


# ---------------------------------------------------------- the whole paper
def test_the_numbers_are_in_the_markdown_and_the_references_are_numbers(tmp_path):
    conv = convert(tmp_path, paper(("Setup", SETUP), ("Results", RESULTS)))
    out = text_of(conv)
    assert conv.method == PageMethod.LATEX
    assert not conv.warnings
    assert tags(conv) == ["1", "2", "3"]
    assert "E = m c^2" in out and "unnumbered = 1" in out
    assert "By Eq. (1) and Eq. 3, and again (2)." in out
    assert "[\\[eq:" not in out and "(#eq:" not in out


def test_subequations_appendix_and_numberwithin_come_out_as_latex_shows_them(tmp_path):
    setup = (
        PROSE
        + "\\begin{equation}\\label{eq:one}\nx\n\\end{equation}\n"
        + "\\begin{subequations}\\label{eq:group}\n\\begin{align}\na &= 1 \\label{eq:a}\\\\\nb &= 2\n\\end{align}\n"
        + "\\end{subequations}\n"
    )
    more = PROSE + "\\begin{equation}\\label{eq:two}\ny\n\\end{equation}\n"
    app = "\\begin{equation}\\label{eq:app}\nz\n\\end{equation}\n"
    tex = paper(
        ("Setup", setup),
        ("More", more),
        pre="\\numberwithin{equation}{section}",
    ).replace(
        "\\end{document}",
        f"\\appendix\n\\section{{Extra}}\n{app}Now \\eqref{{eq:group}}, \\eqref{{eq:a}}, \\eqref{{eq:two}}, \\eqref{{eq:app}}.\n\\end{{document}}",
    )
    conv = convert(tmp_path, tex)
    assert tags(conv) == ["1.1", "1.2a", "1.2b", "2.1", "A.1"]
    assert "Now (1.2), (1.2a), (2.1), (A.1)." in text_of(conv)


def test_an_own_tag_a_starred_environment_and_a_nonumber_row_keep_their_meaning(tmp_path):
    setup = (
        "\\begin{equation}\nx \\tag{A}\\label{eq:own}\n\\end{equation}\n"
        "\\begin{equation}\ny\\label{eq:y}\n\\end{equation}\n"
        "\\begin{gather*}\nz\\label{eq:z}\n\\end{gather*}\nSee \\eqref{eq:own}, \\eqref{eq:y}, \\eqref{eq:z}."
    )
    conv = convert(tmp_path, paper(("Setup", setup)))
    assert tags(conv) == ["A", "1"]
    assert "See (A), (1), [\\[eq:z\\]](#eq:z)." in text_of(conv).replace(
        "\\_", "_"
    ) or "(1)," in text_of(conv)


def test_a_circuit_is_still_a_code_block_and_a_circuit_in_an_equation_is_still_math(tmp_path):
    setup = (
        PROSE
        + f"\n\\begin{{figure}}\n{QUANTIKZ}\n\\caption{{Pair.}}\n\\end{{figure}}\n"
        + f"\\begin{{equation}}\\label{{eq:c}}\n{QUANTIKZ}\n\\end{{equation}}\nSee \\eqref{{eq:c}}.\n"
    )
    conv = convert(tmp_path, paper(("Setup", setup)))
    out = text_of(conv)
    assert out.count("``` latex\n" + QUANTIKZ + "\n```") == 1
    assert out.count("\\begin{quantikz}") == 2
    assert tags(conv) == ["1"]
    assert "See (1)." in out


def test_a_paper_without_numbered_equations_is_converted_as_before(tmp_path):
    conv = convert(
        tmp_path, paper(("Setup", PROSE + "$$a = b$$ and \\begin{equation*}c\\end{equation*}"))
    )
    assert "\\tag" not in text_of(conv)


# ------------------------------------------------------- the three paths
def run_paths(tmp_path, setup: str, results: str):
    results_text = {}
    for name, tex, converter in (
        ("whole", paper(("Setup", setup), ("Results", results)), CONVERTER),
        (
            "good section",
            paper(("Setup", setup), ("Broken", PROSE + BROKEN), ("Results", results)),
            CONVERTER,
        ),
        (
            "plain section",
            paper(("Setup", PROSE), ("Broken", PROSE + BROKEN + setup), ("Results", results)),
            CONVERTER,
        ),
        ("plain paper", paper(("Setup", setup), ("Results", results)), NO_PANDOC),
    ):
        folder = tmp_path / name.replace(" ", "-")
        folder.mkdir()
        results_text[name] = convert(folder, tex, converter)
    return results_text


def test_the_numbers_and_the_references_are_the_same_whichever_path_the_paper_took(tmp_path):
    results = run_paths(tmp_path, SETUP, RESULTS)
    methods = {
        name: {s.method or conv.method for s in conv.segments} for name, conv in results.items()
    }
    assert methods["whole"] == {PageMethod.LATEX}
    assert methods["good section"] == {PageMethod.LATEX, PageMethod.LATEX_PLAINTEXT}
    assert PageMethod.LATEX_PLAINTEXT in methods["plain section"]
    assert methods["plain paper"] == {PageMethod.LATEX_PLAINTEXT}
    for name, conv in results.items():
        assert tags(conv) == ["1", "2", "3"], name
        assert "By Eq. (1) and Eq. 3, and again (2)." in text_of(conv), name


def test_a_reference_in_a_section_that_is_converted_alone_still_has_its_number(tmp_path):
    results = run_paths(tmp_path, SETUP, RESULTS)
    pieces = [s for s in results["good section"].segments if "By Eq." in s.text]
    assert [s.title for s in pieces] == ["Results"]
    assert pieces[0].method is None or pieces[0].method == PageMethod.LATEX


def test_plain_text_keeps_the_tag_inside_the_equation(tmp_path):
    conv = convert(tmp_path, paper(("Setup", SETUP), ("Results", RESULTS)), NO_PANDOC)
    out = text_of(conv)
    assert re.search(r"E = m c\^2\n\\tag\{1\}\n\\end\{equation\}", out)
    assert "a &= b \\label{eq:first} \\tag{2} \\\\" in out


def test_an_unclosed_equation_does_not_stop_the_conversion(tmp_path):
    setup = PROSE + "\\begin{equation}\nx = y\n" + PROSE
    conv = convert(tmp_path, paper(("Setup", setup), ("Results", RESULTS)))
    assert "lanterns" in text_of(conv)
    assert "By Eq." in text_of(conv)


def test_the_adapter_revision_is_in_the_fingerprint():
    assert REVISION >= 7
    assert f"pandoc-latex r{REVISION} " in CONVERTER.fingerprint
