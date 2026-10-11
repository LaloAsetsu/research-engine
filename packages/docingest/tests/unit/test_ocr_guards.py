"""Checks on OCR output: loops, text a page cannot hold, truncation, repeated pages."""

import pytest

from docingest.domain.ocr_guards import (
    OcrGuards,
    OutputProblem,
    capacity_chars,
    content_chars,
    duplicate_pages,
    find_problem,
    image_area_sq_in,
    is_looping,
    loop_share,
    page_area_sq_in,
    retry_max_tokens,
    trailing_punct_run,
)

G = OcrGuards()
LETTER = page_area_sq_in(1275, 1650, 150)  # 93.5 square inches

PROSE = (
    "A harbour town keeps its tide tables pinned beside the fish market. Each morning the "
    "clerk copies the day's high water into a ledger, and each evening the ferry master "
    "checks the ledger against the sky before deciding whether to sail. "
)


def sentences(n: int, tag: str = "") -> str:
    """n different sentences (a different set for each tag)."""
    return " ".join(
        f"Sentence {tag}number {i} says something about topic {i * 7}." for i in range(n)
    )


# ------------------------------------------------------------------------- loops
@pytest.mark.parametrize(
    "text",
    [
        "loop " * 50,  # one word, many times
        "The state collapses to zero or one. " * 7,  # a sentence, back to back
        sentences(5) + " " + "and then the model kept going " * 8,  # a loop after real text
        "a b c d e f g h i j k l " * 6,
    ],
)
def test_text_that_repeats_back_to_back_is_a_loop(text):
    assert is_looping(text, G)


@pytest.mark.parametrize(
    "text",
    [
        PROSE * 2,  # a phrase twice is not a loop
        sentences(80),  # a template with a different number each time
        "| 0 | " * 120,  # rows of zeros
        "\n".join("| 1 " + "| 0 " * 11 + "|" for _ in range(12)),  # a 12x12 identity, flattened
        "".join(f"Chapter {i} .......... {i * 9}\n" for i in range(1, 40)),  # dot leaders
        "yes " * 10,  # short unit, few repeats
        "",
    ],
)
def test_ordinary_text_is_not_a_loop(text):
    assert not is_looping(text, G)


def test_a_reference_list_that_repeats_a_venue_is_not_a_loop():
    refs = "\n".join(
        f"[{i}] Author{i} and Other{i}. Title number {i}. In Proceedings of the Conference on "
        f"Empirical Methods in Natural Language Processing, pages {i}-{i + 9}, 2020."
        for i in range(1, 30)
    )
    assert not is_looping(refs, G)


# Content that repeats by design inside a page of other content, and ends by itself.
PAGE = sentences(40)  # about 360 words of different text
REPEATED_BY_DESIGN = {
    "N/A rows": "\n".join("| N/A | N/A | N/A |" for _ in range(14)),
    "check marks": "\n".join("| x | x | x | x | x |" for _ in range(9)),
    "repeated header rows": "\n".join("| Mean (SD) | Median [IQR] |" for _ in range(6)),
    "matrix of fractions": "\n".join(" & ".join([r"\frac{1}{n}"] * 7) + r" \\\\" for _ in range(7)),
    "form lines": "\n".join("Name: ____ Signature: ____ Date: ____" for _ in range(6)),
    "questionnaire": "\n".join("( ) True ( ) False ( ) Not given" for _ in range(7)),
}


@pytest.mark.parametrize("block", REPEATED_BY_DESIGN.values(), ids=REPEATED_BY_DESIGN)
def test_content_repeated_by_design_is_not_a_loop_when_the_reply_stopped(block):
    text = f"{PAGE}\n\n{block}\n\n{sentences(5, 'b')}"
    assert is_looping(text, G)  # the repeat is there...
    assert 0 < loop_share(text, G) < G.loop_min_share  # ...but it is a small part of the page
    assert find_problem(text, "stop", LETTER, G) is None


@pytest.mark.parametrize("block", REPEATED_BY_DESIGN.values(), ids=REPEATED_BY_DESIGN)
def test_the_same_repeat_is_a_loop_when_the_reply_ran_into_the_token_limit(block):
    text = f"{PAGE}\n\n{block}"
    assert find_problem(text, "length", LETTER, G) is OutputProblem.LOOP


def test_a_repeat_that_is_most_of_the_text_is_a_loop_even_when_the_reply_stopped():
    assert (
        find_problem("and then the model kept going " * 30, "stop", LETTER, G) is OutputProblem.LOOP
    )
    assert find_problem(REPEATED_BY_DESIGN["N/A rows"], "stop", LETTER, G) is OutputProblem.LOOP


