"""Dot leaders on contents lines: the pure function and where the pipeline applies it."""

import pytest
from fakes import (
    FakeConverter,
    FakeOcr,
    FakePage,
    ScriptedOcr,
    digital,
    make_service,
    scanned,
)

from docingest.domain.layout import PageLayout, TextLine
from docingest.domain.models import PageMethod, PageSignals, SourceKind
from docingest.domain.text import collapse_dot_leaders
from docingest.ports import Segment


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Introduction . . . . . . . 23", "Introduction 23"),
        ("Introduction ............... 23", "Introduction 23"),
        ("Introduction..........23", "Introduction 23"),
        ("Introduction . . . . 23", "Introduction 23"),  # four dots is the shortest leader
        ("Introduction .... 23", "Introduction 23"),
        ("3.1 Sorting networks . . . . . . 41", "3.1 Sorting networks 41"),
        ("1.2.3 Heaps . . . . . . . 17", "1.2.3 Heaps 17"),
        ("Chapter 3. . . . . . 12", "Chapter 3 12"),  # the dot of "3." is part of the run
        ("Preface .......... xiv", "Preface xiv"),
        ("Preface .......... XIV", "Preface XIV"),
        ("Index of terms ...... A-12", "Index of terms A-12"),
        ("Appendix …… 120", "Appendix 120"),  # ellipsis characters
        ("Appendix · · · · · 120", "Appendix 120"),  # middle dots
        ("  - Nested entry . . . . . 7", "  - Nested entry 7"),  # list indent is kept
        ("**Part I** . . . . . 1", "**Part I** 1"),
        ("Title\t.....\t9", "Title 9"),
        ("Title .....  ..... 23", "Title 23"),  # more than one blank between the dots
        ("Title . . . . .   . . . . 23", "Title 23"),
        ("Title .  .  .  .  .  23", "Title 23"),
        ("Title ......  ......", "Title"),
        ("Title .....\xa0..... 23", "Title 23"),  # a no-break space is a blank too
        ("Preface .... iii", "Preface iii"),
        ("Preface .... cccxcix", "Preface cccxcix"),
        ("Preface .... XLIV", "Preface XLIV"),
        ("A very long title that was wrapped ..........", "A very long title that was wrapped"),
        (". . . . . . 41", "41"),  # the leader line of an entry whose title wrapped
    ],
)
def test_leaders_between_an_entry_and_its_page_number_are_collapsed(line, expected):
    once = collapse_dot_leaders(line)
    assert once == expected
    assert collapse_dot_leaders(once) == once  # one pass is enough


@pytest.mark.parametrize(
    "line",
    [
        "Introduction 23",  # no leaders already
        "Wait... what do you mean?",  # ellipsis of prose
        "Wait . . . what do you mean?",  # spaced ellipsis
        "He trailed off...",
        "He trailed off....",  # an ellipsis and its full stop
        "She paused . . . .",
        "Introduction ... 23",  # three dots are an ellipsis, not a leader
        "See section 1.2.3 and 4.5.6 for details.",
        "Version 2.0.1 was released in 2019.",
        "Figure 1.2 shows ....... that",  # leaders in the middle of a sentence
        "Title . . . . . 23 and more text",  # something other than a page number follows
        "Title . . . . . 2019.",  # a sentence that ends with a full stop
        "A formula $x_1 \\ldots x_n$ holds.",
        "| Name | ..... | 23 |",  # not a contents line
        "Wait .... did",  # a word made of Roman letters is not a page number
        "Hmm .... mild",
        "She waited.... civil",
        "A drink .... mix",
        "Preface .... iiii",  # not a well formed numeral
        "Preface .... mm",  # beyond front matter
        "Wait .  .  . what",  # three dots with wide blanks
        "She paused .  .  .  .",  # four dots and nothing after: prose
        "........",  # nothing but dots
        "",
        "   ",
    ],
)
def test_prose_numbers_and_other_dots_are_left_alone(line):
    assert collapse_dot_leaders(line) == line


