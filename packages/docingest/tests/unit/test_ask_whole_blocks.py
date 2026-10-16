"""AskService with ``whole_blocks``: the display equation, code block or table that a retrieved
chunk cuts, completed from the paper's text before the answerer reads the chunks. Fakes only;
invented papers."""

import asyncio
import gc
import time

import pytest
from fakes import FakeEmbedder, FakeQA, FixedIndex, InMemoryStore, add_doc

import docingest.application.ask as ask_module
from docingest.application.ask import (
    AskService,
    EquationLookup,
    Retrieval,
    WholeBlocks,
    with_block_notes,
    with_equation_lookup,
    with_whole_blocks,
)
from docingest.application.index import IndexService, document_chunks
from docingest.bootstrap import Container
from docingest.config import AppConfig
from docingest.domain.blocks import partial_note
from docingest.domain.chunking import PAGE_BREAK, Chunk
from docingest.domain.circuits import gate_list
from docingest.domain.text import split_pages

CHARS, OVERLAP = 300, 40  # chunks start at 0, 260, 520, 780, 1040, 1300
A, B = "a" * 64, "b" * 64
BLOCKS = WholeBlocks(CHARS, sources=10)
ASKED = " lamp quantum spectra resonators"  # four content words of the invented titles

CIRCUIT = "\\begin{quantikz}\n& \\gate{H} & \\ctrl{1} & \\qw \\\\\n& \\qw & \\targ{} & \\qw\n\\end{quantikz}"
FENCED = f"``` latex\n{CIRCUIT}\n```"
LIST = gate_list(CIRCUIT).text
EQUATION = "$$ H_{a} = \\sum_i s_i \\left( x_i + y_i \\right) + \\lambda \\sum_j t_j \\tag{4}$$"
TABLE = "| a | b |\n|---|---|\n" + "| 1 | 2 |\n" * 11 + "| 3 | 4 |"
LONG_TABLE = "| a | b |\n|---|---|\n" + "| 1 | 2 |\n" * 200 + "| 3 | 4 |"  # over 1,200 characters
NEXT = "## Next part"  # a heading after a code block: not a caption


