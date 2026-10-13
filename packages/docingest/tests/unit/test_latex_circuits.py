"""Quantum circuits kept as LaTeX code blocks (latex_source.protect_circuits and the plain-text path).

String in, string out: no pandoc binary. Every circuit and sentence is invented.
``tests/integration/test_pandoc_latex_circuits.py`` runs the same shapes through the real pandoc,
all three conversion paths included.
"""

import random
import re
import time
from itertools import pairwise

import pytest

from docingest.adapters.converters import latex_source
from docingest.adapters.converters.latex_source import (
    circuit_spans,
    prepare_for_pandoc,
    protect_circuits,
)
from docingest.adapters.converters.pandoc_latex import (
    PandocLatexConverter,
    assemble,
    collapse_blank_lines,
)
from docingest.config import LatexConfig

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
YQUANT_STAR = "\\begin{yquant*}\nqubit q;\nh q;\n\\end{yquant*}"
TIKZCD_CIRCUIT = "\\begin{tikzcd}\n\\lstick{a} & \\gate{H} & \\qw & \\meter{}\n\\end{tikzcd}"
TIKZCD_DIAGRAM = '\\begin{tikzcd}\nA \\arrow[r, "f"] & B \\arrow[d] \\\\\nC & D\n\\end{tikzcd}'
CIRCUITS = {
    "quantikz": QUANTIKZ,
    "qcircuit": QCIRCUIT,
    "yquant": YQUANT,
    "yquant*": YQUANT_STAR,
    "tikzcd with gates": TIKZCD_CIRCUIT,
}
LISTING = re.compile(r"\\begin\{minted\}\{latex\}\n(.*?)\n\\end\{minted\}", re.S)


def doc(body: str) -> str:
    return f"\\documentclass{{article}}\n\\begin{{document}}\n{body}\n\\end{{document}}\n"


def listed(text: str) -> list[str]:
    """The circuits that were written as listings, in order."""
    return LISTING.findall(text)


def balanced(text: str) -> bool:
    """Braces pair up (an escaped one does not count)."""
    depth = 0
    for c in re.sub(r"\\.", "", text):
        depth += (c == "{") - (c == "}")
        if depth < 0:
            return False
    return depth == 0


# ------------------------------------------------------------ the encodings
@pytest.mark.parametrize("source", CIRCUITS.values(), ids=CIRCUITS)
def test_each_encoding_becomes_a_listing_with_its_own_source(source):
    out = protect_circuits(doc(f"Before.\n\n{source}\n\nAfter."))
    assert listed(out) == [source]
    assert out.index("Before.") < out.index("\\begin{minted}") < out.index("After.")
    assert source not in out.replace(f"\n{source}\n", "")  # written once, not twice


def test_a_circuit_comes_out_between_the_sentences_around_it_and_before_its_caption():
    body = f"Intro.\n\\begin{{figure}}\n\\centering\n{QUANTIKZ}\n\\caption{{A Bell pair.}}\n\\end{{figure}}\nOutro."
    out = protect_circuits(doc(body))
    assert listed(out) == [QUANTIKZ]
    order = [
        "Intro.",
        "\\begin{figure}",
        "\\begin{minted}",
        "\\end{minted}",
        "\\caption{A Bell pair.}",
    ]
    assert [out.index(x) for x in order] == sorted(out.index(x) for x in order)
    assert out.index("\\caption{A Bell pair.}") < out.index("\\end{figure}") < out.index("Outro.")


def test_several_circuits_each_get_a_listing_in_order():
    out = protect_circuits(doc(f"A\n{QUANTIKZ}\nB\n{QCIRCUIT}\nC\n{YQUANT}\nD"))
    assert listed(out) == [QUANTIKZ, QCIRCUIT, YQUANT]
    assert re.sub(r"\s+", " ", LISTING.sub("", out)).count("A B C D") == 1


def test_a_qcircuit_group_with_nested_and_escaped_braces_is_found_whole():
    source = "\\Qcircuit @C=1em { & \\gate{\\frac{a}{\\sqrt{2}}} & \\lstick{\\{0\\}} & \\qw }"
    out = protect_circuits(doc(f"x {source} y"))
    assert listed(out) == [source]
    assert "x" in out
    assert out.rstrip().endswith("\\end{document}")


def test_a_tikzcd_is_a_circuit_only_when_it_has_circuit_commands():
    assert protect_circuits(doc(TIKZCD_DIAGRAM)) == doc(TIKZCD_DIAGRAM)
    assert listed(protect_circuits(doc(TIKZCD_CIRCUIT))) == [TIKZCD_CIRCUIT]
    mixed = f"{TIKZCD_DIAGRAM}\n\n{TIKZCD_CIRCUIT}"
    out = protect_circuits(doc(mixed))
    assert listed(out) == [TIKZCD_CIRCUIT]
    assert TIKZCD_DIAGRAM in out


def test_a_circuit_inside_another_is_part_of_the_outer_one():
    outer = "\\begin{quantikz}\n\\gate{\\begin{quantikz} \\gate{X} \\end{quantikz}} & \\qw\n\\end{quantikz}"
    out = protect_circuits(doc(outer))
    assert listed(out) == [outer]


