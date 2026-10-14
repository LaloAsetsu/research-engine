"""Equation numbers written into the math (latex_source.number_equations).

String in, string out: no pandoc binary. Every equation and sentence is invented.
``tests/integration/test_pandoc_latex_equations.py`` runs the same shapes through the real
pandoc, all three conversion paths included.
"""

import re
import time

import pytest

from docingest.adapters.converters.latex_source import (
    flatten,
    number_equations,
    package_macros,
    prepare_for_pandoc,
)

TAG = re.compile(r"\\tag\{([^{}]*)\}")


def doc(body: str, *, cls: str = "article", pre: str = "") -> str:
    return f"\\documentclass{{{cls}}}\n{pre}\n\\begin{{document}}\n{body}\n\\end{{document}}\n"


def numbers(body: str, **kw) -> list[str]:
    """The numbers written, in order."""
    return list(number_equations(doc(body, **kw)).written)


def written_tags(text: str) -> list[str]:
    return TAG.findall(text)


def eq(label: str = "", body: str = "x = y") -> str:
    mark = f"\\label{{{label}}}" if label else ""
    return f"\\begin{{equation}}{mark}\n{body}\n\\end{{equation}}"


# ------------------------------------------------------------ the environments
def test_equations_are_numbered_in_order_and_the_tag_is_written_in_the_math():
    out = number_equations(doc(f"{eq()}\n\nText.\n\n{eq()}"))
    assert out.written == ("1", "2")
    assert out.stopped is None
    assert written_tags(out.text) == ["1", "2"]
    assert re.search(r"x = y\n\\tag\{1\}\n\\end\{equation\}", out.text)


def test_starred_environments_and_display_math_get_no_number():
    body = (
        "\\begin{equation*}a\\end{equation*}\n\\begin{align*}b\\end{align*}\n$$c$$\n\\[d\\]\n"
        "\\begin{displaymath}e\\end{displaymath}\n\\begin{gather*}f\\end{gather*}\n"
        "\\begin{eqnarray*}g\\end{eqnarray*}\n\\begin{multline*}h\\end{multline*}"
    )
    out = number_equations(doc(body))
    assert out.text == doc(body)
    assert out.written == ()


@pytest.mark.parametrize("mark", ["\\nonumber", "\\notag"])
def test_nonumber_in_an_equation_takes_no_number_and_does_not_count(mark):
    assert numbers(f"{eq()}\n\\begin{{equation}}y {mark}\\end{{equation}}\n{eq()}") == ["1", "2"]


def test_align_numbers_each_row_but_the_ones_marked():
    body = "\\begin{align}\na &= 1 \\\\\nb &= 2 \\nonumber \\\\\nc &= 3 \\notag \\\\\nd &= 4\n\\end{align}"
    out = number_equations(doc(body))
    assert out.written == ("1", "2")
    rows = out.text.split("\\\\")
    assert "\\tag{1}" in rows[0] and "\\tag{2}" in rows[3] and "\\tag" not in rows[1] + rows[2]


@pytest.mark.parametrize(
    "begin",
    ["align", "alignat}{2", "xalignat}{2", "flalign", "gather"],
)
def test_a_row_end_before_the_end_of_an_amsmath_environment_makes_an_empty_numbered_row(begin):
    name = begin.split("}")[0]
    out = number_equations(doc(f"\\begin{{{begin}}}a \\\\ b \\\\\n\\end{{{name}}}\n" + eq()))
    assert out.written == ("1", "2", "4")  # 3 is the empty row: LaTeX numbers it, nothing to tag


def test_the_empty_last_row_shifts_every_later_number_and_the_references_to_them():
    first = "\\begin{align}a &= 1 \\\\ b &= 2 \\\\\n\\end{align}\n"
    body = eq() + eq() + first + eq("after") + "See \\eqref{after}."
    out = number_equations(doc(body))
    assert out.written == ("1", "2", "3", "4", "6")
    assert "See (6)." in out.text


def test_the_numbers_of_the_empty_rows_are_reported_apart_from_the_written_ones():
    out = number_equations(doc(eq() + "\\begin{align}a \\\\ b \\\\\n\\end{align}\n" + eq()))
    assert out.written == ("1", "2", "3", "5") and out.empty == ("4",)


def test_an_empty_last_row_after_rows_that_have_no_number_still_takes_one():
    body = "\\begin{align}a \\nonumber \\\\ b \\nonumber \\\\\n\\end{align}\n" + eq()
    assert numbers(body) == ["2"]  # the first two rows have none, the empty row has 1


@pytest.mark.parametrize("tail", ["\\\\[2pt]", "\\\\*", "\\\\ %", "\\\\*[1ex] %"])
def test_a_last_row_with_only_the_options_of_the_row_end_is_empty(tail):
    out = number_equations(doc(f"\\begin{{align}}a \\\\ b {tail}\n\\end{{align}}\n" + eq()))
    assert out.written == ("1", "2", "4")
    assert out.text.count("\\tag") == 3


def test_eqnarray_counts_the_empty_row_after_a_final_row_end():
    out = number_equations(
        doc("\\begin{eqnarray}a &=& 1 \\\\ b &=& 2 \\\\\n\\end{eqnarray}\n" + eq())
    )
    assert out.written == ("1", "2", "4")  # 3 went to the empty row, which has nothing to tag


@pytest.mark.parametrize("name", ["gather", "flalign", "xalignat"])
def test_rowwise_environments(name):
    assert numbers(f"\\begin{{{name}}}a \\\\ b\n\\end{{{name}}}") == ["1", "2"]


def test_alignat_takes_its_argument_without_taking_it_for_a_row():
    assert numbers("\\begin{alignat}{2}a &= 1 & b &= 2 \\\\ c &= 3\n\\end{alignat}") == ["1", "2"]


def test_multline_has_one_number_for_all_its_lines():
    assert numbers("\\begin{multline}a \\\\ b \\\\ c\n\\end{multline}\n" + eq()) == ["1", "2"]


def test_row_ends_inside_braces_or_nested_environments_are_not_rows():
    body = (
        "\\begin{align}\n"
        "a &= \\begin{pmatrix} 1 \\\\ 2 \\end{pmatrix} \\\\\n"
        "b &= \\substack{p \\\\ q} \\\\\n"
        "c &= \\begin{cases} 1 & x \\\\ 0 & y \\end{cases}\n"
        "\\end{align}"
    )
    assert numbers(body) == ["1", "2", "3"]


def test_a_nested_aligned_in_an_equation_is_one_equation():
    body = "\\begin{equation}\\begin{aligned} a &= b \\\\ c &= d \\end{aligned}\\end{equation}"
    assert numbers(body) == ["1"]


