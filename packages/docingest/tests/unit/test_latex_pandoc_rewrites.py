"""The source rewrites that keep pandoc 3.9 from failing on common LaTeX (latex_source).

String in, string out: no pandoc binary. Every fixture is invented. The REVTeX-style
``.bbl`` below has the shape of what a bibliography style writes (``@`` helper macros,
``\\href`` with a line break before its arguments, ``\\href@noop``, ``\\Eprint``), with
made-up authors and titles. ``tests/integration/test_pandoc_latex.py`` runs the same
shapes through the real pandoc.
"""

import re
import time

import pytest

from docingest.adapters.converters.latex_source import (
    clean_citation_keys,
    prepare_for_pandoc,
    rewrite_bibliography,
    sanitize_citation_keys,
    shim_at_macros,
    simplify_layout,
)

REVTEX_BBL = """\
\\begin{thebibliography}{2}%
\\makeatletter
\\providecommand \\@ifxundefined [1]{%
 \\@ifx{#1\\undefined}
}%
\\providecommand \\@firstofone [1]{#1}%
\\providecommand \\bibinfo [0]{\\@secondoftwo}%
\\providecommand \\Eprint [0]{\\href }%
\\providecommand \\BibitemShut [1]{\\csname bibitem#1\\endcsname}%
\\providecommand{\\url}[1]{\\texttt{#1}}%
\\bibitem [{Quill(2031)}]{quill31}%
  \\bibinfo {author} {I. Quill},\\ \\href
  {https://example.org/quill31} {\\bibinfo {journal} {Basin Letters} (2031)}\\BibitemShut {NoStop}%
\\bibitem [{Tallow(2029)}]{tallow29}%
  \\href@noop {} {\\bibinfo {journal} {Marsh Notes} (2029)}\\BibitemShut {NoStop}%
\\end{thebibliography}
"""


# ------------------------------------------------------------- @-macro shim
def test_definitions_of_at_names_are_dropped():
    src = "\\providecommand \\@ifxundefined [1]{%\n \\@ifx{#1\\undefined}\n}%\nBody."
    assert shim_at_macros(src) == "%\nBody."


@pytest.mark.parametrize(
    ("definition", "plain"),
    [
        ("\\providecommand \\bibinfo [0]{\\@secondoftwo}", "\\providecommand{\\bibinfo}[2]{#2}"),
        ("\\providecommand\\pick[0]{ \\@firstoftwo }", "\\providecommand{\\pick}[2]{#1}"),
        ("\\newcommand{\\eat}[0]{\\@gobble}", "\\providecommand{\\eat}[1]{}"),
        ("\\providecommand \\two [2]{\\@gobbletwo}", "\\providecommand{\\two}[2]{}"),
        ("\\providecommand{\\same}[1]{\\@firstofone}", "\\providecommand{\\same}[1]{#1}"),
    ],
)
def test_a_body_that_is_one_internal_gets_its_plain_equivalent(definition, plain):
    assert shim_at_macros(f"A {definition} B") == f"A {plain} B"


@pytest.mark.parametrize(
    ("definition", "plain"),
    [
        (
            "\\providecommand \\BibitemShut [1]{\\csname bibitem#1\\endcsname}",
            "\\providecommand{\\BibitemShut}[1]{}",
        ),
        ("\\providecommand \\EOS [0]{\\spacefactor3000\\relax}", "\\providecommand{\\EOS}{}"),
        ("\\def\\hide#1#2{\\catcode`\\#=12 }", "\\providecommand{\\hide}[2]{}"),
        # IEEEtran: the language check is built on \csname, the title (#2) is printed
        (
            "\\providecommand{\\BIBforeignlanguage}[2]{{\\expandafter\\ifx\\csname l@#1\\endcsname"
            "\\relax \\typeout{** WARNING: unknown language #1}\\else\\language=\\csname l@#1"
            "\\endcsname\\fi #2}}",
            "\\providecommand{\\BIBforeignlanguage}[2]{#2}",
        ),
        (
            "\\newcommand{\\both}[2]{\\csname x#1\\endcsname #2 #1 #2}",
            "\\providecommand{\\both}[2]{#2#1}",
        ),
        ("\\newcommand{\\say}[1]{\\typeout{#1}\\spacefactor1000}", "\\providecommand{\\say}[1]{}"),
    ],
)
def test_a_body_built_on_csname_or_catcode_keeps_only_the_arguments_it_prints(definition, plain):
    assert shim_at_macros(definition, opaque=True) == plain


def test_such_bodies_are_left_alone_outside_a_bibliography_preamble():
    src = "\\newcommand{\\etal}{\\emph{et al}.\\spacefactor1000\\ }\\newcommand{\\sym}[1]{\\csname #1\\endcsname}"
    assert shim_at_macros(src) == src
    assert prepare_for_pandoc(src)[0] == src


