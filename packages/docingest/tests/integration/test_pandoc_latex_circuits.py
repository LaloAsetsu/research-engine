"""Quantum circuits kept as LaTeX code blocks, with the real pandoc and pylatexenc.

Invented papers. The same circuit must come out as the same fenced block whichever path the
paper took: pandoc on the whole paper, pandoc on a section next to a section that went to plain
text, and plain text for the whole paper.
"""

import re
import time

import pytest

from docingest.adapters.converters.pandoc_latex import REVISION, PandocLatexConverter
from docingest.config import LatexConfig
from docingest.domain.models import PageMethod

CONVERTER = PandocLatexConverter()
NO_PANDOC = PandocLatexConverter(LatexConfig(pandoc_path="/nonexistent/pandoc"))
needs_pandoc = pytest.mark.skipif(CONVERTER.pandoc_version is None, reason="no pandoc binary")
pytestmark = needs_pandoc

QUANTIKZ = (
    "\\begin{quantikz}[row sep=0.3cm]\n"
    "\\lstick{$\\ket{0}$} & \\gate{H} & \\ctrl{1} & \\qw \\\\\n"
    "\\lstick{$\\ket{0}$} & \\qw & \\targ{} & \\meter{}\n"
    "\\end{quantikz}"
)
QCIRCUIT = (
    "\\Qcircuit @C=1em @R=.7em {\n"
    "  & \\gate{\\sqrt{X}} & \\ctrl{1} & \\qw \\\\\n"
    "  & \\lstick{\\ket{0}} & \\targ & \\qw\n"
    "}"
)
YQUANT = "\\begin{yquant}\nqubit {q} q[2];\nh q[0];\ncnot q[1] | q[0];\n\\end{yquant}"
TIKZCD_CIRCUIT = "\\begin{tikzcd}\n\\lstick{a} & \\gate{H} & \\qw & \\meter{}\n\\end{tikzcd}"
TIKZCD_DIAGRAM = '\\begin{tikzcd}\nA \\arrow[r, "f"] & B\n\\end{tikzcd}'
ENCODINGS = {
    "quantikz": QUANTIKZ,
    "qcircuit": QCIRCUIT,
    "yquant": YQUANT,
    "tikzcd": TIKZCD_CIRCUIT,
}
PROSE = "The lanterns drift with the tide and the wicks burn slowly through the night. "
# pandoc 3.9 stops on an environment inside the argument of a command.
BROKEN = "\\textbf{\\begin{center} wrapped words \\end{center}}"
FENCED = re.compile(r"^``` latex\n(.*?)\n```$", re.S | re.M)


def paper(*sections: tuple[str, str]) -> str:
    body = "".join(f"\\section{{{title}}}\n{text}\n" for title, text in sections)
    return (
        "\\documentclass{article}\n\\usepackage{quantikz}\n"
        "\\title{Lamps}\n\\author{Ada Quill}\n"
        f"\\begin{{document}}\n\\maketitle\n{body}\\end{{document}}\n"
    )


def figure(circuit: str, caption: str = "A pair of lamps.") -> str:
    return f"\\begin{{figure}}\n\\centering\n{circuit}\n\\caption{{{caption}}}\n\\end{{figure}}"


def convert(tmp_path, tex: str, converter: PandocLatexConverter = CONVERTER):
    (tmp_path / "main.tex").write_text(tex)
    return converter.convert(tmp_path / "main.tex")


def text_of(conv) -> str:
    return "\n\n".join(s.text for s in conv.segments)


def blocks(conv) -> list[str]:
    return FENCED.findall(text_of(conv))


# ---------------------------------------------------------- the whole paper
@pytest.mark.parametrize("circuit", ENCODINGS.values(), ids=ENCODINGS)
def test_each_encoding_is_a_latex_code_block_with_its_caption_after_it(tmp_path, circuit):
    conv = convert(tmp_path, paper(("Setup", PROSE + figure(circuit)), ("Results", PROSE)))
    out = text_of(conv)
    assert conv.method == PageMethod.LATEX
    assert not conv.warnings
    assert blocks(conv) == [circuit]
    assert out.index("lanterns") < out.index("``` latex") < out.index("A pair of lamps.")
    assert "& & &" not in out


def test_the_block_sits_in_the_segment_of_its_section(tmp_path):
    conv = convert(
        tmp_path, paper(("Setup", PROSE), ("Circuit", PROSE + figure(QUANTIKZ)), ("Last", PROSE))
    )
    holding = [s.title for s in conv.segments if "``` latex" in s.text]
    assert holding == ["Circuit"]