def test_a_row_end_with_options_still_ends_the_row():
    assert numbers("\\begin{align}a \\\\[2pt] b \\\\* c\n\\end{align}") == ["1", "2", "3"]


# ------------------------------------------------------------------- \tag
def test_an_own_tag_takes_the_place_of_the_number_and_does_not_count():
    body = f"{eq()}\n\\begin{{equation}}y \\tag{{A}}\\end{{equation}}\n{eq()}"
    out = number_equations(doc(body))
    assert out.written == ("1", "2")
    assert written_tags(out.text) == ["1", "A", "2"]


def test_a_starred_tag_and_a_tag_in_a_row_of_an_align():
    body = "\\begin{align}a \\tag*{(*)} \\\\ b \\tag{7} \\\\ c\n\\end{align}"
    assert numbers(body) == ["1"]


# ---------------------------------------------------------- subequations
def test_subequations_number_the_group_and_letter_the_equations():
    body = (
        f"{eq()}\n\\begin{{subequations}}\n{eq()}\n"
        "\\begin{align}a \\\\ b\\end{align}\n\\end{subequations}\n" + eq()
    )
    assert numbers(body) == ["1", "2a", "2b", "2c", "3"]


def test_the_label_of_a_group_is_the_number_of_the_group():
    body = (
        "\\begin{subequations}\\label{grp}\n"
        f"{eq('first')}\n{eq('second')}\n\\end{{subequations}}\n"
        "See \\eqref{grp}, \\eqref{first} and \\ref{second}."
    )
    out = number_equations(doc(body))
    assert "See (1), (1a) and 1b." in out.text


def test_subequations_follow_the_section_prefix():
    pre = "\\numberwithin{equation}{section}"
    body = "\\section{A}\n" + eq() + "\\begin{subequations}" + eq() + eq() + "\\end{subequations}"
    assert numbers(body, pre=pre) == ["1.1", "1.2a", "1.2b"]


# --------------------------------------------------- counters and numbering
def test_numberwithin_section_restarts_with_each_section_and_shows_the_section():
    body = (
        eq() + "\\section{One}\n" + eq() + eq() + "\\section*{Free}\n" + eq()
        + "\\section{Two}\n" + eq() + "\\subsection{Sub}\n" + eq()
    )  # fmt: skip
    got = numbers(body, pre="\\numberwithin{equation}{section}")
    assert got == ["0.1", "1.1", "1.2", "1.3", "2.1", "2.2"]


def test_a_section_command_that_is_an_argument_is_not_a_heading():
    pre = (
        "\\numberwithin{equation}{section}\\usepackage{titlesec}\n"
        "\\titleformat{\\section}{\\bfseries}{\\thesection}{1em}{}\n"
        "\\titlespacing*{\\subsection}{0pt}{1ex}{1ex}\\let\\oldsection\\section"
    )
    body = "\\section{A}" + eq() + "\\section [Short] {B}\n" + eq() + "\\section*{C}" + eq()
    assert numbers(body, pre=pre) == ["1.1", "2.1", "2.2"]


def test_counterwithin_and_the_reset_without_the_prefix():
    body = "\\section{A}" + eq() + eq() + "\\section{B}" + eq()
    assert numbers(body, pre="\\counterwithin{equation}{section}") == ["1.1", "1.2", "2.1"]
    assert numbers(body, pre="\\counterwithin*{equation}{section}") == ["1", "2", "1"]
    assert numbers(body, pre="\\makeatletter\\@addtoreset{equation}{section}") == ["1", "2", "1"]


def test_numbering_within_subsections():
    body = "\\section{A}\\subsection{B}" + eq() + "\\subsection{C}" + eq()
    got = numbers(body, pre="\\numberwithin{equation}{subsection}")
    assert got == ["1.1.1", "1.2.1"]


def test_appendix_letters_the_sections_in_an_article():
    pre = "\\numberwithin{equation}{section}"
    body = "\\section{A}" + eq() + "\\appendix\n\\section{B}" + eq() + eq() + "\\section{C}" + eq()
    assert numbers(body, pre=pre) == ["1.1", "A.1", "A.2", "B.1"]


def test_an_article_appendix_without_numberwithin_goes_on_counting():
    assert numbers("\\section{A}" + eq() + "\\appendix\n\\section{B}" + eq()) == ["1", "2"]


def test_revtex_appendix_restarts_and_prefixes_the_letter():
    body = "\\section{A}" + eq() + "\\appendix\n\\section{B}" + eq() + eq() + "\\section{C}" + eq()
    assert numbers(body, cls="revtex4-2") == ["1", "A1", "A2", "B1"]


def test_the_appendix_environment_is_the_same_as_the_command():
    body = "\\section{A}" + eq() + "\\begin{appendices}\\section{B}" + eq() + "\\end{appendices}"
    assert numbers(body, pre="\\numberwithin{equation}{section}") == ["1.1", "A.1"]


def test_setcounter_addtocounter_and_stepcounter():
    body = (
        "\\setcounter{equation}{9}" + eq() + "\\addtocounter{equation}{5}" + eq()
        + "\\stepcounter{equation}" + eq() + "\\refstepcounter{equation}" + eq()
    )  # fmt: skip
    assert numbers(body) == ["10", "16", "18", "20"]


def test_a_supplement_that_restarts_with_a_prefix():
    body = (
        eq() + "\\setcounter{equation}{0}\\renewcommand{\\theequation}{S\\arabic{equation}}\n"
        + eq() + eq()
    )  # fmt: skip
    assert numbers(body) == ["1", "S1", "S2"]


@pytest.mark.parametrize(
    ("shown", "expected"),
    [
        ("\\thesection.\\arabic{equation}", ["2.1"]),
        ("\\Alph{section}\\arabic{equation}", ["B1"]),
        ("(\\arabic{equation})", ["(1)"]),
        ("E\\roman{equation}", ["Ei"]),
        ("\\alph{equation}", ["a"]),
        ("\\Roman{equation}.", ["I."]),
        ("S\\@arabic\\c@equation", ["S1"]),
    ],
)
def test_the_form_of_theequation_is_followed(shown, expected):
    body = "\\section{A}\\section{B}\\setcounter{equation}{0}" + eq()
    pre = f"\\renewcommand{{\\theequation}}{{{shown}}}"
    got = numbers(body, pre=pre)
    if expected == ["(1)"]:  # parentheses are not written into a \tag
        assert got == []
    else:
        assert got == expected


def test_a_redefinition_with_commands_that_are_not_counters_stops_the_numbering():
    pre = "\\renewcommand{\\theequation}{\\textbf{\\arabic{equation}}}"
    out = number_equations(doc(eq(), pre=pre))
    assert out.written == ()
    assert out.stopped


