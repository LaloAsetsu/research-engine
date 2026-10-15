"""AskService with ``equation_lookup``: the chunk of an equation number the question names, added
to the retrieved chunks under a header that names the paper. Fakes only; invented papers."""

import asyncio
import gc
import time

import pytest
from fakes import FakeEmbedder, FakeQA, FixedIndex, InMemoryStore, add_doc

import docingest.application.ask as ask_module
from docingest.application.ask import (
    MAX_ADDED,
    MAX_PER_NUMBER,
    AskService,
    EquationLabel,
    EquationLookup,
    Retrieval,
    with_equation_headers,
    with_equation_lookup,
)
from docingest.application.index import IndexService, document_chunks
from docingest.bootstrap import Container
from docingest.config import AppConfig
from docingest.domain.chunking import Chunk
from docingest.domain.circuits import gate_list
from docingest.domain.text import split_pages

CHARS, OVERLAP = 300, 40  # chunks start at 0, 260, 520, 780, 1040, 1300
A, B, C, D, X = ("a" * 64, "b" * 64, "c" * 64, "d" * 64, "x" * 64)
LOOKUP = EquationLookup(CHARS, OVERLAP, sources=10)
ASKED = " lamp quantum spectra resonators"  # four content words of the invented titles

CIRCUIT = "\\begin{quantikz}\n& \\gate{H} & \\ctrl{1} & \\qw \\\\\n& \\qw & \\targ{} & \\qw\n\\end{quantikz}"
BLOCK = f"``` latex\n{CIRCUIT}\n```"
LIST = gate_list(CIRCUIT).text