def test_circuit_spans_are_in_order_and_do_not_overlap():
    text = doc(
        f"{QUANTIKZ}\n{QCIRCUIT}\n\\begin{{tikzpicture}}\\node{{{YQUANT}}};\\end{{tikzpicture}}"
    )
    spans = circuit_spans(text)
    assert len(spans) == 3
    assert all(a < b for a, b in spans)
    assert all(x[1] <= y[0] for x, y in pairwise(spans))
    assert text[spans[0][0] : spans[0][1]] == QUANTIKZ


def test_text_without_a_circuit_is_returned_as_it_is():
    text = doc("Words, $x^2$, \\begin{tabular}{c}a\\end{tabular} and \\resizebox{3cm}{!}{box}.")
    assert protect_circuits(text) == text
    assert circuit_spans(text) == []


# ------------------------------------------------------------ nested wrappers
BOXES = {
    "resizebox": "\\resizebox{\\linewidth}{!}{%s}",
    "resizebox*": "\\resizebox*{\\linewidth}{!}{%s}",
    "scalebox": "\\scalebox{0.8}{%s}",
    "scalebox two factors": "\\scalebox{0.8}[0.9]{%s}",
    "raisebox": "\\raisebox{-2pt}[1ex][0pt]{%s}",
    "rotatebox": "\\rotatebox[origin=c]{90}{%s}",
    "fbox": "\\fbox{%s}",
    "makebox": "\\makebox[3cm][c]{%s}",
    "framebox": "\\framebox[3cm]{%s}",
    "adjustbox command": "\\adjustbox{max width=\\linewidth}{%s}",
    "resizebox with a comment after the brace": "\\resizebox{\\linewidth}{!}{%%\n%s}",
    "scalebox in a resizebox": "\\resizebox{\\linewidth}{!}{\\scalebox{0.5}{%s}}",
}


@pytest.mark.parametrize("wrapper", BOXES.values(), ids=BOXES)
def test_a_box_around_a_circuit_is_taken_off_and_the_circuit_stays(wrapper):
    body = f"Before.\n\\begin{{figure}}\n{wrapper % QUANTIKZ}\n\\caption{{Cap.}}\n\\end{{figure}}"
    out = protect_circuits(doc(body))
    assert listed(out) == [QUANTIKZ]
    assert not re.search(
        r"\\(resizebox|scalebox|raisebox|rotatebox|fbox|makebox|framebox|adjustbox)", out
    )
    assert balanced(out) and balanced(doc(body))
    assert "\\caption{Cap.}" in out
    assert "\\begin{figure}" in out
    assert "\\end{figure}" in out


def test_the_adjustbox_environment_is_taken_off_with_its_options():
    body = f"\\begin{{adjustbox}}{{max width=\\textwidth}}\n{QCIRCUIT}\n\\end{{adjustbox}}"
    out = protect_circuits(doc(body))
    assert listed(out) == [QCIRCUIT]
    assert "adjustbox" not in out
    assert "max width" not in out


def test_a_minipage_is_left_in_place():
    body = f"\\begin{{minipage}}{{0.5\\textwidth}}\n\\resizebox{{\\linewidth}}{{!}}{{{QUANTIKZ}}}\n\\end{{minipage}}"
    out = protect_circuits(doc(body))
    assert out.startswith(doc("")[:40])
    assert "\\begin{minipage}{0.5\\textwidth}" in out
    assert "\\end{minipage}" in out
    assert listed(out) == [QUANTIKZ]


COMMANDS = {
    "centerline": "\\centerline{%s}",
    "mbox": "\\mbox{\n%s\n}",
    "parbox": "\\parbox[t]{3cm}{%s}",
    "textbf": "\\textbf{%s}",
    "centerline and mbox": "\\centerline{\\mbox{ %s }}",
    "unknown command": "\\mystyle{%s}",
    "with a star and options": "\\myframe*[a=b]{x}[c]{%s}",
}


@pytest.mark.parametrize("wrapper", COMMANDS.values(), ids=COMMANDS)
def test_a_command_around_a_circuit_is_taken_off_whatever_it_is(wrapper):
    out = protect_circuits(doc(f"Before.\n{wrapper % QUANTIKZ}\nAfter."))
    assert listed(out) == [QUANTIKZ]
    assert not re.search(r"\\(centerline|mbox|parbox|textbf|mystyle|myframe)", out)
    assert "Before." in out
    assert "After." in out
    assert balanced(out)


def test_a_bare_group_or_a_group_after_a_line_break_is_not_a_command_argument():
    for around in ("{\\small %s}", "\\begin{center}\n{%s}\n\\end{center}", "\\vspace{5pt}\n{%s}"):
        out = protect_circuits(doc(around % QUANTIKZ))
        assert listed(out) == [QUANTIKZ]
        assert balanced(out)
        assert around.split("%s")[0].strip() in out


def test_a_circuit_in_a_caption_or_in_arguments_too_deep_to_read_is_left_alone():
    caption = doc(f"\\caption{{Words {QUANTIKZ} more}}")
    assert protect_circuits(caption) == caption
    deep = doc(f"\\wrap{{a{{b}}}}{{{QCIRCUIT}}}")
    assert protect_circuits(deep) == deep


def test_the_circuits_around_a_command_all_go_and_it_goes_once():
    out = protect_circuits(doc(f"\\centerline{{{QUANTIKZ} and {QCIRCUIT}}}"))
    assert listed(out) == [QUANTIKZ, QCIRCUIT]
    assert "centerline" not in out
    assert balanced(out)