def test_the_revtex_option_that_numbers_by_section_is_followed():
    text = doc("\\section{A}" + eq() + eq() + "\\section{B}" + eq(), cls="revtex4-2")
    out = number_equations(text.replace("{revtex4-2}", "[eqsecnum]{revtex4-2}"))
    assert out.written == ("1.1", "1.2", "2.1")


def test_a_class_option_that_numbers_by_section_stops_the_numbering_in_other_classes():
    out = number_equations(
        doc(eq(), cls="somejournal").replace("{somejournal}", "[eqsecnum]{somejournal}")
    )
    assert out.written == ()
    assert "eqsecnum" in (out.stopped or "")


def test_revtex_goes_on_counting_until_the_first_section_of_the_appendix():
    body = eq() + "\\appendix\n\\section{B}" + eq() + "\\section{C}" + eq()
    assert numbers(body, cls="revtex4-1") == ["1", "A1", "B1"]
    starred = eq() + "\\appendix*\n\\section{B}" + eq() + eq()
    assert numbers(starred, cls="revtex4-2") == ["1", "A.1", "A.2"]


def test_revtex_equations_before_any_section_of_the_appendix_have_no_letter_to_show():
    # LaTeX reports an error for \\Alph{0} and prints nothing: the numbers go on without a prefix
    assert numbers(eq() + "\\appendix\n" + eq() + eq(), cls="revtex4-2") == ["1", "2", "3"]


def test_a_subequation_beyond_the_alphabet_stops_the_numbering():
    rows = "\\\\".join("a" for _ in range(27))
    out = number_equations(
        doc(f"\\begin{{subequations}}\\begin{{align}}{rows}\\end{{align}}\\end{{subequations}}")
    )
    assert out.written[-1] == "1z" and out.stopped


def test_the_appendix_package_does_not_change_what_revtex_does_at_the_appendix():
    body = eq() + "\\appendix\n\\section{B}" + eq() + "\\section{C}" + eq()
    assert numbers(body, cls="revtex4-2", pre="\\usepackage[toc]{appendix}") == ["1", "A1", "B1"]


def test_the_prl_option_of_revtex_stops_the_numbering_at_the_appendix():
    body = eq() + "\\appendix\n\\section{B}" + eq()
    text = doc(body, cls="revtex4-2").replace("{revtex4-2}", "[aps,prl,reprint]{revtex4-2}")
    out = number_equations(text)
    assert out.written == ("1",) and out.stopped
    other = doc(body, cls="revtex4-2").replace("{revtex4-2}", "[aps,prx,reprint]{revtex4-2}")
    assert number_equations(other).written == ("1", "A1")


def test_a_revtex_paper_with_its_own_theequation_and_an_appendix_stops_the_numbering():
    own = "\\renewcommand{\\theequation}{S\\arabic{equation}}"
    before = number_equations(
        doc(eq() + "\\appendix\n\\section{B}" + eq(), cls="revtex4-2", pre=own)
    )
    after = number_equations(
        doc(eq() + "\\appendix\n" + own + "\\section{B}" + eq(), cls="revtex4-2")
    )
    assert before.written == ("S1",) and before.stopped
    assert after.written == ("1",) and after.stopped


def test_a_counter_set_to_a_value_that_is_not_a_number_stops_the_numbering():
    out = number_equations(doc(eq() + "\\setcounter{equation}{\\value{page}}" + eq()))
    assert out.written == ("1",)
    assert out.stopped


def test_chapters_number_the_equations_of_a_book():
    body = "\\chapter{A}" + eq() + eq() + "\\chapter{B}" + eq()
    assert numbers(body, cls="book") == ["1.1", "1.2", "2.1"]
    assert numbers("\\chapter{A}\\appendix\\chapter{B}" + eq(), cls="report") == ["A.1"]


def test_a_chapter_style_command_in_an_article_is_not_a_chapter():
    pre = "\\usepackage{titlesec}\\titleformat{\\chapter}{\\bfseries}{}{0pt}{}"
    assert numbers(eq(), pre=pre) == ["1"]


def test_a_book_without_chapters_or_chapters_in_an_unknown_class_stop():
    assert number_equations(doc(eq(), cls="book")).stopped
    assert number_equations(doc("\\chapter{A}" + eq(), cls="thesisx")).written == ()


def test_an_appendix_in_a_class_that_is_not_modelled_stops_the_numbering():
    out = number_equations(doc(eq() + "\\appendix\n\\section{B}" + eq(), cls="somejournal"))
    assert out.written == ("1",)
    assert out.stopped


def test_a_starred_appendix_is_only_modelled_for_revtex():
    body = eq() + "\\appendix*\\section{B}" + eq()
    assert number_equations(doc(body)).stopped
    assert numbers(body, cls="revtex4-2") == ["1", "A.1"]


def test_a_theorem_counted_with_the_equations_takes_a_number():
    pre = "\\newtheorem{thm}[equation]{Theorem}\n\\newtheorem{lem}[thm]{Lemma}"
    body = eq() + "\\begin{thm}t\\end{thm}" + eq() + "\\begin{lem}l\\end{lem}" + eq()
    assert numbers(body, pre=pre) == ["1", "3", "5"]
    assert numbers(eq() + "\\begin{thm}t\\end{thm}" + eq(), pre="\\newtheorem{thm}{Theorem}") == [
        "1",
        "2",
    ]


def test_a_macro_that_restarts_the_numbers_is_followed_where_it_is_used():
    pre = (
        "\\newcommand{\\supplement}{\\setcounter{equation}{0}\n"
        "\\renewcommand{\\theequation}{S\\arabic{equation}}}"
    )
    body = eq("main") + eq() + "\\supplement\n" + eq("sup") + eq() + "\\eqref{main} \\eqref{sup}"
    out = number_equations(doc(body, pre=pre))
    assert out.written == ("1", "2", "S1", "S2")
    assert "(1) (S1)" in out.text


def test_a_macro_with_an_argument_that_starts_a_section_steps_the_section_counter():
    pre = "\\newcommand{\\sect}[1]{\\section{#1}\\label{s:#1}}\\numberwithin{equation}{section}"
    out = number_equations(doc("\\sect{A}" + eq() + "\\sect{B}" + eq(), pre=pre))
    assert out.written == ("1.1", "2.1")


def test_a_macro_that_sets_a_counter_and_begins_an_equation_stops_the_numbering():
    pre = "\\newcommand{\\fresh}{\\setcounter{equation}{0}\\begin{equation}}"
    out = number_equations(doc(eq() + "\\fresh x \\end{equation}" + eq(), pre=pre))
    assert out.written == ("1",) and out.stopped