def test_a_contents_page_is_collapsed_line_by_line_and_the_rest_is_kept():
    page = (
        "## Contents\n"
        "\n"
        "1 Introduction . . . . . . . 1\n"
        "1.1 Motivation ............ 2\n"
        "2 Methods . . . . . . . . . . 9\n"
        "\n"
        "The text goes on... and on. See 2.1.3 for more.\n"
    )
    assert collapse_dot_leaders(page) == (
        "## Contents\n"
        "\n"
        "1 Introduction 1\n"
        "1.1 Motivation 2\n"
        "2 Methods 9\n"
        "\n"
        "The text goes on... and on. See 2.1.3 for more.\n"
    )


def test_collapsing_twice_changes_nothing_and_text_without_dots_is_returned_as_is():
    once = collapse_dot_leaders("Intro . . . . . . 3\nMethods ........ 9")
    assert collapse_dot_leaders(once) == once == "Intro 3\nMethods 9"
    text = "No dots here\nat all"
    assert collapse_dot_leaders(text) is text


# ------------------------------------------------------------------ the pipeline
CONTENTS = "1 Introduction . . . . . . . 1\n2 Methods ............ 9"
COLLAPSED = "1 Introduction 1\n2 Methods 9"


def test_ocr_pages_and_text_layer_pages_both_lose_their_leaders(src):
    layer = digital((CONTENTS + "\n") * 8)
    svc = make_service({"c.pdf": [layer, scanned()]}, ocr=FakeOcr(text=CONTENTS))
    doc = svc.ingest(src("c.pdf"))
    first, second = (p.strip() for p in svc.store.markdown(doc).split("<!-- page ")[1:])
    assert [p.method for p in doc.manifest.pages] == [PageMethod.TEXT_LAYER, PageMethod.VLM_OCR]
    assert "." * 4 not in first and ". . . ." not in first and COLLAPSED in first
    assert second.endswith("-->\n" + COLLAPSED) and ". . ." not in second
    assert doc.manifest.pages[1].n_chars == len(COLLAPSED)  # counts the text that is written
    assert doc.manifest.pages[0].n_chars == len(first.split("-->\n", 1)[1])


def test_the_model_may_or_may_not_write_leaders_and_the_page_comes_out_the_same(src):
    with_leaders = make_service({"c.pdf": [scanned()]}, ocr=ScriptedOcr(CONTENTS))
    without = make_service({"c.pdf": [scanned()]}, ocr=ScriptedOcr(COLLAPSED))
    a = with_leaders.store.markdown(with_leaders.ingest(src("c.pdf")))
    b = without.store.markdown(without.ingest(src("c.pdf")))
    assert a == b


def test_image_frames_lose_their_leaders(src):
    svc = make_service(ocr=FakeOcr(text=CONTENTS))
    doc = svc.ingest(src("contents.png"))
    assert svc.store.markdown(doc).strip().endswith("-->\n" + COLLAPSED)
    assert doc.manifest.pages[0].n_chars == len(COLLAPSED)


def test_pages_without_leaders_are_untouched_and_converters_are_not_post_processed(src):
    svc = make_service(
        {"p.pdf": [digital("Plain text of a page. Wait... what? " * 8)]},
        converters={SourceKind.TEXT: FakeConverter([Segment(CONTENTS)])},
    )
    doc = svc.ingest(src("p.pdf"))
    assert "Wait... what?" in svc.store.markdown(doc)
    note = svc.ingest(src("note.md"))
    assert ". . . ." in svc.store.markdown(note)  # converted kinds keep what the converter wrote


