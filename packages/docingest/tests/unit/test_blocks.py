"""``domain.blocks``: the blocks a chunk can cut (display math, code, tables), their captions and the
completion of the ones a chunk cuts.

Every text here is invented. Offsets are never typed by hand: a helper finds the piece in the
text, so a test reads as "this piece is a block of this kind".
"""

import gc
import time
from itertools import pairwise

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from docingest.domain import blocks as blocks_module
from docingest.domain.blocks import (
    Block,
    Completion,
    Whole,
    attach_captions,
    complete,
    find_blocks,
    partial_note,
    whole_blocks,
)


def spans(text: str, *kinds: str) -> list[tuple[str, str]]:
    """``(kind, text)`` of every block, in order; only these kinds when some are given."""
    return [
        (b.kind, text[b.start : b.end]) for b in find_blocks(text) if not kinds or b.kind in kinds
    ]


def displays(text: str) -> list[str]:
    return [piece for _, piece in spans(text, "display")]


# --------------------------------------------------------------------------- display math


def test_dollar_dollar_is_display_math_on_one_line_or_many():
    text = "A $$E=mc^2$$ and\n\n$$\\begin{aligned}\na &= b\\\\\nc &= d\n\\end{aligned}$$ end."
    assert displays(text) == [
        "$$E=mc^2$$",
        "$$\\begin{aligned}\na &= b\\\\\nc &= d\n\\end{aligned}$$",
    ]


def test_a_display_equation_may_hold_a_blank_line_and_pipes_at_the_line_start():
    text = "x $$\n|a|\n\n| b |\n|---|\n$$ y"
    assert spans(text) == [("display", "$$\n|a|\n\n| b |\n|---|\n$$")]


def test_brackets_inside_the_text_of_a_link_are_not_display_math():
    # the "[" right before "\\[" is the start of a link text; the link target is not next to it
    assert displays("see [\\[x^2\\] more](#l) now") == []
    assert (
        displays("see [ \\[x^2\\] more](#l) now") != []
    )  # with a space it is not the link's bracket


def test_brackets_are_display_math_when_they_hold_math():
    text = "A \\[ E = mc \\] and \\[\n\\frac{a}{b}\n\\] and \\[x^2\\] and \\[x_i\\]."
    assert displays(text) == ["\\[ E = mc \\]", "\\[\n\\frac{a}{b}\n\\]", "\\[x^2\\]", "\\[x_i\\]"]


@pytest.mark.parametrize(
    "literal",
    [
        "see \\[12\\] and \\[3, 4\\]",  # a citation
        "[\\[eq:1\\]](#eq:1)",  # the text of a link
        "Eq. ([\\[eq:a_b\\]](#eq:a_b))",
        "\\[95% CI $+0.04$\\]",  # math in dollars inside a remark
        "\\[Fig. [6](#f)(a)\\]",  # a link inside a remark
        "\\[ht=-5mm\\]",  # a table option, no command, not padded
        "\\[\\[9,1,3\\]\\]",  # a quantum code name
        "\\[one\n\npara\\]",  # math has no blank line
        "\\[x^2 and $a_1$\\]",  # math characters, but also math in dollars: a remark
        "\\[x^2 see [6](#f)\\]",  # math characters, but a link inside
        "\\[ x^2\n\ny \\]",  # padded, but a blank line inside
    ],
)
def test_the_escaped_brackets_of_pandoc_are_not_display_math(literal):
    assert displays(f"Text {literal} text.") == []


def test_a_raw_math_environment_is_display_math():
    text = "a \\begin{equation}x=1\\end{equation} b \\begin{align*}a&=b\\end{align*} c"
    assert displays(text) == [
        "\\begin{equation}x=1\\end{equation}",
        "\\begin{align*}a&=b\\end{align*}",
    ]


def test_other_environments_are_plain_text():
    text = "a \\begin{tabular}{c} x \\end{tabular} b"
    assert find_blocks(text) == []


def test_an_environment_closes_only_with_its_own_end():
    text = "\\begin{equation}a\\end{align}b"
    assert find_blocks(text) == []


@pytest.mark.parametrize(
    "text",
    ["a $$ never closed", "a \\[ x^2 never closed", "a \\begin{equation} x never closed", "$$"],
)
def test_a_delimiter_that_never_closes_is_plain_text(text):
    assert find_blocks(text) == []


def test_a_closing_delimiter_too_far_away_is_not_a_closer(monkeypatch):
    monkeypatch.setattr(blocks_module, "BLOCK_MAX", 20)
    near, far = "$$" + "x" * 10 + "$$", "$$" + "x" * 30 + "$$"
    assert displays(f"{near} then {far}") == [near]


def test_the_default_limits_of_a_block():
    """The numbers themselves: 50,000 characters for a block, 5,000 for brackets, 500 for
    inline math."""
    assert len(displays("a $$" + "x" * 49_000 + "$$ b")) == 1
    assert displays("a $$" + "x" * 51_000 + "$$ b") == []
    assert displays("a \\[ " + "x" * 4_900 + " \\] b") != []
    assert displays("a \\[ " + "x" * 5_100 + " \\] b") == []
    assert spans("a $" + "x" * 490 + "$ b") != []
    assert spans("a $" + "x" * 510 + "$ b") == []
    assert len(spans("```\n" + "x\n" * 24_000 + "```")) == 1
    assert spans("```\n" + "x\n" * 26_000 + "```") == []


def test_a_closer_that_sticks_out_of_one_window_is_found_from_the_next_opener(monkeypatch):
    """The first opener cannot reach the closer (it ends past its window); the second can, and
    the memory of the empty window must not hide the part of it that the first window cut."""
    monkeypatch.setattr(blocks_module, "BLOCK_MAX", 60)
    first = "\\begin{equation}"
    text = first + "abcd" + first + "x" * 14 + "\\end{equation}"
    assert (
        text.index("\\end") == 50 and len(text) == 64
    )  # the closer starts in the window of the first
    assert displays(text) == [text[20:]]