def test_macros_that_use_each_other_are_followed_to_a_limit():
    pre = "\\newcommand{\\a}{\\setcounter{equation}{5}}\\newcommand{\\b}{\\a}"
    assert numbers("\\b" + eq(), pre=pre) == ["6"]
    loop = "\\newcommand{\\a}{\\a\\setcounter{equation}{5}}"
    assert number_equations(doc("\\a" + eq(), pre=loop)).stopped


@pytest.mark.parametrize(
    "pre",
    ["\\usepackage{mathtools}\\mathtoolsset{showonlyrefs=true}", "\\usepackage{amsmath,autonum}",
     "\\usepackage{cases}\\usepackage{numcases}"],
)  # fmt: skip
def test_packages_that_number_only_some_equations_stop_the_numbering(pre):
    assert number_equations(doc(eq(), pre=pre)).written == ()


def test_showonlyrefs_set_to_false_is_harmless():
    assert numbers(eq(), pre="\\mathtoolsset{showonlyrefs=false}") == ["1"]


@pytest.mark.parametrize(
    "cls", ["quantumarticle", "iopjournal", "ieeeaccess", "wlscirep", "amsart"]
)
def test_classes_whose_appendix_goes_on_counting(cls):
    assert numbers(eq() + "\\appendix\n\\section{B}" + eq(), cls=cls) == ["1", "2"]


def test_an_empty_equation_or_align_still_takes_its_number():
    assert numbers("\\begin{equation}\\end{equation}" + eq()) == ["2"]
    assert numbers("\\begin{align}\\end{align}" + eq()) == ["2"]


def test_a_label_after_the_first_equation_of_a_group_is_not_the_label_of_the_group():
    body = (
        "\\begin{subequations}"
        + eq("a")
        + "text \\label{stray}\\end{subequations}\\ref{stray} \\ref{a}"
    )
    out = number_equations(doc(body))
    assert "\\ref{stray} 1a" in out.text


def test_a_new_section_restarts_the_subsection_and_with_it_the_equations():
    body = (
        "\\section{A}\\subsection{a}" + eq() + eq() + "\\section{B}\\subsection{b}" + eq()
        + "\\subsection{c}" + eq()
    )  # fmt: skip
    got = numbers(body, pre="\\numberwithin{equation}{subsection}")
    assert got == ["1.1.1", "1.1.2", "2.1.1", "2.2.1"]


def test_stepcounter_of_a_section_restarts_the_equations():
    body = "\\stepcounter{section}" + eq() + "\\refstepcounter{section}" + eq()
    assert numbers(body, pre="\\numberwithin{equation}{section}") == ["1.1", "2.1"]


def test_a_macro_with_an_argument_or_with_text_around_a_nonumber_is_not_that_nonumber():
    for pre in (
        "\\newcommand{\\skip}[1]{\\nonumber}",
        "\\newcommand{\\skip}{\\nonumber \\text{x}}",
    ):
        out = number_equations(doc("\\begin{align}a \\skip \\\\ b\\end{align}", pre=pre))
        assert out.written == () and out.stopped, pre
    alias = "\\newcommand{\\skip}{\\nonumber}"
    assert numbers("\\begin{align}a \\skip \\\\ b\\end{align}", pre=alias) == ["1"]


def test_a_macro_that_sets_a_counter_and_has_a_row_end_stops_the_numbering_anywhere():
    pre = "\\newcommand{\\both}{\\setcounter{equation}{0}\\\\}"
    assert number_equations(doc("\\both" + eq(), pre=pre)).stopped


@pytest.mark.parametrize("pre", ["\\newcommand{\\seven}{\\tag{7}}", "\\newcommand{\\seven}{\\tag}"])
def test_a_macro_that_holds_a_tag_stops_the_numbering_inside_an_equation(pre):
    out = number_equations(doc("\\begin{equation}x \\seven{7}\\end{equation}" + eq(), pre=pre))
    assert out.written == () and out.stopped


def test_an_equation_after_a_section_without_a_subsection_restarts_with_the_subsection_at_zero():
    body = "\\section{A}\\subsection{a}" + eq() + "\\section{B}" + eq()
    assert numbers(body, pre="\\numberwithin{equation}{subsection}") == ["1.1.1", "2.0.1"]


def test_braces_that_close_before_they_open_stop_the_numbering():
    assert number_equations(doc("\\begin{equation}a} {b\\end{equation}")).stopped


def test_a_text_that_ends_inside_an_equation_stops_the_numbering():
    out = number_equations("\\begin{equation}x")
    assert out.stopped and out.written == ()


def test_a_group_label_after_the_numbering_stopped_is_not_resolved():
    body = (
        eq()
        + "\\begin{dmath}x\\end{dmath}\\begin{subequations}\\label{g}"
        + eq()
        + "\\end{subequations}\\ref{g}"
    )
    out = number_equations(doc(body))
    assert out.stopped and out.resolved == 0


def test_a_counter_set_to_a_word_stops_the_numbering():
    out = number_equations(doc(eq() + "\\setcounter{equation}{five}" + eq()))
    assert out.written == ("1",) and out.stopped


# ------------------------------------------------------------- conditionals
@pytest.mark.parametrize(
    "held",
    [
        "\\ifdraft\\section{A}\\fi",
        "\\iftoggle{long}{\\setcounter{equation}{0}}{}",
        "\\ifthenelse{\\boolean{x}}{}{\\stepcounter{equation}}",
        "\\IfFileExists{a.tex}{\\appendix}{}",
        "\\ifx\\a\\b \\else \\ifnum1=1 \\section{A}\\fi \\fi",
        "\\@ifclassloaded{revtex4}{\\section{A}}{}",
        "\\ifdraft" + eq() + "\\fi",
        "\\ifdraft\\begin{subequations}\\end{subequations}\\fi",
    ],
)
def test_a_counter_or_an_equation_inside_a_conditional_stops_the_numbering(held):
    out = number_equations(doc(eq() + held + eq()))
    assert out.stopped and "conditional" in out.stopped
    assert out.written in (("1",), ("1", "2"))  # what came first keeps its number


def test_a_conditional_around_text_or_around_a_label_is_harmless():
    body = (
        "\\ifdraft text \\label{x}\\fi " + eq() + "\\ifmmode a\\fi $a \\iff b$ "
        "\\ifthenelse{\\boolean{x}}{\\textbf{a}}{b} \\IfFileExists{a}{b}{c} \\@ifnextchar[{a}{b} "
        + eq()
    )
    assert numbers(body) == ["1", "2"]


