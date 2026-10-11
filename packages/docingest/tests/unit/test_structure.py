"""Running headers, paragraphs, hyphenation and headings of a text layer, on synthetic lines."""

from docingest.domain.layout import PageLayout, TextLine
from docingest.domain.structure import structure_pages
from docingest.domain.text import HYPHEN_MARK

HEIGHT = 792.0
WORDS = [
    "alpha",
    "beta",
    "gamma",
    "delta",
    "epsilon",
    "zeta",
    "theta",
    "kappa",
    "lambda",
    "sigma",
    "omega",
]


def line(text, baseline, *, size=10.0, bold=False, x0=72.0, x1=None, upright=True):
    return TextLine(
        text, size, bold, x0, x1 if x1 is not None else x0 + 6 * len(text), baseline, upright
    )


def page(*lines):
    return PageLayout(tuple(lines), 612.0, HEIGHT)


def prose(n, top, *, seed=0, x0=72.0, indent=0.0, last="a short closing line of the paragraph."):
    """``n`` full-width lines (x1 = 540) starting at ``top``, then a short one that ends it."""
    out = []
    for i in range(n):
        words = " ".join(WORDS[(seed + i + j) % len(WORDS)] for j in range(8))
        first = i == 0 and indent
        out.append(line(words, top + 12 * i, x0=x0 + (indent if first else 0), x1=540))
    out.append(line(last, top + 12 * n, x0=x0, x1=x0 + 220))
    return out


def structure(*pages, vocab=frozenset()):
    return structure_pages(dict(enumerate(pages)), set(vocab))


def texts(result):
    return [result[i].text for i in sorted(result)]


# ------------------------------------------------------------------------------ furniture


def numbered_pages(n, header=lambda i: f"Signal Processing Review {i + 1}"):
    return [
        page(
            line(header(i), 40, size=9),
            *prose(3, 150 + 10 * i, seed=i),
            line(str(i + 1), 760, size=9, x0=300),
        )
        for i in range(n)
    ]


def test_running_header_and_page_number_are_removed_and_recorded():
    result = structure(*numbered_pages(4))
    for i, text in enumerate(texts(result)):
        assert "Signal Processing Review" not in text
        assert str(i + 1) not in text.split()
        assert text.endswith("closing line of the paragraph.")
        assert result[i].removed == (f"Signal Processing Review {i + 1}", str(i + 1))


def test_a_header_that_comes_back_changed_by_ocr_noise_is_removed():
    headers = ["Signal Sampling", "Signal Sampllng", "Signal Sampling"]
    pages = numbered_pages(3, header=lambda i: headers[i])
    assert all("Signal" not in t for t in texts(structure(*pages)))


def test_text_that_appears_on_one_page_only_is_kept():
    pages = numbered_pages(4, header=lambda i: f"Part {'ABCD'[i] * 5} of the series")
    assert all(t.startswith("Part") for t in texts(structure(*pages)))


def test_a_title_set_larger_than_the_header_that_repeats_it_is_kept():
    title = "Signal Processing Review"
    first = page(line(title, 100, size=20), *prose(3, 200))
    rest = [page(line(title, 40, size=9), *prose(3, 150, seed=i)) for i in range(3)]
    result = structure(first, *rest)
    assert result[0].text.startswith(f"# {title}")
    assert all(title not in t for t in texts(result)[1:])


def test_roman_and_labelled_page_numbers_are_removed():
    for number in ("iv", "Page 7 of 40", "- 12 -", "[ 58 ]"):
        pages = [page(*prose(3, 150), line(number, 760, size=9, x0=300))]
        assert structure(*pages)[0].removed == (number,)


def test_numbers_in_the_first_row_of_a_table_are_not_page_numbers():
    row = [line(n, 60, size=9, x0=72 + 80 * k) for k, n in enumerate(("12", "34", "56"))]
    result = structure(page(*row, *prose(3, 150)))
    assert result[0].removed == () and "12" in result[0].text


def test_a_header_with_a_changing_title_is_removed_in_a_long_document():
    pages = [
        page(
            line(f"Section {i}.{i + 1} {WORDS[i]} {i + 20}", 40, size=9, x1=250),
            *prose(3, 150, seed=i),
        )
        for i in range(8)
    ]
    assert all(t.startswith("alpha") or t[0].islower() for t in texts(structure(*pages)))
    assert all("Section" not in t for t in texts(structure(*pages)))