def test_structured_text_layer_pages_lose_their_leaders_too(src):
    def entry(text, y):
        return TextLine(text, 10.0, False, 72.0, 500.0, y)

    def contents(n):
        lines = tuple(
            entry(f"{k} Chapter number {k} . . . . . . . . . {k * 10}", 100 + 14 * k)
            for k in range(1, 9)
        )
        flat = "\n".join(ln.text for ln in lines)
        signals = PageSignals(n_chars=len("".join(flat.split())), alpha_ratio=0.9)
        return FakePage(signals, flat, layout_=PageLayout(lines, 612.0, 792.0))

    svc = make_service({"c.pdf": [contents(1), contents(2)]})
    doc = svc.ingest(src("c.pdf"))
    markdown = svc.store.markdown(doc)
    assert doc.manifest.pages[0].method == PageMethod.TEXT_LAYER
    assert "3 Chapter number 3 30" in markdown and ". . ." not in markdown
    assert doc.manifest.pages[0].n_chars == len(
        markdown.split("-->\n")[1].split("\n<!--")[0].strip()
    )


def test_a_scan_with_a_text_layer_and_the_fallback_after_rejected_ocr_both_lose_leaders(src):
    from test_scan_layer_routing import LOOP, scan_with_layer

    # Rule 5 sends the scan to OCR and the model writes leaders.
    read = make_service({"a.pdf": [scan_with_layer()]}, ocr=FakeOcr(text=CONTENTS))
    doc = read.ingest(src("a.pdf"))
    page = doc.manifest.pages[0]
    assert page.method == PageMethod.VLM_OCR
    assert read.store.markdown(doc).strip().endswith("-->\n" + COLLAPSED)
    assert page.n_chars == len(COLLAPSED)

    # The model loops, so the page falls back to its flat text layer, which has leaders.
    layer = (CONTENTS + "\n") * 12
    fallback = make_service({"b.pdf": [scan_with_layer(layer)]}, ocr=ScriptedOcr(LOOP))
    doc = fallback.ingest(src("b.pdf"))
    page = doc.manifest.pages[0]
    text = fallback.store.markdown(doc)
    assert page.method == PageMethod.TEXT_LAYER and "OCR output rejected" in page.warnings[0]
    assert COLLAPSED in text and ". . ." not in text and "......" not in text
    assert page.n_chars == len(text.split("-->\n", 1)[1].strip())


def test_every_collapsed_text_is_a_fixed_point():
    text = "\n".join(
        [
            "Intro .....  ..... 3",
            "Methods .  .  .  .  .  9",
            "Results ......  ......",
            "Wait... what . . . ?",
            "Coda ........ xiv",
        ]
    )
    once = collapse_dot_leaders(text)
    assert once.split("\n") == [
        "Intro 3",
        "Methods 9",
        "Results",
        "Wait... what . . . ?",
        "Coda xiv",
    ]
    assert collapse_dot_leaders(once) == once


def test_the_output_checks_see_the_text_as_the_model_wrote_it(src):
    """The leaders are collapsed after the checks. A 2 by 2 inch page holds about 600
    characters; ten entries with 80 dots each are far more than that as written and a
    short page once collapsed, so a collapse that ran first would accept the page."""
    from fakes import inked

    entries = "\n".join(f"Entry {k} " + "." * 80 + f" {k}" for k in range(1, 11))
    assert len(collapse_dot_leaders(entries).replace(" ", "")) < 150
    small = scanned(inked((144, 144), lines=2))
    svc = make_service({"c.pdf": [small]}, ocr=ScriptedOcr(entries))
    doc = svc.ingest(src("c.pdf"))
    page = doc.manifest.pages[0]
    assert page.n_chars == 0 and "more text than the page can hold" in page.warnings[0]
    assert "Entry 1" not in svc.store.markdown(doc)

    # The same entries on a page that does hold them are accepted and then collapsed.
    roomy = make_service({"d.pdf": [scanned()]}, ocr=ScriptedOcr(entries))
    kept = roomy.store.markdown(roomy.ingest(src("d.pdf")))
    assert "Entry 10 10" in kept and "...." not in kept