def test_a_name_the_paper_defines_is_not_a_conditional():
    pre = (
        "\\newcommand{\\ifo}{\\mathrm{ifo}}\\def\\ifft{\\mathcal{F}}"
        "\\DeclareMathOperator{\\ifrac}{ifrac}"
    )
    body = eq() + "$\\hat{H}_{\\ifo}$ and $\\ifft \\ifrac$" + eq() + "\\section{A}" + eq()
    assert numbers(body, pre=pre) == ["1", "2", "3"]


def test_an_unknown_conditional_followed_by_braces_takes_its_branches_as_arguments():
    body = (
        eq() + "\\ifdraft{\\textbf{draft}}{final} \\ifentrytype{article}{a}{b} " + eq()
        + "\\ifdraft{\\section{A}}{}" + eq()
    )  # fmt: skip
    out = number_equations(doc(body))
    assert out.written == ("1", "2") and out.stopped


def test_a_conditional_that_newif_declares_is_closed_by_its_fi_even_with_braces_after_it():
    pre = "\\newif\\ifsupplement"
    body = eq() + "\\ifsupplement{x}\\section{A}\\fi" + eq()
    out = number_equations(doc(body, pre=pre))
    assert out.written == ("1",) and out.stopped
    assert numbers(eq() + "\\ifsupplement{x}\\fi" + eq(), pre=pre) == ["1", "2"]


def test_a_tex_conditional_with_a_brace_group_after_it_still_runs_to_its_fi():
    body = eq() + "\\ifmmode{x}\\section{A}\\fi" + eq()
    out = number_equations(doc(body))
    assert out.written == ("1",) and out.stopped


def test_a_starred_section_or_a_counter_the_numbering_does_not_use_does_not_stop_it():
    body = (
        eq() + "\\ifdraft\\section*{Free}\\fi" + "\\@ifclassloaded{revtex4-2}{\\setcounter{topnumber}{4}}{}"
        + "\\ifdraft\\numberwithin{figure}{section}\\stepcounter{figure}\\fi" + eq()
    )  # fmt: skip
    assert numbers(body) == ["1", "2"]
    assert number_equations(doc(eq() + "\\ifdraft\\setcounter{equation}{5}\\fi" + eq())).stopped
    assert number_equations(doc(eq() + "\\ifdraft\\stepcounter{section}\\fi" + eq())).stopped


@pytest.mark.parametrize(
    "held",
    [
        "\\ifshort\\nonumber\\fi",
        "\\ifarxiv\\tag{S1}\\fi",
        "\\ifwide\\\\\\fi",
        "\\ifx\\a\\b\\notag\\fi",
    ],
)
def test_a_row_end_a_nonumber_or_a_tag_inside_a_conditional_in_an_equation_stops_the_numbering(
    held,
):
    body = eq() + f"\\begin{{align}}a {held} b \\\\ c\\end{{align}}" + eq()
    out = number_equations(doc(body))
    assert out.written == ("1",) and out.stopped and "conditional" in out.stopped


@pytest.mark.parametrize(
    ("pre", "held"),
    [
        ("\\newcommand{\\nn}{\\nonumber}", "\\ifshort\\nn\\fi"),
        ("\\def\\nn{\\notag}", "\\ifx\\a\\b\\nn\\fi"),
        ("\\newcommand{\\nl}{\\\\}", "\\ifwide\\nl\\fi"),
        ("\\newcommand{\\tg}{\\tag}", "\\ifarxiv\\tg{S1}\\fi"),
    ],
)
def test_a_macro_of_the_paper_for_a_nonumber_or_a_row_end_inside_a_conditional_stops_the_numbering(
    pre, held
):
    body = eq() + f"\\begin{{align}}a {held} b \\\\ c\\end{{align}}" + eq()
    out = number_equations(doc(body, pre=pre))
    assert out.written == ("1",) and out.stopped and "conditional" in out.stopped
    assert number_equations(doc(eq(), pre=pre)).stopped is None  # defined and not used: fine


def test_a_conditional_a_package_declares_runs_to_its_else_or_fi_even_with_a_brace_group():
    for held in ("\\ifpdf{x}\\else\\section{A}\\fi", "\\ifpdf{x}{y}\\else\\appendix\\fi"):
        out = number_equations(doc(eq() + held + eq()))
        assert out.written == ("1",) and out.stopped, held
    after = number_equations(doc(eq() + "\\ifpdf{x}\\fi \\section{A}" + eq()))
    assert after.stopped is None and after.written == ("1", "2")
    assert numbers(eq() + "\\ifdraft{x}{y} \\ifdraft{\\textbf{a}}{b}" + eq()) == ["1", "2"]


def test_a_conditional_in_an_equation_that_holds_none_of_them_is_harmless():
    body = "\\begin{align}a \\ifmmode x\\fi \\\\ b \\ifx\\a\\b c\\fi\\end{align}" + eq()
    assert numbers(body) == ["1", "2", "3"]


def test_a_conditional_that_a_macro_definition_holds_or_that_is_false_is_not_read():
    pre = "\\newif\\ifdraft\\newcommand{\\a}{\\ifdraft\\section{A}\\fi}"
    body = "\\iffalse \\section{B}\\fi " + eq() + "\\section{C}" + eq()
    assert numbers(body, pre="\\numberwithin{equation}{section}" + pre) == ["0.1", "1.1"]


def test_a_conditional_that_never_closes_stops_the_numbering_after_it():
    out = number_equations(doc(eq() + "\\ifdraft " + eq()))
    assert out.stopped and out.written == ("1",)


def test_the_main_document_branch_of_a_subfiles_conditional_is_the_second():
    body = "\\ifSubfilesClassLoaded{\\section{Alone}}{}" + eq() + "\\section{B}" + eq()
    out = number_equations(doc(body, pre="\\numberwithin{equation}{section}"))
    assert out.stopped is None and out.written == ("0.1", "1.1")
    other = "\\ifSubfilesClassLoaded{}{\\section{Main}}" + eq()
    again = number_equations(doc(other, pre="\\numberwithin{equation}{section}"))
    assert again.stopped is None and again.written == ("1.1",)


def test_a_counter_redefined_inside_a_conditional_stops_the_numbering():
    body = eq() + "\\ifdraft\\renewcommand{\\theequation}{S\\arabic{equation}}\\fi" + eq()
    out = number_equations(doc(body))
    assert out.stopped and out.written == ("1",)