def test_a_dense_table_cut_at_the_limit_with_a_block_of_na_rows_is_truncated_not_a_loop():
    rows = [f"| Subject {i} | {i * 3} | {i * 7} | yes |" for i in range(60)]
    block = ["| N/A | N/A | N/A |"] * 14
    text = "\n".join([*rows[:30], *block, *rows[30:]])
    assert is_looping(text, G)  # the repeat is there, in the middle
    assert find_problem(text, "length", LETTER, G) is OutputProblem.TRUNCATED
    assert find_problem(text, "stop", LETTER, G) is None


def test_a_loop_cut_at_the_limit_runs_to_the_end_of_the_text():
    runaway = sentences(30) + " " + "and then the model kept going " * 8 + "and then the mod"
    assert find_problem(runaway, "length", LETTER, G) is OutputProblem.LOOP
    assert find_problem(runaway, "stop", LETTER, G) is None  # a short block, then it ended


CONTENTS = "\n".join(
    f"{n}.{m} Section title number {n * 10 + m} {'. ' * 20}{n * 40 + m * 3}"
    for n in range(1, 6)
    for m in range(1, 6)
)


@pytest.mark.parametrize("leader", [". ", ".", ". . ", "_ ", "- ", "\u2026 ", "\u00b7 "])
def test_a_dot_leader_that_runs_to_the_token_limit_is_a_loop(leader):
    # Contents page: the first entry, then the leader until max_tokens (8 thousand characters).
    runaway = "10.1.3 The circuit " + leader * 4000
    assert find_problem(runaway, "length", LETTER, G) is OutputProblem.LOOP
    assert trailing_punct_run(runaway) >= G.loop_min_punct_run


def test_a_contents_page_with_leaders_is_no_loop_even_when_cut_at_the_limit():
    assert find_problem(CONTENTS, "stop", LETTER, G) is None
    # cut after an entry: it ends with a title or a page number, not with a leader
    assert find_problem(CONTENTS, "length", LETTER, G) is OutputProblem.TRUNCATED
    # cut inside a leader: short, so not a runaway
    cut = CONTENTS + "\n6.1 Another title " + ". " * 30
    assert find_problem(cut, "length", LETTER, G) is OutputProblem.TRUNCATED
    assert find_problem(cut, "stop", LETTER, G) is None


def test_a_long_run_of_punctuation_that_the_reply_ended_by_itself_is_not_a_loop():
    assert (
        find_problem("10.1.3 The circuit " + ". " * 4000, "stop", LETTER, G)
        is not OutputProblem.LOOP
    )


def test_labelled_rows_of_the_same_answer_are_no_repeat_at_all():
    rows = "\n".join(f"| Item {i} | N/A | N/A | N/A |" for i in range(20))
    assert not is_looping(rows, G)


# ------------------------------------------------------------------------ capacity
def test_capacity_scales_with_the_page_and_has_a_floor():
    assert capacity_chars(LETTER, G) == round(150 * LETTER)
    assert capacity_chars(0.01, G) == G.min_capacity_chars  # a snippet image
    assert page_area_sq_in(1275, 1650, 150) == pytest.approx(93.5, abs=0.1)


def test_html_table_markup_and_pipes_do_not_count_as_content():
    cells = [[f"{r * 7 + c}" for c in range(12)] for r in range(50)]  # 50 x 12
    pipes = "\n".join("| " + " | ".join(row) + " |" for row in cells)
    rule = "| " + " | ".join(["---"] * 12) + " |"
    html = (
        "<table>"
        + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in cells)
        + "</table>"
    )
    plain = "".join("".join(row) for row in cells)
    assert content_chars(html) == content_chars(f"{pipes}\n{rule}") == len(plain)
    small_page = page_area_sq_in(750, 1050, 150)  # 5 x 7 in; the bound is 5250
    book_page = small_page
    assert len(html) > capacity_chars(book_page, G)  # what the old count saw
    assert find_problem(html, "stop", book_page, G) is None


def test_a_page_of_unknown_size_has_no_length_bound():
    assert find_problem("word " * 40000, "stop", None, G) is OutputProblem.LOOP  # a loop is a loop
    huge = " ".join(f"w{i}" for i in range(40000))
    assert find_problem(huge, "stop", None, G) is None
    assert find_problem(huge, "stop", 0.5, G) is OutputProblem.TOO_LONG


@pytest.mark.parametrize(
    ("dpi", "area"),
    [((150, 150), 4.0), ((300.0, 300.0), 1.0), (150, 4.0), ((150, 300), 2.0), ([150, 150], 4.0)],
)
def test_image_area_comes_from_the_images_own_resolution(dpi, area):
    assert image_area_sq_in(300, 300, dpi) == pytest.approx(area)


@pytest.mark.parametrize("dpi", [None, (1, 1), (1.0, 72), "72", (72,), 0, (0, 0)])
def test_a_missing_or_placeholder_resolution_gives_no_area(dpi):
    assert image_area_sq_in(300, 300, dpi) is None