def test_rotated_text_is_neither_furniture_nor_joined():
    stamp = line("arXiv:2502.18443v3 [cs.CL] 2 Jul 2025", 400, x0=20, upright=False)
    result = structure(page(stamp, *prose(2, 150)))
    assert "arXiv:2502.18443v3 [cs.CL] 2 Jul 2025" in result[0].text.split("\n\n")


# --------------------------------------------------------------------- body text near the margin


def book(n, *, first=None, last=None, header=True, footer=True, opener=None, size_first=10.0):
    """``n`` letter pages: a header row at 50, body from 90 to 700, a page number at 760.

    ``first`` and ``last`` map a page number to a text set as that page's first or last body
    line; ``opener`` maps a page number to a line at the top of a page that has no header.
    """
    first, last, opener = first or {}, last or {}, opener or {}
    pages = []
    for p in range(n):
        lines = []
        if header and p not in opener:
            lines.append(line(f"{p + 1} Signal Sampling", 50, size=9, x1=200))
        if p in opener:
            lines.append(line(opener[p], 60, size=14, bold=True))
        for k, base in enumerate(range(90, 701, 12)):
            text = first.get(p) if k == 0 else last.get(p) if base + 12 > 700 else None
            lines.append(
                line(
                    text
                    or " ".join(
                        WORDS[(p + k + j) % len(WORDS)] + str(p * 100 + k) for j in range(8)
                    ),
                    base,
                    x1=540 if not text else 72 + 6 * len(text),
                    bold=bool(text) and size_first != 10.0,
                    size=size_first if text and k == 0 else 10.0,
                )
            )
        if footer:
            lines.append(line(str(p + 1), 760, size=9, x0=300))
        pages.append(page(*lines))
    return pages


def test_short_words_at_the_first_or_last_body_row_of_a_few_pages_are_kept():
    pages = book(
        40,
        first={3: "where", 10: "where", 17: "where", 5: "Proof.", 12: "Proof.", 19: "Proof."},
        last={4: "and", 11: "and", 18: "and"},
    )
    result = structure(*pages)
    for p in (3, 10, 17, 5, 12, 19):
        assert result[p].removed == (f"{p + 1} Signal Sampling", str(p + 1))
        assert result[p].text.split("\n")[0] in ("where", "Proof.")
    for p in (4, 11, 18):
        assert result[p].text.rstrip().endswith("and")


def test_a_bold_heading_at_the_first_body_row_of_a_few_pages_is_kept():
    pages = book(40, first={7: "Exercises", 15: "Exercises", 23: "Exercises"}, size_first=12.0)
    result = structure(*pages)
    assert all("Exercises" in result[p].text for p in (7, 15, 23))
    assert all("Exercises" not in r for p in (7, 15, 23) for r in result[p].removed)


def test_a_chapter_opener_line_every_ten_pages_is_not_a_running_header():
    pages = book(40, opener={p: f"Chapter {p // 10 + 1}" for p in (0, 10, 20, 30)})
    result = structure(*pages)
    assert all(f"Chapter {p // 10 + 1}" in result[p].text for p in (0, 10, 20, 30))


def test_a_header_on_every_other_page_is_still_found_in_a_long_book():
    pages = [
        page(
            line("Signal Sampling" if p % 2 else f"Chapter {p // 10 + 1}", 50, size=9, x1=200),
            *prose(3, 150, seed=p),
        )
        for p in range(40)
    ]
    result = structure(*pages)
    assert all("Signal Sampling" not in result[p].text for p in range(1, 40, 2))
    assert all(r.startswith("Chapter") for p in range(0, 40, 2) for r in result[p].removed)


def test_a_lone_roman_numeral_letter_at_the_last_body_row_is_math_not_a_page_number():
    pages = book(6, footer=False, last=dict.fromkeys(range(6), "x"))
    assert all(not r for r in (res.removed[1:] for res in structure(*pages).values()))
    assert all(t.rstrip().endswith("x") for t in texts(structure(*pages)))


# ------------------------------------------------------------------------------ paragraphs


def test_lines_of_a_paragraph_are_joined_and_blank_lines_separate_paragraphs():
    lines = [*prose(2, 150), *prose(2, 150 + 12 * 3 + 12, seed=3)]
    out = structure(page(*lines))[0].text
    assert len(out.split("\n\n")) == 2
    assert "\n" not in out.split("\n\n")[0]
    assert out.split("\n\n")[0].endswith("a short closing line of the paragraph.")