class SpyText(str):
    """A text that records every window ``str.find`` is asked to search."""

    windows: list

    def __new__(cls, value):
        obj = super().__new__(cls, value)
        obj.windows = []
        return obj

    def find(self, sub, start=0, end=None):
        self.windows.append((start, end))
        return super().find(sub, start, end)


def test_a_window_that_held_no_closer_is_not_searched_again(monkeypatch):
    """Openers that never close cost one pass over the text in all, not one window each."""
    monkeypatch.setattr(blocks_module, "BLOCK_MAX", 500)
    text = SpyText("\\begin{align} a\n" * 3_000 + "z" * 1_000)
    assert find_blocks(text) == []
    scanned = sum(end - start for start, end in text.windows)
    assert scanned < 2 * len(text), (scanned, len(text))


def test_an_equation_after_a_stray_opener_is_still_found(monkeypatch):
    monkeypatch.setattr(blocks_module, "BLOCK_MAX", 40)
    text = "\\begin{equation} stray\n" + "w " * 30 + "\n$$a$$"
    assert displays(text) == ["$$a$$"]


def test_an_escaped_dollar_opens_nothing():
    assert find_blocks("costs \\$5 and \\$6 more, \\$\\$ and \\$x\\$") == []
    assert displays("a \\\\$$y$$") == ["$$y$$"]  # an escaped backslash does not escape the dollar
    assert spans("cost \\$5 and $x$ end") == [("inline", "$x$")]  # \$ is not an opener


# --------------------------------------------------------------------------- inline math


def test_inline_math_is_a_block():
    assert spans("so $x_1$ and $\\alpha + \\beta$ hold.") == [
        ("inline", "$x_1$"),
        ("inline", "$\\alpha + \\beta$"),
    ]


def test_inline_math_followed_by_a_digit_is_still_math():
    # pandoc's writer gives "$\to$16c" for 8a$\to$16c, so a digit after the closing dollar
    # must not reject it (a rejected one pairs the dollars wrongly for the rest of the text)
    text = "Li 8a$\\to$16c$\\to$8a pairs and $$E=0$$"
    assert spans(text) == [("inline", "$\\to$"), ("inline", "$\\to$"), ("display", "$$E=0$$")]


def test_an_escaped_opener_is_not_an_opener():
    assert spans("a \\$x$ b") == []
    assert spans("a \\\\$x$ b") == [("inline", "$x$")]  # \\ is a backslash, the dollar is free


def test_a_dollar_amount_is_not_math():
    assert find_blocks("It costs $5 and then $6 more.") == []
    assert spans("It costs $5 and then $6 more, so $x$ it is.") == [("inline", "$x$")]


def test_the_first_dollar_after_an_opener_is_its_end_or_it_has_none():
    # a later dollar is not borrowed: "$a ... $b$" is not one formula
    assert spans("so $a and $b$ end") == [("inline", "$b$")]
    assert spans("so $a\\$ and \\$b$ end") == [
        ("inline", "$a\\$ and \\$b$")
    ]  # escaped ones skipped


@pytest.mark.parametrize("text", ["a $ x$ b", "a $x b", "a $\n\nx$ b", "a $ b"])
def test_dollars_that_are_not_a_pair_of_delimiters(text):
    assert find_blocks(text) == []


def test_inline_math_does_not_cross_a_paragraph():
    assert find_blocks("a $x\n\ny$ b") == []
    assert spans("a $x\ny$ b") == [("inline", "$x\ny$")]


def test_inline_math_has_a_length_limit(monkeypatch):
    monkeypatch.setattr(blocks_module, "INLINE_MAX", 10)
    assert spans("a $" + "x" * 8 + "$ b $" + "x" * 30 + "$ c") == [("inline", "$" + "x" * 8 + "$")]


def test_display_math_is_not_two_inline_maths():
    assert spans("a $$x$$ b") == [("display", "$$x$$")]


def test_a_stray_dollar_does_not_take_the_start_of_a_display_equation_as_its_end():
    assert spans("see $a b and then $$E=0$$ end") == [("display", "$$E=0$$")]
    assert spans("see $a b and then $$E=0$$ and $c$ end") == [
        ("display", "$$E=0$$"),
        ("inline", "$c$"),
    ]


# --------------------------------------------------------------------------- code


def test_a_fenced_block_runs_to_its_closing_fence():
    text = "before\n\n``` latex\n\\begin{quantikz}\n\\gate{H}\n\\end{quantikz}\n```\n\nafter"
    assert spans(text) == [
        ("code", "``` latex\n\\begin{quantikz}\n\\gate{H}\n\\end{quantikz}\n```")
    ]


def test_a_fence_needs_a_closing_fence_of_the_same_character_and_enough_length():
    text = "````\na\n```\nb\n~~~\n````\nout\n~~~\nx\n```\nnot closed"
    assert spans(text) == [("code", "````\na\n```\nb\n~~~\n````")]


def test_tilde_fences_and_indented_fences():
    text = "~~~ python\nx = 1\n~~~\n\n  ```\ny\n  ```\n"
    assert [kind for kind, _ in spans(text)] == ["code", "code"]
    assert find_blocks("    ```\nx\n    ```\n") == []  # four spaces: indented code, not a fence
    assert (
        find_blocks("    ```\nx\n```\n") == []
    )  # four spaces: not an opener, and the last one never closes


def test_a_closing_fence_has_nothing_after_it():
    text = "```\ncode\n``` not a closer\nmore\n```"
    assert spans(text) == [("code", text)]


def test_a_fence_that_never_closes_is_plain_text():
    assert find_blocks("```latex\n\\begin{quantikz}\n") == []


def test_inline_code_with_backticks_is_not_a_fence():
    assert find_blocks("```x``` and ```y```\n```\n") == []