def test_a_subfloat_with_a_short_and_a_long_caption_keeps_the_long_one():
    out = protect_circuits(doc(f"\\subfloat[Short][Long caption]{{{QUANTIKZ}}}"))
    assert listed(out) == [QUANTIKZ]
    assert out.index("\\end{minted}") < out.index("Long caption")
    assert "Short" not in out


def test_a_command_around_math_that_is_not_a_box_that_empties_stays():
    text = doc(f"\\textbf{{${QUANTIKZ}$}} and \\mbox{{${QCIRCUIT}$}}")
    assert protect_circuits(text) == text


def test_a_tikzcd_is_not_a_circuit_because_a_gate_command_follows_it():
    text = doc(f"{TIKZCD_DIAGRAM}\\qw and \\gate{{H}}")
    assert protect_circuits(text) == text


def test_the_commands_inside_a_table_cell_do_not_decide_where_the_circuit_goes():
    table = f"\\begin{{tabular}}{{c}}\\wrap{{a{{b}}}}{{{QUANTIKZ}}}\\end{{tabular}}"
    out = protect_circuits(doc(table))
    assert listed(out) == [QUANTIKZ]
    assert out.index("\\end{tabular}") < out.index("\\begin{minted}")


def test_a_box_without_a_circuit_is_left_alone():
    text = doc("\\resizebox{\\linewidth}{!}{\\begin{tabular}{c}a\\end{tabular}} and \\fbox{x}")
    assert protect_circuits(text) == text


def test_a_box_with_arguments_in_the_wrong_place_is_left_alone():
    text = doc(f"\\resizebox{{3cm}} text {QUANTIKZ} and \\scalebox{{unclosed {QCIRCUIT}")
    out = protect_circuits(text)
    assert listed(out) == [QUANTIKZ, QCIRCUIT]
    assert "\\resizebox{3cm} text" in out
    assert "\\scalebox{unclosed" in out


def test_a_subfloat_caption_follows_its_circuit():
    body = f"\\subfloat[First half]{{\\resizebox{{3cm}}{{!}}{{{QUANTIKZ}}}}}"
    out = protect_circuits(doc(body))
    assert listed(out) == [QUANTIKZ]
    assert "subfloat" not in out
    assert out.index("\\end{minted}") < out.index("First half")


def test_a_subcaptionbox_keeps_its_caption_too():
    out = protect_circuits(doc(f"\\subcaptionbox{{Second half}}[3cm]{{{QCIRCUIT}}}"))
    assert listed(out) == [QCIRCUIT]
    assert "subcaptionbox" not in out
    assert out.index("\\end{minted}") < out.index("Second half")


def test_a_subfloat_without_a_caption_adds_nothing():
    assert protect_circuits(doc(f"\\subfloat{{{QUANTIKZ}}}")) == protect_circuits(doc(QUANTIKZ))


def test_a_box_around_math_that_holds_a_circuit_is_taken_off_and_the_math_stays():
    math = f"${QUANTIKZ}$"
    out = protect_circuits(
        doc(
            f"\\begin{{minipage}}{{3cm}}\\resizebox{{0.85\\linewidth}}{{!}}{{{math}}}\\end{{minipage}}"
        )
    )
    assert "resizebox" not in out
    assert math in out
    assert listed(out) == []
    assert balanced(out)


def test_a_box_inside_math_is_not_touched():
    text = doc("so $\\scalebox{2}{\\Qcircuit{\\qw}}$ is")
    assert protect_circuits(text) == text


def test_a_qcircuit_may_be_followed_by_an_option_without_a_space():
    source = "\\Qcircuit@C=1em@R=1.5em{\n\\lstick{X}&\\qw&\\qswap\\qwx\\\\\n}"
    assert listed(protect_circuits(doc(f"x\n{source}\ny"))) == [source]


# ------------------------------------------------------- TikZ pictures, tables
def test_a_circuit_in_a_tikz_node_takes_the_whole_picture_with_it():
    picture = f"\\begin{{tikzpicture}}\n\\node[scale=0.75] {{\n{QUANTIKZ}\n}};\n\\draw (0,0) -- (1,0);\n\\end{{tikzpicture}}"
    out = protect_circuits(doc(f"Before.\n{picture}\nAfter."))
    assert listed(out) == [picture]
    assert out.count("\\begin{quantikz}") == 1


def test_two_circuits_in_one_picture_make_one_listing():
    picture = f"\\begin{{tikzpicture}}\\node at (0,0) {{{QUANTIKZ}}};\\node at (0,2) {{{QCIRCUIT}}};\\end{{tikzpicture}}"
    assert listed(protect_circuits(doc(picture))) == [picture]


def test_a_picture_without_a_circuit_is_left_alone():
    text = doc("\\begin{tikzpicture}\\draw (0,0) -- (1,1);\\end{tikzpicture}")
    assert protect_circuits(text) == text