def test_a_first_line_indent_starts_a_paragraph_without_a_gap():
    first = prose(2, 150)
    second = prose(2, 150 + 12 * 3, seed=5, indent=18)
    assert len(structure(page(*first, *second))[0].text.split("\n\n")) == 2


def test_a_short_line_that_ends_a_sentence_ends_the_paragraph():
    lines = [*prose(2, 150, last="Closing words here."), *prose(2, 150 + 36, seed=2)]
    assert len(structure(page(*lines))[0].text.split("\n\n")) == 2


def test_list_items_and_reference_entries_are_paragraphs_of_their_own():
    items = [
        line("[1] First reference entry that is long enough to wrap onto", 150, x1=540),
        line("a second line of the first entry.", 162, x1=300),
        line("[2] Second reference entry that also wraps onto another", 174, x1=540),
        line("• a bullet item", 186, x1=200),
    ]
    out = structure(page(*prose(3, 300), *items))[0].text.split("\n\n")
    assert any(p.startswith("[1] First") and p.endswith("first entry.") for p in out)
    assert "• a bullet item" in out and any(p.startswith("[2]") for p in out)


def test_table_rows_and_formulas_keep_their_line_breaks():
    rows = [line(f"{r}.5   {r}.25   {r}.75   {r}.10", 150 + 12 * r, x1=300) for r in range(4)]
    assert structure(page(*rows, *prose(2, 300)))[0].text.split("\n\n")[0] == "\n".join(
        r.text for r in rows
    )


def test_a_superscript_does_not_split_its_line():
    text = [line("x", 150, x1=80), line("2", 147, size=6, x0=80, x1=86), line("+ y", 150, x0=86)]
    assert structure(page(*text, *prose(2, 300)))[0].text.split("\n\n")[0] == "x\n2\n+ y"


def test_a_column_change_is_a_paragraph_break():
    left = [
        line(f"left column line {i} " + "word " * 8, 150 + 12 * i, x0=72, x1=290) for i in range(5)
    ]
    right = [
        line(f"right column line {i} " + "word " * 8, 150 + 12 * i, x0=320, x1=540)
        for i in range(5)
    ]
    out = structure(page(*left, *right))[0].text
    assert "line 4 word word word word word word word word right column" not in out
    assert out.count("\n\n") >= 1


def test_lines_of_chinese_text_are_joined_without_a_space():
    lines = [line("这是第一行的文字内容", 150, x1=540), line("这是第二行的文字内容。", 162, x1=300)]
    lines += prose(2, 300)
    assert structure(page(*lines))[0].text.startswith("这是第一行的文字内容这是第二行的文字内容。")


# ---------------------------------------------------------------------------- hyphenation


def hyphenated(first_end):
    return page(
        line("the sequence transduc" + first_end, 150, x1=540),
        line("tion model works on 2019-", 162, x1=540),
        line("2020 data and well-", 174, x1=540),
        line("known tasks with care.", 186, x1=300),
    )


def test_a_hyphenated_word_is_rejoined_when_it_occurs_whole_elsewhere():
    out = structure(hyphenated(HYPHEN_MARK), vocab={"transduction", "well"})[0].text
    assert "transduction model" in out
    assert "2019-2020" in out  # an identifier keeps its hyphen
    assert "well-known" in out


def test_a_real_hyphen_at_a_line_end_follows_the_same_rule():
    assert "transduction model" in structure(hyphenated("-"), vocab={"transduction"})[0].text
    assert "transduc-tion model" in structure(hyphenated("-"))[0].text


def test_the_hyphen_is_kept_when_the_joined_word_is_unknown():
    out = structure(hyphenated(HYPHEN_MARK))[0].text
    assert "transduc-tion" in out and "\x02" not in out


# ------------------------------------------------------------------------------- headings


def body_page(*extra):
    return page(*extra, *prose(4, 300), *prose(4, 400, seed=2))


def test_large_text_is_a_heading_and_the_title_is_level_one():
    out = structure(
        body_page(line("Filters and Noise", 100, size=18), line("Background work", 200, size=13))
    )[0].text.split("\n\n")
    assert out[0] == "# Filters and Noise"
    assert out[1] == "## Background work"


def test_numbered_bold_lines_get_the_level_of_their_number():
    extra = [
        line("2 Methods", 100, bold=True),
        line("2.1 Sampling", 160, bold=True),
        line("2.1.3 Detail of it", 220, bold=True),
        line("Abstract", 250, bold=True),
    ]
    out = structure(body_page(*extra))[0].text.split("\n\n")
    assert [p for p in out if p.startswith("#")] == [
        "## 2 Methods",
        "### 2.1 Sampling",
        "#### 2.1.3 Detail of it",
        "## Abstract",
    ]