def test_math_delimiters_in_code_are_plain_text():
    text = "```latex\n\\begin{quantikz}\n\\lstick{$q_0$} & $$ \\qw \\\\[2mm] \\[\n\\end{quantikz}\n```\n"
    assert spans(text) == [("code", text.rstrip("\n"))]


def test_a_code_fence_inside_display_math_is_part_of_it():
    text = "$$\n```\nx\n```\n$$"
    assert spans(text) == [("display", text)]


# --------------------------------------------------------------------------- tables


TABLE = "| a | b |\n|:---:|---:|\n| 1 | $x$ |\n| 2 | 3 |"


def test_a_pipe_table_runs_from_its_header_to_its_last_row():
    text = f"Intro.\n\n{TABLE}\n\nOutro."
    assert spans(text) == [("table", TABLE)]


def test_a_table_at_the_start_and_one_without_a_leading_pipe():
    assert spans(f"{TABLE}\nrest") == [("table", TABLE)]
    bare = "a | b\n--|--\n1 | 2"
    assert spans(bare) == [("table", bare)]


def test_inline_math_in_the_header_belongs_to_the_table():
    table = "| $x$ | $y$ |\n|---|---|\n| 1 | 2 |"
    assert spans(f"Text $z$ here.\n\n{table}\n") == [("inline", "$z$"), ("table", table)]


def test_a_blank_line_ends_a_table_and_a_line_without_a_pipe_too():
    text = "| a | b |\n|---|---|\n| 1 | 2 |\n\n| 3 | 4 |\nplain\n"
    assert spans(text) == [("table", "| a | b |\n|---|---|\n| 1 | 2 |")]


def test_a_delimiter_row_at_the_very_start_has_no_header():
    assert find_blocks("|---|---|\n| 1 | 2 |\n") == []


def test_a_rule_is_not_a_table():
    assert find_blocks("Title\n---\ntext\n\n-----\n") == []
    assert find_blocks("a | b\n---\ntext\n") == []  # a pipe in the header, none in the rule
    assert find_blocks("A line\n|---|---|\n") == []  # no pipe in the header line


def test_a_table_after_a_block_is_found_and_the_blocks_do_not_overlap():
    text = f"$$a$$\n{TABLE}\n```\nx\n```\n"
    found = find_blocks(text)
    assert [b.kind for b in found] == ["display", "table", "code"]
    assert all(a.end <= b.start for a, b in pairwise(found))


def test_a_header_line_cannot_start_inside_a_block():
    text = "$$\nx\n$$ a | b\n|---|---|\n| 1 | 2 |\n"
    assert [b.kind for b in find_blocks(text)] == ["display"]


def test_pipes_in_math_are_not_a_table():
    assert displays("$$\n|a|\n|---|\n|b|\n$$") == ["$$\n|a|\n|---|\n|b|\n$$"]
    assert [b.kind for b in find_blocks("$$\n|a|\n|---|\n|b|\n$$")] == ["display"]


# --------------------------------------------------------------------------- fragments


FRAGMENTS = st.one_of(
    st.tuples(st.just("plain"), st.text("abcdefg ,.", min_size=1, max_size=40)),
    st.tuples(
        st.just("inline"),
        st.text("xyz_^1+ ", min_size=1, max_size=20).map(lambda s: f"${s.strip() or 'x'}$"),
    ),
    st.tuples(
        st.just("display"), st.text("xyz_^1+= ", min_size=1, max_size=40).map(lambda s: f"$${s}$$")
    ),
    st.tuples(
        st.just("display"),
        st.text("xyz_^1+ ", min_size=1, max_size=30).map(lambda s: f"\\[ {s} \\]"),
    ),
    st.tuples(
        st.just("code"),
        st.lists(st.text("abc{}$\\ ", max_size=15), min_size=1, max_size=5).map(
            lambda ls: "```latex\n" + "\n".join(ls) + "\n```"
        ),
    ),
    st.tuples(
        st.just("table"),
        st.integers(1, 4).map(
            lambda n: "| a | b |\n|---|---|\n" + "| 1 | 2 |\n" * (n - 1) + "| 3 | 4 |"
        ),
    ),
)


@settings(max_examples=300, deadline=None)
@given(st.lists(FRAGMENTS, min_size=1, max_size=12))
def test_blocks_put_in_a_text_are_the_blocks_found(fragments):
    """Fragments joined by blank lines: the blocks found are the ones that were put in."""
    text, expected, pos = "", [], 0
    for kind, piece in fragments:
        text += piece + "\n\n"
        if kind != "plain":
            expected.append(Block(pos, pos + len(piece), kind))
        pos += len(piece) + 2
    assert find_blocks(text) == expected


# --------------------------------------------------------------------------- captions

CIRCUIT_CODE = "```latex\n\\begin{quantikz}\n& \\gate{H} & \\qw\n\\end{quantikz}\n```"
CIRCUIT_MATH = "$$\\begin{quantikz}\n& \\gate{H} & \\qw\n\\end{quantikz}$$"
TABLE = "| a | b |\n|---|---|\n| 1 | 2 |"


def captioned(text: str, caption_max: int = 300) -> list[tuple[str, str]]:
    """``(kind, text)`` of every block after the captions are attached."""
    found = attach_captions(text, find_blocks(text), caption_max=caption_max)
    return [(b.kind, text[b.start : b.end]) for b in found]


def test_the_paragraph_after_a_code_block_is_its_caption():
    caption = "Noise rules for single-qubit gates. See Sec. [2](#sec:x)."
    text = f"Intro.\n\n{CIRCUIT_CODE}\n\n{caption}\n\nNext paragraph."
    assert captioned(text) == [("code", f"{CIRCUIT_CODE}\n\n{caption}")]