def test_a_circuit_in_a_table_cell_goes_after_the_table_and_the_cells_stay():
    table = (
        "\\begin{tabular}{ll}\n"
        f"Hadamard & {QUANTIKZ} \\\\\n"
        "Pauli & text \\\\\n"
        f"Swap & {QCIRCUIT} \\\\\n"
        "\\end{tabular}"
    )
    out = protect_circuits(doc(f"\\begin{{table}}\n{table}\n\\caption{{Gates.}}\n\\end{{table}}"))
    assert listed(out) == [QUANTIKZ, QCIRCUIT]
    before = out[: out.index("\\end{tabular}")]
    assert "\\begin{minted}" not in before
    assert "Hadamard &" in before
    assert "Pauli & text" in before
    assert "Swap &" in before
    assert (
        out.index("\\end{tabular}") < out.index("\\begin{minted}") < out.index("\\caption{Gates.}")
    )


def test_a_circuit_in_a_nested_table_goes_after_the_outer_table():
    inner = f"\\begin{{tabular}}{{c}}{QUANTIKZ}\\end{{tabular}}"
    out = protect_circuits(doc(f"\\begin{{tabular}}{{c}}{inner}\\end{{tabular}}\nEnd."))
    assert listed(out) == [QUANTIKZ]
    assert out.rindex("\\end{tabular}") < out.index("\\begin{minted}") < out.index("End.")


def test_a_table_in_a_resizebox_comes_out_of_it_when_it_holds_a_circuit():
    body = (
        f"\\resizebox{{\\textwidth}}{{!}}{{\\begin{{tabular}}{{cc}}a & {QUANTIKZ}\\end{{tabular}}}}"
    )
    out = protect_circuits(doc(body))
    assert "resizebox" not in out
    assert listed(out) == [QUANTIKZ]
    assert out.index("\\end{tabular}") < out.index("\\begin{minted}")


# ------------------------------------------------------------- left alone
MATH_CONTEXTS = {
    "display dollars": "$$ \\vcenter{%s} $$",
    "bracket display": "\\[ \\vcenter{%s} \\]",
    "parenthesis math": "\\( %s \\)",
    "inline dollars": "so $%s$ is",
    "equation": "\\begin{equation}\n%s\n\\end{equation}",
    "equation star": "\\begin{equation*}\n%s\n\\end{equation*}",
    "align": "\\begin{align}\n a &= %s \\\\\n b &= c\n\\end{align}",
    "displaymath": "\\begin{displaymath}\n%s\n\\end{displaymath}",
    "inline math with a box in it": "$x = \\mbox{%s}$",
}


@pytest.mark.parametrize("source", [QUANTIKZ, QCIRCUIT], ids=["quantikz", "qcircuit"])
@pytest.mark.parametrize("context", MATH_CONTEXTS.values(), ids=MATH_CONTEXTS)
def test_a_circuit_in_math_is_left_for_pandoc_to_copy(context, source):
    text = doc(f"Before.\n\n{context % source}\n\nAfter.")
    assert protect_circuits(text) == text


def test_dollars_inside_a_circuit_do_not_end_the_math_it_sits_in():
    inline = "$\\begin{quantikz}\\lstick{$q_0$} & \\gate{H} \\end{quantikz}$"
    out = protect_circuits(doc(f"We write $x$ and $y$, then {inline}.\n\n{QCIRCUIT}"))
    assert listed(out) == [QCIRCUIT]
    assert inline in out


def test_math_ends_where_it_closes_so_a_circuit_after_it_is_found():
    text = doc(f"$$a+b$$ then {QUANTIKZ} and \\[c\\] then {QCIRCUIT} and $d$$e$ then {YQUANT}")
    assert listed(protect_circuits(text)) == [QUANTIKZ, QCIRCUIT, YQUANT]


def test_an_escaped_dollar_is_text():
    text = doc(f"It costs \\$5 or \\$6.\n\n{QUANTIKZ}")
    assert listed(protect_circuits(text)) == [QUANTIKZ]


def test_a_stray_dollar_does_not_make_the_rest_of_the_paper_math():
    text = doc(f"A lone $ sign here\n\n{QUANTIKZ}\n\nMore.")
    assert listed(protect_circuits(text)) == [QUANTIKZ]


def test_a_blank_line_ends_a_display_that_never_closed():
    text = doc(f"\\begin{{equation}} x = 1\n\n{QCIRCUIT}")
    assert listed(protect_circuits(text)) == [QCIRCUIT]


def test_dollars_in_a_listing_are_not_math():
    text = doc(f"\\begin{{verbatim}}\ncost $$ here $\n\\end{{verbatim}}\n{QUANTIKZ}")
    assert listed(protect_circuits(text)) == [QUANTIKZ]


def test_a_circuit_in_the_preamble_or_a_macro_definition_is_left_alone():
    preamble = f"\\newcommand{{\\bell}}{{{QUANTIKZ}}}\n\\def\\other{{{QCIRCUIT}}}\n"
    text = f"\\documentclass{{article}}\n{preamble}\\begin{{document}}\nUse \\bell and\n{YQUANT}\n\\end{{document}}\n"
    out = protect_circuits(text)
    assert listed(out) == [YQUANT]
    assert out.startswith(f"\\documentclass{{article}}\n{preamble}")


def test_a_macro_defined_in_the_body_is_left_alone_too():
    text = doc(f"\\newcommand{{\\bell}}{{{QUANTIKZ}}}\n\\bell\n{QCIRCUIT}")
    assert listed(protect_circuits(text)) == [QCIRCUIT]


