"""domain.equations: the equation numbers a question names, where a paper writes them, which chunk
holds an equation, and the header of an added chunk. Invented text only."""

import gc
import time

import pytest

from docingest.domain.chunking import PAGE_BREAK, Chunk, chunk_pages
from docingest.domain.equations import (
    LOOK_BACK,
    MAX_REFERENCES,
    MIN_TITLE_WORDS,
    TITLE_LENGTH,
    EquationSpan,
    canonical,
    chunk_for,
    content_words,
    covers,
    equation_header,
    equation_references,
    find_equations,
    lead_sentence_start,
    title_overlap,
)

# ------------------------------------------------------------------ the question: what is read


@pytest.mark.parametrize(
    ("question", "numbers"),
    [
        ("In the paper on lamps, what does Eq. (2) state?", ["2"]),
        ("What does eq. (12) define?", ["12"]),
        ("What does EQ. (12) define?", ["12"]),
        ("What does Eq (2) say?", ["2"]),
        ("What does Eq.(2) say?", ["2"]),
        ("What does Eq. 2 say?", ["2"]),
        ("Eq. 2.", ["2"]),
        ("Eqn. (4) is what?", ["4"]),
        ("Which law is Eq.\t(4)?", ["4"]),
        ("What does equation (7) give?", ["7"]),
        ("What does Equation 7 give?", ["7"]),
        ("What does the equation (7) of the paper give?", ["7"]),
        ("What does equation number 7 give?", ["7"]),
        ("What does Equation no. 7 give?", ["7"]),
        ("How is Eq. (3a) different from Eq. (3b)?", ["3a", "3b"]),
        ("What is Eq. (A1)?", ["A1"]),
        ("What is Eq. (A.1)?", ["A.1"]),
        ("What is Eq. (A.1a)?", ["A.1a"]),
        ("What is Eq. (S5)?", ["S5"]),
        ("What is Eq. (s5)?", ["s5"]),
        ("What is Eq. (2.3)?", ["2.3"]),
        ("What is Equation S5?", ["S5"]),
        ("What is Equation 2.3?", ["2.3"]),
        ("What is Equation 3a?", ["3a"]),
        ("What are Eqs. (2) and (3)?", ["2", "3"]),
        ("What are Eqs. (2), (3) and (5)?", ["2", "3", "5"]),
        ("What are Eqs. (2), (3), and (5)?", ["2", "3", "5"]),
        ("What are Eqs. 2 and 3?", ["2", "3"]),
        ("What are Equations (2) or (3)?", ["2", "3"]),
        ("What are Eqs. (2) & (3)?", ["2", "3"]),
        ("What are Eqs. (2), Eq. (3)?", ["2", "3"]),
        ("What are Eqs. (2)-(3)?", ["2", "3"]),
        ("Eqs. (2) to (4)", ["2", "4"]),  # a range names its two ends
        ("Eqs. (2)\u2013(4)", ["2", "4"]),  # an en dash
        ("Eqs. (2)\u2014(4)", ["2", "4"]),  # an em dash
        ("Eqs. (2)--(4)", ["2", "4"]),  # LaTeX double hyphen
        ("Eq.~(2) states", ["2"]),  # a LaTeX tie
        ("Eq.\u00a0(2) states", ["2"]),  # a no-break space
        ("Eq~2 states", ["2"]),
        ("ECUACI\u00d3N (3)", ["3"]),  # upper case is fine for the whole word
        ("Ec. (3)", ["3"]),
        ("Eq. (2) of the first paper and Eq. (7) of the second", ["2", "7"]),
        ("Eq. (2) and then Eq. (2) again", ["2"]),
        ("Eq. (A.1) and Eq. (A1)", ["A.1"]),  # one equation, spelled twice
        ("Eq. (3A) and Eq. (3a)", ["3a"]),  # a capital letter after the digits is no suffix
        # Spanish
        ("En el articulo, que establece la ecuacion (2)?", ["2"]),
        ("En el articulo, que establece la ecuación (2)?", ["2"]),
        ("Que define la Ecuación 5?", ["5"]),
        ("Que dice la Ec. (4)?", ["4"]),
        ("Que dice la ec (4)?", ["4"]),
        ("Que dicen las ecuaciones (2) y (3)?", ["2", "3"]),
        ("Que dicen las Ecs. (2), (3) y (5)?", ["2", "3", "5"]),
        ("Que dicen las ecuaciones 2 o 3?", ["2", "3"]),
        ("Que define la ecuación número 6?", ["6"]),
        ("Que define la ecuación (A.2)?", ["A.2"]),
        ("Que define la ecuación (S3)?", ["S3"]),
    ],
)
def test_the_numbers_a_question_names(question, numbers):
    assert equation_references(question) == numbers