def test_the_paragraph_after_a_circuit_in_display_math_is_its_caption():
    caption = "The circuit that prepares the state."
    for circuit in (
        CIRCUIT_MATH,
        "$$\\Qcircuit @C=1em { & \\gate{H} & \\qw }$$",
        "$$\\begin{yquant} qubit q; h q; \\end{yquant}$$",
        "$$\\begin{tikzcd} a \\arrow[r] & b \\end{tikzcd}$$",
        "\\begin{equation}\\begin{quantikz} & \\qw \\end{quantikz}\\end{equation}",
    ):
        assert captioned(f"{circuit}\n\n{caption}\n\nMore.") == [
            ("display", f"{circuit}\n\n{caption}")
        ]


def test_an_equation_that_is_not_a_circuit_has_no_caption_even_when_a_label_follows():
    for after in ("A plain paragraph.", "Fig. 3 shows the result.", "Table 2 lists them."):
        assert captioned(f"$$a=b$$\n\n{after}\n\nEnd.") == [("display", "$$a=b$$")]


@pytest.mark.parametrize(
    "label",
    [
        "Figure 3. The results.",
        "Fig. 2: The results.",
        "Fig. 2. The results.",
        "FIG. 4. The results.",
        "TABLE II. The results.",
        "Table S1. The results.",
        "Table 12. The results.",
        "Fig.\xa05. The results.",
        "**Figure 1.** The results.",
        "_Figure 1._ The results.",
        "Tab. 3: The results.",
        "Scheme 2. The results.",
        "Listing 2. The results.",
        "Chart 3. The results.",
        "Algorithm 1. The results.",
        "Fig. (3): The results.",
        "figure 7: the results",
    ],
)
def test_a_label_makes_the_paragraph_after_a_table_its_caption(label):
    assert captioned(f"{TABLE}\n\n{label}\n\nProse.") == [("table", f"{TABLE}\n\n{label}")]


@pytest.mark.parametrize(
    "prose",
    [
        "Table of the results.",
        "Figure out the results.",
        "Tablets 3 help.",
        "Fig. a shows it.",
        "Tabulated results.",
        "The results follow.",
        "Table",
        "Table c shows it.",
        "Tables 3 and 4 show the rest of the values.",
        "Figures I think so.",
        "Fig. v: the values.",
        "Table x. results follow.",
        "Table 2 shows the values.",
        "Fig. 3 shows the result.",
        "Table II lists them",
        "Table 3",
        "Fig. v shows it.",
        "TABLE LIVE results.",
    ],
)
def test_prose_after_a_table_is_not_its_caption(prose):
    assert captioned(f"{TABLE}\n\n{prose}\n\nMore.") == [("table", TABLE)]


@pytest.mark.parametrize("form", [": The results.", "Table: The results.", "table: The results."])
def test_pandocs_form_makes_the_paragraph_after_a_table_its_caption(form):
    assert captioned(f"{TABLE}\n\n{form}\n\nProse.") == [("table", f"{TABLE}\n\n{form}")]


def test_a_colon_that_is_not_followed_by_a_space_is_not_pandocs_form():
    assert captioned(f"{TABLE}\n\n:no space\n\nProse.") == [("table", TABLE)]


def test_a_labelled_paragraph_after_code_is_a_caption_and_so_is_any_other():
    for caption in ("Fig. 2. A circuit.", "A circuit.", "the gates of the circuit"):
        assert captioned(f"{CIRCUIT_CODE}\n\n{caption}\n\nMore.") == [
            ("code", f"{CIRCUIT_CODE}\n\n{caption}")
        ]


@pytest.mark.parametrize(
    "paragraph",
    [
        "# A heading",
        "## Another",
        "- an item",
        "* an item",
        "+ an item",
        "1. an item",
        "2) an item",
        "> a quote",
        "<!-- a comment -->",
        "| a | b |",
    ],
)
def test_a_heading_an_item_a_quote_or_a_row_is_not_a_caption(paragraph):
    assert captioned(f"{CIRCUIT_CODE}\n\n{paragraph}\n\nMore.") == [("code", CIRCUIT_CODE)]


def test_only_the_first_paragraph_after_the_block_is_the_caption():
    text = f"{CIRCUIT_CODE}\n\nFirst paragraph.\n\nSecond paragraph.\n"
    assert captioned(text) == [("code", f"{CIRCUIT_CODE}\n\nFirst paragraph.")]


def test_a_caption_may_follow_on_the_next_line_or_after_a_few_blank_lines():
    assert captioned(f"{CIRCUIT_CODE}\nCaption.\n\nMore.") == [
        ("code", f"{CIRCUIT_CODE}\nCaption.")
    ]
    assert captioned(f"{CIRCUIT_CODE}\n\n\n\nCaption.\n\nMore.") == [
        ("code", f"{CIRCUIT_CODE}\n\n\n\nCaption.")
    ]
    assert captioned(f"{CIRCUIT_CODE}\n  \n  Caption.\n\nMore.") == [
        ("code", f"{CIRCUIT_CODE}\n  \n  Caption.")
    ]


def test_text_on_the_line_of_the_closing_fence_or_five_blank_lines_away_is_not_a_caption():
    assert captioned(f"{CIRCUIT_CODE}\n\n\n\n\nCaption.\n\nMore.") == [("code", CIRCUIT_CODE)]
    assert captioned(f"{CIRCUIT_MATH} and Caption.\n\nMore.") == [("display", CIRCUIT_MATH)]
    assert captioned(f"{CIRCUIT_MATH}Caption.\n\nMore.") == [("display", CIRCUIT_MATH)]


def test_a_caption_is_at_most_caption_max_characters_long():
    caption = "c" * 40
    text = f"{CIRCUIT_CODE}\n\n{caption}\n\nMore."
    assert captioned(text, caption_max=40) == [("code", f"{CIRCUIT_CODE}\n\n{caption}")]
    assert captioned(text, caption_max=39) == [("code", CIRCUIT_CODE)]


def test_a_line_of_white_space_ends_the_caption_like_a_blank_line():
    for blank in ("\n \n", "\n\t\n", "\n   \n", "\n\n"):
        text = f"{CIRCUIT_CODE}\n\nFig. 1. A caption line.{blank}Body paragraph two.\n\nMore."
        assert captioned(text) == [("code", f"{CIRCUIT_CODE}\n\nFig. 1. A caption line.")]