@pytest.mark.parametrize(
    ("env", "begin"),
    [
        ("verbatim", "verbatim"),
        ("Verbatim", "Verbatim"),
        ("lstlisting", "lstlisting"),
        ("comment", "comment"),
        ("minted", "minted}{python"),
    ],
)
def test_a_circuit_shown_in_a_listing_is_not_written_twice(env, begin):
    text = doc(f"\\begin{{{begin}}}\n{QUANTIKZ}\n\\end{{{env}}}\n{QCIRCUIT}")
    out = protect_circuits(text)
    assert listed(out) == [QCIRCUIT]
    assert out.count("\\begin{quantikz}") == 1
    assert f"\\begin{{{begin}}}\n{QUANTIKZ}\n\\end{{{env}}}" in out


def test_a_circuit_in_iffalse_is_not_typeset_and_is_left_alone():
    text = doc(f"\\iffalse\n{QUANTIKZ}\n\\fi\n{QCIRCUIT}")
    assert listed(protect_circuits(text)) == [QCIRCUIT]


def test_a_circuit_in_the_preamble_itself_is_left_alone():
    text = f"\\documentclass{{article}}\n{QUANTIKZ}\n\\begin{{document}}\n{QCIRCUIT}\n\\end{{document}}\n"
    assert listed(protect_circuits(text)) == [QCIRCUIT]


def test_a_dollar_in_a_box_in_inline_math_does_not_end_it():
    text = doc("so $\\mbox{$\\Qcircuit{\\qw}$} c$ is")
    assert protect_circuits(text) == text


def test_an_escaped_dollar_does_not_open_math():
    assert listed(protect_circuits(doc(f"It costs \\$5 and then {QUANTIKZ} follows"))) == [QUANTIKZ]


def test_a_lone_dollar_inside_a_display_does_not_end_it():
    text = doc("$$ a $ \\Qcircuit{\\qw} b $$")
    assert protect_circuits(text) == text


def test_the_end_of_an_inner_environment_does_not_end_the_display():
    text = doc("\\begin{equation} \\begin{math} x \\end{math} \\Qcircuit{\\qw} \\end{equation}")
    assert protect_circuits(text) == text


def test_a_circuit_right_after_math_is_found():
    assert listed(protect_circuits(doc(f"$x${QUANTIKZ}"))) == [QUANTIKZ]
    assert listed(protect_circuits(doc(f"\\[x\\]{QCIRCUIT}"))) == [QCIRCUIT]


def test_a_box_next_to_a_circuit_but_not_around_it_stays():
    out = protect_circuits(doc(f"\\fbox{{x}}{QUANTIKZ} \\resizebox{{3cm}}{{!}}{{y}}"))
    assert listed(out) == [QUANTIKZ]
    assert "\\fbox{x}" in out
    assert "\\resizebox{3cm}{!}{y}" in out


def test_two_pictures_side_by_side_are_two_pictures():
    plain = "\\begin{tikzpicture}\\draw (0,0);\\end{tikzpicture}"
    picture = f"\\begin{{tikzpicture}}\\node {{{QUANTIKZ}}};\\end{{tikzpicture}}"
    out = protect_circuits(doc(plain + picture))
    assert listed(out) == [picture]
    assert plain in out.replace(picture, "")


def test_the_word_qcircuit_in_a_sentence_is_not_a_circuit():
    out = protect_circuits(doc(f"The \\Qcircuit command, see {QUANTIKZ} next"))
    assert listed(out) == [QUANTIKZ]


# ------------------------------------------------ edits that must not overlap
def test_a_math_circuit_in_a_box_before_a_text_circuit_in_the_same_picture_is_written_once():
    math = "\\resizebox{2cm}{!}{$\\begin{quantikz} MATHGATE \\end{quantikz}$}"
    picture = f"\\begin{{tikzpicture}}\n\\node {{{math}}};\n\\node at (0,2) {{{QCIRCUIT}}};\n\\end{{tikzpicture}}"
    out = protect_circuits(doc(f"Before.\n{picture}\nAfter."))
    assert listed(out) == [picture]
    assert out.count("MATHGATE") == 1
    assert out.count("\\begin{tikzpicture}") == 1
    assert out.count("\\Qcircuit") == 1
    spans = circuit_spans(doc(picture))
    assert len(spans) == 1


def test_crossed_environments_do_not_make_overlapping_listings():
    text = doc(
        "\\begin{quantikz} AAA \\begin{tikzpicture} \\end{quantikz} BBB "
        "\\begin{quantikz} CCC \\end{quantikz} \\end{tikzpicture} DDD"
    )
    spans = circuit_spans(text)
    assert all(x[1] <= y[0] for x, y in pairwise(spans))
    out = protect_circuits(text)
    for word in ("AAA", "BBB", "CCC", "DDD"):
        assert out.count(word) == 1


PIECES = [
    QUANTIKZ,
    QCIRCUIT,
    TIKZCD_CIRCUIT,
    "\\begin{tikzpicture}",
    "\\end{tikzpicture}",
    "\\begin{quantikz}",
    "\\end{quantikz}",
    "\\begin{tabular}{c}",
    "\\end{tabular}",
    "\\resizebox{2cm}{!}{",
    "\\centerline{",
    "\\begin{adjustbox}{width=1cm}",
    "\\end{adjustbox}",
    "\\subfloat[cap]{",
    "$",
    "$$",
    "\\[",
    "\\]",
    "{",
    "}",
    "\\Qcircuit @C=1em {",
    "\\caption{",
    "\\begin{equation}",
    "\\end{equation}",
    " WORD ",
    "\n",
    "\n\n",
]