# ---------------------------------------------------------------- references
def test_eqref_and_ref_become_the_number_for_equation_labels():
    body = (
        f"{eq('a')}\n{eq('b')}\nBy Eq.~\\eqref{{a}} and Eq.~\\ref{{b}}, then \\eqref{{b}}."
        "\nBefore: \\eqref{later}.\n" + eq("later")
    )
    out = number_equations(doc(body))
    assert "By Eq.~(1) and Eq.~2, then (2)." in out.text
    assert "Before: (3)." in out.text
    assert (out.refs, out.resolved) == (4, 4)


def test_references_to_anything_else_are_left_alone():
    body = (
        "\\section{S}\\label{sec:s}\n\\begin{figure}\\caption{c}\\label{fig:f}\\end{figure}\n"
        + eq("e") + "See \\ref{sec:s}, \\ref{fig:f}, \\eqref{missing}, \\cref{e}, \\autoref{e}, \\ref*{e}."
    )  # fmt: skip
    out = number_equations(doc(body))
    tail = out.text.split("See ")[1]
    assert tail.startswith(
        "\\ref{sec:s}, \\ref{fig:f}, \\eqref{missing}, \\cref{e}, \\autoref{e}, \\ref*{e}."
    )
    assert (out.refs, out.resolved) == (3, 0)


def test_labels_of_unnumbered_equations_are_not_resolved():
    body = (
        "\\begin{equation*}\\label{star}x\\end{equation*}\n"
        "\\begin{align}a \\label{gone}\\nonumber\\\\ b \\label{kept}\\end{align}\n"
        "\\eqref{star} \\eqref{gone} \\eqref{kept}"
    )
    out = number_equations(doc(body))
    assert "\\eqref{star} \\eqref{gone} (1)\n" in out.text


def test_a_label_in_a_row_with_an_own_tag_refers_to_the_tag():
    body = "\\begin{equation}x \\tag{A.1}\\label{t}\\end{equation}\n\\eqref{t}"
    assert "(A.1)" in number_equations(doc(body)).text
    complex_tag = "\\begin{equation}x \\tag{\\ast\\ast}\\label{t}\\end{equation}\n\\eqref{t}"
    assert "\\eqref{t}" in number_equations(doc(complex_tag)).text


def test_a_label_used_twice_is_not_resolved():
    body = (
        eq("dup")
        + eq("dup")
        + "\\eqref{dup}"
        + eq("one")
        + "\\begin{lemma}\\label{one}\\end{lemma}\\ref{one}"
    )
    out = number_equations(doc(body))
    assert out.resolved == 0


def test_references_are_resolved_to_the_number_the_label_has_even_when_numbering_stops_later():
    body = eq("early") + "\\begin{dmath}z\\end{dmath}" + eq("late") + "\\eqref{early} \\eqref{late}"
    out = number_equations(doc(body))
    assert "(1) \\eqref{late}" in out.text
    assert out.stopped and out.written == ("1",)


def test_a_paper_that_defines_its_own_eqref_keeps_it():
    pre = "\\renewcommand{\\eqref}[1]{Eq.~(\\ref{#1})}"
    out = number_equations(doc(eq("a") + "\\eqref{a} and \\ref{a}", pre=pre))
    assert "\\eqref{a} and 1" in out.text


def test_references_inside_macro_definitions_are_not_rewritten():
    pre = "\\newcommand{\\see}[1]{Eq.~\\eqref{#1}}\\newcommand{\\first}{Eq.~\\eqref{a}}"
    out = number_equations(doc(eq("a") + "\\see{a}", pre=pre))
    assert "{Eq.~\\eqref{#1}}" in out.text and "{Eq.~\\eqref{a}}" in out.text


# --------------------------------------------------- macros of the paper
def test_macros_that_stand_for_begin_and_end_are_followed():
    pre = (
        "\\newcommand{\\be}{\\begin{equation}}\n\\newcommand{\\ee}{\\end{equation}}\n"
        "\\def\\bea{\\begin{eqnarray}}\n\\def\\eea{\\end{eqnarray}}\n"
        "\\newcommand{\\nn}{\\nonumber}\n\\newcommand{\\nl}{\\nonumber\\\\}"
    )
    body = (
        "\\be\\label{a} x\\ee\n\\bea p &=& q \\nn\\\\ r &=& s \\label{b}\\eea\n"
        "\\bea u &=& v \\nl w &=& z\\eea\n\\ref{a} \\ref{b}"
    )
    out = number_equations(doc(body, pre=pre))
    assert out.written == ("1", "2", "3")
    assert "1 2" in out.text.split("\\end{document}")[0]
    assert out.stopped is None


def test_let_aliases_and_environment_aliases_are_followed():
    pre = (
        "\\let\\be\\equation\n\\let\\ee\\endequation\n"
        "\\newenvironment{myeq}{\\begin{equation}}{\\end{equation}}"
    )
    assert numbers("\\be x\\ee\n\\begin{myeq}y\\end{myeq}\n" + eq(), pre=pre) == ["1", "2", "3"]


def test_a_let_that_saves_a_command_is_not_a_use_of_it():
    pre = "\\let\\oldappendix\\appendix\n\\let\\oldsection=\\section\n\\numberwithin{equation}{section}"
    assert numbers("\\section{A}" + eq() + "\\section{B}" + eq(), pre=pre) == ["1.1", "2.1"]
    assert numbers(eq(), pre=pre, cls="somejournal") == ["0.1"]


def test_a_macro_that_hides_an_environment_stops_the_numbering_where_it_is_used():
    pre = "\\newcommand{\\eqn}[1]{\\begin{equation}#1\\end{equation}}"
    out = number_equations(doc(eq("a") + "\\eqn{y}" + eq("b"), pre=pre))
    assert out.written == ("1",)
    assert "\\eqn" in (out.stopped or "")
    assert number_equations(doc(eq("a"), pre=pre)).stopped is None  # defined, never used


def test_an_environment_that_hides_a_numbered_one_stops_the_numbering():
    pre = "\\newenvironment{tworows}{\\begin{align}a \\\\}{\\end{align}}"
    out = number_equations(doc(eq() + "\\begin{tworows}b\\end{tworows}" + eq(), pre=pre))
    assert out.written == ("1",) and out.stopped


def test_a_new_definition_of_a_numbered_environment_stops_the_numbering():
    pre = "\\renewenvironment{equation}{\\begin{center}}{\\end{center}}"
    assert number_equations(doc(eq(), pre=pre)).stopped


def test_a_macro_with_a_row_end_stops_the_numbering_only_inside_an_equation():
    pre = "\\newcommand{\\both}[2]{#1 \\\\ #2}"
    outside = number_equations(doc("\\both{a}{b}" + eq(), pre=pre))
    assert outside.written == ("1",) and outside.stopped is None
    inside = number_equations(doc("\\begin{align}\\both{a}{b}\\end{align}", pre=pre))
    assert inside.written == () and inside.stopped