def test_several_circuits_come_out_in_order(tmp_path):
    body = f"{figure(QUANTIKZ, 'One.')}\n{PROSE}\n{figure(QCIRCUIT, 'Two.')}\n{figure(YQUANT, 'Three.')}"
    conv = convert(tmp_path, paper(("Circuits", body)))
    assert blocks(conv) == [QUANTIKZ, QCIRCUIT, YQUANT]
    out = text_of(conv)
    assert out.index("One.") < out.index("\\Qcircuit") < out.index("Two.") < out.index("yquant")


BOXES = {
    "resizebox": "\\resizebox{\\linewidth}{!}{%s}",
    "scalebox": "\\scalebox{0.8}{%s}",
    "raisebox": "\\raisebox{-2pt}{%s}",
    "adjustbox command": "\\adjustbox{max width=\\linewidth}{%s}",
    "adjustbox environment": "\\begin{adjustbox}{max width=\\textwidth}\n%s\n\\end{adjustbox}",
    "resizebox in a minipage": "\\begin{minipage}{0.8\\textwidth}\\resizebox{\\linewidth}{!}{%s}\\end{minipage}",
    "scalebox in a resizebox in a minipage": (
        "\\begin{minipage}{0.8\\textwidth}\\resizebox{\\linewidth}{!}{\\scalebox{0.5}{%s}}"
        "\\end{minipage}"
    ),
}


@pytest.mark.parametrize("wrapper", BOXES.values(), ids=BOXES)
@pytest.mark.parametrize("circuit", [QUANTIKZ, QCIRCUIT], ids=["quantikz", "qcircuit"])
def test_a_circuit_in_a_box_is_kept_with_its_caption(tmp_path, wrapper, circuit):
    conv = convert(tmp_path, paper(("Setup", PROSE + figure(wrapper % circuit, "Boxed pair."))))
    assert blocks(conv) == [circuit]
    assert "Boxed pair." in text_of(conv)
    assert "max width" not in text_of(conv)


def test_subfigures_keep_both_circuits_and_both_captions(tmp_path):
    sub = (
        "\\begin{figure}\n\\begin{subfigure}{0.5\\textwidth}\n%s\n\\caption{Left lamp.}\n\\end{subfigure}\n"
        "\\subfloat[Right lamp]{\\resizebox{3cm}{!}{%s}}\n\\caption{Both lamps.}\n\\end{figure}"
    )
    conv = convert(tmp_path, paper(("Setup", sub % (QUANTIKZ, QCIRCUIT))))
    out = text_of(conv)
    assert blocks(conv) == [QUANTIKZ, QCIRCUIT]
    for caption in ("Left lamp.", "Right lamp", "Both lamps."):
        assert caption in out


def test_a_circuit_in_math_in_a_box_is_kept_as_math_instead_of_vanishing_with_the_box(tmp_path):
    math = f"$\\vcenter{{{QUANTIKZ}}}$"
    wrapped = f"\\begin{{minipage}}{{0.8\\textwidth}}\\resizebox{{\\linewidth}}{{!}}{{{math}}}\\end{{minipage}}"
    conv = convert(tmp_path, paper(("Setup", PROSE + figure(wrapped, "Boxed math."))))
    out = text_of(conv)
    assert blocks(conv) == []
    assert out.count("\\begin{quantikz}") == 1
    assert "Boxed math." in out


@pytest.mark.parametrize(
    "wrapper",
    [
        "\\centerline{%s}",
        "\\mbox{%s}",
        "\\centerline{\\mbox{\n%s\n}}",
        "\\parbox{5cm}{%s}",
        "\\centerline {%s}",
        "\\mbox {%s}",
    ],
)
def test_a_circuit_in_the_argument_of_a_command_does_not_stop_pandoc(tmp_path, wrapper):
    conv = convert(tmp_path, paper(("Setup", PROSE + figure(wrapper % QCIRCUIT, "Held pair."))))
    assert conv.method == PageMethod.LATEX
    assert not conv.warnings
    assert all((s.method or conv.method) == PageMethod.LATEX for s in conv.segments)
    assert blocks(conv) == [QCIRCUIT]
    assert "Held pair." in text_of(conv)