def test_no_text_is_written_twice_whatever_the_nesting():
    rng = random.Random(7)
    for _ in range(400):
        parts = [rng.choice(PIECES) for _ in range(rng.randint(3, 14))]
        marked = "".join(f"{p}M{i}X" for i, p in enumerate(parts))
        out = protect_circuits(doc(marked))
        spans = circuit_spans(doc(marked))
        assert all(x[1] <= y[0] for x, y in pairwise(spans)), marked
        for i in range(len(parts)):
            assert out.count(f"M{i}X") == 1, (i, marked)


# ---------------------------------------------- the source is the author's own
SOURCE_WITH_REWRITABLES = (
    "\\begin{quantikz}[row sep=1mm]\n"
    "\\lstick{\\cite{a,,b}} & \\gate{H} \\vskip 2pt \\raggedright & \\qw \\\\\n"
    "\\lstick{\\today \\And \\AND \\label[x]{y}} & \\qw \\label[type]{z} & \\qw\n"
    "\\end{quantikz}"
)


def test_the_later_rewrites_do_not_touch_the_source_of_a_circuit():
    text = doc(
        f"Before \\today \\raggedright \\cite{{c,,d}}.\n{SOURCE_WITH_REWRITABLES}\nAfter \\vskip 3pt."
    )
    out, _ = prepare_for_pandoc(text)
    assert listed(out) == [SOURCE_WITH_REWRITABLES]
    assert "Before  " in out
    assert "\\cite{c,d}" in out
    assert "\\raggedright" not in out.replace(SOURCE_WITH_REWRITABLES, "")
    assert "\\vskip 3pt" not in out


def test_a_qcircuit_source_is_also_left_as_written():
    source = "\\Qcircuit @C=1em { & \\gate{\\today} & \\lstick{\\cite{a,,b}} \\vskip 1pt }"
    assert listed(prepare_for_pandoc(doc(source))[0]) == [source]


def test_a_circuit_may_hold_a_word_that_looks_like_the_hiding_place():
    source = "\\begin{quantikz} DOCINGESTLISTING0END \\gate{X} \\end{quantikz}"
    out, _ = prepare_for_pandoc(doc(f"A DOCINGESTLISTING1END word.\n{source}\n{QCIRCUIT}"))
    assert listed(out) == [source, QCIRCUIT]
    assert "A DOCINGESTLISTING1END word." in out


def test_a_listing_of_the_paper_that_never_closes_is_left_to_the_rewrites():
    out, _ = prepare_for_pandoc(doc("\\begin{minted}{latex}\nbroken \\today"))
    assert "\\today" not in out


# ---------------------------------------------------------- the small gaps
def test_a_group_after_a_space_is_not_an_argument_of_the_command_before_it():
    out = protect_circuits(doc(f"\\textbf{{Note.}} {{{QUANTIKZ}}} after"))
    assert listed(out) == [QUANTIKZ]
    assert "\\textbf{Note.}" in out


def test_a_link_around_a_circuit_is_taken_off_and_its_url_follows_the_block():
    out = protect_circuits(doc(f"See \\href{{https://example.org/x}}{{{QUANTIKZ}}} now."))
    assert listed(out) == [QUANTIKZ]
    assert "href" not in out
    assert (
        out.index("\\end{minted}") < out.index("\\url{https://example.org/x}") < out.index("now.")
    )
    assert balanced(out)


@pytest.mark.parametrize("command", ["\\centerline", "\\mbox", "\\small"])
def test_spaces_right_after_a_command_do_not_stop_it_from_being_taken_off(command):
    out = protect_circuits(doc(f"x\n{command} {{{QUANTIKZ}}}\ny"))
    assert listed(out) == [QUANTIKZ]
    assert not re.search(r"\\(centerline|mbox|small)", out)
    assert balanced(out)


def test_spaces_after_an_argument_or_text_still_make_a_group_of_its_own():
    for before in ("\\textbf{Note.}", "\\resizebox{3cm}{!}", "word", "\\foo[x]"):
        out = protect_circuits(doc(f"{before}  {{{QUANTIKZ}}}"))
        assert listed(out) == [QUANTIKZ]
        assert before in out


def test_a_group_right_after_begin_on_the_same_line_is_a_group_of_its_own():
    out = protect_circuits(doc(f"\\begin{{center}}{{\\small {QUANTIKZ}}}\\end{{center}}"))
    assert listed(out) == [QUANTIKZ]
    assert out.count("\\begin{center}{\\small") == 1
    assert balanced(out)


def test_a_stray_closing_brace_in_inline_math_does_not_keep_it_open():
    out = protect_circuits(doc(f"so $a }} b$ then {QUANTIKZ}"))
    assert listed(out) == [QUANTIKZ]


def test_the_listing_starts_and_ends_on_a_line_of_its_own():
    out = protect_circuits(doc(f"x{QUANTIKZ}y"))
    assert "x\n\\begin{minted}{latex}\n" in out
    assert "\n\\end{minted}\ny" in out