def test_regular_weight_numbered_and_capital_headings():
    extra = [
        line("III. MEASURING THE NOISE FLOOR", 100),
        line("A. How the clock drifts over a day", 160),
        line("8. Conclusions", 220),
        line("1. buy the milk", 250),
    ]
    out = structure(body_page(*extra))[0].text.split("\n\n")
    assert [p for p in out if p.startswith("#")] == [
        "## III. MEASURING THE NOISE FLOOR",
        "### A. How the clock drifts over a day",
        "## 8. Conclusions",
    ]


def test_bold_lines_of_body_size_without_a_number_are_not_headings():
    out = structure(body_page(line("Maria Lopez and others", 100, bold=True)))[0].text
    assert "#" not in out


def test_captions_formulas_and_contents_entries_are_not_headings():
    extra = [
        line("Figure 2. A very large caption", 100, size=14, bold=True),
        line("E = mc and some more algebra", 130, size=14),
        line("3.1 Prime factors . . . . . . . . . . 58", 160, bold=True),
        line("3.2 Lattice points 60", 190, bold=True),
    ]
    assert "#" not in structure(body_page(*extra))[0].text


def test_a_long_run_in_one_style_is_a_paragraph_not_a_list_of_headings():
    bold = [
        line(f"abstract paragraph sentence number {i} here", 100 + 12 * i, bold=True)
        for i in range(6)
    ]
    assert "#" not in structure(body_page(*bold))[0].text


def test_a_heading_that_wraps_over_two_lines_is_one_heading():
    extra = [
        line("A very long title that", 100, size=18),
        line("wraps onto a second line", 122, size=18),
    ]
    out = structure(body_page(*extra))[0].text.split("\n\n")
    assert out[0] == "# A very long title that wraps onto a second line"


def test_a_page_set_entirely_in_large_type_has_no_headings():
    big = [
        line(f"all of this page is large print line {i}", 100 + 24 * i, size=18) for i in range(6)
    ]
    result = structure(page(*big), page(*prose(6, 150)), page(*prose(6, 150, seed=4)))
    assert "#" not in result[0].text


def test_a_running_head_with_its_page_number_on_the_first_row_is_not_a_heading():
    head = line("64 SIGNAL SAMPLING AND FILTERS", 40)
    foot = line("SIGNAL SAMPLING AND FILTERS 65", 760)
    assert "#" not in structure(body_page(head, foot))[0].text


def test_a_numbered_section_heading_at_the_top_of_a_page_keeps_its_mark():
    head = line("3 Model Architecture", 40, size=12, bold=True)
    out = structure(body_page(head))[0].text.split("\n\n")
    assert out[0] == "## 3 Model Architecture"


def test_an_author_line_is_not_a_heading_but_an_appendix_title_is():
    extra = [
        line("Maria R. Lopez, Ana Ruiz", 100, size=14, bold=True),
        line("Appendix A. Maria R. Lopez notation", 200, size=14, bold=True),
    ]
    out = structure(body_page(*extra))[0].text.split("\n\n")
    assert [p for p in out if p.startswith("#")] == ["## Appendix A. Maria R. Lopez notation"]


def test_a_long_line_of_body_size_is_not_a_heading():
    words = "8. Among the many options open to the sampler we could let it run".title()
    out = structure(body_page(line(words, 100)))[0].text
    assert "#" not in out


# -------------------------------------------------------------------------------- footnotes


def test_a_footnote_in_the_middle_of_the_stream_moves_behind_the_body():
    note = line("1 This footnote explains the claim made above it.", 700, size=7, x1=300)
    out = structure(page(*prose(2, 150), note, *prose(2, 200, seed=4)))[0].text.split("\n\n")
    assert out[-1].startswith("1 This footnote")
    assert len(out) == 3


def test_a_footnote_already_at_the_end_stays_where_it_is():
    note = line("1 This footnote explains the claim made above it.", 700, size=7, x1=300)
    out = structure(page(*prose(2, 150), *prose(2, 200, seed=4), note))[0].text.split("\n\n")
    assert out[-1].startswith("1 This footnote") and len(out) == 3


# ------------------------------------------------------------------------------- fallbacks


def test_pages_without_lines_are_left_to_the_caller():
    assert structure(PageLayout()) == {}
    result = structure(PageLayout(), page(*prose(2, 150)))
    assert set(result) == {1}