@pytest.mark.parametrize(
    "wrapper",
    [
        "\\begin{center}{%s}\\end{center}",
        "\\begin{center}{\\small %s}\\end{center}",
        "{\\small %s}",
    ],
)
def test_a_circuit_in_a_group_of_its_own_is_a_block(tmp_path, wrapper):
    conv = convert(tmp_path, paper(("Setup", PROSE + figure(wrapper % QUANTIKZ, "Grouped pair."))))
    assert conv.method == PageMethod.LATEX
    assert not conv.warnings
    assert blocks(conv) == [QUANTIKZ]


def test_a_circuit_keeps_every_blank_line_and_the_text_the_rewrites_would_change(tmp_path):
    source = (
        "\\begin{quantikz}\n\\gate{H} \\vskip 2pt & \\qw\n\n\n\n"
        "\\lstick{\\cite{a,,b}} & \\gate{X} \\label[x]{y}\n\\end{quantikz}"
    )
    for name, converter in (("pandoc", CONVERTER), ("plain", NO_PANDOC)):
        folder = tmp_path / name
        folder.mkdir()
        conv = convert(folder, paper(("Setup", PROSE + figure(source, "Rough pair."))), converter)
        assert blocks(conv) == [source], name


def test_a_circuit_in_a_link_is_a_block_and_the_url_follows_it(tmp_path):
    link = "\\href{https://example.org/lamps}{%s}" % QCIRCUIT  # noqa: UP031
    conv = convert(tmp_path, paper(("Setup", PROSE + figure(link, "Linked pair."))))
    assert conv.method == PageMethod.LATEX
    assert not conv.warnings
    assert blocks(conv) == [QCIRCUIT]
    assert "https://example.org/lamps" in text_of(conv)


def test_a_group_after_text_in_the_same_paragraph_keeps_the_text(tmp_path):
    conv = convert(tmp_path, paper(("Setup", f"\\textbf{{Kept note.}} {{{QUANTIKZ}}} Tail words.")))
    assert blocks(conv) == [QUANTIKZ]
    assert "Kept note." in text_of(conv)
    assert "Tail words." in text_of(conv)


def test_a_circuit_in_a_tikz_node_is_kept_with_its_picture(tmp_path):
    picture = (
        f"\\begin{{tikzpicture}}\n\\node[scale=0.75] {{\n{QUANTIKZ}\n}};\n\\end{{tikzpicture}}"
    )
    conv = convert(tmp_path, paper(("Setup", PROSE + figure(picture, "Pictured pair."))))
    assert blocks(conv) == [picture]
    assert "Pictured pair." in text_of(conv)


def test_a_circuit_in_a_table_cell_goes_after_the_table_and_the_table_stays_a_table(tmp_path):
    table = (
        "\\begin{table}\n\\begin{tabular}{ll}\nGate & Circuit \\\\\n"
        f"Hadamard & {QUANTIKZ} \\\\\nPauli & plain words \\\\\n\\end{{tabular}}\n"
        "\\caption{The gates.}\n\\end{table}"
    )
    conv = convert(tmp_path, paper(("Gates", table)))
    out = text_of(conv)
    assert "[TABLE]" not in out
    assert "| Hadamard" in out
    assert "plain words" in out
    assert blocks(conv) == [QUANTIKZ]
    assert out.index("| Pauli") < out.index("``` latex")


def test_a_commutative_diagram_is_not_a_circuit(tmp_path):
    conv = convert(tmp_path, paper(("Setup", PROSE + figure(TIKZCD_DIAGRAM))))
    assert blocks(conv) == []
    assert "arrow" not in text_of(conv)


# --------------------------------------------------------------- math
def test_a_circuit_in_math_is_kept_once_as_math_and_not_as_a_block(tmp_path):
    body = (
        f"{PROSE}\n$$\\vcenter{{{QCIRCUIT}}}$$\n{PROSE}\n"
        f"\\begin{{equation}}\n{QUANTIKZ}\n\\end{{equation}}\n{figure(YQUANT)}"
    )
    conv = convert(tmp_path, paper(("Setup", body)))
    out = text_of(conv)
    assert blocks(conv) == [YQUANT]
    assert out.count("\\Qcircuit") == 1
    assert out.count("\\begin{quantikz}") == 1
    assert "$$" in out