def test_blank_lines_are_collapsed_outside_fences_only():
    md = "a\n\n\n\nb\n\n``` latex\nx\n\n\n\ny\n```\n\n\n\nc\n\n~~~~\n\n\nz\n~~~~"
    assert (
        collapse_blank_lines(md) == "a\n\nb\n\n``` latex\nx\n\n\n\ny\n```\n\nc\n\n~~~~\n\n\nz\n~~~~"
    )
    parts = [(1, "S", "# S", "text\n\n\n\n```\nk\n\n\nl\n```\n\n\n\nend")]
    assert assemble(parts)[0].text == "# S\n\ntext\n\n```\nk\n\n\nl\n```\n\nend"


# ---------------------------------------------------------------- malformed
@pytest.mark.parametrize(
    ("broken", "found"),
    [
        ("\\Qcircuit @C=1em { & \\gate{H} & \\qw", []),
        ("\\Qcircuit @C=1em", []),
        ("\\Qcircuit", []),
        ("\\begin{quantikz} \\gate{H} & \\qw", []),
        ("\\end{quantikz} \\gate{H} & \\qw", []),
        ("\\begin{yquant} qubit q;", []),
        ("\\begin{tikzcd} \\gate{H}", []),
        ("\\Qcircuit { & \\gate{H} }}} {{{ \\qw", ["\\Qcircuit { & \\gate{H} }"]),
    ],
)
def test_a_circuit_that_never_closes_is_left_alone_and_the_text_after_it_is_safe(broken, found):
    text = doc(f"Before.\n{broken}\n\n{QUANTIKZ}\n\nAfter the circuits.")
    out = protect_circuits(text)
    assert listed(out) == [*found, QUANTIKZ]
    assert "After the circuits." in out
    assert "Before." in out
    assert out.count(broken) == (0 if found else 1)


def test_a_qcircuit_group_with_a_blank_line_in_it_is_a_missing_brace_not_a_circuit():
    text = doc("\\Qcircuit @C=1em { & \\gate{H}\n\nA paragraph of the paper }\n")
    assert protect_circuits(text) == text


def test_a_circuit_longer_than_the_limit_is_left_alone(monkeypatch):
    monkeypatch.setattr(latex_source, "CIRCUIT_MAX", 40)
    text = doc(f"{QUANTIKZ}\n{{\\Qcircuit {{ & \\qw }}}}")
    out = protect_circuits(text)
    assert QUANTIKZ in out
    assert listed(out) == ["\\Qcircuit { & \\qw }"]


def test_a_document_with_no_begin_document_is_all_body():
    assert listed(protect_circuits(f"Just a fragment.\n{QUANTIKZ}\n")) == [QUANTIKZ]


# ----------------------------------------------------------- the pipeline
def test_prepare_for_pandoc_writes_the_listing_and_keeps_its_other_rewrites():
    src = doc(f"Today is \\today.\n\\begin{{figure}}\n{QUANTIKZ}\n\\caption{{C.}}\n\\end{{figure}}")
    out, warnings = prepare_for_pandoc(src)
    assert listed(out) == [QUANTIKZ]
    assert "\\today" not in out
    assert warnings == []


def test_the_circuit_source_is_not_changed_by_the_comment_free_text_around_it():
    source = (
        "\\begin{quantikz}\n\\gate{H}   & \\qw  \n\n\t\\gate{X} & \\qw \\\\[2mm]\n\\end{quantikz}"
    )
    assert listed(protect_circuits(doc(source))) == [source]


# -------------------------------------------------- linear time on bad input
N = 12_000


def seconds(fn, text) -> float:
    best = float("inf")
    for _ in range(2):
        t0 = time.process_time()
        fn(text)
        best = min(best, time.process_time() - t0)
    return best


UNITS = {
    "unclosed quantikz": lambda n: "\\begin{quantikz} \\gate{H}\n" * n,
    "unclosed Qcircuit groups": lambda n: "\\Qcircuit @C=1em { & \\gate{H} \\qw\n" * n,
    "unclosed tikzcd": lambda n: "\\begin{tikzcd} \\qw\n" * n,
    "stray end": lambda n: "\\end{quantikz}\n" * n,
    "closed circuits": lambda n: "\\begin{quantikz}\\gate{H}\\end{quantikz}\n" * n,
    "nested circuits": lambda n: "\\begin{tikzcd}\\qw" * n + "\\end{tikzcd}" * n,
    "nested pictures": lambda n: (
        "\\begin{tikzpicture}" * n
        + "\\begin{quantikz}\\qw\\end{quantikz}"
        + "\\end{tikzpicture}" * n
    ),
    "boxes with no body": lambda n: (
        "\\resizebox{a}{b}" * n + "\\begin{quantikz}\\qw\\end{quantikz}"
    ),
    "unclosed boxes": lambda n: (
        "\\resizebox{a}{b}{\\scalebox{2}{" * n + "\\begin{quantikz}\\qw\\end{quantikz}"
    ),
    "adjustboxes": lambda n: (
        "\\begin{adjustbox}{x}\\begin{quantikz}\\qw\\end{quantikz}\\end{adjustbox}" * n
    ),
    "open math": lambda n: "$ \\begin{quantikz}\\qw\\end{quantikz}\n" * n,
    "dollars": lambda n: "$$ $ " * n + "\\begin{quantikz}\\qw\\end{quantikz}",
    "unclosed display envs": lambda n: "\\begin{equation} \\Qcircuit{\\qw}\n" * n,
    "tables": lambda n: (
        "\\begin{tabular}{c}\\begin{quantikz}\\qw\\end{quantikz}\\end{tabular}\n" * n
    ),
    "nested tables": lambda n: (
        "\\begin{tabular}{c}" * n + "\\begin{quantikz}\\qw\\end{quantikz}" + "\\end{tabular}" * n
    ),
    "subfloats": lambda n: "\\subfloat[a]{\\begin{quantikz}\\qw\\end{quantikz}}\n" * n,
    "circuits around by commands": lambda n: "\\mbox{\\begin{quantikz}\\qw\\end{quantikz}}\n" * n,
    "circuits in deep groups": lambda n: "{" * n + "\\Qcircuit{\\qw}" * n + "}" * n,
    "circuits in tall command towers": lambda n: "\\mbox{" * n + "\\Qcircuit{\\qw}" * n + "}" * n,
    "braces": lambda n: "{" * n + "\\Qcircuit{\\qw}" + "}" * n,
}


