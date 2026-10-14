"""AskService with ``circuit_lists``: the gate list of each circuit code block, added to the
retrieved chunks before the answerer reads them. Fakes only; an invented paper."""

import asyncio

import pytest
from fakes import FakeEmbedder, FakeIndex, FakeQA, InMemoryStore, add_doc

from docingest.application.ask import AskService, Retrieval, with_gate_lists
from docingest.application.index import IndexService
from docingest.bootstrap import Container
from docingest.config import AppConfig
from docingest.domain.chunking import PAGE_BREAK, Chunk, chunk_pages
from docingest.domain.circuits import gate_list
from docingest.domain.text import split_pages

PAPER, OTHER = "a" * 64, "b" * 64
CHARS, OVERLAP = 300, 40

CIRCUIT = (
    "\\begin{quantikz}\n"
    "\\lstick{$\\ket{a}$} & \\gate{H} & \\ctrl{1} & \\qw \\\\\n"
    "\\lstick{$\\ket{b}$} & \\qw & \\targ{} & \\meter{}\n"
    "\\end{quantikz}"
)
CZ = "\\begin{quantikz}\n& \\ctrl{1} & \\qw \\\\\n& \\ctrl{-1} & \\qw\n\\end{quantikz}"
BLOCK = f"``` latex\n{CIRCUIT}\n```"
CZ_BLOCK = f"``` latex\n{CZ}\n```"
LIST = gate_list(CIRCUIT).text
LIST_CZ = gate_list(CZ).text
LABELLED = "\\begin{quantikz}\\label{fig:lamp}\n& \\gate{Z} & \\qw\n\\end{quantikz}"