# ------------------------------------------------------- the three paths
def run_paths(tmp_path, circuit: str):
    wanted = figure(circuit, "A pair of lamps.")
    results = {}
    for name, tex, converter in (
        ("whole", paper(("Setup", PROSE + wanted), ("Results", PROSE)), CONVERTER),
        (
            "good section",
            paper(("Setup", PROSE + wanted), ("Broken", PROSE + BROKEN), ("Results", PROSE)),
            CONVERTER,
        ),
        (
            "plain section",
            paper(("Setup", PROSE), ("Broken", PROSE + BROKEN + wanted), ("Results", PROSE)),
            CONVERTER,
        ),
        ("plain paper", paper(("Setup", PROSE + wanted), ("Results", PROSE)), NO_PANDOC),
    ):
        folder = tmp_path / name.replace(" ", "-")
        folder.mkdir()
        results[name] = convert(folder, tex, converter)
    return results


@pytest.mark.parametrize("circuit", ENCODINGS.values(), ids=ENCODINGS)
def test_the_block_is_the_same_whichever_path_the_paper_took(tmp_path, circuit):
    results = run_paths(tmp_path, circuit)
    methods = {
        name: {s.method or conv.method for s in conv.segments} for name, conv in results.items()
    }
    assert methods["whole"] == {PageMethod.LATEX}
    assert methods["good section"] == {PageMethod.LATEX, PageMethod.LATEX_PLAINTEXT}
    assert PageMethod.LATEX_PLAINTEXT in methods["plain section"]
    assert methods["plain paper"] == {PageMethod.LATEX_PLAINTEXT}
    assert {name: blocks(conv) for name, conv in results.items()} == {
        name: [circuit] for name in results
    }
    for name, conv in results.items():
        out = text_of(conv)
        assert out.index("``` latex") < out.index("A pair of lamps."), name


def test_a_section_in_plain_text_next_to_pandoc_sections_has_its_block_in_its_own_segment(tmp_path):
    results = run_paths(tmp_path, QUANTIKZ)
    conv = results["plain section"]
    holding = [s for s in conv.segments if "``` latex" in s.text]
    assert [s.title for s in holding] == ["Broken"]
    assert holding[0].method == PageMethod.LATEX_PLAINTEXT
    assert holding[0].warnings


def test_two_circuits_one_in_each_kind_of_section_are_both_exact(tmp_path):
    tex = paper(
        ("Setup", PROSE + figure(QUANTIKZ, "First.")),
        ("Broken", PROSE + BROKEN + figure(QCIRCUIT, "Second.")),
    )
    conv = convert(tmp_path, tex)
    assert blocks(conv) == [QUANTIKZ, QCIRCUIT]


def test_a_circuit_in_a_box_in_the_plain_text_path_is_a_block_too(tmp_path):
    wrapped = figure("\\resizebox{\\linewidth}{!}{%s}" % QUANTIKZ, "Boxed pair.")  # noqa: UP031
    conv = convert(tmp_path, paper(("Setup", PROSE + wrapped), ("Results", PROSE)), NO_PANDOC)
    assert blocks(conv) == [QUANTIKZ]
    assert "resizebox" not in text_of(conv)
    assert "Boxed pair." in text_of(conv)


# ---------------------------------------------------------------- the rest
def test_a_circuit_with_tabs_and_trailing_spaces_is_the_same_text_in_every_path(tmp_path):
    odd = "\\begin{quantikz}\n\t\\gate{H}\t& \\qw  \n\n    \\gate{X} & \\qw \\\\[2mm]\n\\end{quantikz}"
    results = run_paths(tmp_path, odd)
    texts = {name: blocks(conv) for name, conv in results.items()}
    assert len({tuple(b) for b in texts.values()}) == 1
    assert texts["whole"][0].count("\t") == 0
    assert "&" in texts["whole"][0]


def test_unbalanced_and_unclosed_circuits_do_not_stop_the_conversion(tmp_path):
    broken = (
        f"\\Qcircuit @C=1em {{ & \\gate{{H}} & \\qw\n\n{PROSE}\n\\begin{{quantikz}} \\gate{{X}}\n"
    )
    t0 = time.perf_counter()
    conv = convert(tmp_path, paper(("Setup", broken + figure(YQUANT, "Fine."))))
    assert time.perf_counter() - t0 < 60
    assert YQUANT in blocks(conv)
    assert "lanterns" in text_of(conv)


def test_a_paper_without_circuits_is_converted_as_before(tmp_path):
    conv = convert(tmp_path, paper(("Setup", PROSE + figure("\\fbox{plain box}", "Words."))))
    assert "```" not in text_of(conv)
    assert "Words." in text_of(conv)


def test_the_adapter_revision_is_in_the_fingerprint():
    assert REVISION >= 5
    assert f"pandoc-latex r{REVISION} " in CONVERTER.fingerprint