def test_the_bibliography_preamble_is_where_they_are_emptied():
    bbl = (
        "\\begin{thebibliography}{1}\n"
        "\\providecommand{\\BibitemShut}[1]{\\csname bibitem#1\\endcsname}\n"
        "\\bibitem{a} A.\\BibitemShut{NoStop}\n"
        "\\end{thebibliography}"
    )
    assert "\\providecommand{\\BibitemShut}[1]{}" in rewrite_bibliography(bbl)


def test_ieeetran_titles_survive_the_shim():
    bbl = (
        "\\begin{thebibliography}{1}\n"
        "\\providecommand{\\BIBforeignlanguage}[2]{{\\expandafter\\ifx\\csname l@#1\\endcsname"
        "\\relax \\typeout{** WARNING: unknown language #1}\\else\\language=\\csname l@#1"
        "\\endcsname\\fi #2}}\n"
        "\\bibitem{reed30}\nA. Reed, \\BIBforeignlanguage{en}{``Tidal wicks in shallow basins,''} "
        "\\emph{Basin Letters}, vol. 4, 2030.\n"
        "\\end{thebibliography}"
    )
    out, _ = prepare_for_pandoc(bbl)
    assert "\\providecommand{\\BIBforeignlanguage}[2]{#2}" in out
    assert "\\BIBforeignlanguage{en}{``Tidal wicks in shallow basins,''}" in out


def test_other_definitions_and_other_at_bodies_are_left_alone():
    src = (
        "\\providecommand{\\natexlab}[1]{#1}\n"
        "\\newcommand{\\keep}[1]{\\@ifnextchar[{#1}{x}}\n"  # an @ body that is not one internal
        "\\def\\plain#1{\\emph{#1}}\n"
    )
    assert shim_at_macros(src) == src


def test_a_nature_style_bbl_keeps_its_helpers():
    bbl = (
        "\\begin{thebibliography}{1}\n"
        "\\providecommand{\\bibinfo}[2]{#2}\n"
        "\\providecommand{\\bibfield}[2]{#2}\n"
        "\\providecommand{\\natexlab}[1]{#1}\n"
        "\\bibitem{reed30}\n\\bibfield{author}{\\bibinfo{author}{Reed, A.}} Tidal wicks.\n"
        "\\end{thebibliography}\n"
    )
    out, _ = prepare_for_pandoc(bbl)
    for helper in ["\\bibinfo}[2]{#2}", "\\bibfield}[2]{#2}", "\\natexlab}[1]{#1}"]:
        assert helper in out
    assert "\\bibfield{author}{\\bibinfo{author}{Reed, A.}} Tidal wicks." in out


# ------------------------------------------------- REVTeX reference entries
def test_a_href_with_a_line_break_before_its_arguments_is_joined():
    out = rewrite_bibliography(REVTEX_BBL)
    assert "\\href{https://example.org/quill31}{\\bibinfo {journal} {Basin Letters} (2031)}" in out
    assert not re.search(r"\\href\s*\{[^{}]*\}\s+\{", out)


def test_href_noop_without_a_url_is_dropped_and_its_text_kept():
    out = rewrite_bibliography(REVTEX_BBL)
    assert "href@noop" not in out
    assert "{\\bibinfo {journal} {Marsh Notes} (2029)}\\BibitemShut" in out


def test_eprint_is_defined_with_its_two_arguments():
    out = rewrite_bibliography(REVTEX_BBL)
    assert "\\providecommand{\\Eprint}[2]{\\href{#1}{#2}}" in out
    assert "\\Eprint [0]{\\href }" not in out


def test_href_rewrites_stay_inside_the_bibliography():
    body = "See \\href {https://example.org/a}\n {the page}.\n"
    assert rewrite_bibliography(body) == body
    assert prepare_for_pandoc(body)[0] == body


def test_revtex_bbl_through_prepare_has_no_at_definition_left():
    out, _ = prepare_for_pandoc(REVTEX_BBL)
    assert not re.search(r"\\(?:providecommand|newcommand)\s*\{?\\[a-zA-Z]*@", out)
    assert "\\providecommand{\\bibinfo}[2]{#2}" in out
    assert "\\providecommand{\\BibitemShut}[1]{}" in out
    assert "\\providecommand{\\url}" not in out  # rewrite_bibliography drops url overrides
    assert "\\item \\cite{quill31}" in out and "\\item \\cite{tallow29}" in out