@pytest.mark.parametrize("make", UNITS.values(), ids=UNITS)
def test_scanning_is_linear_in_the_size_of_the_text(make):
    small, big = seconds(protect_circuits, make(N // 4)), seconds(protect_circuits, make(N))
    assert big < 10
    assert big < max(8 * small, 0.05), (small, big)


# ------------------------------------------------------- the plain-text path
@pytest.fixture(scope="module")
def plain():
    converter = PandocLatexConverter(LatexConfig(pandoc_path="/nonexistent/pandoc"))
    return lambda body: "\n\n".join(s.text for s in converter._plaintext(doc(body)).segments)


FENCED = re.compile(r"^``` latex\n(.*?)\n```$", re.S | re.M)


def test_the_plain_text_path_writes_the_same_fenced_block(plain):
    prepared = protect_circuits(
        doc(
            f"\\section{{S}}\nWords before.\n\\begin{{figure}}\n{QUANTIKZ}\n\\caption{{A pair.}}\n\\end{{figure}}\nWords after."
        )
    )
    converter = PandocLatexConverter(LatexConfig(pandoc_path="/nonexistent/pandoc"))
    out = "\n\n".join(s.text for s in converter._plaintext(prepared).segments)
    assert FENCED.findall(out) == [QUANTIKZ]
    assert (
        out.index("Words before.")
        < out.index("``` latex")
        < out.index("A pair.")
        < out.index("Words after.")
    )


@pytest.mark.parametrize("source", CIRCUITS.values(), ids=CIRCUITS)
def test_every_encoding_is_exact_in_the_plain_text_path(source):
    converter = PandocLatexConverter(LatexConfig(pandoc_path="/nonexistent/pandoc"))
    out = "\n\n".join(
        s.text for s in converter._plaintext(protect_circuits(doc(f"x\n{source}\ny"))).segments
    )
    assert FENCED.findall(out) == [source]


def test_tabs_become_spaces_as_pandoc_writes_them(plain):
    out = plain(
        "\\begin{minted}{latex}\n\\begin{quantikz}\n\t\\gate{H}\t& \\qw  \n\\end{quantikz}\n\\end{minted}"
    )
    assert FENCED.findall(out) == ["\\begin{quantikz}\n    \\gate{H}    & \\qw  \n\\end{quantikz}"]


def test_a_line_of_backticks_in_the_code_gets_a_longer_fence(plain):
    out = plain("\\begin{minted}{latex}\nA\n```\nB\n\\end{minted}")
    assert out.splitlines()[0] == "```` latex"
    assert out.splitlines()[-1] == "````"


def test_a_listing_in_another_spelling_of_the_language_is_not_a_circuit(plain):
    out = plain("\\begin{minted}{LaTeX}\n\\Qcircuit{\\qw}\n\\end{minted}")
    assert "```" not in out


def test_other_listings_stay_as_they_were(plain):
    out = plain("\\begin{minted}{python}\nx = 1\n\\end{minted}")
    assert "```" not in out
    assert "x = 1" in out


def test_a_word_that_looks_like_the_placeholder_is_not_taken_for_one(plain):
    out = plain(
        "The word DOCINGESTCODE0END is odd.\n\\begin{minted}{latex}\n\\Qcircuit{\\qw}\n\\end{minted}\nEnd."
    )
    assert "DOCINGESTCODE0END is odd." in out
    assert FENCED.findall(out) == ["\\Qcircuit{\\qw}"]


def test_the_block_survives_when_pylatexenc_fails_on_the_text_around_it(monkeypatch):
    class Failing:
        def __init__(self, *args, **kwargs):
            pass

        def latex_to_text(self, _):
            raise ValueError("no")

    monkeypatch.setattr("pylatexenc.latex2text.LatexNodes2Text", Failing)
    converter = PandocLatexConverter(LatexConfig(pandoc_path="/nonexistent/pandoc"))
    attempt = converter._plaintext(
        protect_circuits(doc(f"\\section{{S}}\nWords \\emph{{here}}.\n{QCIRCUIT}\nMore."))
    )
    out = "\n\n".join(s.text for s in attempt.segments)
    assert FENCED.findall(out) == [QCIRCUIT]
    assert "Words" in out
    assert "More." in out
    assert attempt.warnings


def test_text_without_a_listing_takes_the_old_route(plain):
    out = plain("\\section{S}\nJust words.")
    assert "Just words." in out
    assert "```" not in out