def pad(n: int) -> str:
    """Invented words, exactly ``n`` characters."""
    return ("lamp " * (n // 5 + 1))[:n]


# Page 1: a whole block inside the first chunk. Page 2: a block that starts at joined offset 240,
# so the first chunk (0 to 300) cuts it and the second (260 to 560) holds its end. Page 3: a
# circuit in display math, which gets no list.
PAGE_1 = f"Pair drawn here.\n\n{BLOCK}\n\nFig 1."
PAGE_2 = pad(240 - len(PAGE_1) - len(PAGE_BREAK) - 1) + "\n" + CZ_BLOCK + "\n\nFig 2. " + pad(300)
PAGE_3 = f"Display math: $${CZ}$$ ends. " + pad(200)


def build(pages=(PAGE_1, PAGE_2, PAGE_3), *, circuit_lists=True, contexts=50):
    store = InMemoryStore()
    add_doc(store, PAPER, [(text, f"Part {i}") for i, text in enumerate(pages)], name="p.txt")
    add_doc(store, OTHER, [("Graph spectra and the Laplacian of a graph. " * 12, "Intro")])
    embedder = FakeEmbedder()
    index = FakeIndex(embedder_fingerprint=embedder.fingerprint)
    IndexService(
        store=store,
        embedder=embedder,
        index=index,
        chunk_chars=CHARS,
        overlap=OVERLAP,
        log=lambda _: None,
    ).build()
    qa = FakeQA()
    retrieval = Retrieval(embedder, index, None, 100, contexts)
    svc = AskService(store=store, qa=qa, retrieval=lambda: retrieval, circuit_lists=circuit_lists)
    return store, qa, svc


def ask(svc, question="lamp pair figure"):
    asyncio.run(svc.ask(question, lambda _: None))


def stored_chunks(store, doc_id=PAPER):
    """The chunks the index holds, from the same pages the index was built from."""
    doc = next(d for d in store.corpus()[0] if d.manifest.doc_id == doc_id)
    return chunk_pages(doc_id, split_pages(store.markdown(doc)), chunk_chars=CHARS, overlap=OVERLAP)


def given(qa, doc_id=PAPER):
    assert qa.contexts is not None
    return {c.start: c for c in qa.contexts if c.doc_id == doc_id}


# ------------------------------------------------------------------ on and off


def test_the_fixture_has_the_layout_the_tests_describe():
    store, _, _ = build()
    first, second, *_ = stored_chunks(store)
    assert BLOCK in first.text and "Fig 1." in first.text  # a whole block, then its caption
    assert (
        CZ_BLOCK.split("\n", maxsplit=1)[0] in first.text and CZ_BLOCK not in first.text
    )  # cut by the end
    assert "\\end{quantikz}\n```" in second.text and CZ not in second.text  # its end alone
    assert second.start == CHARS - OVERLAP


def test_the_chunk_that_holds_a_block_gets_the_gate_list_right_after_the_closing_fence():
    _, qa, svc = build()
    ask(svc)
    text = given(qa)[0].text
    assert f"{BLOCK}\n\n{LIST}\n" in text
    assert text.index(LIST) < text.index("Fig 1.")  # between the block and its caption


def test_a_block_that_starts_in_the_previous_chunk_gets_the_list_of_the_whole_block():
    _store, qa, svc = build()
    ask(svc)
    text = given(qa)[CHARS - OVERLAP].text
    assert "\\begin{quantikz}" not in text  # the chunk never shows the start of the circuit
    assert f"\\end{{quantikz}}\n```\n\n{LIST_CZ}\n" in text
    assert "Wires: 1; 2" in LIST_CZ and "controlled-Z" in LIST_CZ


def test_a_block_cut_by_the_end_of_a_chunk_gets_no_list_in_that_chunk():
    _store, qa, svc = build()
    ask(svc)
    first = given(qa)[0].text
    assert LIST_CZ not in first and first.count("Gate list") == 1  # only the whole block's list


def test_only_the_chunks_that_hold_a_closing_fence_get_a_list():
    store, qa, svc = build()
    ask(svc)
    for chunk in stored_chunks(store):
        closes = "\\end{quantikz}\n```" in chunk.text
        assert ("Gate list" in given(qa)[chunk.start].text) == closes


def test_with_the_switch_off_the_answerer_gets_the_chunks_as_the_index_has_them():
    store, qa, svc = build(circuit_lists=False)
    ask(svc)
    held = {c.start: c.text for c in stored_chunks(store)}
    assert {k: c.text for k, c in given(qa).items()} == held


def test_the_switch_is_off_by_default():
    assert AskService(store=InMemoryStore(), qa=FakeQA())._circuit_lists is False


def test_only_the_text_of_a_chunk_changes():
    store, qa, svc = build()
    ask(svc)
    held = {c.start: c for c in stored_chunks(store)}
    for start, chunk in given(qa).items():
        original = held[start]
        same = ("doc_id", "name", "first_page", "last_page", "is_reference", "start")
        assert all(getattr(chunk, f) == getattr(original, f) for f in same)
        stripped = chunk.text.replace(f"\n\n{LIST}\n", "").replace(f"\n\n{LIST_CZ}\n", "")
        assert stripped == original.text


def test_a_circuit_that_is_in_display_math_gets_no_list():
    _store, qa, svc = build()
    ask(svc)
    math = [c for c in given(qa).values() if "Display math:" in c.text]
    assert math and not any("Gate list" in c.text for c in math)
    # the display-math circuit is the same source as the block of page 2, yet only a block is read
    assert all(c.text.count(CZ) <= 1 for c in math)


def test_the_other_paper_and_the_chunks_without_a_circuit_are_equal_to_the_index_ones():
    store, qa, svc = build()
    ask(svc)
    held = {c.start: c for c in stored_chunks(store, OTHER)}
    assert given(qa, OTHER) == held
    own = {c.start: c for c in stored_chunks(store)}
    for start, chunk in given(qa).items():
        if "Gate list" not in chunk.text:
            assert chunk == own[start]


def test_the_chunks_keep_their_retrieval_order():
    _, qa, svc = build()
    ask(svc)
    _, plain_qa, plain_svc = build(circuit_lists=False)
    ask(plain_svc)
    assert qa.contexts is not None and plain_qa.contexts is not None
    assert [(c.doc_id, c.start) for c in qa.contexts] == [
        (c.doc_id, c.start) for c in plain_qa.contexts
    ]


def test_a_question_that_does_not_use_the_index_is_not_touched():
    _store, qa, svc = build()
    asyncio.run(svc.ask("lamps", lambda _: None, use_index=False))
    assert qa.contexts is None and not any("Gate list" in md for md in qa.markdown)


# ------------------------------------------------------------------ what is read, and when


class CountingStore(InMemoryStore):
    def __init__(self):
        super().__init__()
        self.reads: list[str] = []

    def markdown(self, doc):
        self.reads.append(doc.manifest.doc_id)
        return super().markdown(doc)


def test_the_markdown_of_a_paper_is_read_once_however_many_of_its_chunks_hold_a_fence():
    store = CountingStore()
    pages = [(f"{pad(90)}\n\n{BLOCK}\n\n{pad(90)}", f"Part {i}") for i in range(4)]
    add_doc(store, PAPER, pages)
    docs = store.corpus()[0]
    contexts = chunk_pages(
        PAPER, split_pages(store.markdown(docs[0])), chunk_chars=CHARS, overlap=OVERLAP
    )
    store.reads.clear()
    assert sum("```" in c.text for c in contexts) >= 4
    out = with_gate_lists(contexts, docs, store, lambda _: None)
    assert store.reads == [PAPER]
    assert sum("Gate list" in c.text for c in out) >= 4


def test_the_markdown_is_not_read_when_no_chunk_holds_a_fence():
    store = CountingStore()
    add_doc(store, PAPER, [(pad(1500), "A")])
    docs = store.corpus()[0]
    contexts = chunk_pages(PAPER, {1: pad(1500)}, chunk_chars=CHARS, overlap=OVERLAP)
    store.reads.clear()
    assert with_gate_lists(contexts, docs, store, lambda _: None) == contexts
    assert store.reads == []


def test_a_chunk_of_a_document_that_changed_since_it_was_indexed_is_left_as_it_is():
    store, _qa, _svc = build()
    retrieved = stored_chunks(store)
    # the stored Markdown is edited after the index was built: the offsets no longer point at it
    manifest, markdown = store.canonical[PAPER]
    store.canonical[PAPER] = (manifest, markdown.replace("lamp", "lantern"))
    docs = store.corpus()[0]
    assert with_gate_lists(retrieved, docs, store, lambda _: None) == retrieved


def test_a_chunk_of_a_paper_not_among_the_sources_is_left_as_it_is():
    store, _, _ = build()
    chunks = stored_chunks(store)
    assert with_gate_lists(chunks, [], store, lambda _: None) == chunks


def test_the_offsets_of_the_chunks_match_the_pages_as_the_chunker_joins_them():
    store, _, _ = build()
    pages = split_pages(store.markdown(store.corpus()[0][0]))
    joined = "".join(text + PAGE_BREAK for text in pages.values())
    for chunk in stored_chunks(store):
        assert joined[chunk.start : chunk.start + len(chunk.text)] == chunk.text


def test_a_circuit_on_a_later_page_is_found_through_the_page_breaks():
    store = InMemoryStore()
    add_doc(store, PAPER, [(pad(400), "A"), (f"{pad(60)}\n\n{BLOCK}\n\nEnd.", "B")])
    docs = store.corpus()[0]
    contexts = chunk_pages(
        PAPER, split_pages(store.markdown(docs[0])), chunk_chars=CHARS, overlap=OVERLAP
    )
    out = with_gate_lists(contexts, docs, store, lambda _: None)
    assert sum(LIST in c.text for c in out) >= 1
    assert all(c.first_page <= c.last_page for c in out)


def test_the_label_of_a_circuit_goes_to_the_header_of_its_list():
    store = InMemoryStore()
    page = f"{pad(60)}\n\n``` latex\n{LABELLED}\n```\n\nEnd."
    add_doc(store, PAPER, [(page, "A")])
    docs = store.corpus()[0]
    contexts = chunk_pages(PAPER, split_pages(store.markdown(docs[0])), chunk_chars=2000, overlap=0)
    [chunk] = with_gate_lists(contexts, docs, store, lambda _: None)
    assert "Gate list for \\label{fig:lamp} (" in chunk.text


# ------------------------------------------------------------------ the composition root


@pytest.mark.parametrize("skip", [True, False])
def test_the_container_turns_the_lists_on_with_skip_summary_and_only_then(skip):
    cfg = AppConfig.model_validate({"qa": {"skip_summary": skip}})
    ask_service = Container(cfg, log=lambda _: None, overrides={"store": InMemoryStore()}).ask
    assert ask_service._circuit_lists is skip


# ------------------------------------------------------------------ a chunk that starts where a block ends


def test_a_chunk_that_starts_where_a_block_ends_gets_only_the_list_of_its_own_circuit():
    store = InMemoryStore()
    add_doc(store, PAPER, [(f"{pad(40)}\n\n{BLOCK}\n\n{pad(30)}\n\n{CZ_BLOCK}\n\nTAIL", "A")])
    docs = store.corpus()[0]
    joined = "".join(t + PAGE_BREAK for t in split_pages(store.markdown(docs[0])).values())
    first_end = joined.index("```\n\n", joined.index("\\end{quantikz}")) + 3
    text = joined[first_end : first_end + 400]
    chunk = Chunk(PAPER, "a pages 1-1", text, 1, 1, start=first_end)
    [out] = with_gate_lists([chunk], docs, store, lambda _: None)
    assert out.text.count("Gate list") == 1 and LIST_CZ in out.text and LIST not in out.text
    assert out.text.index(pad(30)[:10]) < out.text.index(LIST_CZ)  # nothing above the first fence


# ------------------------------------------------------------------ a failure never stops the answer


def sources(store):
    return store.corpus()[0]


def chunks_with_fences(store):
    return [c for c in stored_chunks(store) if "```" in c.text]


def test_a_markdown_that_cannot_be_read_leaves_the_chunks_and_warns_once_for_the_paper():
    store, _, _ = build()
    chunks = chunks_with_fences(store)
    assert len(chunks) >= 2

    def broken(doc):
        raise OSError("document.md is missing")

    store.markdown = broken  # type: ignore[method-assign]
    warnings: list[str] = []
    assert with_gate_lists(chunks, sources(store), store, warnings.append) == chunks
    assert len(warnings) == 1
    assert warnings[0] == f"gate list skipped for {PAPER[:16]}: document.md is missing"


def test_a_circuit_the_reader_chokes_on_leaves_its_chunk_as_it_is_and_the_others_are_read(
    monkeypatch,
):
    import docingest.application.ask as ask_module

    store, _, _ = build()
    chunks = chunks_with_fences(store)
    real = ask_module.add_gate_lists
    calls: list[int] = []

    def flaky(text, start, document, blocks):
        calls.append(start)
        if len(calls) == 1:
            raise RecursionError("too deep")
        return real(text, start, document, blocks)

    monkeypatch.setattr(ask_module, "add_gate_lists", flaky)
    warnings: list[str] = []
    out = with_gate_lists(chunks, sources(store), store, warnings.append)
    assert out[0] == chunks[0] and any("Gate list" in c.text for c in out[1:])
    assert warnings == [f"gate list skipped for {PAPER[:16]}: too deep"]


def test_the_answer_still_comes_when_the_lists_fail_and_the_user_is_told():
    store, qa, svc = build()
    held = {c.start: c.text for c in stored_chunks(store)}

    def broken(doc):
        raise OSError("no document.md")

    store.markdown = broken  # type: ignore[method-assign]
    warnings: list[str] = []
    answer = asyncio.run(svc.ask("lamp pair figure", warnings.append))
    assert answer.startswith("answer to") and given(qa) and warnings
    assert {k: c.text for k, c in given(qa).items()} == held
    assert all(w.startswith("gate list skipped for ") for w in warnings)