# ------------------------------------------------------------ citation keys
@pytest.mark.parametrize(
    ("keys", "clean"),
    [
        ("a,b", "a,b"),
        ("a, b ,c", "a,b,c"),
        (",a", "a"),
        ("a, ,b", "a,b"),
        ("a,", "a"),
        ("", ""),
        (" , ", ""),
        ("a$b", "a_x24_b"),
        ("x\u2019y", "x_x2019_y"),  # curly quote
        ("fo\u2011o", "fo_x2011_o"),  # non-breaking hyphen
        ("a|b", "a_x7c_b"),
        (
            "M\u00fcller2020,\u0141ukasiewicz:1920",
            "M\u00fcller2020,\u0141ukasiewicz:1920",
        ),  # letters of any script
        ("a{b}c", "abc"),
        ("a b", "a_x20_b"),
        ("%\n\t\ta,%a note, with a comma\n\tb", "a,b"),  # comments inside a cite
        ("50\\%", "50_x5c__x25_"),
        ("Smith:2020/ok.v1+x-y_z", "Smith:2020/ok.v1+x-y_z"),
    ],
)
def test_clean_citation_keys(keys, clean):
    assert clean_citation_keys(keys) == clean


@pytest.mark.parametrize(
    ("src", "out"),
    [
        ("\\cite{,a}", "\\cite{a}"),
        ("\\cite{a, ,b}", "\\cite{a,b}"),
        ("\\cite{a$b,c}", "\\cite{a_x24_b,c}"),
        ("\\citep[p.~3]{a|b}", "\\citep[p.~3]{a_x7c_b}"),
        ("\\citet*[see][p.~3]{a$b}", "\\citet*[see][p.~3]{a_x24_b}"),
        ("\\textcite{x\u2019y} and \\autocite{z}", "\\textcite{x_x2019_y} and \\autocite{z}"),
        ("\\nocite{a b}", "\\nocite{a_x20_b}"),
        ("\\nocite{*}", "\\nocite{*}"),
        ("\\citep{M\u00fcller2020}", "\\citep{M\u00fcller2020}"),
        ("A\\cite{,} B", "A B"),
        ("\\cite{}", ""),
        ("\\cite{ok}", "\\cite{ok}"),
        ("\\cite{%\n\t\ta,\n\t\tb}", "\\cite{a,b}"),
    ],
)
def test_citation_commands_get_clean_keys(src, out):
    assert sanitize_citation_keys(src) == out


@pytest.mark.parametrize(
    "src",
    [
        "\\edef\\citenum#1{\\ifnum#1>0 x\\fi}",
        "\\def\\citeauthor#1{\\cite{#1}}",
        "\\renewcommand{\\cite}[1]{[#1]}",
        "\\newcommand\\citep[1]{\\cite{#1}}",
        "\\let\\citet\\cite",
        "\\newcommand{\\mycite}[1]{\\cite{#1}}",  # a macro parameter is not a key
        "\\cite{unclosed",
    ],
)
def test_definitions_and_macro_parameters_are_not_touched(src):
    assert sanitize_citation_keys(src) == src


def test_an_edef_of_citenum_keeps_its_body_while_its_uses_are_cleaned():
    src = "\\edef\\citenum{a$b}\n\\citenum{a$b} and \\cite{a$b}"
    out, _ = prepare_for_pandoc(src)
    assert out == "\\edef\\citenum{a$b}\n\\citenum{a_x24_b} and \\cite{a_x24_b}"


def test_a_bibitem_key_and_a_citation_of_it_stay_equal():
    text = (
        "Odd keys \\cite{lamp$32,,x y}.\n"
        "\\begin{thebibliography}{2}\n"
        "\\bibitem{lamp$32} Lamp. Preprint.\n"
        "\\bibitem{x y} Ex. Note.\n"
        "\\bibitem{} Anonymous.\n"
        "\\end{thebibliography}\n"
    )
    out, _ = prepare_for_pandoc(text)
    assert "\\cite{lamp_x24_32,x_x20_y}" in out
    assert "\\item \\cite{lamp_x24_32}  Lamp. Preprint." in out
    assert "\\item \\cite{x_x20_y}  Ex. Note." in out
    assert "\\item  Anonymous." in out  # an empty key leaves no empty citation
    assert "$" not in out


def test_rewrite_bibliography_cleans_the_keys_on_its_own():
    bbl = "\\begin{thebibliography}{1}\n\\bibitem{a$b} Entry.\n\\end{thebibliography}"
    assert "\\item \\cite{a_x24_b} " in rewrite_bibliography(bbl)


def test_distinct_keys_stay_distinct_after_cleaning():
    keys = [
        "a$b",
        "a&b",
        "a_b",
        "a b",
        "a|b",
        "a\u2019b",
        "a\u2011b",
        "a-b",
        "a:b",
        "M\u00fcller",
        "M\u00e4ller",
    ]
    cleaned = [clean_citation_keys(k) for k in keys]
    assert len(set(cleaned)) == len(keys)
    assert all(re.fullmatch(r"[\w:.+/\-]+", k) for k in cleaned)