def test_densest_real_pages_fit_and_a_runaway_does_not():
    dense_page = "x" * 8200  # 88 chars per square inch, the densest page measured
    assert find_problem(dense_page, "stop", LETTER, G) is None
    small_book_page = page_area_sq_in(900, 1350, 150)  # 6 x 9 in
    assert find_problem("x " * 12000, "stop", small_book_page, G) is OutputProblem.TOO_LONG


# ----------------------------------------------------------------------- the verdict
@pytest.mark.parametrize(
    ("text", "finish", "problem"),
    [
        (PROSE, "stop", None),
        (PROSE, None, None),
        (PROSE, "length", OutputProblem.TRUNCATED),
        ("", "stop", OutputProblem.EMPTY),
        ("  ---  ", "stop", OutputProblem.EMPTY),
        ("loop " * 100, "length", OutputProblem.LOOP),  # a loop is the worse news
        ("loop " * 100, "stop", OutputProblem.LOOP),
        ("word " * 40000, "length", OutputProblem.TOO_LONG),  # and too long the worst
    ],
)
def test_find_problem(text, finish, problem):
    assert find_problem(text, finish, LETTER, G) is problem


# --------------------------------------------------------------------- max_tokens
def test_a_dense_page_gets_more_room_up_to_a_ceiling():
    assert retry_max_tokens(4096, G) == 8192
    assert retry_max_tokens(6000, G) == 8192
    assert retry_max_tokens(8192, G) == 8192  # already at the ceiling
    assert retry_max_tokens(12000, G) == 12000  # a larger configured limit is never lowered
    assert retry_max_tokens(100, OcrGuards(retry_max_tokens_factor=1)) == 100


# ------------------------------------------------------------------------ repeats
INVENTED = (
    "Beekeeping has been a quiet craft in the valley for as long as anyone can say. A hive "
    "gives wax, honey and pollen, and the keeper learns to read the weather from the "
    "traffic at the entrance, long before the first frost arrives."
)


def test_identical_pages_are_reported_except_the_first():
    pages = {3: INVENTED, 8: INVENTED, 9: PROSE, 20: INVENTED}
    assert duplicate_pages(pages, G) == {8: 3, 20: 3}


def test_near_identical_pages_are_reported_too():
    page = sentences(40)  # a page of ~400 words; two of them differ on the other page
    changed = page.replace("number 3 says", "number 3 states").replace("topic 7.", "topic 8.")
    assert changed != page
    assert duplicate_pages({1: page, 2: changed}, G) == {2: 1}


def test_different_pages_are_left_alone_and_so_is_short_text():
    assert duplicate_pages({1: INVENTED, 2: PROSE, 3: sentences(30)}, G) == {}
    assert duplicate_pages({1: "Part IV", 2: "Part IV", 3: "Appendices"}, G) == {}
    assert duplicate_pages({}, G) == {}


def test_a_page_twice_as_long_is_not_a_repeat():
    assert duplicate_pages({1: INVENTED, 2: INVENTED + " " + PROSE + sentences(10)}, G) == {}


def test_a_page_that_is_another_page_twice_over_is_a_repeat():
    page = sentences(40)
    assert duplicate_pages({1: page, 2: page + " " + page}, G) == {2: 1}
    assert duplicate_pages({1: page + " " + page, 2: page}, G) == {2: 1}  # in either order


def test_an_incremental_slide_deck_keeps_every_slide():
    # Each slide is the previous one plus a bullet (a beamer overlay, slides scanned as images).
    bullets = [f"Bullet {i} adds a point about the topic of item {i * 3}." for i in range(1, 20)]
    sizes = (6, 8, 11, 13, 16)  # about 60 to 160 words
    slides = {n: " ".join(bullets[:size]) for n, size in enumerate(sizes, start=1)}
    assert duplicate_pages(slides, G) == {}


def test_a_chain_of_slowly_changing_pages_is_not_one_group():
    a = sentences(100)

    def reword(text: str, indexes) -> str:
        for i in indexes:
            text = text.replace(f"Sentence number {i} says", f"Phrase {i} reads", 1)
        return text

    b = reword(a, range(0, 4))
    c = reword(b, range(4, 8))
    # B repeats A (0.945) and C repeats B (0.938), but C is only 0.887 like A: not a repeat.
    assert duplicate_pages({1: a, 2: b}, G) == {2: 1}
    assert duplicate_pages({1: b, 2: c}, G) == {2: 1}
    assert duplicate_pages({1: a, 2: b, 3: c}, G) == {2: 1}


def test_many_similar_length_pages_are_compared_quickly():
    pages = {i: sentences(40).replace("Sentence", f"S{i}x") for i in range(300)}
    assert duplicate_pages(pages, G) == {}


# ---------------------------------------------------------------- the settings model
def test_guard_settings_are_validated_and_frozen():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        OcrGuards(max_attempts=0)
    with pytest.raises(ValidationError):
        OcrGuards(unknown=1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        G.max_attempts = 5  # type: ignore[misc]