def test_a_caption_at_the_end_of_the_text_needs_no_blank_line_after_it():
    for end in ("", "\n", "\n\n", "\n \n"):
        text = f"{CIRCUIT_CODE}\n\nLast words.{end}"
        assert captioned(text) == [("code", f"{CIRCUIT_CODE}\n\nLast words.")]
    long = f"{CIRCUIT_CODE}\n\n" + "w" * 40
    assert captioned(long, caption_max=40) == [("code", long)]
    assert captioned(long, caption_max=39) == [("code", CIRCUIT_CODE)]


def test_an_empty_text_after_the_block_is_no_caption():
    assert captioned(CIRCUIT_CODE) == [("code", CIRCUIT_CODE)]
    assert captioned(CIRCUIT_CODE + "\n\n") == [("code", CIRCUIT_CODE)]
    assert captioned(CIRCUIT_CODE + "\n \t \n") == [("code", CIRCUIT_CODE)]


def test_the_inline_math_of_a_caption_goes_into_the_block():
    text = (
        f"Pre $x$ post.\n\n{CIRCUIT_CODE}\n\nThe state $\\ket{{0}}$ and $y$ end.\n\nThen $z$ here."
    )
    got = attach_captions(text, find_blocks(text), caption_max=300)
    assert [(b.kind, text[b.start : b.end]) for b in got] == [
        ("inline", "$x$"),
        ("code", f"{CIRCUIT_CODE}\n\nThe state $\\ket{{0}}$ and $y$ end."),
        ("inline", "$z$"),
    ]
    assert all(a.end <= b.start for a, b in pairwise(got))


def test_a_paragraph_that_holds_another_block_is_not_a_caption():
    assert captioned(f"{CIRCUIT_CODE}\n\nA caption $$x$$ with an equation.\n\nMore.") == [
        ("code", CIRCUIT_CODE),
        ("display", "$$x$$"),
    ]
    assert captioned(f"{CIRCUIT_CODE}\n\nA caption\n```\ncode\n```\n\nMore.") == [
        ("code", CIRCUIT_CODE),
        ("code", "```\ncode\n```\n\nMore."),  # the second block has this caption
    ]


def test_a_block_right_after_a_block_is_not_its_caption():
    got = captioned(f"{CIRCUIT_CODE}\n\n{CIRCUIT_MATH}\n\n{TABLE}\n\nEnd.")
    assert got == [("code", CIRCUIT_CODE), ("display", CIRCUIT_MATH), ("table", TABLE)]


def test_each_of_two_blocks_keeps_its_own_caption():
    text = f"{CIRCUIT_CODE}\n\nCaption one.\n\n{TABLE}\n\nTable 2. Caption two.\n\nEnd."
    assert captioned(text) == [
        ("code", f"{CIRCUIT_CODE}\n\nCaption one."),
        ("table", f"{TABLE}\n\nTable 2. Caption two."),
    ]


def test_inline_math_has_no_caption():
    assert captioned("so $x$\n\nFig. 3 shows.\n\nEnd.") == [("inline", "$x$")]


def test_a_text_without_such_blocks_comes_back_unchanged():
    text = "Prose $x$ and $$a=b$$ where c.\n\nMore prose.\n\n$$d$$\n\nFig. 3. Not a caption."
    assert attach_captions(text, find_blocks(text), caption_max=300) == find_blocks(text)


def test_the_blocks_come_back_as_a_new_list():
    text = f"{CIRCUIT_CODE}\n\nCaption."
    blocks = find_blocks(text)
    attach_captions(text, blocks, caption_max=300)
    assert blocks == [Block(0, len(CIRCUIT_CODE), "code")]


CAPTION_FRAGMENTS = st.one_of(
    FRAGMENTS,
    st.tuples(st.just("caption"), st.text("abcdefg ,.", min_size=1, max_size=40)),
    st.tuples(
        st.just("caption"), st.text("abcdefg ,.", min_size=1, max_size=30).map("Fig. 1. ".__add__)
    ),
)


@settings(max_examples=300, deadline=None)
@given(st.lists(CAPTION_FRAGMENTS, min_size=1, max_size=12), st.integers(10, 80))
def test_the_blocks_with_captions_never_overlap_and_only_grow_over_a_paragraph(fragments, limit):
    text = ""
    for _, piece in fragments:
        text += piece.strip() + "\n\n"
    plain = find_blocks(text)
    got = attach_captions(text, plain, caption_max=limit)
    assert all(a.end <= b.start for a, b in pairwise(got))
    kept = {b.start: b for b in got}
    for b in plain:
        if b.start in kept:
            grown = kept[b.start]
            assert grown.kind == b.kind and grown.end >= b.end
            if grown.end > b.end:
                tail = text[b.end : grown.end]
                assert b.kind != "inline" and "\n\n" not in tail.strip()
                assert len(tail.strip()) <= limit
        else:
            assert b.kind == "inline"  # swallowed by a caption


# --------------------------------------------------------------------------- whole blocks


def test_whole_blocks_are_the_blocks_with_their_captions_and_without_inline_math():
    caption = "Fig. 2. The circuit of $U$."
    text = f"So $x$ holds.\n\n{CIRCUIT_CODE}\n\n{caption}\n\nThen $$a=b$$ and\n\n{TABLE}\n\nEnd."
    got = [
        (w.kind, text[w.start : w.core], text[w.core : w.end])
        for w in whole_blocks(text, caption_max=99)
    ]
    assert got == [
        ("code", CIRCUIT_CODE, f"\n\n{caption}"),
        ("display", "$$a=b$$", ""),
        ("table", TABLE, ""),
    ]