def test_clean_keys_are_unchanged_by_a_second_pass():
    once, _ = prepare_for_pandoc("\\cite{a$b}\n" + REVTEX_BBL)
    twice, _ = prepare_for_pandoc(once)
    assert twice == once


# ----------------------------------------------------------- small rewrites
def test_cleveref_label_option_is_dropped():
    assert simplify_layout("\\section{A}\\label[app]{sec:a} \\label {x}") == (
        "\\section{A}\\label{sec:a} \\label {x}"
    )
    assert simplify_layout("\\label[type] {k}") == "\\label{k}"


@pytest.mark.parametrize(
    ("src", "out"),
    [
        ("\\global\\long\\def\\ket#1{|#1\\rangle}", "\\def\\ket#1{|#1\\rangle}"),
        ("\\long\\def\\a{x}", "\\def\\a{x}"),
        ("\\global \\def\\a{x}", "\\def\\a{x}"),
        ("\\protected\\long\\global\\def\\a{x}", "\\def\\a{x}"),
        ("\\edef\\a#1{x}\\xdef \\b #1#2{y}", "\\def\\a#1{x}\\def \\b #1#2{y}"),
        # pandoc reads these as they are, with their meaning
        (
            "\\gdef\\a{x}\\xdef\\b{y}\\edef\\c{z}\\gdef\\d#1{w}",
            "\\gdef\\a{x}\\xdef\\b{y}\\edef\\c{z}\\gdef\\d#1{w}",
        ),
        ("\\xdef\\savedX{\\X}", "\\xdef\\savedX{\\X}"),
        ("\\def\\a{x}", "\\def\\a{x}"),
        ("\\globaldefs=1 \\defect", "\\globaldefs=1 \\defect"),
    ],
)
def test_lyx_definition_prefixes_and_parameterised_edef_become_plain_def(src, out):
    assert simplify_layout(src) == out


def test_newcolumntype_definitions_are_dropped():
    src = (
        "\\newcolumntype{P}[1]{>{\\centering\\arraybackslash}p{#1}}\n"
        "\\newcolumntype {L} {>{\\raggedright}m{3cm}}\n"
        "\\begin{tabular}{lP{2cm}}a&b\\end{tabular}\n"
    )
    out = simplify_layout(src)
    assert "newcolumntype" not in out
    assert out.endswith("\n\\begin{tabular}{lP{2cm}}a&b\\end{tabular}\n")


def test_raggedright_is_dropped_but_not_longer_names():
    out = simplify_layout("\\caption{\\raggedright A caption} \\raggedrightx")
    assert out == "\\caption{  A caption} \\raggedrightx"


@pytest.mark.parametrize(
    ("src", "out"),
    [
        ("a\\vskip\\baselineskip b", "a  b"),
        ("a\\vskip -0.25ex b", "a  b"),
        ("a\\vskip 3pt plus 1fil minus 2pt b", "a  b"),
        ("a\\vskip 2em", "a "),
        ("\\vskipx", "\\vskipx"),
        ("a\\vskip 2 inches", "a\\vskip 2 inches"),
        ("a\\vskip 2bp plus 1fill b", "a  b"),
        ("\\vspace{1cm}", "\\vspace{1cm}"),
    ],
)
def test_vskip_glue_is_dropped(src, out):
    assert simplify_layout(src) == out


def test_linebreakand_in_an_author_block_becomes_and():
    src = "\\author{Ada Wren\\linebreakand Bo Hale}"
    assert simplify_layout(src) == "\\author{Ada Wren\\and Bo Hale}"
    assert prepare_for_pandoc(src)[0] == "\\author{Ada Wren\\and Bo Hale}"


# ------------------------------------------------------------------ guards
def test_text_without_these_constructs_is_unchanged():
    src = (
        "\\documentclass{article}\n\\newcommand{\\R}{\\mathbb{R}}\n"
        "\\begin{document}\nWe cite \\cite{a,b} and \\ref{x}. $x\\in\\R$\n\\end{document}\n"
    )
    assert prepare_for_pandoc(src) == (src, [])


N_UNCLOSED = 8_000


@pytest.mark.parametrize(
    "unit",
    [
        "\\cite[",
        "\\label[",
        "\\cite{",
        "\\newcolumntype{a}{",
        "\\href{",
        "\\def\\a#1{\\csname ",
        "\\global\\long ",
        "\\global",
        "\\edef",
    ],
)
def test_unclosed_constructs_are_rewritten_in_linear_time(unit):
    def seconds(n: int) -> float:
        text = unit * n
        best = float("inf")
        for _ in range(2):
            t0 = time.process_time()
            prepare_for_pandoc(text)
            best = min(best, time.process_time() - t0)
        return best

    small, big = seconds(N_UNCLOSED // 4), seconds(N_UNCLOSED)
    assert big < 5
    assert big < max(8 * small, 0.05), (small, big)