def pad(n: int, letter: str = "a") -> str:
    """Invented words, exactly ``n`` characters, different for each paper."""
    return (f"{letter}lamp " * (n // 6 + 1))[:n]


def laid(*items: tuple[int, str, str], end: int = 1500, letter: str = "a") -> str:
    """Words with each ``(offset, block, text after the block)`` where the offset says: the block
    starts there, a blank line before and after it."""
    out = ""
    for at, block, after in items:
        out += pad(at - len(out) - 2, letter) + "\n\n" + block + "\n\n"
        if after:
            out += after + "\n\n"
    return out + pad(end - len(out), letter)


def stored(pages: dict[str, list[str]]):
    store = InMemoryStore()
    for doc_id, texts in pages.items():
        add_doc(
            store, doc_id, [(t, f"Part {i}") for i, t in enumerate(texts)], name="p.txt"
        ).title = f"Lamp {doc_id[0]} quantum spectra resonators"
    return store


def sources(store):
    return store.corpus()[0]


def joined(store, doc_id) -> str:
    doc = next(d for d in sources(store) if d.manifest.doc_id == doc_id)
    return "".join(t + PAGE_BREAK for t in split_pages(store.markdown(doc)).values())


def chunks_of(store, doc_id):
    doc = next(d for d in sources(store) if d.manifest.doc_id == doc_id)
    return document_chunks(doc, split_pages(store.markdown(doc)), CHARS, OVERLAP)


def at(store, doc_id, start):
    return next(c for c in chunks_of(store, doc_id) if c.start == start)


def whole(store, picks, *, blocks=BLOCKS, warn=None):
    """``with_whole_blocks`` on the chunks ``picks`` (``[(doc id, start)]``): the contexts and notes."""
    return with_whole_blocks(
        [at(store, d, s) for d, s in picks],
        sources(store),
        store,
        warn or (lambda _: None),
        blocks=blocks,
    )


def one(letter: str, text: str):
    return stored({letter * 64: [text]})


# ------------------------------------------------------------------ a display equation


def test_the_fixture_has_the_layout_the_tests_describe():
    text = laid((285, EQUATION, ""))
    store = one("a", text)
    first, second = chunks_of(store, A)[:2]
    cut = text.index(EQUATION)
    assert cut < first.start + len(first.text) < cut + len(EQUATION)  # the first stops inside
    assert second.start == CHARS - OVERLAP and EQUATION in second.text  # the second holds it


def test_a_chunk_that_stops_inside_a_display_equation_gets_the_rest_of_it():
    text = laid((285, EQUATION, ""))
    store = one("a", text)
    [out], notes = whole(store, [(A, 0)])
    end = text.index(EQUATION) + len(EQUATION)
    assert out.text == text[:end] and out.start == 0 and notes == {}
    assert out.text.endswith("\\tag{4}$$")
    assert out.text.startswith(at(store, A, 0).text)  # what it was, then the rest
    assert (out.doc_id, out.first_page, out.last_page, out.name) == (
        A,
        1,
        1,
        at(store, A, 0).name,
    )


def test_a_chunk_that_starts_inside_a_display_equation_gets_the_start_of_it():
    text = laid((245, EQUATION, ""))
    store = one("a", text)
    [out], _ = whole(store, [(A, 260)])
    start = text.index(EQUATION)
    assert out.start == start and out.text == text[start : 260 + 300]
    assert out.text.startswith("$$ H_{a}")


def test_a_chunk_that_holds_the_whole_block_is_returned_as_it_is():
    store = one("a", laid((285, EQUATION, "")))
    contexts, notes = whole(store, [(A, 260)])
    assert contexts == [at(store, A, 260)] and notes == {}


def test_the_rest_of_an_equation_is_not_added_to_a_paper_without_one():
    store = one("a", pad(1500))
    contexts, _ = whole(store, [(A, 0), (A, 260)])
    assert contexts == [at(store, A, 0), at(store, A, 260)]


# ------------------------------------------------------------------ a circuit, a table and a caption


def test_a_circuit_code_block_cut_at_the_end_is_completed():
    text = laid((230, FENCED, NEXT))
    store = one("a", text)
    [out], _ = whole(store, [(A, 0)])
    end = text.index(FENCED) + len(FENCED)
    assert at(store, A, 0).text.count("```") == 1  # cut: the closing fence is missing
    assert out.text == text[:end] and out.text.endswith("\\end{quantikz}\n```")


def test_a_circuit_code_block_cut_at_the_start_is_completed():
    text = laid((200, FENCED, NEXT))
    store = one("a", text)
    [out], _ = whole(store, [(A, 260)])
    assert out.start == text.index(FENCED) and out.text.startswith("``` latex\n\\begin{quantikz}")


def test_a_table_cut_in_a_row_is_completed():
    text = laid((240, TABLE, ""))
    store = one("a", text)
    [out], _ = whole(store, [(A, 0)])
    end = text.index(TABLE) + len(TABLE)
    assert end > 300 and out.text == text[:end] and out.text.endswith("| 3 | 4 |")


def test_the_caption_that_follows_a_cut_circuit_comes_with_it():
    caption = "Fig. 2. The circuit that prepares the pair."
    text = laid((230, FENCED, caption))
    store = one("a", text)
    [out], _ = whole(store, [(A, 0)])
    assert out.text.endswith(caption)


def test_a_chunk_that_stops_between_a_block_and_its_caption_gets_the_caption():
    caption = "Fig. 2. The circuit that prepares the pair."
    block_end = 150 + len(FENCED)
    text = laid((150, FENCED, caption))
    store = one("a", text)
    chunk = Chunk(A, "n", text[:block_end], 1, 1, start=0)  # an index chunk that ends at the fence
    [out], _ = with_whole_blocks([chunk], sources(store), store, lambda _: None, blocks=BLOCKS)
    assert out.text == text[: block_end + 2 + len(caption)]


def test_an_equation_that_is_not_a_circuit_gets_no_caption():
    text = laid((150, EQUATION, "Fig. 2. A paragraph after an equation."))
    store = one("a", text)
    end = 150 + len(EQUATION)
    chunk = Chunk(A, "n", text[:end], 1, 1, start=0)
    [out], _ = with_whole_blocks([chunk], sources(store), store, lambda _: None, blocks=BLOCKS)
    assert out == chunk


def test_a_paragraph_longer_than_chunk_chars_is_no_caption_and_one_within_it_is():
    small = WholeBlocks(
        100, sources=10
    )  # captions up to 100 characters; the limit of 300 is not the issue
    for caption, added in (("Fig. 2. " + pad(72), True), ("Fig. 2. " + pad(142), False)):
        text = laid((150, FENCED, caption), end=900)
        end = text.index(FENCED) + len(FENCED)
        assert (
            len(FENCED) + 2 + len(caption) <= 300
        )  # with the caption it would still fit the bound
        store = one("a", text)
        chunk = Chunk(A, "n", text[:end], 1, 1, start=0)
        [out], _ = with_whole_blocks([chunk], sources(store), store, lambda _: None, blocks=small)
        assert out.text == (text[: end + 2 + len(caption)] if added else text[:end])


def test_a_block_with_a_long_caption_is_completed_without_the_caption():
    block = f"``` latex\n{CIRCUIT * 2}\n```"
    text = laid((230, block, "Fig. 2. " + pad(280)), end=1500)  # block 250 + caption 290 < 900
    store = one("a", text)
    small = WholeBlocks(100, sources=10)  # limit 300: the block fits, with its caption it does not
    [out], _ = with_whole_blocks(
        [Chunk(A, "n", text[:300], 1, 1, start=0)],
        sources(store),
        store,
        lambda _: None,
        blocks=small,
    )
    assert out.text == text[: text.index(block) + len(block)]


# ------------------------------------------------------------------ the bound


def test_a_block_too_long_to_complete_is_left_as_it_is_and_the_chunk_says_so():
    text = laid((240, LONG_TABLE, ""), end=2400)
    assert len(LONG_TABLE) > 3 * CHARS
    store = one("a", text)
    contexts, notes = whole(store, [(A, 0), (A, 260)])
    assert contexts == [at(store, A, 0), at(store, A, 260)]  # no text added
    assert notes == {(A, 0): ("table",), (A, 260): ("table",)}
    out = with_block_notes(contexts, notes)
    assert out[0].text == partial_note(["table"]) + "\n\n" + contexts[0].text
    assert out[1].text.startswith("Note: this passage shows only part of a longer table;")


def test_a_block_of_exactly_three_chunks_is_still_completed():
    table = "| a |\n|---|\n| " + "1" * (3 * CHARS - 16) + " |"
    assert len(table) == 3 * CHARS
    text = laid((240, table, ""), end=1500)
    store = one("a", text)
    [out], notes = whole(store, [(A, 0)])
    assert out.text == text[: text.index(table) + len(table)] and notes == {}


def test_a_block_one_character_over_three_chunks_is_not():
    table = "| a |\n|---|\n| " + "1" * (3 * CHARS - 15) + " |"
    text = laid((240, table, ""), end=1500)
    store = one("a", text)
    [out], notes = whole(store, [(A, 0)])
    assert out == at(store, A, 0) and notes == {(A, 0): ("table",)}


def test_the_bound_follows_the_chunk_size_of_the_setting():
    text = laid((240, TABLE, ""))
    store = one("a", text)
    tiny = WholeBlocks(40, sources=10)  # the table is longer than 3 * 40
    contexts, notes = whole(store, [(A, 0)], blocks=tiny)
    assert contexts == [at(store, A, 0)] and notes == {(A, 0): ("table",)}


def test_a_too_long_block_that_the_chunks_hold_between_them_needs_no_note():
    block = "| a |\n|---|\n| " + "1" * 1000 + " |"  # over 900, and chunks 0 to 3 hold all of it
    text = laid((200, block, ""), end=1500)
    store = one("a", text)
    _, notes = whole(store, [(A, 0), (A, 260), (A, 520), (A, 780), (A, 1040)])
    assert notes == {}


# ------------------------------------------------------------------ no text twice


def test_neighbouring_chunks_that_hold_a_block_between_them_get_nothing_added():
    text = laid((250, FENCED, NEXT))  # the first chunk stops inside it, the second starts inside
    store = one("a", text)
    assert text.index(FENCED) < 260 and text.index(FENCED) + len(FENCED) > 300
    contexts, notes = whole(store, [(A, 0), (A, 260)])
    assert contexts == [at(store, A, 0), at(store, A, 260)] and notes == {}


def test_only_the_text_that_no_chunk_holds_is_added_and_to_the_chunk_next_to_it():
    block = "| a | b |\n|---|---|\n" + "| 1 | 2 |\n" * 70 + "| 3 | 4 |"  # from 230 to about 1000
    text = laid((230, block, ""), end=1500)
    store = one("a", text)
    end = text.index(block) + len(block)
    assert 560 < end < 1040
    contexts, _ = whole(store, [(A, 0), (A, 260)])
    assert contexts[0] == at(store, A, 0)  # the second chunk holds the rest of what it cuts
    assert contexts[1].start == 260 and contexts[1].text == text[260:end]
    held = [(c.start, c.start + len(c.text)) for c in contexts]
    assert held == [(0, 300), (260, end)]  # the overlap of the two is the only text held twice


def test_the_better_ranked_chunk_next_to_missing_text_takes_it():
    block = "| a | b |\n|---|---|\n" + "| 1 | 2 |\n" * 80 + "| 3 | 4 |"  # about 800 characters
    text = laid((250, block, ""), end=1500)
    store = one("a", text)
    best_first, _ = whole(store, [(A, 0), (A, 780)])
    best_last, _ = whole(store, [(A, 780), (A, 0)])
    start = text.index(block)
    end = start + len(block)
    assert best_first[0].start == 0 and best_first[0].start + len(best_first[0].text) == 780
    assert best_first[1] == at(store, A, 780)
    assert best_last[0].start == 300 and best_last[0].start + len(best_last[0].text) == 780 + 300
    assert best_last[1] == at(store, A, 0)
    assert start < 300 and end > 780


# ------------------------------------------------------------------ the order of the contexts and of the additions


def test_the_chunks_keep_their_order_and_only_the_cut_ones_change():
    text = laid((285, EQUATION, ""))
    store = one("a", text)
    other = stored({B: [pad(1500, "b")]})
    contexts_in = [at(store, A, 520), at(store, A, 0), at(other, B, 0), at(store, A, 780)]
    sources_ = sources(store) + sources(other)
    out, _ = with_whole_blocks(contexts_in, sources_, store, lambda _: None, blocks=BLOCKS)
    assert [(c.doc_id[0], c.start) for c in out] == [("a", 520), ("a", 0), ("b", 0), ("a", 780)]
    assert out[0] == contexts_in[0] and out[2] == contexts_in[2] and out[3] == contexts_in[3]
    assert out[1].text.endswith("\\tag{4}$$") and out[1].start == 0


def build(
    pages, picks, *, blocks=BLOCKS, circuit_lists=False, lookup=None, contexts=10, store=None
):
    store = store or stored(pages)
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
        equation_lookup=lookup,
        whole_blocks=blocks,
    )
    return store, qa, svc


def ask(svc, question="What do the lamps show?", warn=lambda _: None, **kw):
    return asyncio.run(svc.ask(question, warn, **kw))


def test_the_answerer_gets_the_completed_chunks():
    text = laid((285, EQUATION, ""))
    store, qa, svc = build({A: [text]}, [(A, 0), (A, 520)])
    ask(svc)
    assert qa.contexts is not None
    assert [c.start for c in qa.contexts] == [0, 520]
    assert qa.contexts[0].text == text[: text.index(EQUATION) + len(EQUATION)]
    assert qa.contexts[1] == at(store, A, 520)


def test_the_switch_is_off_by_default_and_with_it_off_the_chunks_are_as_the_index_has_them():
    store, qa, svc = build({A: [laid((285, EQUATION, ""))]}, [(A, 0)], blocks=None)
    ask(svc)
    assert qa.contexts == [at(store, A, 0)]
    assert AskService(store=InMemoryStore(), qa=FakeQA())._whole_blocks is None


def test_a_question_that_does_not_use_the_index_is_not_touched():
    _, qa, svc = build({A: [laid((285, EQUATION, ""))]}, [(A, 0)])
    ask(svc, use_index=False)
    assert qa.contexts is None


def test_the_gate_list_lands_right_after_the_circuit_a_chunk_was_completed_with():
    text = laid((230, FENCED, "Fig. 2. The pair."))
    store, qa, svc = build({A: [text]}, [(A, 0)], circuit_lists=True)
    ask(svc)
    [out] = qa.contexts or []
    assert (
        at(store, A, 0).text.count("```") == 1
    )  # the chunk cut the circuit: no list without the fix
    assert f"{FENCED}\n\n{LIST}\n" in out.text
    assert out.text.count("Gate list") == 1
    assert out.text.index(LIST) < out.text.index("Fig. 2.")  # between the circuit and its caption
    assert out.text.endswith("Fig. 2. The pair.")


def test_a_circuit_cut_at_the_start_gets_its_list_once_after_the_block_it_was_given():
    text = laid((200, FENCED, NEXT))
    _, qa, svc = build({A: [text]}, [(A, 260)], circuit_lists=True)
    ask(svc)
    [out] = qa.contexts or []
    assert out.text.startswith("``` latex") and out.text.count(LIST) == 1
    assert f"{FENCED}\n\n{LIST}\n" in out.text


def test_without_the_completion_the_cut_circuit_gets_no_list():
    text = laid((230, FENCED, NEXT))
    _, qa, svc = build({A: [text]}, [(A, 0)], circuit_lists=True, blocks=None)
    ask(svc)
    [out] = qa.contexts or []
    assert "Gate list" not in out.text


EQ_TAG = "$$ H_{a} = \\sum_i s_i \\left( x_i + y_i \\right) + \\lambda \\sum_j t_j \\tag{2}$$"
LOOKUP = EquationLookup(CHARS, OVERLAP, sources=10)
HEADER = f"Equation (2) of paper {A[:16]}, Lamp a quantum spectra resonators:"


def test_an_equation_the_completion_made_whole_is_not_added_again_by_the_lookup():
    text = laid((285, EQ_TAG, ""))
    _, qa, svc = build({A: [text]}, [(A, 0)], lookup=LOOKUP)
    ask(svc, "In the lamp paper, what does Eq. (2) state?" + ASKED)
    [out] = qa.contexts or []  # no second chunk for the equation: the first one holds it now
    assert out.text.startswith(HEADER + "\n\n") and out.text.endswith("\\tag{2}$$")


def test_without_the_completion_the_lookup_adds_the_chunk_instead():
    text = laid((285, EQ_TAG, ""))
    _, qa, svc = build({A: [text]}, [(A, 0)], lookup=LOOKUP, blocks=None)
    ask(svc, "In the lamp paper, what does Eq. (2) state?" + ASKED)
    assert len(qa.contexts or []) == 2


def test_the_lookup_still_adds_a_chunk_after_a_completed_one():
    text = laid((230, FENCED, NEXT), (860, EQ_TAG, ""))
    _, qa, svc = build({A: [text]}, [(A, 0)], lookup=LOOKUP, circuit_lists=True)
    ask(svc, "In the lamp paper, what does Eq. (2) state?" + ASKED)
    first, second = qa.contexts or []
    assert (
        first.start == 0
        and f"{FENCED}\n\n{LIST}\n" in first.text
        and "Equation (" not in first.text
    )
    assert second.text.startswith(HEADER + "\n\n") and "\\tag{2}" in second.text


def test_the_order_is_blocks_then_gate_lists_then_notes_then_headers():
    long_table = "| a | b |\n|---|---|\n" + "| 1 | 2 |\n" * 200 + "| 3 | 4 |"
    text = laid((20, FENCED, NEXT), (160, EQ_TAG, ""), (260, long_table, ""), end=2400)
    # chunk 0 holds the circuit and the equation whole; the table (over three chunks) is cut at its end
    _, qa, svc = build({A: [text]}, [(A, 0)], lookup=LOOKUP, circuit_lists=True)
    ask(svc, "In the lamp paper, what does Eq. (2) state?" + ASKED)
    [out] = qa.contexts or []
    note = partial_note(["table"])
    assert out.text.startswith(f"{HEADER}\n\n{note}\n\n")  # header, then the note, then the chunk
    body = out.text[len(HEADER) + len(note) + 4 :]
    assert body.startswith(pad(18)[:10]) and f"{FENCED}\n\n{LIST}\n" in body
    assert body.index(LIST) < body.index("\\tag{2}") < body.index("| a | b |")


def test_a_note_alone_is_put_before_the_chunk():
    text = laid((240, LONG_TABLE, ""), end=2400)
    store, qa, svc = build({A: [text]}, [(A, 0)])
    ask(svc)
    [out] = qa.contexts or []
    assert out.text == partial_note(["table"]) + "\n\n" + at(store, A, 0).text


# ------------------------------------------------------------------ the lookup next to a completed chunk


def test_the_lookup_keeps_a_chunk_that_only_goes_on_after_the_text_of_the_one_it_would_add():
    # an equation of over three chunks, which is not completed, starts more than a chunk before the
    # chunk with its tag, so the lookup would add that chunk as the index has it (the end of the
    # equation); the retrieved twin was completed at its end, by a table the chunk cuts
    equation = "$$ H = " + "x + " * 260 + "z \\tag{5}$$"
    text = laid((100, equation, ""), (1300, TABLE, ""), end=1800)
    store = one("a", text)
    tag_at = text.index("\\tag{5}")
    twin = next(c for c in chunks_of(store, A) if c.start <= tag_at and tag_at + 8 <= c.start + 300)
    assert twin.start - text.index(equation) > CHARS and text.index(TABLE) < twin.start + 300
    done, _ = with_whole_blocks([twin], sources(store), store, lambda _: None, blocks=BLOCKS)
    assert done[0].start == twin.start and len(done[0].text) > len(twin.text)  # same start, longer
    out, labels = with_equation_lookup(
        done,
        sources(store),
        "Eq. (5)?" + ASKED,
        store,
        lambda _: None,
        lookup=LOOKUP,
        completed=True,
    )
    assert out == done and labels[(A, twin.start)].partial == {"5"}
    # without completed blocks a twin whose text differs is the old text, and the lookup replaces it
    out, _ = with_equation_lookup(
        done, sources(store), "Eq. (5)?" + ASKED, store, lambda _: None, lookup=LOOKUP
    )
    assert out != done


# ------------------------------------------------------------------ pages and names


def test_an_extended_chunk_takes_the_pages_it_now_reaches():
    # a display equation that the converter left open at the end of page 1 and closed on page 3
    first = pad(150) + "\n\n$$ H = a + b"
    second = pad(200)
    third = "c + d $$ and " + pad(1200)
    store = stored({A: [first, second, third]})
    document = joined(store, A)
    chunk = at(store, A, 0)
    stop = chunk.start + len(chunk.text)
    assert (chunk.first_page, chunk.last_page) == (1, 2)  # the chunk is cut inside the equation
    assert document.index("$$ H") < stop < document.index("c + d $$")
    [out], _ = with_whole_blocks([chunk], sources(store), store, lambda _: None, blocks=BLOCKS)
    assert out.text.endswith("c + d $$") and len(out.text) > len(chunk.text)
    assert (out.first_page, out.last_page) == (1, 3)
    assert out.name == f"{A[:16]} pages 1-3"


def test_the_pages_and_the_name_follow_a_chunk_extended_into_a_page_it_did_not_reach():
    chunk = Chunk(A, f"{A[:16]} pages 1-1", "x", 1, 1, start=0)
    pages = [(0, 1), (100, 2), (200, 3)]
    out = ask_module._extended(chunk, "x" * 300, 0, 250, pages)
    assert (out.first_page, out.last_page, out.name) == (1, 3, f"{A[:16]} pages 1-3")
    out = ask_module._extended(chunk, "x" * 300, 0, 100, pages)  # ends where page 2 begins
    assert (out.last_page, out.name) == (1, chunk.name)
    back = Chunk(A, f"{A[:16]} pages 3-3", "x", 3, 3, start=250)
    out = ask_module._extended(back, "x" * 300, 120, 251, pages)
    assert (out.first_page, out.last_page, out.start) == (2, 3, 120)


def test_an_unextended_chunk_keeps_its_name_and_pages():
    store = one("a", laid((285, EQUATION, "")))
    [out], _ = whole(store, [(A, 520)])
    assert out == at(store, A, 520)


# ------------------------------------------------------------------ the prompt carries at most sources chunks


def test_only_the_chunks_the_prompt_carries_are_completed():
    text = laid((285, EQUATION, ""), (545, EQUATION, ""))
    store = one("a", text)
    contexts, _ = whole(store, [(A, 0), (A, 260)], blocks=WholeBlocks(CHARS, sources=1))
    assert contexts[0].text.endswith("\\tag{4}$$") and contexts[1] == at(store, A, 260)


def test_a_chunk_beyond_the_prompt_does_not_count_as_holding_text():
    text = laid((250, FENCED, NEXT))
    store = one("a", text)
    with_it, _ = whole(store, [(A, 0), (A, 260)], blocks=WholeBlocks(CHARS, sources=2))
    without, _ = whole(store, [(A, 0), (A, 260)], blocks=WholeBlocks(CHARS, sources=1))
    assert with_it[0] == at(store, A, 0)  # the second chunk holds the end: nothing added
    assert without[0].text.endswith("\\end{quantikz}\n```")  # it is not in the prompt: added


def test_a_chunk_whose_text_is_not_the_papers_text_at_its_offset_is_left_alone():
    text = laid((285, EQUATION, ""))
    store = one("a", text)
    old = at(store, A, 0)
    stale = Chunk(A, old.name, "Old text. " + old.text[10:], 1, 1, start=0)
    warnings: list[str] = []
    [out], notes = with_whole_blocks([stale], sources(store), store, warnings.append, blocks=BLOCKS)
    assert out == stale and notes == {} and warnings == []


def test_a_chunk_of_a_paper_that_left_the_corpus_is_left_alone():
    store = one("a", laid((285, EQUATION, "")))
    chunk = at(store, A, 0)
    out, _ = with_whole_blocks([chunk], [], store, lambda _: None, blocks=BLOCKS)
    assert out == [chunk]


# ------------------------------------------------------------------ a failure never stops the answer


def test_a_markdown_that_cannot_be_read_leaves_the_chunks_and_warns_once_for_the_paper():
    store = one("a", laid((285, EQUATION, "")))
    chunks = [at(store, A, 0), at(store, A, 520)]

    def broken(doc):
        raise OSError("document.md is missing")

    store.markdown = broken  # type: ignore[method-assign]
    warnings: list[str] = []
    out, notes = with_whole_blocks(chunks, sources(store), store, warnings.append, blocks=BLOCKS)
    assert out == chunks and notes == {}
    assert warnings == [f"whole blocks skipped for {A[:16]}: document.md is missing"]


def test_a_chunk_that_cannot_be_extended_is_kept_and_the_others_are_still_completed(monkeypatch):
    text = laid((285, EQUATION, ""), (790, EQUATION, ""))
    store = one("a", text)
    real = ask_module._extended
    calls: list[int] = []

    def flaky(chunk, *args):
        calls.append(chunk.start)
        if len(calls) == 1:
            raise RecursionError("too deep")
        return real(chunk, *args)

    monkeypatch.setattr(ask_module, "_extended", flaky)
    warnings: list[str] = []
    out, _ = whole_with(store, [(A, 0), (A, 520)], warnings)
    assert out[0] == at(store, A, 0) and out[1].text.endswith("\\tag{4}$$")
    assert warnings == [f"whole blocks skipped for {A[:16]}: too deep"]


def whole_with(store, picks, warnings):
    return whole(store, picks, warn=warnings.append)


def test_a_paper_whose_chunks_all_fail_is_warned_about_once(monkeypatch):
    text = laid((285, EQUATION, ""), (790, EQUATION, ""))
    store = one("a", text)

    def boom(*args):
        raise RecursionError("too deep")

    monkeypatch.setattr(ask_module, "_extended", boom)
    warnings: list[str] = []
    out, _ = whole_with(store, [(A, 0), (A, 520)], warnings)
    assert out == [at(store, A, 0), at(store, A, 520)]
    assert warnings == [f"whole blocks skipped for {A[:16]}: too deep"]


def test_a_planner_that_fails_leaves_the_paper_as_it_is_and_warns_once(monkeypatch):
    store = one("a", laid((285, EQUATION, "")))

    def boom(*args, **kw):
        raise ValueError("bad plan")

    monkeypatch.setattr(ask_module, "complete", boom)
    warnings: list[str] = []
    out, notes = whole_with(store, [(A, 0), (A, 260)], warnings)
    assert out == [at(store, A, 0), at(store, A, 260)] and notes == {}
    assert warnings == [f"whole blocks skipped for {A[:16]}: bad plan"]


def test_one_paper_failing_does_not_stop_the_other():
    store = stored({A: [laid((285, EQUATION, ""))], B: [laid((285, EQUATION, ""), letter="b")]})
    contexts = [at(store, A, 0), at(store, B, 0)]
    real = store.markdown

    def picky(doc):
        if doc.manifest.doc_id == A:
            raise OSError("no document.md")
        return real(doc)

    store.markdown = picky  # type: ignore[method-assign]
    warnings: list[str] = []
    out, _ = with_whole_blocks(contexts, sources(store), store, warnings.append, blocks=BLOCKS)
    assert out[0] == contexts[0]
    assert out[1].text.endswith("\\tag{4}$$") and len(warnings) == 1


def test_the_answer_still_comes_when_the_completion_fails_and_the_user_is_told():
    store, qa, svc = build({A: [laid((285, EQUATION, ""))]}, [(A, 0)])
    held = at(store, A, 0)

    def broken(doc):
        raise OSError("no document.md")

    store.markdown = broken  # type: ignore[method-assign]
    warnings: list[str] = []
    answer = ask(svc, warn=warnings.append)
    assert answer.startswith("answer to") and qa.contexts == [held]
    assert warnings[-1] == f"whole blocks skipped for {A[:16]}: no document.md"


# ------------------------------------------------------------------ the switch from skip_summary


@pytest.mark.parametrize(
    ("skip", "key"), [(True, True), (True, False), (False, True), (False, False)]
)
def test_the_container_turns_the_completion_on_with_the_key_and_skip_summary_only(skip, key):
    cfg = AppConfig.model_validate(
        {
            "qa": {
                "skip_summary": skip,
                "whole_blocks": key,
                "chunk_chars": 700,
                "overlap": 70,
                "answer_max_sources": 6,
            }
        }
    )
    service = Container(cfg, log=lambda _: None, overrides={"store": InMemoryStore()}).ask
    assert service._whole_blocks == (WholeBlocks(700, 6) if skip and key else None)


def test_the_container_leaves_the_other_additions_to_skip_summary_alone():
    cfg = AppConfig.model_validate({"qa": {"skip_summary": True}})
    service = Container(cfg, log=lambda _: None, overrides={"store": InMemoryStore()}).ask
    assert service._whole_blocks is None
    assert service._circuit_lists and service._equation_lookup is not None


# ------------------------------------------------------------------ how much it adds


def test_the_text_added_to_a_chunk_is_at_most_the_bound_on_each_side():
    items = (200, LONG_TABLE[:880], ""), (1600, TABLE, "")
    text = laid(*items, end=2400)
    store = one("a", text)
    contexts, _ = whole(store, [(A, c.start) for c in chunks_of(store, A)])
    for before, after in zip((at(store, A, c.start) for c in contexts), contexts, strict=True):
        assert len(after.text) - len(before.text) <= 2 * 3 * CHARS


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


def test_completing_is_linear_in_the_size_of_the_paper():
    def cpu(n):
        body = "".join(
            f"{pad(400)}\n\n{EQUATION}\n\n{FENCED}\n\n{NEXT}\n\n{TABLE}\n\n" for _ in range(n)
        )
        store = one("a", body)
        chunks = chunks_of(store, A)
        picks = [chunks[0], chunks[len(chunks) // 2], chunks[-1]]
        docs = sources(store)

        def run():
            with_whole_blocks(picks, docs, store, lambda _: None, blocks=BLOCKS)

        return best_cpu(run)

    small, big = cpu(40), cpu(160)
    assert big < max(8 * small, 0.05), (small, big)