@pytest.mark.parametrize(
    "question",
    [
        "What is the equation of motion of the lamp?",
        "Which equations does the paper derive?",
        "Which equation (the first one) is used?",
        "What equation is solved?",
        "What does the paper say about equations?",
        "How many equations (2 or more) define it?",
        "How many equations (at least 3) are there?",
        "Which equation (a) is meant?",
        "Eq. (iv) of the paper",
        "In 2019 the equation 2019 was solved",
        "Equation 2D of the lamp",
        "In equation 1D chains, what is the spectrum?",
        "The equation 20 terms long",
        "The equation 3 times larger",
        "Eq. 2.3.4 is a section number",
        "Eq. (2.3.4) is a section number",
        "the eq. 3.5 GHz drive",
        "the eq. 5 MHz drive",
        "in eq. 5 mK the lamp",
        "equation 10 photons",
        "equation 12 dB gain",
        "in the eq. 3 ns pulse",
        "equation 5 % of lamps",
        "apply equation 3 veces",
        "la ecuacion 2 por ciento",
        "la ecuacion 4 pasos",
        "la ecuacion 3 terminos",
        "la ecuacion 3 t\u00e9rminos",
        "con la ecuacion 2 fotones",
        "EC (3) is the European Commission",
        "EC 2 funds it",
        "ECS (3) lamps",
        "A hypothesis of 3 qubits",
        "The Schrodinger equation for 2 qubits",
        "See Fig. (2) and Table 3 and Section 4",
        "What does figure 2 show?",
        "Which lemma (3) holds?",
        "Nonequation 3",
        "equationally (4)",
        "Que es la ecuacion de Schrodinger?",
        "Que dicen las ecuaciones del articulo?",
        "En el ec (no number)",
        "Cual es la ecuacion 2019?",
        "",
    ],
)
def test_prose_that_names_no_equation_number(question):
    assert equation_references(question) == []


def test_a_number_must_follow_the_word_at_once():
    assert equation_references("Which equation, in Section (3), is used?") == []
    assert equation_references("Section 3 has the equation. (4) is the next") == []
    assert equation_references("It has Eqs.. (4)") == []


def test_a_count_after_the_numbers_is_not_a_second_number():
    assert equation_references("What are Eqs. (2) and 3 qubits?") == ["2"]
    assert equation_references("What are Eqs. 2 and 3 terms?") == ["2"]


def test_a_bare_number_is_not_read_as_a_continuation_of_a_parenthesized_one():
    assert equation_references("Eq. (2) and 5 other things") == ["2"]


def test_at_most_four_numbers_are_read_and_the_first_four_win():
    q = "Eqs. (1), (2), (3), (4), (5), (6)"
    assert equation_references(q) == ["1", "2", "3", "4"] and MAX_REFERENCES == 4
    assert equation_references("Eq. (1), Eq. (2), Eq. (3), Eq. (4) and Eq. (5)") == [
        "1",
        "2",
        "3",
        "4",
    ]


def test_a_numbers_are_returned_as_written_and_canonical_compares_spellings():
    assert equation_references("Eq. (S5)") == ["S5"]
    assert canonical("S5") == "s5" == canonical("s5")
    assert canonical("A.1") == canonical("A1") == canonical("a-1") == "a1"
    assert canonical("2.3") == "2.3"  # a section number is not a letter prefix
    assert canonical(" 3a ") == "3a"
    assert canonical("3A") == canonical("3a")


def test_a_very_long_question_is_read_in_one_pass():
    def cpu(n):
        q = "Eq. (2) and (3) in the lamp paper, equation of lamps, " * n

        def run():
            equation_references(q)

        return best_cpu(run)

    small, big = cpu(400), cpu(1600)
    assert big < max(8 * small, 0.05)


# ------------------------------------------------------------------ the paper: where a number is


def spans(document, *numbers):
    return find_equations(document, [canonical(n) for n in numbers])