@pytest.mark.parametrize("name", ["dmath", "dgroup", "dseries", "IEEEeqnarray", "empheq"])
def test_environments_of_packages_that_are_not_modelled_stop_the_numbering(name):
    out = number_equations(doc(eq() + f"\\begin{{{name}}}x\\end{{{name}}}" + eq()))
    assert out.written == ("1",)
    assert name in (out.stopped or "")


def test_a_local_style_that_numbers_within_sections_is_followed(tmp_path):
    (tmp_path / "numbered.sty").write_text(
        "\\ProvidesPackage{numbered}\n\\numberwithin{equation}{section}\n"
        "\\numberwithin{figure}{section}\n\\newcommand{\\half}{\\frac12}\n"
    )
    main = tmp_path / "main.tex"
    main.write_text(
        doc("\\section{A}" + eq() + "\\section{B}" + eq(), pre="\\usepackage{numbered}")
    )
    flat = flatten(main, tmp_path, max_chars=10**6)
    assert number_equations(flat.text).written == ("1.1", "2.1")
    assert "\\numberwithin{equation}{section}" in package_macros(
        (tmp_path / "numbered.sty").read_text()
    )


# ------------------------------------------------------ what is not read
def test_verbatim_text_comment_blocks_and_iffalse_are_not_read():
    body = (
        "\\begin{verbatim}\n\\begin{equation}x\\end{equation} \\eqref{a}\n\\end{verbatim}\n"
        "\\verb|\\ref{a}|\n"
        "\\begin{lstlisting}\n\\begin{align}y\\end{align}\n\\end{lstlisting}\n"
        "\\iffalse \\begin{equation}z\\end{equation} \\fi\n"
        "\\begin{comment}\\begin{equation}w\\end{equation}\\end{comment}\n" + eq("a") + "\\ref{a}"
    )
    out = number_equations(doc(body))
    assert out.written == ("1",)
    for kept in (
        "\\eqref{a}\n\\end{verbatim}",
        "\\verb|\\ref{a}|",
        "}y\\end",
        "}z\\end",
        "}w\\end",
    ):
        assert kept in out.text
    assert out.text.count("\\tag") == 1


def test_a_document_without_numbered_equations_is_returned_as_it_is():
    text = doc("Plain text with \\ref{x} and $$a$$ and \\begin{align*}b\\end{align*}.")
    assert number_equations(text).text == text
    only_refs = doc("\\ref{x} \\eqref{y} \\label{x}")
    assert number_equations(only_refs).text == only_refs


def test_a_circuit_in_an_equation_is_kept_and_its_row_ends_are_not_rows():
    circuit = "\\begin{quantikz}\\gate{H} & \\qw \\\\ \\gate{X} & \\qw\\end{quantikz}"
    qcircuit = "\\Qcircuit @C=1em { & \\gate{H} \\\\ & \\qw }"
    body = f"\\begin{{equation}}\n{circuit}\n\\end{{equation}}\n\\begin{{align}}{qcircuit} \\\\ y\\end{{align}}"
    out = number_equations(doc(body))
    assert out.written == ("1", "2", "3")  # the equation, then the two rows of the align
    assert circuit in out.text and qcircuit in out.text


def test_a_circuit_listing_of_the_pipeline_keeps_its_text_whatever_it_holds():
    listing = "\\begin{quantikz}\\gate{H}\\label{a}\\\\ \\eqref{a} \\begin{equation}\\end{quantikz}"
    text = doc(f"Figure.\n\n{listing}\n\nThen {eq('b')} \\eqref{{b}}.")
    out, _ = prepare_for_pandoc(text)
    assert "\\begin{minted}{latex}\n" + listing + "\n\\end{minted}" in out
    assert "\\eqref{a}" in out
    assert "Then" in out and "\\tag{1}" in out
    assert out.count("\\tag") == 1


# ------------------------------------------------------- budget of macro uses
def test_macros_that_use_each_other_many_times_stop_the_numbering_instead_of_growing():
    fan = 160  # 160 ** 3 uses of the innermost macro, if every use were read
    pre = "\\newcommand{\\a}{\\stepcounter{section}}"
    for name, inner in zip("bcd", "abc", strict=True):
        pre += f"\\newcommand{{\\{name}}}{{{('\\' + inner) * fan}}}"
    t0 = time.process_time()
    out = number_equations(doc("\\d" + eq(), pre=pre))
    assert time.process_time() - t0 < 1
    assert out.stopped and "expand" in out.stopped