def test_a_block_with_no_caption_ends_where_it_ends():
    text = f"Intro.\n\n{TABLE}\n\nProse after a table."
    [w] = whole_blocks(text, caption_max=99)
    assert w == Whole(
        text.index(TABLE), text.index(TABLE) + len(TABLE), text.index(TABLE) + len(TABLE), "table"
    )


# --------------------------------------------------------------------------- completion

LIMIT = 300
EQ = "$$ H = \\sum_i a_i \\tag{1}$$"
FENCE = (
    "```latex\n\\begin{quantikz}\n& \\gate{H} & \\qw \\\\\n& \\qw & \\targ{}\n\\end{quantikz}\n```"
)
AFTER = "## Next part"  # a heading is no caption
GRID = "| a | b |\n|---|---|\n" + "| 1 | 2 |\n" * 6 + "| 3 | 4 |"


def words(n: int, tag: str = "w") -> str:
    """Invented prose of exactly ``n`` characters (no blocks, no blank line)."""
    return ((tag + "x ") * n)[:n]


def doc(*parts: str) -> str:
    return "\n\n".join(parts)


def span_of(text: str, piece: str, before: int = 0, after: int = 0) -> tuple[int, int]:
    """The offsets of ``piece`` in ``text``, moved ``before`` earlier and ``after`` later."""
    at = text.index(piece)
    return at - before, at + len(piece) + after


def plan(text, spans, limit=LIMIT, caption_max=LIMIT):
    return complete(spans, whole_blocks(text, caption_max=caption_max), limit=limit)


def shown(text, c: Completion) -> str:
    return text[c.start : c.stop]


def test_a_chunk_that_stops_inside_a_display_equation_gets_the_rest_of_it():
    text = doc(words(80), EQ, words(80))
    a = text.index(EQ)
    [c] = plan(text, [(0, a + 12)])
    assert c == Completion(0, a + len(EQ))
    assert shown(text, c).endswith("\\tag{1}$$")


def test_a_chunk_that_starts_inside_a_display_equation_gets_the_start_of_it():
    text = doc(words(80), EQ, words(80))
    a = text.index(EQ)
    [c] = plan(text, [(a + 12, len(text))])
    assert c == Completion(a, len(text))
    assert shown(text, c).startswith("$$ H =")


def test_a_circuit_code_block_cut_at_either_end_is_completed():
    text = doc(words(80), FENCE, AFTER)
    a = text.index(FENCE)
    [end_cut] = plan(text, [(0, a + 20)])
    [start_cut] = plan(text, [(a + 20, len(text))])
    assert shown(text, end_cut).endswith("\\end{quantikz}\n```")
    assert shown(text, start_cut).startswith("```latex\n\\begin{quantikz}")


def test_a_table_cut_in_a_row_is_completed():
    text = doc(words(80), GRID, words(80))
    a = text.index(GRID)
    [c] = plan(
        text,
        [
            (0, a + 40),
        ],
    )
    assert shown(text, c).endswith("| 3 | 4 |")
    [d] = plan(text, [(a + 40, len(text))])
    assert shown(text, d).startswith("| a | b |")


def test_a_chunk_that_holds_the_whole_block_or_stops_at_its_border_is_left_alone():
    text = doc(words(80), FENCE, AFTER)
    a, b = span_of(text, FENCE)
    assert plan(text, [(0, len(text))]) == [Completion(0, len(text))]
    assert plan(text, [(0, a)]) == [Completion(0, a)]  # ends where the block starts
    assert plan(text, [(b, len(text))]) == [Completion(b, len(text))]  # starts where it ends
    assert plan(text, [(a, b)]) == [Completion(a, b)]


def test_a_chunk_with_no_block_near_it_is_left_alone():
    text = doc(words(300), FENCE, AFTER)
    assert plan(text, [(10, 200)]) == [Completion(10, 200)]
    assert plan(text, []) == []


def test_the_caption_after_a_block_comes_with_it_when_a_chunk_stops_in_front_of_it():
    caption = "Fig. 2. Circuit that prepares the state."
    text = doc(words(80), FENCE, caption, AFTER)
    _, end = span_of(text, FENCE)
    [at_border] = plan(text, [(0, end)])
    [inside] = plan(text, [(0, end + 10)])
    assert shown(text, at_border).endswith(caption)
    assert shown(text, inside).endswith(caption)


def test_a_chunk_that_starts_inside_a_caption_gets_the_block_before_it():
    caption = "Fig. 2. Circuit that prepares the state."
    text = doc(words(80), FENCE, caption, AFTER)
    at = text.index(caption) + 8
    [c] = plan(text, [(at, len(text))])
    assert shown(text, c).startswith("```latex") and caption in shown(text, c)


def test_a_display_equation_that_is_not_a_circuit_has_no_caption_to_add():
    text = doc(words(80), EQ, "where a is a number. " + words(60))
    _, end = span_of(text, EQ)
    assert plan(text, [(0, end)]) == [Completion(0, end)]


def test_the_block_alone_is_completed_when_its_caption_would_pass_the_bound():
    caption = "Fig. 2. " + words(120)
    text = doc(words(40), FENCE, caption, AFTER)
    a, b = span_of(text, FENCE)
    limit = len(FENCE) + 40
    assert len(FENCE) <= limit < b + 2 + len(caption) - a
    [alone] = plan(text, [(0, a + 10)], limit=limit, caption_max=400)
    assert (alone.start, alone.stop, alone.partial) == (0, b, ())
    [with_caption] = plan(text, [(0, a + 10)], limit=limit + 200, caption_max=400)
    assert shown(text, with_caption).endswith(caption)


def test_a_block_up_to_the_bound_is_completed_and_one_character_more_is_not():
    for extra, complete_it in ((0, True), (1, False)):
        table = "| a |\n|---|\n| " + "1" * (LIMIT + extra - 16) + " |"
        assert len(table) == LIMIT + extra
        text = doc(words(40), table, words(40))
        a = text.index(table)
        [c] = plan(text, [(0, a + 30)])
        assert (c.stop == a + len(table)) is complete_it
        assert (c.partial == ("table",)) is (not complete_it)