DOC = (
    "Intro words here.\n\n"
    "$$ H = \\sum_i \\sigma_i \\tag{1}$$\n\n"
    "Some prose between them.\n\n"
    "$$\\begin{aligned} a &= b \\tag{2}\\\\ c &= d \\tag{3}\\end{aligned}$$\n\n"
    "Tail."
)


def test_the_first_tag_with_a_number_is_found_with_the_start_of_its_display_equation():
    found = spans(DOC, "1")
    span = found["1"]
    assert DOC[span.tag_start : span.end] == "\\tag{1}"
    assert DOC[span.start : span.start + 2] == "$$"
    assert DOC[span.start : span.end] == "$$ H = \\sum_i \\sigma_i \\tag{1}"
    assert span.number == "1" and span.tag == "1"


def test_two_numbers_of_one_display_block_share_the_start_of_the_block():
    found = spans(DOC, "2", "3")
    assert found["2"].start == found["3"].start
    assert found["2"].tag_start < found["3"].tag_start
    assert DOC[found["3"].start : found["3"].start + 2] == "$$"


def test_a_number_the_paper_does_not_tag_is_missing_and_the_others_are_found():
    assert set(spans(DOC, "1", "9")) == {"1"}
    assert spans(DOC, "9") == {}
    assert spans(DOC) == {}
    assert spans("", "1") == {}


def test_the_spelling_of_a_tag_is_kept_and_compared_canonically():
    doc = "$$ x \\tag{A.1}$$ then $$ y \\tag*{S5}$$ and $$ z \\tag{ 3a }$$"
    found = spans(doc, "A1", "s5", "3A")
    assert found["a1"].tag == "A.1" and found["s5"].tag == "S5" and found["3a"].tag == "3a"
    assert doc[found["a1"].tag_start : found["a1"].end] == "\\tag{A.1}"


def test_a_tag_of_twelve_characters_is_found_and_one_of_thirteen_is_not():
    twelve, thirteen = "S12.3a-4.5.6", "S12.3a-4.5.67"
    assert (len(twelve), len(thirteen)) == (12, 13)
    doc = f"$$ x \\tag{{{twelve}}}$$ $$ y \\tag{{{thirteen}}}$$"
    assert spans(doc, twelve) and not spans(doc, thirteen)
    assert spans("$$ x \\tag{A.12a}$$", "A12a")["a12a"].tag == "A.12a"


def test_a_tag_that_is_not_a_number_as_written_is_not_found():
    doc = (
        "$$ x \\tag{\\ref{y}}$$ $$ y \\tag{two words}$$ $$ z \\tag{}$$ $$ w \\tag{1234567890123}$$"
    )
    assert spans(doc, "y", "two", "1234567890123") == {}


def test_a_second_tag_with_the_same_number_is_not_looked_at():
    doc = "$$ first \\tag{2}$$ ... $$ again \\tag{2}$$"
    span = spans(doc, "2")["2"]
    assert doc[span.start : span.end] == "$$ first \\tag{2}"


def test_a_second_tag_with_the_same_number_is_not_looked_at_while_another_number_is_wanted():
    doc = "$$ first \\tag{2}$$ ... $$ again \\tag{2}$$ ... $$ other \\tag{3}$$"
    found = spans(doc, "2", "3")
    assert doc[found["2"].start : found["2"].end] == "$$ first \\tag{2}"
    assert doc[found["3"].start : found["3"].end] == "$$ other \\tag{3}"


def test_without_a_dollar_pair_the_equation_starts_at_the_nearest_environment_or_at_the_tag():
    env = "text \\begin{equation} x = y \\tag{4} \\end{equation}"
    assert env[spans(env, "4")["4"].start :].startswith("\\begin{equation}")
    bracket = "text \\[ x = y \\tag{4} \\]"
    assert bracket[spans(bracket, "4")["4"].start :].startswith("\\[ x")
    bare = "text x = y \\tag{4}"
    span = spans(bare, "4")["4"]
    assert span.start == span.tag_start