def test_a_chain_of_macros_longer_than_the_rounds_that_look_for_it_stops_the_numbering():
    def name(i: int) -> str:
        return "m" + chr(97 + i // 26) + chr(97 + i % 26)

    pre = f"\\newcommand{{\\{name(0)}}}{{\\stepcounter{{section}}}}"
    pre += "".join(f"\\newcommand{{\\{name(i)}}}{{\\{name(i - 1)}}}" for i in range(1, 60))
    assert number_equations(doc(f"\\{name(59)}" + eq(), pre=pre)).stopped


def test_an_effect_macro_inside_a_conditional_stops_the_numbering():
    pre = "\\newcommand{\\fresh}{\\setcounter{equation}{0}}"
    out = number_equations(doc(eq() + "\\ifdraft\\fresh\\fi" + eq(), pre=pre))
    assert out.stopped and out.written == ("1",)
    be = "\\newcommand{\\be}{\\begin{equation}}\\newcommand{\\ee}{\\end{equation}}"
    out = number_equations(doc(eq() + "\\ifdraft\\be x \\ee\\fi" + eq(), pre=be))
    assert out.stopped and out.written == ("1",)


def test_macros_nested_too_deeply_stop_the_numbering_at_once():
    pre = "\\newcommand{\\a}{\\stepcounter{section}}"
    for name, inner in zip("bcdef", "abcde", strict=True):
        pre += f"\\newcommand{{\\{name}}}{{{('\\' + inner) * 3}}}"
    assert number_equations(doc("\\f" + eq(), pre=pre)).stopped


# ------------------------------------------------------------------- books
def test_the_front_matter_of_a_book_has_no_chapter_numbers():
    body = (
        "\\frontmatter\\chapter{Preface}" + eq() + "\\mainmatter\\chapter{Intro}" + eq()
        + "\\chapter{More}" + eq() + "\\backmatter\\chapter{Index}" + eq()
    )  # fmt: skip
    assert numbers(body, cls="book") == ["1", "1.1", "2.1", "2.2"]


def test_a_section_in_the_front_matter_of_a_book_stops_the_numbering():
    out = number_equations(doc("\\chapter{A}\\frontmatter\\section{B}" + eq(), cls="book"))
    assert out.stopped and out.written == ()


def test_equations_before_the_first_chapter_of_a_book_have_no_chapter_prefix():
    body = eq() + "\\chapter{A}" + eq() + eq()
    assert numbers(body, cls="book") == ["1", "1.1", "1.2"]
    assert numbers(body, cls="report") == ["1", "1.1", "1.2"]


def test_numberwithin_chapter_keeps_the_chapter_even_when_it_is_zero():
    got = numbers(eq() + "\\chapter{A}" + eq(), cls="book", pre="\\numberwithin{equation}{chapter}")
    assert got == ["0.1", "1.1"]


def test_a_book_section_number_is_the_chapter_and_the_section():
    pre = "\\renewcommand{\\theequation}{\\thesection-\\arabic{equation}}\\setcounter{equation}{0}"
    body = "\\chapter{A}\\section{a}" + eq() + "\\chapter{B}\\section{b}\\section{c}" + eq()
    out = number_equations(doc(body, cls="book", pre=pre))
    assert out.written == ("1.1-1", "2.2-1")


# ------------------------------------------------------ sections in Roman
def test_revtex_and_ieeetran_number_their_sections_in_roman():
    pre = "\\numberwithin{equation}{section}"
    body = "\\section{A}" + eq() + "\\section{B}" + eq() + "\\section{C}" + eq()
    assert numbers(body, cls="revtex4-2", pre=pre) == ["I.1", "II.1", "III.1"]
    assert numbers(body, cls="IEEEtran", pre=pre) == ["I.1", "II.1", "III.1"]
    assert numbers(body, pre=pre) == ["1.1", "2.1", "3.1"]


def test_revtex_with_eqsecnum_numbers_the_sections_in_arabic_as_the_class_does():
    text = doc("\\section{A}" + eq() + "\\section{B}" + eq(), cls="revtex4-2")
    out = number_equations(text.replace("{revtex4-2}", "[eqsecnum]{revtex4-2}"))
    assert out.written == ("1.1", "2.1")


def test_numbering_within_subsections_stops_where_the_class_letters_them():
    body = "\\section{A}\\subsection{a}" + eq()
    pre = "\\numberwithin{equation}{subsection}"
    assert number_equations(doc(body, cls="revtex4-2", pre=pre)).stopped
    assert number_equations(
        doc(body, cls="ieeeaccess", pre="\\numberwithin{equation}{section}")
    ).stopped


# ----------------------------------------------------------------- malformed
def test_an_environment_that_never_closes_stops_the_numbering():
    out = number_equations(doc(eq() + "\\begin{equation}x\n" + "text"))
    assert out.written == ("1",)
    assert out.stopped


def test_mismatched_and_nested_environments_stop_the_numbering():
    assert number_equations(doc("\\begin{align}a\\end{gather}" + eq())).stopped
    assert number_equations(
        doc("\\begin{equation}\\begin{align}a\\end{align}\\end{equation}")
    ).stopped
    assert number_equations(doc("\\begin{align}\\begin{aligned}a\\end{align}")).stopped
    assert number_equations(doc("\\begin{equation}{a\\end{equation}")).stopped
    assert number_equations(doc("\\begin{equation}a}\\end{equation}")).stopped


def test_nested_and_unclosed_subequations_do_not_break_anything():
    body = "\\begin{subequations}\\begin{subequations}" + eq() + "\\end{subequations}"
    assert number_equations(doc(body)).stopped
    assert numbers("\\begin{subequations}" + eq()) == ["1a"]


# --------------------------------------------------------------- the pipeline
def test_prepare_for_pandoc_numbers_the_equations_after_the_other_rewrites():
    text = doc("\\label[eq]{p}" + eq("a") + "\\eqref{a}\\cite{x,,y}")
    out, _ = prepare_for_pandoc(text)
    assert "\\tag{1}" in out
    assert "(1)\\cite{x,y}" in out


# --------------------------------------------------------------- linear time
def _scaled(make, small: int = 1500) -> tuple[float, float]:
    """CPU seconds, the best of three, of a text and of one four times longer."""

    def timed(n: int) -> float:
        text = make(n)
        t0 = time.process_time()
        number_equations(text)
        return time.process_time() - t0

    timed(50)
    return min(timed(small) for _ in range(3)), min(timed(small * 4) for _ in range(3))


@pytest.mark.parametrize(
    "make",
    [
        lambda n: doc(("\\begin{align}a \\\\ b \\label{x}\\end{align} \\eqref{x}\n") * n),
        lambda n: doc(("\\begin{equation}\n" + "{" * 3 + "a\n") * n),
        lambda n: doc("\\begin{align}" + "a & b \\\\ " * (20 * n) + "\\end{align}"),
        lambda n: doc("\\begin{equation}" * n),
        lambda n: doc("\\begin{subequations}" * n + "\\ref{x}" * n),
        lambda n: doc("\\section{A}\\setcounter{equation}{3}\\stepcounter{equation}" * n + eq()),
        lambda n: doc("\\newcommand{\\be}{\\begin{equation}}" * n + "\\be x \\end{equation}" * n),
        lambda n: doc("\\begin{align}" * n + "\\label{" * n),
        lambda n: doc(("\\begin{align}a \\tag{" * n) + "}"),
        lambda n: doc("\\documentclass[" * n + "\\usepackage[" * n + "\\newtheorem{a}[" * n + eq()),
        lambda n: doc("\\newenvironment{a}{\\begin{align}" * n + "\\newtheorem{t}[equation]{T}" * n),
        lambda n: doc("\\begin{subequations}\\label{g}" * n + "\\setcounter{equation}{" * n),
        lambda n: doc(eq() + "\\begin{subequations}" + ("\\label{a}" + " " * 10) * (10 * n)),
        lambda n: doc("\\ifx " * n + "\\fi " * (n // 2)),
        lambda n: doc("\\ifthenelse{a}{b}{" * n + "\\IfFileExists{x}" * n),
        lambda n: doc("\\ifx a\\section{A}\\fi" * n + eq()),
    ],
    ids=[
        "many equations", "unbalanced braces", "long align", "unclosed begins", "subequations",
        "counter commands", "many macros", "unclosed labels", "unclosed tag", "unclosed options",
        "unclosed environment definitions", "unclosed counter arguments", "labels in a group",
        "unclosed conditionals", "unclosed branch commands", "conditionals around sections",
    ],
)  # fmt: skip
def test_the_numbering_is_linear(make):
    small, large = _scaled(make)
    assert large < 8 * max(small, 0.01)  # linear costs about 4 times, quadratic 16 times