def test_the_bound_counts_the_block_with_its_caption_first_and_the_block_alone_second():
    caption = "Fig. 2. " + words(60)
    text = doc(words(40), FENCE, caption, AFTER)
    a, b = span_of(text, FENCE)
    with_caption = b + 2 + len(caption) - a
    cut = [(0, a + 10)]
    # the block with its caption just fits, one character less and the caption stays out
    [fits] = plan(text, cut, limit=with_caption, caption_max=400)
    [tight] = plan(text, cut, limit=with_caption - 1, caption_max=400)
    assert (fits.stop, tight.stop) == (b + 2 + len(caption), b)
    # the block alone just fits, one character less and nothing is added
    [alone] = plan(text, cut, limit=len(FENCE), caption_max=400)
    [over] = plan(text, cut, limit=len(FENCE) - 1, caption_max=400)
    assert alone.stop == b and (over.stop, over.partial) == (a + 10, ("code",))


def test_a_chunk_that_starts_after_a_block_left_short_of_its_caption_does_not_take_it():
    caption = "Fig. 2. " + words(120)
    text = doc(words(40), FENCE, caption, AFTER)
    a, b = span_of(text, FENCE)
    limit = len(FENCE) + 20  # the block fits, with its caption it does not
    first, second = (0, a + 30), (b, len(text))  # the second starts right after the block
    low_first = plan(text, [first, second], limit=limit, caption_max=400)
    high_first = plan(text, [second, first], limit=limit, caption_max=400)
    assert [(c.start, c.stop) for c in low_first] == [(0, b), second]
    assert [(c.start, c.stop) for c in high_first] == [second, (0, b)]


def test_a_too_long_block_that_chunks_hold_end_to_end_with_no_overlap_needs_no_note():
    text = doc(words(40), GRID, words(40))
    a, b = span_of(text, GRID)
    half = (a + b) // 2
    plans = plan(text, [(0, half), (half, len(text))], limit=len(GRID) - 1)
    assert [c.partial for c in plans] == [(), ()]


@pytest.mark.parametrize(
    ("piece", "kind"), [(FENCE, "code"), (EQ + " " * 0, "display"), (GRID, "table")]
)
def test_the_note_names_the_kind_of_the_block_that_is_too_long(piece, kind):
    text = doc(words(40), piece, AFTER)
    a = text.index(piece)
    [c] = plan(text, [(0, a + 10)], limit=10)
    assert c.partial == (kind,)


def test_a_block_too_long_to_complete_is_left_as_it_is_and_the_chunk_says_so():
    text = doc(words(40), GRID * 3, words(40))
    a = text.index(GRID)
    one, two = (0, a + 100), (a + 90, a + 190)
    plans = plan(text, [one, two], limit=100)
    assert [(c.start, c.stop) for c in plans] == [one, two]
    assert [c.partial for c in plans] == [("table",), ("table",)]


def test_a_too_long_block_that_the_chunks_hold_between_them_needs_no_note():
    text = doc(words(40), GRID, words(40))
    a, b = span_of(text, GRID)
    half = (a + b) // 2
    plans = plan(text, [(0, half + 5), (half - 5, len(text))], limit=len(GRID) - 1)
    assert [c.partial for c in plans] == [(), ()]


def test_the_note_names_the_kind_of_each_block_once():
    assert partial_note(["table"]) == (
        "Note: this passage shows only part of a longer table; the rest is not in this passage."
    )
    assert partial_note(["code", "table", "code"]) == (
        "Note: this passage shows only part of a longer code block and of a longer table; "
        "the rest is not in this passage."
    )
    assert "display equation" in partial_note(["display"])


def test_neighbouring_chunks_that_hold_a_block_between_them_add_nothing_to_each_other():
    text = doc(words(80), FENCE, AFTER)
    a, _ = span_of(text, FENCE)
    mid = a + 40
    first, second = (0, mid + 50), (mid - 50, len(text))  # they share 100 characters
    assert plan(text, [first, second]) == [Completion(*first), Completion(*second)]


def test_text_that_another_chunk_holds_is_not_added_a_second_time():
    text = doc(words(80), FENCE, AFTER)
    a, b = span_of(text, FENCE)
    # the first stops inside the block, the second holds the block's end and more
    first, second = (0, a + 30), (b - 40, len(text))
    got = plan(text, [first, second])
    assert got[0].stop == second[0]  # up to where the second begins: no further
    assert got[1].start == second[0]
    covered = [shown(text, c) for c in got]
    assert (covered[0] + covered[1]).count("\\end{quantikz}") == 1


def test_a_hole_between_two_chunks_goes_to_the_better_ranked_one():
    text = doc(words(30), GRID, words(30))
    a, b = span_of(text, GRID)
    low, high = (0, a + 20), (b - 20, len(text))
    best_first = plan(text, [low, high])
    assert best_first[0].stop == b - 20 and best_first[1].start == high[0]
    best_last = plan(text, [high, low])
    assert best_last[0].start == a + 20 and best_last[1].stop == low[1]


def test_a_block_cut_by_one_chunk_at_each_end_gives_each_the_missing_side():
    first_eq = "$$ A = b $$"
    text = doc(words(60), first_eq, words(100), FENCE, AFTER)
    a = text.index(first_eq)
    f = text.index(FENCE)
    # one chunk stops inside the first equation and starts inside the circuit's block
    [c] = plan(text, [(a + 4, f + 25)])
    assert c.start == a and c.stop == f + len(FENCE)


def test_a_chunk_inside_a_long_block_is_extended_on_both_sides_within_the_bound():
    text = doc(words(30), GRID, words(30))
    a, b = span_of(text, GRID)
    [c] = plan(text, [(a + 20, b - 20)])
    assert (c.start, c.stop) == (a, b)