def test_a_dollar_pair_that_opens_the_block_is_the_start_however_far_back():
    doc = "text\n\n$$\n" + "x + " * (LOOK_BACK // 2) + "\\tag{4}$$"  # a block of 3,000 characters
    span = spans(doc, "4")["4"]
    assert span.start == doc.index("$$") and span.end - span.start > 2 * LOOK_BACK


def test_a_block_longer_than_the_look_back_is_found_when_its_first_pair_is_the_nearest():
    doc = (
        "p $$ a $$\n\n"
        + "$$\\begin{align}\n"
        + "a &= b \\\\\n" * 400
        + "c &= d \\tag{4}\\end{align}$$"
    )
    span = spans(doc, "4")["4"]
    assert span.start == doc.index("$$\\begin{align}") and span.end - span.start > LOOK_BACK


def test_a_stray_dollar_pair_earlier_does_not_turn_the_start_into_an_inner_environment():
    # one stray $$ before makes the count of pairs before the display odd-then-even wrong; the
    # display itself has no blank line, so its opening $$ is the start, not the inner \begin{cases}
    doc = (
        "stray $$ here\n\n"
        "$$\\begin{align} x_o = \\begin{cases} 1 & a \\\\ 2 & b \\end{cases} \\tag{4}\\end{align}$$"
    )
    assert doc.count("$$") % 2 == 1  # the display looks as if it opens with a closing pair
    opener = doc.index("$$\\begin{align}")
    span = spans(doc, "4")["4"]
    assert span.start == opener
    later = (
        doc
        + "\n\nmore\n\n$$\\begin{align} y = \\begin{cases} 1 \\end{cases} \\tag{5}\\end{align}$$"
    )
    assert spans(later, "5")["5"].start == later.rindex("$$\\begin{align}")


def test_a_pair_with_a_blank_line_before_the_tag_is_a_closer_not_the_start():
    doc = "$$ x $$\n\n" + "p" * 50 + " y = z \\tag{4}"
    span = spans(doc, "4")["4"]
    assert span.start == span.tag_start


def test_the_environment_fallback_is_bounded_by_the_look_back():
    far = "$$ x $$\n\n\\begin{equation} " + "y " * LOOK_BACK + "\\tag{4}"
    assert spans(far, "4")["4"].start == far.index("\\tag")  # the \begin is too far back
    near = "$$ x $$\n\n\\begin{equation} " + "y " * 100 + "\\tag{4}"
    assert spans(near, "4")["4"].start == near.index("\\begin{equation}")
    assert find_equations(far, ["4"], look_back=len(far))["4"].start == far.index("\\begin{")


def test_a_display_after_a_closed_dollar_pair_starts_at_its_own_environment():
    doc = "$$ x $$\n\n" + "p" * 200 + "\\begin{equation} y = z \\tag{4} \\end{equation}" + "q" * 50
    span = spans(doc, "4")["4"]
    assert span.start == doc.index("\\begin{equation}")  # not the closing pair at 5


def test_a_bracket_display_after_a_closed_dollar_pair_starts_at_its_bracket():
    doc = "$$ x $$\n\n" + "p" * 50 + "\\[ y = z \\tag{4} \\]"
    assert spans(doc, "4")["4"].start == doc.index("\\[")


def test_an_environment_inside_a_dollar_pair_that_opens_leaves_the_pair_the_start():
    doc = "p $$ \\begin{aligned} a &= b \\tag{1}\\\\ c &= d \\tag{2}\\end{aligned}$$"
    assert spans(doc, "2")["2"].start == doc.index("$$")
    assert spans(doc, "1")["1"].start == doc.index("$$")


def test_a_closing_pair_with_no_blank_line_before_the_tag_is_taken_as_the_opener():
    # the safe side: too early makes the chunk say it holds the end of the equation
    doc = "$$ x $$ " + "p" * 50 + " y = z \\tag{4}"
    assert spans(doc, "4")["4"].start == doc.rindex("$$")  # the nearest one


def test_the_nearest_dollar_pair_before_the_tag_is_the_start():
    doc = "$$ a \\tag{1}$$ words $$ b \\tag{2}$$"
    span = spans(doc, "2")["2"]
    assert doc[span.start :] == "$$ b \\tag{2}$$"


def test_the_search_ends_when_every_number_is_found():
    tags = "".join(f"$$ x \\tag{{{i}}}$$ " for i in range(1, 2000))
    # the first tag decides; nothing after it is read for the results
    found = spans(tags, "1")
    assert list(found) == ["1"] and found["1"].tag_start < 20


def test_finding_equations_is_linear_in_the_size_of_the_text():
    def cpu(n):
        doc = ("$$ x_i = y_i + z_i \\tag{12}$$ words words \\tag{ \\tag{ " * n) + "$$ q \\tag{9}$$"

        def run():
            find_equations(doc, ["9", "77"])

        return best_cpu(run)

    small, big = cpu(2500), cpu(10000)
    assert big < max(8 * small, 0.05)


def test_a_text_full_of_dollar_pairs_does_not_slow_the_start_search():
    def cpu(n):
        doc = ("$$$$$$$$ " * n) + "\\tag{9}"

        def run():
            find_equations(doc, ["9"])

        return best_cpu(run)

    small, big = cpu(2500), cpu(10000)
    assert big < max(8 * small, 0.05)


# ------------------------------------------------------------------ the paper named by the question


def test_content_words_drop_stop_words_short_words_accents_and_plurals():
    assert content_words("What does the Eq. (2) of the Resonators state?") == {"resonator"}
    assert content_words("Qubits and qubit; lamps, lamp; \u00e1tomos at\u00f3mico") == {
        "qubit",
        "lamp",
        "atomo",
        "atomico",
    }
    assert content_words("El art\u00edculo sobre la ecuaci\u00f3n de las l\u00e1mparas") == {
        "lampara"
    }
    assert content_words("") == set() and content_words("of the and") == set()


def test_endings_are_folded_so_that_a_question_and_a_title_meet():
    assert content_words("comparing") == content_words("compared")
    assert content_words("characterizing") == content_words("characterization")
    assert content_words("resonators") == content_words("resonator")
    assert content_words("energies") == content_words("energy")
    assert content_words("analysis") == {"analysis"}  # an s after i or u is not a plural
    assert content_words("glass") == {"glass"}


def test_title_overlap_counts_the_distinct_content_words_in_common():
    q = "In the paper characterizing and comparing energy relaxation across many fluxonium qubits"
    assert (
        title_overlap(q, "Characterization and Comparison of Energy Relaxation in Fluxonium Qubits")
        >= 4
    )
    assert title_overlap(q, "Universally Robust Control of Open Quantum Systems") == 0
    assert title_overlap(q, None) == 0 and title_overlap(q, "") == 0
    assert title_overlap("lamp lamp lamp lamp", "Lamp") == 1  # distinct words


def test_a_spanish_question_meets_a_spanish_title_and_not_an_english_one():
    q = "\u00bfQu\u00e9 establece la ecuaci\u00f3n (2) del art\u00edculo sobre espectros cu\u00e1nticos de l\u00e1mparas y resonadores?"
    assert title_overlap(q, "Espectros cu\u00e1nticos de l\u00e1mparas y resonadores") == 4
    assert title_overlap(q, "Quantum spectra of lamps and resonators") == 0


def test_the_threshold_is_four_words():
    assert MIN_TITLE_WORDS == 4


# ------------------------------------------------------------------ the sentence before an equation


def lead(document: str, limit: int = 300) -> str:
    """The text from the sentence before the first ``$$`` of ``document`` to that ``$$``."""
    at = document.index("$$")
    return document[lead_sentence_start(document, at, limit) : at]


def test_the_sentence_before_a_display_in_the_paragraph_before_is_taken():
    doc = "Intro. We find a new thing.\n\nThe readout leads to a crosstalk error [1]:\n\n$$ x \\tag{2}$$"
    assert lead(doc) == "The readout leads to a crosstalk error [1]:\n\n"


def test_the_last_sentence_of_the_paragraph_before_is_taken_not_the_whole_paragraph():
    doc = "Alpha is large. Beta is small! The result is given by\nthe sum below:\n\n$$ x $$"
    assert lead(doc) == "The result is given by\nthe sum below:\n\n"


def test_a_sentence_in_the_same_paragraph_is_taken():
    doc = "Some words. The energy follows $$ x \\tag{2}$$"
    assert lead(doc) == "The energy follows "


def test_nothing_is_taken_when_another_display_comes_before():
    doc = "Words. $$ a = b $$\n\n$$ c = d \\tag{2}$$"
    at = doc.rindex("$$ c")
    assert lead_sentence_start(doc, at, 300) == at


def test_nothing_is_taken_when_the_sentence_is_longer_than_the_limit():
    doc = "Short. " + "word " * 100 + "ends here:\n\n$$ x $$"
    at = doc.index("$$")
    assert lead_sentence_start(doc, at, 100) == at  # no break within 100 characters
    assert lead_sentence_start(doc, at, 1000) == doc.index("word")


def test_nothing_is_taken_at_the_start_of_the_text_or_with_no_break():
    assert lead_sentence_start("$$ x $$", 0, 300) == 0
    doc = "no break at all before this:\n\n$$ x $$"
    at = doc.index("$$")
    assert lead_sentence_start(doc, at, 300) == at


def test_the_limit_is_the_largest_distance_looked_back():
    doc = "A. " + "x" * 50 + ":\n\n$$ y $$"
    at = doc.index("$$")
    assert lead_sentence_start(doc, at, 60) == doc.index("x")
    assert lead_sentence_start(doc, at, 40) == at


# ------------------------------------------------------------------ the chunk that holds it


def pages_for(*texts):
    return {i + 1: t for i, t in enumerate(texts)}


def joined(pages):
    return "".join(t + PAGE_BREAK for t in pages.values())


def filler(n):
    return ("lamp " * (n // 5 + 1))[:n]


def test_the_chunk_that_holds_the_whole_equation_is_picked():
    page = filler(300) + "\n$$ a + b \\tag{5}$$\n" + filler(600)
    pages = pages_for(page)
    chunks = chunk_pages("a" * 64, pages, chunk_chars=300, overlap=40)
    span = spans(joined(pages), "5")["5"]
    chunk = chunk_for(chunks, span)
    assert chunk is not None and covers(chunk, span)
    assert "\\tag{5}" in chunk.text and chunk.start == 260


def test_of_two_chunks_that_hold_the_tag_the_one_with_more_of_the_equation_is_picked():
    # the chunks start at 0, 260, 520; the tag sits at 270 to 277: in chunks 0 (as its last
    # characters, with the equation before it) and 1 (as its first, with none of the equation)
    equation = "$$ a_1 + a_2 + a_3 + a_4 + a_5 \\tag{5}$$"
    pages = pages_for(filler(260 - len("$$ a_1 + a_2 + a_3 + a_4 + a_5 ")) + equation + filler(500))
    chunks = chunk_pages("a" * 64, pages, chunk_chars=300, overlap=40)
    span = spans(joined(pages), "5")["5"]
    holders = [c for c in chunks if c.start <= span.tag_start and span.end <= c.start + len(c.text)]
    assert len(holders) == 2
    picked = chunk_for(chunks, span)
    assert picked is holders[0] and covers(picked, span) and not covers(holders[1], span)


def test_a_tag_cut_by_the_end_of_a_chunk_is_in_the_chunk_that_holds_it_whole():
    pages = pages_for(filler(291) + "$$ x \\tag{5}$$" + filler(400))  # the tag at 296 to 303
    chunks = chunk_pages("a" * 64, pages, chunk_chars=300, overlap=40)
    span = spans(joined(pages), "5")["5"]
    assert span.tag_start == 296 and chunks[0].text.endswith("\\tag")
    picked = chunk_for(chunks, span)
    assert picked is chunks[1] and "\\tag{5}" in picked.text


def test_two_chunks_that_hold_the_equation_equally_give_the_earlier_one():
    pages = pages_for(filler(270) + "$$\\tag{5}$$" + filler(400))  # in the overlap 260 to 300
    chunks = chunk_pages("a" * 64, pages, chunk_chars=300, overlap=40)
    span = spans(joined(pages), "5")["5"]
    holders = [c for c in chunks if covers(c, span)]
    assert len(holders) == 2 and chunk_for(chunks, span) is holders[0]


def test_a_chunk_that_cuts_the_tag_is_not_picked_even_when_it_holds_more_of_the_equation():
    # the equation starts at 200 and its tag at 296 to 303: chunk 0 (0 to 300) holds 96 characters
    # of the equation but only "\\tag" of the tag, chunk 1 (260 to 560) holds the tag whole
    expression = ("x + " * 40)[: 296 - len(filler(200)) - 3]
    pages = pages_for(filler(200) + "$$ " + expression + "\\tag{5}$$" + filler(400))
    chunks = chunk_pages("a" * 64, pages, chunk_chars=300, overlap=40)
    span = spans(joined(pages), "5")["5"]
    assert (span.start, span.tag_start) == (200, 296) and chunks[0].text.endswith("\\tag")
    assert chunk_for(chunks, span) is chunks[1]


def test_of_two_chunks_that_hold_the_tag_the_one_with_more_of_the_equation_wins_in_any_order():
    # chunks of any chunker, in any order: the equation lies at 280 to 350
    near = Chunk("a" * 64, "a pages 1-1", "x" * 400, 1, 1, start=300)  # 300 to 700: 50 of it
    most = Chunk("a" * 64, "a pages 1-1", "x" * 400, 1, 1, start=0)  # 0 to 400: all of it
    span = EquationSpan("5", "5", 280, 343, 350)
    assert chunk_for([near, most], span) is most and chunk_for([most, near], span) is most


def test_covers_is_exact_at_both_ends_of_the_equation():
    span = EquationSpan("5", "5", 100, 150, 157)
    text = "x" * 100
    assert covers(Chunk("a", "n", text, 1, 1, start=100), span) is True  # 100 to 200
    assert covers(Chunk("a", "n", "x" * 57, 1, 1, start=100), span) is True  # ends at 157 exactly
    assert covers(Chunk("a", "n", "x" * 56, 1, 1, start=100), span) is False  # one short
    assert covers(Chunk("a", "n", "x" * 57, 1, 1, start=101), span) is False  # starts one late
    assert covers(Chunk("a", "n", "x" * 58, 1, 1, start=99), span) is True


def test_no_chunk_holding_the_tag_gives_none():
    span = EquationSpan("5", "5", 10, 20, 27)
    chunk = Chunk("a" * 64, "a pages 1-1", "x" * 15, 1, 1, start=0)
    assert chunk_for([chunk], span) is None and chunk_for([], span) is None
    assert not covers(chunk, span)


def test_an_equation_longer_than_a_chunk_gets_the_chunk_with_its_end():
    pages = pages_for(filler(100) + "$$ " + "x + " * 120 + " \\tag{5}$$" + filler(300))
    chunks = chunk_pages("a" * 64, pages, chunk_chars=300, overlap=40)
    span = spans(joined(pages), "5")["5"]
    picked = chunk_for(chunks, span)
    assert picked is not None and "\\tag{5}" in picked.text and not covers(picked, span)


# ------------------------------------------------------------------ the header


def test_the_header_names_the_number_the_paper_and_its_title():
    assert (
        equation_header(["2"], "ab" * 32, "The lamp paper")
        == "Equation (2) of paper abababababababab, The lamp paper:"
    )


def test_the_header_of_several_numbers_lists_them():
    assert equation_header(["2", "3"], "c" * 64, "T").startswith("Equations (2) and (3) of paper ")
    assert equation_header(["2", "A.1", "7"], "c" * 64, "T").startswith(
        "Equations (2), (A.1) and (7) of paper "
    )


def test_the_header_has_no_title_part_when_the_paper_has_none():
    assert equation_header(["2"], "d" * 64, None) == "Equation (2) of paper dddddddddddddddd:"
    assert equation_header(["2"], "d" * 64, "  \n ") == "Equation (2) of paper dddddddddddddddd:"


def test_a_number_whose_equation_starts_before_the_chunk_is_called_its_end():
    head = equation_header(["5"], "a" * 64, "T", partial={"5"})
    assert head == (
        "The end of Equation (5) of paper aaaaaaaaaaaaaaaa, T "
        "(the start of the cut equation is in the text before this chunk):"
    )


def test_a_header_with_whole_and_cut_numbers_names_each_kind():
    head = equation_header(["2", "3", "4"], "b" * 64, None, partial={"3", "4"})
    assert head.startswith("Equation (2) and the end of Equations (3) and (4) of paper bbbb")
    assert head.endswith("this chunk):") and ", " not in head.split("paper ", 1)[1][:17]
    assert equation_header(["2", "3"], "c" * 64, "T", partial={"9"}).startswith(
        "Equations (2) and (3)"
    )


def test_a_long_title_is_cut_and_whitespace_is_one_space():
    header = equation_header(["2"], "e" * 64, "word  " * 60 + "\nend")
    title = header.split(", ", maxsplit=1)[1].removesuffix(":")
    assert len(title) == TITLE_LENGTH and title.endswith("...")
    assert "  " not in header and "\n" not in header


def test_the_header_keeps_latex_braces_of_a_title_as_they_are():
    assert "$\\ket{0}$" in equation_header(["2"], "f" * 64, "States $\\ket{0}$")


# ------------------------------------------------------------------ helpers


def best_cpu(fn):
    """Best of 3 runs in CPU time, with the cyclic collector off (see test_circuits)."""
    best = float("inf")
    collecting = gc.isenabled()
    gc.disable()
    try:
        for _ in range(3):
            t0 = time.process_time()
            fn()
            best = min(best, time.process_time() - t0)
    finally:
        if collecting:
            gc.enable()
    return best