def pad(n: int, letter: str = "x") -> str:
    """Invented words, exactly ``n`` characters, different for each paper."""
    return (f"{letter}lamp " * (n // 6 + 1))[:n]


def equation(letter: str, tag: str) -> str:
    return f"$$ H_{{{letter}}} = \\sum_i s_i \\tag{{{tag}}}$$"


def page(letter: str, equations: dict[int, str], end: int = 1500) -> str:
    """Words with each equation at its offset: ``{offset: tag}``. At 360 an equation is in the
    chunk that starts at 260 only, at 880 in the one at 780, at 1200 in the one at 1040."""
    out = ""
    for at, tag in sorted(equations.items()):
        out += pad(at - len(out), letter) + equation(letter, tag)
    return out + pad(end - len(out), letter)


TWO_THREE_SEVEN = {360: "2", 880: "3", 1200: "7"}
CHUNK_OF = {"2": 260, "3": 780, "7": 1040}


def make_store(papers: dict[str, dict[int, str]], store=None):
    """``papers``: id to ``{offset: tag}``. Titles are 'Lamp <letter> quantum spectra
    resonators'."""
    store = store or InMemoryStore()
    for doc_id, equations in papers.items():
        manifest = add_doc(store, doc_id, [(page(doc_id[0], equations), "Part")], name="p.txt")
        manifest.title = f"Lamp {doc_id[0]} quantum spectra resonators"
    return store


def sources(store):
    return store.corpus()[0]


def chunks_of(store, doc_id):
    """The chunks the index holds, cut from the same pages."""
    doc = next(d for d in sources(store) if d.manifest.doc_id == doc_id)
    return document_chunks(doc, split_pages(store.markdown(doc)), CHARS, OVERLAP)


def at(store, doc_id, start):
    return next(c for c in chunks_of(store, doc_id) if c.start == start)


def lookup_labels(store, question, contexts, *, sources_=10, warn=None):
    """The contexts and the labels as the service gets them (``EquationLabel`` values)."""
    return with_equation_lookup(
        contexts,
        sources(store),
        question + ASKED,
        store,
        warn or (lambda _: None),
        lookup=EquationLookup(CHARS, OVERLAP, sources_),
    )


def lookup(store, question, contexts, **kw):
    """The contexts and ``{(doc id, chunk start): numbers}``."""
    out, labels = lookup_labels(store, question, contexts, **kw)
    return out, {key: label.tags for key, label in labels.items()}


def starts(contexts):
    return [(c.doc_id[0], c.start) for c in contexts]


@pytest.fixture
def abc():
    return make_store({A: TWO_THREE_SEVEN, B: TWO_THREE_SEVEN, C: TWO_THREE_SEVEN})


# ------------------------------------------------------------------ what is added


def test_the_fixture_has_the_layout_the_tests_describe(abc):
    for letter in (A, B, C):
        for tag, start in CHUNK_OF.items():
            holding = [c.start for c in chunks_of(abc, letter) if f"\\tag{{{tag}}}" in c.text]
            assert holding == [start]
        assert [c.start for c in chunks_of(abc, letter)] == [0, 260, 520, 780, 1040, 1300]


def test_the_chunk_that_holds_the_number_of_a_paper_among_the_contexts_is_added(abc):
    contexts = [at(abc, A, 0)]
    out, labels = lookup(abc, "What does Eq. (2) state?", contexts)
    assert starts(out) == [("a", 0), ("a", 260)]
    assert out[1] == at(abc, A, 260) and "\\tag{2}" in out[1].text
    assert labels == {(A, 260): ["2"]}


def test_each_paper_among_the_contexts_gets_its_own_chunk_behind_its_best_chunk(abc):
    contexts = [at(abc, A, 0), at(abc, B, 0), at(abc, A, 520), at(abc, C, 0)]
    out, labels = lookup(abc, "Eq. (2)?", contexts)
    # C is the third paper: the number has its two chunks by then
    assert starts(out) == [("a", 0), ("a", 260), ("b", 0), ("b", 260), ("a", 520), ("c", 0)]
    assert labels == {(A, 260): ["2"], (B, 260): ["2"]}
    assert "H_{a}" in out[1].text and "H_{b}" in out[3].text  # each paper's own equation


def test_two_papers_that_both_have_the_number_give_two_different_chunks(abc):
    out, _ = lookup(abc, "Eq. (2)?", [at(abc, A, 0), at(abc, B, 0)])
    added = [c for c in out if "\\tag{2}" in c.text]
    assert [c.doc_id for c in added] == [A, B] and added[0].text != added[1].text


def test_a_paper_that_is_not_among_the_contexts_is_not_searched():
    store = make_store({A: TWO_THREE_SEVEN, D: TWO_THREE_SEVEN})
    out, labels = lookup(store, "Eq. (2)?", [at(store, A, 0)])
    assert {c.doc_id for c in out} == {A} and set(labels) == {(A, 260)}


def test_a_paper_without_the_number_does_not_use_up_a_place():
    store = make_store({X: {360: "9"}, A: TWO_THREE_SEVEN, B: TWO_THREE_SEVEN, C: TWO_THREE_SEVEN})
    contexts = [at(store, X, 0), at(store, A, 0), at(store, B, 0), at(store, C, 0)]
    out, labels = lookup(store, "Eq. (2)?", contexts)
    assert set(labels) == {(A, 260), (B, 260)}
    assert starts(out)[:3] == [("x", 0), ("a", 0), ("a", 260)]


def test_the_number_is_read_from_the_spelling_of_the_question(abc):
    store = make_store({A: {360: "A.1", 880: "3a"}, B: {360: "S5"}})
    for question, paper, start, tag in [
        ("Eq. (A1)?", A, 260, "A.1"),
        ("Eq. (a.1)?", A, 260, "A.1"),
        ("Equation 3a?", A, 780, "3a"),
        ("Eq. (S5)?", B, 260, "S5"),
    ]:
        _, labels = lookup(store, question, [at(store, paper, 0)])
        assert labels == {(paper, start): [tag]}, question


def test_a_spanish_question_finds_the_same_chunk(abc):
    out, labels = lookup(abc, "Que establece la ecuación (2) del articulo?", [at(abc, A, 0)])
    assert starts(out) == [("a", 0), ("a", 260)] and labels == {(A, 260): ["2"]}
    _, labels = lookup(abc, "Que dicen las Ecs. (2) y (3)?", [at(abc, A, 0)])
    assert labels == {(A, 260): ["2"], (A, 780): ["3"]}


def test_a_question_without_a_number_changes_nothing_and_reads_nothing():
    class Counting(InMemoryStore):
        reads = 0

        def markdown(self, doc):
            self.reads += 1
            return super().markdown(doc)

    store = make_store({A: TWO_THREE_SEVEN}, Counting())
    contexts = [at(store, A, 0)]
    store.reads = 0
    out, labels = lookup(store, "What is the equation of the lamp?", contexts)
    assert out is contexts and labels == {} and store.reads == 0


def test_a_number_the_paper_does_not_have_adds_nothing():
    store = make_store({A: TWO_THREE_SEVEN})
    contexts = [at(store, A, 0)]
    out, labels = lookup(store, "Eq. (41)?", contexts)
    assert out == contexts and labels == {}


def test_two_numbers_in_one_chunk_add_it_once_and_label_both():
    store = make_store({A: {360: "2", 420: "3"}})
    out, labels = lookup(store, "Eqs. (2) and (3)?", [at(store, A, 0)])
    assert starts(out) == [("a", 0), ("a", 260)]
    assert labels == {(A, 260): ["2", "3"]}


def test_a_retrieved_chunk_that_holds_the_whole_equation_is_labelled_not_added(abc):
    contexts = [at(abc, A, 0), at(abc, A, 260), at(abc, B, 0)]
    out, labels = lookup(abc, "Eq. (2)?", contexts)
    assert starts(out) == [("a", 0), ("a", 260), ("b", 0), ("b", 260)]
    assert labels == {(A, 260): ["2"], (B, 260): ["2"]}


def test_a_retrieved_chunk_that_holds_the_tag_and_part_of_the_equation_does_not_stand_for_it():
    # the equation lies at 240 to 285: whole in the chunk at 0, cut by the chunk at 260
    store = make_store({A: {240: "5"}})
    cut, whole = at(store, A, 260), at(store, A, 0)
    assert "\\tag{5}" in cut.text and "\\tag{5}" in whole.text
    out, labels = lookup(store, "Eq. (5)?", [cut])
    assert out == [cut, whole] and labels == {(A, 0): ["5"]}


def test_a_retrieved_chunk_of_another_cut_that_holds_the_whole_equation_is_labelled_not_added(abc):
    # a chunker with other boundaries: this chunk starts at 200 and holds the equation at 360
    document = "".join(t + "\n\n" for t in split_pages(abc.markdown(sources(abc)[0])).values())
    own = Chunk(A, "a pages 1-1", document[200:700], 1, 1, start=200)
    assert "\\tag{2}" in own.text and own.start != 260
    out, labels = lookup(abc, "Eq. (2)?", [own], sources_=10)
    assert out == [own] and labels == {(A, 200): ["2"]}


def test_a_failure_in_the_middle_of_a_paper_adds_nothing_for_it(abc, monkeypatch):
    real = ask_module.chunk_for
    calls = []

    def flaky(chunks, span):
        calls.append(span.number)
        if len(calls) == 2:
            raise RecursionError("too deep")
        return real(chunks, span)

    monkeypatch.setattr(ask_module, "chunk_for", flaky)
    warnings: list[str] = []
    contexts = [at(abc, A, 0), at(abc, B, 0)]
    out, labels = lookup(abc, "Eqs. (2) and (3)?", contexts, sources_=30, warn=warnings.append)
    # paper A found number 2 and failed on number 3: nothing from A, B is served
    assert warnings == [f"equation lookup skipped for {A[:16]}: too deep"]
    assert {k[0] for k in labels} == {B} and A not in {c.doc_id for c in out if c.start}


def test_the_chunk_cut_like_the_index_is_added_with_every_field_the_index_has():
    store = make_store({A: TWO_THREE_SEVEN})
    out, _ = lookup(store, "Eq. (3)?", [at(store, A, 0)])
    assert out[1] == at(store, A, 780)  # name, pages, is_reference, start and text


def test_the_section_title_marks_a_chunk_of_the_references_like_in_the_index():
    store = InMemoryStore()
    manifest = add_doc(
        store, A, [(pad(400, "a"), "Intro"), (f"{pad(40, 'a')}{equation('a', '2')}", "References")]
    )
    manifest.title = "Lamp a quantum spectra resonators"
    docs = sources(store)
    first = document_chunks(docs[0], split_pages(store.markdown(docs[0])), CHARS, OVERLAP)[0]
    out, _ = lookup(store, "Eq. (2)?", [first])
    assert out[-1].is_reference and not out[0].is_reference


# ------------------------------------------------------------------ the bounds


def test_at_most_two_chunks_are_added_for_one_number_the_first_two_papers():
    store = make_store(dict.fromkeys((A, B, C, D), TWO_THREE_SEVEN))
    contexts = [at(store, d, 0) for d in (C, A, D, B)]
    out, labels = lookup(store, "Eq. (2)?", contexts)
    assert MAX_PER_NUMBER == 2
    assert set(labels) == {(C, 260), (A, 260)}  # the two best ranked, not the first by name
    assert len(out) == len(contexts) + 2


def test_a_retrieved_chunk_that_holds_the_equation_counts_for_the_two_of_its_number():
    store = make_store(dict.fromkeys((A, B, C), TWO_THREE_SEVEN))
    contexts = [at(store, A, 0), at(store, B, 260), at(store, C, 0)]
    out, labels = lookup(store, "Eq. (2)?", contexts)
    assert set(labels) == {(A, 260), (B, 260)} and len(out) == len(contexts) + 1


def test_at_most_four_chunks_are_added_in_all_and_every_number_gets_its_first_before_any_second():
    store = make_store(dict.fromkeys((A, B, C), TWO_THREE_SEVEN))
    contexts = [at(store, d, 0) for d in (A, B, C)]
    out, labels = lookup(store, "Eqs. (2), (3) and (7)?", contexts, sources_=30)
    added = [c for c in out if c not in contexts]
    assert MAX_ADDED == 4 and len(added) == 4
    # the first paper answers all three numbers, then the second paper answers the first number
    assert [(c.doc_id[0], c.start) for c in added] == [
        ("a", 260),
        ("a", 780),
        ("a", 1040),
        ("b", 260),
    ]
    assert set(labels) == {(A, 260), (A, 780), (A, 1040), (B, 260)}


def test_the_bound_counts_chunks_added_and_not_numbers_asked():
    store = make_store({A: TWO_THREE_SEVEN, B: TWO_THREE_SEVEN})
    contexts = [at(store, A, 0), at(store, B, 0)]
    out, _ = lookup(store, "Eqs. (2), (3), (7) and (9)?", contexts, sources_=30)
    assert len(out) == len(contexts) + 4  # (9) tags nothing; A gives 3, B gives (2)


def test_a_fifth_number_in_the_question_is_not_looked_for():
    store = make_store({A: {360: "1", 420: "2", 480: "3", 540: "4", 600: "5"}})
    _, labels = lookup(store, "Eqs. (1), (2), (3), (4) and (5)?", [at(store, A, 0)], sources_=30)
    tags = [t for tags in labels.values() for t in tags]
    assert "5" not in tags and set(tags) == {"1", "2", "3", "4"}


# ------------------------------------------------------------------ the size of the prompt


def full_prompt(store, papers, n):
    """``n`` contexts: the lead chunk of each paper in ``papers``, then the chunks at 520 and on."""
    lead = [at(store, d, 0) for d in papers]
    rest = [c for d in papers for c in chunks_of(store, d) if c.start >= 520]
    return (lead + rest)[:n]


def test_added_chunks_replace_the_lowest_ranked_ones_when_the_prompt_is_full(abc):
    contexts = full_prompt(abc, (A, B, C), 10)
    out, _ = lookup(abc, "Eq. (2)?", contexts, sources_=10)
    assert len(out) == 10
    assert out[:3] == [contexts[0], at(abc, A, 260), contexts[1]]  # behind each paper's best
    assert at(abc, B, 260) in out
    assert contexts[-1] not in out and contexts[-2] not in out  # the two lowest are gone
    assert [c for c in out if c in contexts] == contexts[:8]  # the others keep their order


def test_the_best_ranked_chunk_is_never_replaced(abc):
    contexts = [at(abc, A, 0)]
    out, labels = lookup(abc, "Eq. (2)?", contexts, sources_=1)
    assert out == contexts and labels == {}  # no place for the pick: the one chunk stays


def test_with_one_place_to_give_only_the_first_pick_is_added(abc):
    contexts = [at(abc, A, 0), at(abc, B, 0)]
    out, labels = lookup(abc, "Eq. (2)?", contexts, sources_=2)
    assert starts(out) == [("a", 0), ("a", 260)]  # B's chunk made the place for A's pick
    assert set(labels) == {(A, 260)}  # B's pick got no place and no label


def test_a_chunk_that_gets_a_label_is_not_the_one_that_is_replaced(abc):
    contexts = [at(abc, A, 0), at(abc, B, 0), at(abc, A, 260)]
    out, labels = lookup(abc, "Eq. (2)?", contexts, sources_=3)
    assert starts(out) == [("a", 0), ("b", 260), ("a", 260)]  # B's best chunk made the place
    assert set(labels) == {(A, 260), (B, 260)}


def test_when_the_prompt_has_free_places_nothing_is_replaced(abc):
    contexts = full_prompt(abc, (A, B, C), 6)
    out, _ = lookup(abc, "Eq. (2)?", contexts, sources_=10)
    assert len(out) == 8 and all(c in out for c in contexts)


def test_only_as_many_are_replaced_as_there_are_free_places_short(abc):
    contexts = full_prompt(abc, (A, B, C), 9)
    out, _ = lookup(abc, "Eq. (2)?", contexts, sources_=10)
    assert len(out) == 10 and contexts[-1] not in out and contexts[-2] in out


def test_the_prompt_never_holds_more_chunks_than_the_setting_allows(abc):
    for n, sources_ in [(10, 10), (10, 5), (3, 3), (7, 9), (4, 20), (2, 2)]:
        contexts = full_prompt(abc, (A, B, C), n)
        out, _ = lookup(abc, "Eqs. (2), (3), (7)?", contexts, sources_=sources_)
        assert len(out) <= max(len(contexts), sources_), (n, sources_)
        assert len(out) >= len(contexts) or len(contexts) > sources_ is False


def test_only_the_papers_in_the_prompt_are_searched_and_the_rest_is_left_as_it_is(abc):
    contexts = [at(abc, A, 0), at(abc, B, 0), at(abc, C, 0), at(abc, C, 520)]
    out, labels = lookup(abc, "Eq. (2)?", contexts, sources_=2)
    # two places: B's chunk makes the place for A's pick, C is beyond the prompt
    assert set(labels) == {(A, 260)}
    assert out == [contexts[0], at(abc, A, 260), contexts[2], contexts[3]]


def test_the_new_order_puts_the_added_chunk_right_after_the_best_chunk_of_its_paper(abc):
    contexts = [at(abc, B, 0), at(abc, A, 0), at(abc, B, 520), at(abc, A, 520)]
    out, _ = lookup(abc, "Eq. (2)?", contexts, sources_=10)
    assert starts(out) == [
        ("b", 0),
        ("b", 260),
        ("a", 0),
        ("a", 260),
        ("b", 520),
        ("a", 520),
    ]


def test_a_paper_whose_only_chunk_is_replaced_keeps_its_place_in_the_ranking(abc):
    contexts = [at(abc, A, 0), at(abc, B, 0), at(abc, C, 0)]
    out, labels = lookup(abc, "Eq. (2)?", contexts, sources_=3)
    # the two lowest chunks (C's and B's) make the places; B's pick stands where B's chunk stood
    assert starts(out) == [("a", 0), ("a", 260), ("b", 260)]
    assert set(labels) == {(A, 260), (B, 260)}


# ------------------------------------------------------------------ no paper leaves the prompt


LETTERS = "abcdefghi"
NINE = {letter * 64: {360: "2"} for letter in LETTERS}


def layout(store, order: str):
    """One chunk per letter of ``order``, taken in turn from the chunks that hold no tag."""
    used: dict[str, int] = {}
    contexts = []
    for letter in order:
        doc_id = letter * 64
        k = used.get(letter, 0)
        used[letter] = k + 1
        contexts.append([c for c in chunks_of(store, doc_id) if c.start != 260][k])
    return contexts


def test_a_paper_with_a_single_chunk_keeps_it_while_another_chunk_can_give_the_place():
    # the gold paper (g) has its only chunk at the ninth place and papers ahead of it have more
    store = make_store(NINE)
    contexts = layout(store, "abcbdebfgh")
    out, labels = lookup(store, "In the tutorial, what does Eq. (2) state?", contexts)
    assert {c.doc_id for c in out} == {c.doc_id for c in contexts}  # no paper is gone
    assert len(out) == 10 and set(labels) == {(A, 260), (B, 260)}
    assert [c.doc_id[0] for c in out].count("b") == 2  # b0 and b's equation: two b chunks went
    assert contexts[6] not in out and contexts[3] not in out


def test_the_place_is_taken_from_a_paper_with_another_chunk_before_a_lower_ranked_single():
    store = make_store({d: {360: "2"} for d in (A, B, C, D)})
    contexts = [
        at(store, A, 0),
        at(store, B, 0),
        at(store, C, 0),
        at(store, C, 520),
        at(store, D, 0),
    ]
    out, _ = lookup(store, "Eq. (2)?", contexts, sources_=5)
    # c's second chunk goes first; then nothing is safe and the lowest ranked, d's, goes
    assert starts(out) == [("a", 0), ("a", 260), ("b", 0), ("b", 260), ("c", 0)]


def test_when_every_chunk_is_the_only_one_of_its_paper_the_lowest_ranked_still_gives_the_place():
    store = make_store(NINE)
    contexts = layout(store, "abcdefghi")
    out, _ = lookup(store, "Eq. (2)?", contexts, sources_=9)
    assert len(out) == 9 and [c.doc_id[0] for c in out][:4] == ["a", "a", "b", "b"]
    assert contexts[-1] not in out and contexts[-2] not in out


def test_a_second_chunk_of_a_paper_is_not_given_up_after_the_first_one_was():
    # b has two chunks, both low: only one of them may go, the other keeps b in the prompt
    store = make_store(NINE)
    contexts = layout(store, "acdebbgh")
    out, _ = lookup(store, "Eqs. (2)?", contexts, sources_=8)
    assert "b" in {c.doc_id[0] for c in out}


# ------------------------------------------------------------------ an index older than the Markdown


def shifted(store, doc_id, by):
    """The paper re-ingested with ``by`` more characters before its first words, the index not."""
    manifest, markdown = store.canonical[doc_id]
    marker = "-->\n"
    at_ = markdown.index(marker) + len(marker)
    store.canonical[doc_id] = (manifest, markdown[:at_] + "q" * by + markdown[at_:])


def test_a_retrieved_chunk_whose_text_is_no_longer_the_papers_is_not_the_equation():
    store = make_store({A: {360: "2"}})
    old = at(store, A, 260)  # holds the equation at 360 to 391 as the index saw it
    lead = chunks_of(store, A)[0]
    shifted(store, A, 100)  # the equation is now at 460 to 491, still inside 260 to 560
    out, labels = lookup(store, "Eq. (2)?", [lead, old])
    fresh = [c for c in out if c.start == 260]
    assert len(fresh) == 1 and fresh[0] != old  # the old chunk was replaced, not labelled
    assert set(labels) == {(A, 260)} and "\\tag{2}" in fresh[0].text
    document = "".join(t + "\n\n" for t in split_pages(store.markdown(sources(store)[0])).values())
    assert document[260 : 260 + len(fresh[0].text)] == fresh[0].text


def test_a_stale_chunk_that_does_not_reach_the_new_equation_is_left_alone_and_one_is_added():
    store = make_store({A: {360: "2"}})
    old, lead = at(store, A, 260), at(store, A, 0)
    shifted(store, A, 400)  # the equation is now at 760 to 791: nothing of the old chunk
    out, labels = lookup(store, "Eq. (2)?", [lead, old])
    assert old in out and old.start not in {k[1] for k in labels}
    added = [c for c in out if (c.doc_id, c.start) in labels]
    assert len(added) == 1 and "\\tag{2}" in added[0].text and added[0] != old


def test_the_header_goes_on_the_new_chunk_and_never_on_the_stale_one():
    store = make_store({A: {360: "2"}})
    old, lead = at(store, A, 260), chunks_of(store, A)[0]
    shifted(store, A, 100)
    out, labels = lookup_labels(store, "Eq. (2)?", [lead, old])
    final = with_equation_headers(out, labels, sources(store))
    assert old.text not in "".join(c.text for c in final)
    assert sum(c.text.startswith("Equation (2) of paper") for c in final) == 1


# ------------------------------------------------------------------ an equation cut by a chunk boundary


def long_paper(prefix: int, words: int):
    """A paper with one equation, Eq. (5), that starts at ``prefix`` and has ``words`` terms."""
    equation = "$$ " + "x + " * words + "y \\tag{5}$$"
    text = pad(prefix, "a") + equation + pad(1500 - prefix - len(equation), "a")
    store = InMemoryStore()
    manifest = add_doc(store, A, [(text, "Part")], name="p.txt")
    manifest.title = "Lamp a quantum spectra resonators"
    return store, text


def test_an_equation_cut_by_a_boundary_is_extended_back_to_its_start():
    store, _ = long_paper(200, 30)  # 200 to 331: the tag is whole only in the chunk at 260
    index_chunk = at(store, A, 260)
    assert "\\tag{5}" in index_chunk.text and "$$ x" not in index_chunk.text
    out, labels = lookup_labels(store, "Eq. (5)?", [at(store, A, 0)])
    added = out[1]
    document = "".join(t + "\n\n" for t in split_pages(store.markdown(sources(store)[0])).values())
    assert added.start == 200 and added.text.startswith("$$ x + ")
    assert added.text == document[200:560]  # from the start of the equation to the chunk's end
    assert labels[(A, 200)].tags == ["5"] and not labels[(A, 200)].partial


def test_the_extended_chunk_takes_the_page_where_it_now_starts():
    store = InMemoryStore()
    first = pad(200, "a") + "$$ " + "x + " * 20  # the equation starts on page 1 ...
    second = "x + " * 20 + "y \\tag{5}$$" + pad(500, "a")  # ... and ends on page 2
    add_doc(
        store, A, [(first, "Part"), (second, "Part")], name="p.txt"
    ).title = "Lamp a quantum spectra resonators"
    chunk = next(c for c in chunks_of(store, A) if "\\tag{5}" in c.text and "$$ x" not in c.text)
    out, _ = lookup_labels(store, "Eq. (5)?", [chunks_of(store, A)[0]])
    added = out[1]
    assert (chunk.first_page, added.first_page) == (2, 1)
    assert added.last_page == chunk.last_page
    assert added.name == f"{A[:16]} pages 1-{chunk.last_page}"


def test_an_equation_that_starts_too_far_back_is_not_extended_and_is_called_the_end():
    store, _ = long_paper(100, 300)  # about 1,200 characters, from 100
    out, labels = lookup_labels(store, "Eq. (5)?", [at(store, A, 0)])
    added = out[1]
    assert added.start == 1040 and "$$ x" not in added.text  # the index chunk, as it is
    assert labels[(A, 1040)].tags == ["5"] and labels[(A, 1040)].partial == {"5"}
    [chunk] = with_equation_headers([added], labels, sources(store))
    assert chunk.text.startswith(
        f"The end of Equation (5) of paper {A[:16]}, Lamp a quantum spectra resonators (the start of the cut equation is "
        "in the text before this chunk):\n\n"
    )


def test_the_extension_is_bounded_by_the_chunk_size():
    store, _ = long_paper(200, 30)
    small = EquationLookup(40, 4, 10)  # a gap of 60 is more than the 40 characters of a chunk
    out, labels = with_equation_lookup(
        [at(store, A, 0)], sources(store), "Eq. (5)?" + ASKED, store, lambda _: None, lookup=small
    )
    added = [c for c in out if (c.doc_id, c.start) in labels]
    assert added and all("$$ x" not in c.text for c in added)
    assert all(label.partial == {"5"} for label in labels.values())


def test_a_retrieved_chunk_with_only_the_end_gets_the_end_header_not_the_whole_one():
    store, _ = long_paper(100, 300)
    end_chunk = lookup_labels(store, "Eq. (5)?", [at(store, A, 0)])[0][1]
    out, labels = lookup_labels(store, "Eq. (5)?", [at(store, A, 0), end_chunk])
    assert out == [at(store, A, 0), end_chunk]  # nothing added: the same chunk is in place
    assert labels[(A, end_chunk.start)].partial == {"5"}


def test_a_retrieved_chunk_with_the_end_does_not_stand_for_an_equation_that_can_be_extended():
    store, _ = long_paper(200, 30)
    end_chunk = at(store, A, 260)
    out, labels = lookup_labels(store, "Eq. (5)?", [at(store, A, 0), end_chunk])
    assert end_chunk in out and any(c.start == 200 for c in out)  # the whole one is added
    assert (A, 260) not in labels and labels[(A, 200)].tags == ["5"]


def test_the_header_of_a_whole_equation_is_unchanged(abc):
    out, labels = lookup_labels(abc, "Eq. (2)?", [at(abc, A, 0)])
    [chunk] = with_equation_headers([out[1]], labels, sources(abc))
    assert chunk.text.startswith(
        f"Equation (2) of paper {A[:16]}, Lamp a quantum spectra resonators:\n\n"
    )


# ------------------------------------------------------------------ the paper the question names


def titled(store, doc_id, title):
    """Change the title of a stored paper (None: no title)."""
    store.canonical[doc_id][0].title = title


def test_a_paper_whose_title_shares_four_words_with_the_question_gives_its_equation_and_one_that_shares_less_gives_nothing():
    store = make_store({A: {360: "2"}, B: {360: "2"}})
    titled(store, B, "Graph spectra and the Laplacian of a network")  # no word in common
    contexts = [at(store, A, 0), at(store, B, 0)]
    out, labels = lookup(
        store, "In the paper on lamp quantum spectra, what does Eq. (2) state?", contexts
    )
    assert set(labels) == {(A, 260)} and starts(out) == [("a", 0), ("a", 260), ("b", 0)]


def test_three_shared_words_are_not_enough_and_four_are():
    store = make_store({A: {360: "2"}})
    for title, added in [
        ("Lamp quantum spectra", False),  # three words
        ("Lamp quantum spectra resonators", True),  # four words
        ("Lamp quantum spectra resonators of a lattice", True),
        ("Lamps, Quantum; SPECTRA: resonator", True),  # case, plurals and punctuation do not matter
        ("Quantum spectra resonators", False),
    ]:
        titled(store, A, title)
        question = "What does Eq. (2) state in the paper on the lamp quantum spectra of resonators?"
        out, labels = with_equation_lookup(
            [at(store, A, 0)], sources(store), question, store, lambda _: None, lookup=LOOKUP
        )
        assert bool(labels) is added, title
        assert (len(out) == 2) is added


def test_when_no_paper_passes_nothing_is_added_nothing_is_read_and_the_prompt_is_as_it_was():
    store = make_store({d: {360: "2"} for d in (A, B)}, CountingStore())
    for d in (A, B):
        titled(store, d, "Graph spectra of a network")
    contexts = [at(store, A, 0), at(store, B, 0), at(store, A, 520)]
    store.reads.clear()
    out, labels = lookup(
        store, "In the paper on lamp quantum spectra, what does Eq. (2) state?", contexts
    )
    assert out == contexts and labels == {} and store.reads == []


def test_a_retrieved_chunk_of_a_paper_that_does_not_pass_gets_no_header():
    store = make_store({A: {360: "2"}, B: {360: "2"}})
    titled(store, B, "Graph spectra of a network")
    contexts = [at(store, A, 0), at(store, B, 260)]  # b's chunk holds its Eq. (2)
    out, labels = lookup(store, "Eq. (2)?", contexts)
    assert (B, 260) not in labels and at(store, B, 260) in out


def test_a_paper_without_a_title_does_not_pass_and_the_metadata_title_does():
    from docingest.domain.models import SourceMetadata

    store = make_store({A: {360: "2"}})
    titled(store, A, None)
    contexts = [at(store, A, 0)]
    assert lookup(store, "Eq. (2)?", contexts)[1] == {}
    store.canonical[A][0].metadata = SourceMetadata(title="Lamp quantum spectra resonators")
    assert set(lookup(store, "Eq. (2)?", contexts)[1]) == {(A, 260)}


def test_the_gate_looks_at_the_papers_in_order_and_a_failing_one_does_not_use_up_a_place():
    store = make_store({d: {360: "2"} for d in (X, A, B, C)}, CountingStore())
    titled(store, X, "Graph spectra of a network")
    contexts = [at(store, d, 0) for d in (X, A, B, C)]
    store.reads.clear()
    _, labels = lookup(store, "Eq. (2)?", contexts)
    assert set(labels) == {(A, 260), (B, 260)} and store.reads == [A, B]  # x is not even read


def test_a_spanish_question_passes_for_a_spanish_title_and_not_for_an_english_one():
    store = make_store({A: {360: "2"}, B: {360: "2"}})
    titled(store, A, "Espectros cu\u00e1nticos de l\u00e1mparas y resonadores")
    titled(store, B, "Quantum spectra of lamps and resonators")
    contexts = [at(store, A, 0), at(store, B, 0)]
    question = (
        "\u00bfQu\u00e9 establece la ecuaci\u00f3n (2) del art\u00edculo sobre espectros "
        "cu\u00e1nticos de l\u00e1mparas y resonadores?"
    )
    out, labels = with_equation_lookup(
        contexts, sources(store), question, store, lambda _: None, lookup=LOOKUP
    )
    assert set(labels) == {(A, 260)} and len(out) == 3


def test_the_service_gives_the_answerer_the_chunks_as_retrieved_when_no_title_matches():
    store, qa, svc = build({A: TWO_THREE_SEVEN}, [(A, 0), (A, 520)])
    titled(store, A, "Graph spectra of a network")
    asyncio.run(svc.ask("What does Eq. (2) state in the paper on lamps?", lambda _: None))
    assert qa.contexts == [at(store, A, 0), at(store, A, 520)]


# ------------------------------------------------------------------ the sentence before the equation


LEAD = (
    "The readout crosstalk error is the quantity given by the relation below, which also sets the "
    "scale of the leading correction in the regime of weak driving of the coupled resonators:"
)


def lead_paper(longer: int = 0):
    """A paper whose Eq. (5) comes after a lead sentence that starts before the chunk (at 260)
    that holds the equation; ``longer`` more words make the sentence longer than a chunk."""
    sentence = LEAD + " and so on" * longer
    text = (
        pad(100, "a") + ". " + sentence + "\n\n$$ " + "x + " * 20 + "y \\tag{5}$$" + pad(900, "a")
    )
    store = InMemoryStore()
    add_doc(store, A, [(text, "Part")], name="p.txt").title = "Lamp a quantum spectra resonators"
    return store, text


def covering_chunk(store, text):
    eq_at = text.index("$$")
    return next(c for c in chunks_of(store, A) if c.start <= eq_at and "\\tag{5}" in c.text)


def test_the_added_chunk_starts_at_the_sentence_before_the_equation_when_that_is_near():
    store, text = lead_paper()
    lead_at, covering = text.index(LEAD), covering_chunk(store, text)
    assert covering.start > lead_at and covering.start - lead_at <= CHARS  # it starts after it
    out, labels = lookup_labels(store, "Eq. (5)?", [chunks_of(store, A)[0]])
    added = out[1]
    assert added.start == lead_at and added.text.startswith(LEAD + "\n\n$$ x + ")
    assert added.text.endswith(covering.text[-30:]) and labels[(A, lead_at)].tags == ["5"]
    assert not labels[(A, lead_at)].partial


def test_a_lead_sentence_longer_than_the_chunk_size_is_left_out():
    store, text = lead_paper(longer=40)  # a sentence of about 600 characters
    out, _ = lookup_labels(store, "Eq. (5)?", [chunks_of(store, A)[0]])
    added = out[1]
    assert (
        "readout crosstalk" not in added.text and added.start == covering_chunk(store, text).start
    )


def test_a_lead_sentence_longer_than_the_chunk_size_is_left_out_even_when_it_starts_near():
    # the sentence starts 270 characters before the chunk at 520 but is 400 characters long
    sentence = "word " * 79 + "end:"
    text = pad(248, "a") + ". " + sentence + "\n\n$$ " + "x + " * 5 + "y \\tag{5}$$" + pad(900, "a")
    store = InMemoryStore()
    add_doc(store, A, [(text, "Part")], name="p.txt").title = "Lamp a quantum spectra resonators"
    assert covering_chunk(store, text).start == 520 and text.index("word") == 250
    out, _ = lookup_labels(store, "Eq. (5)?", [chunks_of(store, A)[0]])
    assert out[1].start == 520  # the chunk as the index has it, not started at the sentence


def test_the_lead_sentence_is_in_the_chunk_the_service_gives_with_its_header():
    store, _ = lead_paper()
    _, qa, svc = build({}, [(A, 0)], store=store)
    ask(svc, "What does Eq. (5) state?")
    added = (qa.contexts or [])[1]
    head = f"Equation (5) of paper {A[:16]}, Lamp a quantum spectra resonators:"
    assert added.text.startswith(f"{head}\n\n{LEAD}\n\n$$ x + ")


def test_the_equation_start_is_used_when_the_lead_is_too_far_but_the_equation_is_not():
    # chunks start at 0, 260, 520: the equation is cut by the boundary at 520, its lead sentence
    # of 290 characters starts more than a chunk before it
    lead = "word " * 58 + "end:"
    equation = "$$ " + "x + " * 36 + "y \\tag{5}$$"
    text = pad(150, "a") + ". " + lead + "\n\n" + equation + pad(900, "a")
    eq_at = text.index("$$")
    store = InMemoryStore()
    add_doc(store, A, [(text, "Part")], name="p.txt").title = "Lamp a quantum spectra resonators"
    tag_at = text.index("\\tag")
    holder = next(
        c for c in chunks_of(store, A) if c.start <= tag_at and tag_at + 7 <= c.start + len(c.text)
    )
    assert holder.start > eq_at and holder.start - eq_at < CHARS
    assert holder.start - text.index("word") > CHARS
    out, _ = lookup_labels(store, "Eq. (5)?", [chunks_of(store, A)[0]])
    assert out[1].start == eq_at and out[1].text.startswith("$$ x")


def test_a_retrieved_chunk_that_holds_the_equation_is_not_given_a_lead_sentence():
    store, text = lead_paper()
    covering = covering_chunk(store, text)
    out, labels = lookup_labels(store, "Eq. (5)?", [chunks_of(store, A)[0], covering])
    assert out == [chunks_of(store, A)[0], covering] and set(labels) == {(A, covering.start)}


# ------------------------------------------------------------------ what is read


class CountingStore(InMemoryStore):
    def __init__(self):
        super().__init__()
        self.reads: list[str] = []

    def markdown(self, doc):
        self.reads.append(doc.manifest.doc_id)
        return super().markdown(doc)


def test_the_markdown_of_a_paper_is_read_once_however_many_numbers_are_asked():
    store = make_store(
        {A: TWO_THREE_SEVEN, B: TWO_THREE_SEVEN, C: TWO_THREE_SEVEN}, CountingStore()
    )
    contexts = [at(store, d, 0) for d in (A, B, C)]
    store.reads.clear()
    lookup(store, "Eqs. (2), (3) and (7)?", contexts, sources_=30)
    assert store.reads == [A, B]  # C is never needed: the places are taken


def test_a_paper_is_read_only_while_some_number_still_needs_it():
    store = make_store(dict.fromkeys((A, B, C, D), TWO_THREE_SEVEN), CountingStore())
    contexts = [at(store, d, 0) for d in (A, B, C, D)]
    store.reads.clear()
    lookup(store, "Eq. (2)?", contexts)
    assert store.reads == [A, B]


def test_papers_are_read_in_the_order_of_their_best_chunk_until_two_have_the_number():
    store = make_store(
        {X: {}, A: TWO_THREE_SEVEN, B: TWO_THREE_SEVEN, C: TWO_THREE_SEVEN}, CountingStore()
    )
    contexts = [at(store, d, 0) for d in (X, A, B, C)]
    store.reads.clear()
    lookup(store, "Eq. (2)?", contexts)
    assert store.reads == [X, A, B]


# ------------------------------------------------------------------ a failure never stops an answer


def test_a_markdown_that_cannot_be_read_adds_nothing_for_that_paper_and_warns_once(abc):
    contexts = [at(abc, A, 0), at(abc, B, 0), at(abc, C, 0)]
    real = abc.markdown

    def markdown(doc):
        if doc.manifest.doc_id == A:
            raise OSError("document.md is missing")
        return real(doc)

    abc.markdown = markdown  # type: ignore[method-assign]
    warnings: list[str] = []
    out, labels = lookup(abc, "Eqs. (2) and (3)?", contexts, sources_=30, warn=warnings.append)
    assert warnings == [f"equation lookup skipped for {A[:16]}: document.md is missing"]
    assert set(labels) == {(B, 260), (B, 780), (C, 260), (C, 780)} and A not in {
        c.doc_id for c in out if c.start != 0
    }
    assert contexts[0] in out


def test_when_every_paper_fails_the_contexts_come_back_as_they_were_with_one_warning_each(abc):
    contexts = [at(abc, d, 0) for d in (A, B)]

    def broken(doc):
        raise OSError("gone")

    abc.markdown = broken  # type: ignore[method-assign]
    warnings: list[str] = []
    out, labels = lookup(abc, "Eqs. (2) and (3)?", contexts, warn=warnings.append)
    assert out == contexts and labels == {}
    assert len(warnings) == 2 and {w.split(":")[0] for w in warnings} == {
        f"equation lookup skipped for {A[:16]}",
        f"equation lookup skipped for {B[:16]}",
    }


def test_a_chunker_that_fails_is_a_failure_of_that_paper_too(abc, monkeypatch):
    def broken(*_):
        raise RecursionError("too deep")

    monkeypatch.setattr(ask_module, "document_chunks", broken)
    warnings: list[str] = []
    contexts = [at(abc, A, 0)]
    out, labels = lookup(abc, "Eq. (2)?", contexts, warn=warnings.append)
    assert out == contexts and labels == {}
    assert warnings == [f"equation lookup skipped for {A[:16]}: too deep"]


def test_a_document_that_changed_since_it_was_indexed_loses_a_number_that_is_gone():
    store = make_store({A: TWO_THREE_SEVEN})
    contexts = [at(store, A, 0)]
    manifest, markdown = store.canonical[A]
    store.canonical[A] = (manifest, markdown.replace("\\tag{2}", "\\tag{22}"))
    warnings: list[str] = []
    out, labels = lookup(store, "Eq. (2)?", contexts, warn=warnings.append)
    assert out == contexts and labels == {} and warnings == []


# ------------------------------------------------------------------ the headers


def test_a_header_goes_before_the_text_of_a_labelled_chunk_and_nothing_else_changes(abc):
    chunk = at(abc, A, 260)
    other = at(abc, A, 0)
    out = with_equation_headers([other, chunk], {(A, 260): EquationLabel(["2"])}, sources(abc))
    assert out[0] is other
    head = f"Equation (2) of paper {A[:16]}, Lamp a quantum spectra resonators:"
    assert out[1].text == f"{head}\n\n{chunk.text}"
    same = ("doc_id", "name", "first_page", "last_page", "is_reference", "start")
    assert all(getattr(out[1], f) == getattr(chunk, f) for f in same)


def test_the_header_names_every_number_of_the_chunk(abc):
    out = with_equation_headers(
        [at(abc, A, 260)], {(A, 260): EquationLabel(["2", "3"])}, sources(abc)
    )
    assert out[0].text.startswith(
        f"Equations (2) and (3) of paper {A[:16]}, Lamp a quantum spectra resonators:\n\n"
    )


def test_a_paper_with_no_title_is_named_by_its_file(abc):
    abc.canonical[A][0].title = None
    out = with_equation_headers([at(abc, A, 260)], {(A, 260): EquationLabel(["2"])}, sources(abc))
    assert out[0].text.startswith(f"Equation (2) of paper {A[:16]}, p.txt:\n\n")


def test_a_chunk_of_a_paper_that_is_not_a_source_gets_no_header(abc):
    chunk = at(abc, A, 260)
    assert with_equation_headers([chunk], {(A, 260): EquationLabel(["2"])}, []) == [chunk]


# ------------------------------------------------------------------ the whole service


def build(papers, picks, *, lookup_=LOOKUP, circuit_lists=False, contexts=10, store=None):
    """A service over ``papers``; the index returns the chunks ``picks`` (``[(paper, start)]``)."""
    store = make_store(papers, store)
    embedder = FakeEmbedder()
    index = FixedIndex(embedder_fingerprint=embedder.fingerprint)
    IndexService(
        store=store,
        embedder=embedder,
        index=index,
        chunk_chars=CHARS,
        overlap=OVERLAP,
        log=lambda _: None,
    ).build()
    index.answer = [at(store, d, s) for d, s in picks]
    qa = FakeQA()
    retrieval = Retrieval(embedder, index, None, 100, contexts)
    svc = AskService(
        store=store,
        qa=qa,
        retrieval=lambda: retrieval,
        circuit_lists=circuit_lists,
        equation_lookup=lookup_,
    )
    return store, qa, svc


def ask(svc, question, warn=lambda _: None, **kw):
    return asyncio.run(svc.ask(question + ASKED, warn, **kw))


def test_the_answerer_gets_the_equation_chunk_with_its_header_and_the_rest_as_retrieved():
    store, qa, svc = build({A: TWO_THREE_SEVEN, B: TWO_THREE_SEVEN}, [(A, 0), (B, 0), (A, 520)])
    ask(svc, "In the lamp paper, what does Eq. (2) state?")
    assert qa.contexts is not None
    assert starts(qa.contexts) == [("a", 0), ("a", 260), ("b", 0), ("b", 260), ("a", 520)]
    added = qa.contexts[1]
    assert (
        added.text
        == f"Equation (2) of paper {A[:16]}, Lamp a quantum spectra resonators:\n\n{at(store, A, 260).text}"
    )
    assert qa.contexts[3].text.startswith(
        f"Equation (2) of paper {B[:16]}, Lamp b quantum spectra resonators:\n\n"
    )
    assert [qa.contexts[i] for i in (0, 2, 4)] == [
        at(store, A, 0),
        at(store, B, 0),
        at(store, A, 520),
    ]


def test_the_lookup_is_off_without_the_setting():
    _, qa, svc = build({A: TWO_THREE_SEVEN}, [(A, 0), (A, 520)], lookup_=None)
    ask(svc, "What does Eq. (2) state?")
    assert starts(qa.contexts or []) == [("a", 0), ("a", 520)]
    assert AskService(store=InMemoryStore(), qa=FakeQA())._equation_lookup is None


def test_a_question_without_a_number_gets_the_chunks_as_retrieved():
    store, qa, svc = build({A: TWO_THREE_SEVEN}, [(A, 0), (A, 520)])
    ask(svc, "What do the lamps state?")
    assert qa.contexts == [at(store, A, 0), at(store, A, 520)]


def test_a_question_that_does_not_use_the_index_is_not_touched():
    _, qa, svc = build({A: TWO_THREE_SEVEN}, [(A, 0)])
    ask(svc, "What does Eq. (2) state?", use_index=False)
    assert qa.contexts is None and not any("Equation (2)" in md for md in qa.markdown)


def test_the_answerer_gets_the_manifests_of_the_papers_it_reads_and_no_more():
    _, qa, svc = build(
        {A: TWO_THREE_SEVEN, B: TWO_THREE_SEVEN},
        [(A, 0), (B, 0)],
        lookup_=EquationLookup(CHARS, OVERLAP, sources=2),
    )
    ask(svc, "What does Eq. (2) state?")
    # two places: B's only chunk made the place for A's pick, so B is not among the papers now
    assert starts(qa.contexts or []) == [("a", 0), ("a", 260)]
    assert qa.seen == ["p.txt"]


def test_a_failing_lookup_still_gives_the_answer_the_chunks_and_a_warning():
    store, qa, svc = build({A: TWO_THREE_SEVEN}, [(A, 0), (A, 520)])

    def broken(doc):
        raise OSError("no document.md")

    store.markdown = broken  # type: ignore[method-assign]
    warnings: list[str] = []
    answer = ask(svc, "What does Eq. (2) state?", warnings.append)
    assert answer.startswith("answer to")
    assert starts(qa.contexts or []) == [("a", 0), ("a", 520)]
    assert warnings == [f"equation lookup skipped for {A[:16]}: no document.md"]


def test_the_service_gives_an_equation_cut_by_a_boundary_whole_under_its_header():
    store, _ = long_paper(200, 30)
    _, qa, svc = build({}, [(A, 0)], store=store)
    ask(svc, "What does Eq. (5) state?")
    added = (qa.contexts or [])[1]
    assert added.text.startswith(
        f"Equation (5) of paper {A[:16]}, Lamp a quantum spectra resonators:\n\n$$ x + "
    )
    assert added.start == 200 and "y \\tag{5}$$" in added.text


def test_a_spanish_question_through_the_service():
    _, qa, svc = build({A: TWO_THREE_SEVEN}, [(A, 0)])
    ask(svc, "En el articulo, que establece la ecuación (3)?")
    assert starts(qa.contexts or []) == [("a", 0), ("a", 780)]
    assert (qa.contexts or [])[1].text.startswith(f"Equation (3) of paper {A[:16]}")


def test_the_gate_lists_are_made_before_the_header_moves_the_text():
    block_page = (
        pad(360, "g")
        + equation("g", "2")
        + pad(20, "g")
        + "\n"
        + BLOCK
        + "\n\nFig 1. "
        + pad(300, "g")
    )
    store = InMemoryStore()
    add_doc(
        store, "g" * 64, [(block_page, "Part")], name="g.txt"
    ).title = "Lamp g quantum spectra resonators"
    embedder = FakeEmbedder()
    index = FixedIndex(embedder_fingerprint=embedder.fingerprint)
    IndexService(
        store=store,
        embedder=embedder,
        index=index,
        chunk_chars=CHARS,
        overlap=OVERLAP,
        log=lambda _: None,
    ).build()
    paper = "g" * 64
    chunks = chunks_of(store, paper)
    holder = next(c for c in chunks if "```" in c.text and "\\tag{2}" in c.text)
    index.answer = [chunks[0]]
    qa = FakeQA()
    svc = AskService(
        store=store,
        qa=qa,
        retrieval=lambda: Retrieval(embedder, index, None, 100, 10),
        circuit_lists=True,
        equation_lookup=LOOKUP,
    )
    ask(svc, "What does Eq. (2) state?")
    added = next(c for c in qa.contexts or [] if c.start == holder.start)
    assert added.text.startswith(
        f"Equation (2) of paper {paper[:16]}, Lamp g quantum spectra resonators:\n\n"
    )
    assert f"{BLOCK}\n\n{LIST}\n" in added.text  # the list is there, after the block
    assert added.text.index("tag{2}") < added.text.index(LIST)


def test_a_retrieved_chunk_that_holds_the_equation_gets_the_header_in_place():
    store, qa, svc = build({A: TWO_THREE_SEVEN, B: TWO_THREE_SEVEN}, [(A, 0), (B, 260), (A, 260)])
    ask(svc, "What does Eq. (2) state?")
    texts = {(c.doc_id[0], c.start): c.text for c in qa.contexts or []}
    assert list(texts) == [("a", 0), ("b", 260), ("a", 260)]  # nothing added, nothing replaced
    assert texts[("b", 260)].startswith("Equation (2) of paper ")
    assert texts[("a", 260)].startswith("Equation (2) of paper ")
    assert texts[("a", 0)] == at(store, A, 0).text


# ------------------------------------------------------------------ the composition root


@pytest.mark.parametrize("skip", [True, False])
def test_the_container_turns_the_lookup_on_with_skip_summary_and_only_then(skip):
    cfg = AppConfig.model_validate(
        {"qa": {"skip_summary": skip, "chunk_chars": 700, "overlap": 70, "answer_max_sources": 6}}
    )
    service = Container(cfg, log=lambda _: None, overrides={"store": InMemoryStore()}).ask
    expected = EquationLookup(700, 70, 6) if skip else None
    assert service._equation_lookup == expected


def test_the_container_passes_the_chunk_settings_the_index_is_built_with():
    cfg = AppConfig.model_validate({"qa": {"skip_summary": True}})
    service = Container(cfg, log=lambda _: None, overrides={"store": InMemoryStore()}).ask
    assert service._equation_lookup == EquationLookup(
        cfg.qa.chunk_chars, cfg.qa.overlap, cfg.qa.answer_max_sources
    )


# ------------------------------------------------------------------ linear time


def best_cpu(fn):
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


def test_the_lookup_is_linear_in_the_size_of_the_paper():
    def cpu(n_pages):
        store = InMemoryStore()
        words = [(pad(4000, "a"), f"P{i}") for i in range(n_pages - 1)]
        add_doc(store, A, [*words, (pad(100, "a") + equation("a", "9"), "Last")])
        docs = store.corpus()[0]
        lead = document_chunks(docs[0], split_pages(store.markdown(docs[0])), CHARS, OVERLAP)[:1]

        def run():  # the number is on the last page: the whole paper is read and cut
            with_equation_lookup(
                lead, docs, "Eq. (9)?" + ASKED, store, lambda _: None, lookup=LOOKUP
            )

        return best_cpu(run)

    small, big = cpu(15), cpu(60)
    assert big < max(8 * small, 0.05)