def test_a_chunk_that_is_the_whole_text_is_left_alone():
    text = doc(words(30), FENCE, AFTER)
    [c] = plan(text, [(0, len(text))])
    assert c == Completion(0, len(text))


@settings(max_examples=300, deadline=None)
@given(
    st.lists(FRAGMENTS, min_size=1, max_size=10),
    st.lists(st.tuples(st.integers(0, 400), st.integers(1, 200)), min_size=1, max_size=4),
    st.integers(20, 200),
)
def test_a_completion_only_grows_a_chunk_by_text_of_one_block_that_no_other_chunk_holds(
    fragments, raw, limit
):
    text = "".join(piece + "\n\n" for _, piece in fragments)
    n = len(text)
    spans = []
    for start, size in raw:
        a = min(start, n - 1)
        spans.append((a, min(n, a + size)))
    wholes = whole_blocks(text, caption_max=80)
    got = complete(spans, wholes, limit=limit)
    assert len(got) == len(spans)
    for i, ((a, b), c) in enumerate(zip(spans, got, strict=True)):
        assert c.start <= a and b <= c.stop and c.start >= 0 and c.stop <= n
        for lo, hi in ((c.start, a), (b, c.stop)):
            if lo == hi:
                continue
            assert hi - lo <= limit
            assert any(w.start <= lo and hi <= w.end for w in wholes)  # inside one block
            for j, (x, y) in enumerate(spans):
                if j != i:
                    assert hi <= x or y <= lo  # no other chunk holds any of it


def test_completing_is_linear_in_the_number_of_blocks():
    def build(n: int):
        text = "".join(
            f"Words {i} here.\n\n{EQ}\n\n{FENCE}\n\nFig. {i}. Cap.\n\n" for i in range(n)
        )
        spans = [(0, 400), (len(text) // 2, len(text) // 2 + 400), (len(text) - 400, len(text))]
        return text, spans

    def work(args):
        text, spans = args
        complete(spans, whole_blocks(text, caption_max=900), limit=2700)

    small, big = build(250), build(1000)
    t_small, t_big = seconds(work, small), seconds(work, big)
    assert t_big < max(8 * t_small, 0.05), (t_small, t_big)


# --------------------------------------------------------------------------- linear time


def seconds(fn, text) -> float:
    best = float("inf")
    for _ in range(3):  # best of 3: the least disturbed run
        gc.collect()
        t0 = time.process_time()  # CPU time: waiting for a busy CPU is not counted
        fn(text)
        best = min(best, time.process_time() - t0)
    return best


def scan(text: str) -> None:
    whole_blocks(text, caption_max=900)


N = 4_000

BAD = {
    "unclosed display": lambda n: "$$ a b\n" * n,
    "unclosed brackets": lambda n: "\\[ x^2\n" * n,
    "brackets with a far closer": lambda n: "\\[ $x$ y\n" * n + "\\]",
    "unclosed environments": lambda n: "\\begin{equation} a\n" * n,
    "environments with a far closer": lambda n: "\\begin{align} a\n" * n + "\\end{align}",
    "unclosed fences": lambda n: "```latex\nx\n" * n,
    "fences of another length": lambda n: "`````\nx\n```\n" * n,
    "dollars that never pair": lambda n: "$a " * n,
    "dollars at the paragraph end": lambda n: "$a\n\n" * n,
    "currency": lambda n: "It costs $5 and $6. " * n,
    "inline math": lambda n: "so $x_i$ and $y$ hold. " * n,
    "display math": lambda n: "We get $$a=b$$ where $c$ is d. " * n,
    "equations in a row": lambda n: "$$a$$" * n,
    "stray rules": lambda n: "|---|---|\n" * n,
    "rule then spaces": lambda n: "a | b\n|---" + " " * (6 * n) + "x\n",
    "rule then tabs": lambda n: "a | b\n---" + "\t" * (6 * n) + "x\n",
    "rule with spaces between cells": lambda n: "a | b\n|---" + " |---" * n + " " * (6 * n) + "x\n",
    "rules with headers": lambda n: "a | b\n|---|\n" * n,
    "one long table": lambda n: "| a | b |\n" + "|---|---|\n| 1 | 2 |\n" * n,
    "pipes": lambda n: "| a | b |\n" * n,
    "escapes": lambda n: "\\$\\[\\\\" * n,
    "code blocks and text without a blank line": lambda n: "```\na\n```\nword word word word\n" * n,
    "code blocks and captions": lambda n: "```\na\n```\n\nA caption $x$ here.\n\n" * n,
    "a code block and a long paragraph": lambda n: "```\na\n```\n\n" + "word " * (4 * n),
    "a code block and a long run of spaces": lambda n: "```\na\n```" + " " * (6 * n) + "\n\nx",
    "a code block and newlines": lambda n: "```\na\n```" + "\n" * (6 * n) + "x",
    "a code block and lines of spaces": lambda n: "```\na\n```\n\nx\n" + " \n" * n,
    "a code block and a long line of spaces": lambda n: (
        "```\na\n```\n\nx\n" + " " * (6 * n) + "\ny"
    ),
    "newlines each followed by spaces": lambda n: "```\na\n```\n\nx" + "\n" + (" " * 3 + "x\n") * n,
    "tables and labels": lambda n: "| a | b |\n|---|---|\n\nTable 1. Words.\n\n" * n,
    "circuits in display math": lambda n: "$$\\begin{quantikz} a $$\n\nA caption.\n\n" * n,
    "circuits in display math without a blank line": lambda n: (
        "$$\\begin{quantikz} a $$\nword\n" * n
    ),
    "display math, inline math and words": lambda n: "$$a$$\nx $y$ $z$ x $$a$$\n" * n,
}


@pytest.mark.parametrize("make", BAD.values(), ids=BAD)
def test_scanning_is_linear_in_the_size_of_the_text(make):
    small, big = seconds(scan, make(N // 4)), seconds(scan, make(N))
    assert big < 10
    assert big < max(8 * small, 0.05), (small, big)
