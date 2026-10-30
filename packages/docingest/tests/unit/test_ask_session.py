"""AskSession with fakes: the index stays open between questions and is reloaded when, and only
when, it has a new commit; the structured result; one retrieval at a time; the answer from
passages the caller gives."""

import asyncio
import itertools
import threading

import pytest
import test_ask_whole_blocks as blocks
from fakes import FakeEmbedder, FakeIndex, FakeQA, FakeReranker, InMemoryStore, add_doc

from docingest.application.ask import (
    UNPLACED_WARNING,
    AskOptions,
    AskService,
    AskSession,
    GivenPassage,
    Retrieval,
)
from docingest.application.index import IndexService
from docingest.domain.errors import IndexNotReadyError

CHARS, OVERLAP = 60, 10
PAPER_A, PAPER_B, PAPER_C = "a" * 64, "b" * 64, "c" * 64
TEXT_A = [("Attention weights use a softmax over scaled dot products. " * 4, "Method")]
TEXT_B = [("Qubits decohere when they couple to the environment. " * 4, "Intro")]
TEXT_C = [("Graph spectra and the Laplacian of a graph. " * 4, "Intro")]
QUESTION = "how do qubits decohere in the environment"


class CountingStore(InMemoryStore):
    def __init__(self):
        super().__init__()
        self.listings = 0

    def corpus(self):
        self.listings += 1
        return super().corpus()


class WarningStore(CountingStore):
    """A corpus listing that always comes with a warning, as one with a partial run does."""

    def corpus(self):
        documents, warnings = super().corpus()
        return documents, [*warnings, "listing: a warning"]


class ThreadStore(InMemoryStore):
    """Remembers the thread of every read."""

    def __init__(self):
        super().__init__()
        self.threads: set[int] = set()

    def corpus(self):
        self.threads.add(threading.get_ident())
        return super().corpus()

    def markdown(self, doc):
        self.threads.add(threading.get_ident())
        return super().markdown(doc)


class CountingEmbedder(FakeEmbedder):
    def __init__(self):
        super().__init__()
        self.queries: list[str] = []

    def embed_query(self, text):
        self.queries.append(text)
        return super().embed_query(text)


class CommitsDuringSearch(CountingEmbedder):
    """Runs ``before_search`` once, inside the first question's retrieval (the lock is held)."""

    def __init__(self):
        super().__init__()
        self.before_search = None

    def embed_query(self, text):
        hook, self.before_search = self.before_search, None
        if hook is not None:
            hook()
        return super().embed_query(text)


class Gated(FakeEmbedder):
    """Embedding waits for a thread event and records how many questions are inside at once."""

    def __init__(self):
        super().__init__()
        self.inside = 0
        self.most = 0
        self.release = threading.Event()
        self.entered = threading.Event()
        self._guard = threading.Lock()

    def embed_query(self, text):
        with self._guard:
            self.inside += 1
            self.most = max(self.most, self.inside)
        self.entered.set()
        self.release.wait(5)
        with self._guard:
            self.inside -= 1
        return super().embed_query(text)


class SlowQA(FakeQA):
    """Answers wait for each other: two answers can only finish if both are inside at once."""

    def __init__(self, parties=2):
        super().__init__()
        self.barrier = asyncio.Event()
        self.waiting = 0
        self.parties = parties
        self.most = 0

    async def ask(self, question, documents, warn, contexts=None):
        self.waiting += 1
        self.most = max(self.most, self.waiting)
        if self.waiting >= self.parties:
            self.barrier.set()
        await asyncio.wait_for(self.barrier.wait(), 5)
        return await super().ask(question, documents, warn, contexts)


class FailingOnce(FakeQA):
    def __init__(self):
        super().__init__()
        self.failures = 1

    async def ask(self, question, documents, warn, contexts=None):
        if self.failures:
            self.failures -= 1
            raise ConnectionError("the model server is down")
        return await super().ask(question, documents, warn, contexts)


def make_store():
    store = CountingStore()
    add_doc(store, PAPER_A, TEXT_A, name="a.txt")
    add_doc(store, PAPER_B, TEXT_B, name="b.txt")
    return store


def build(store, embedder=None, qa=None, *, reranker=None, contexts=3, **kw):
    embedder = embedder or CountingEmbedder()
    index = FakeIndex(embedder_fingerprint=embedder.fingerprint)
    index_service = IndexService(
        store=store,
        embedder=embedder,
        index=index,
        chunk_chars=CHARS,
        overlap=OVERLAP,
        log=lambda _: None,
    )
    index_service.build()
    index.closed = 0
    store.listings = 0  # the build listed the corpus
    retrieval = Retrieval(embedder, index, reranker or FakeReranker(), 50, contexts)
    qa = qa or FakeQA()
    service = AskService(store=store, qa=qa, retrieval=lambda: retrieval, **kw)
    return service, index, index_service, qa


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ the index stays open


def test_the_index_is_not_closed_and_the_corpus_not_listed_again_between_questions():
    store = make_store()
    service, index, _, _ = build(store)
    session = AskSession(service)
    index.committed_calls = 0

    async def many():
        return [await session.ask(QUESTION) for _ in range(4)]

    results = run(many())
    assert len(results) == 4
    assert index.closed == 0  # AskService.ask closes after every question; the session never does
    assert store.listings == 1
    assert (
        index.committed_calls == 4
    )  # one read per question: the reload check is the readiness check


def test_a_new_commit_reloads_the_index_and_the_corpus_before_the_next_question():
    store = make_store()
    service, index, index_service, _ = build(store)
    session = AskSession(service)
    first = run(session.ask(QUESTION))
    assert first.index_generation == "g1" and set(first.sources) <= {PAPER_A, PAPER_B}

    add_doc(store, PAPER_C, TEXT_C, name="c.txt")  # ingested, but not committed to the index
    again = run(session.ask("graph spectra laplacian"))
    assert again.index_generation == "g1" and index.closed == 0 and store.listings == 1
    assert again.warnings == ()  # the listing is the one loaded: the paper is seen at the reload

    index_service.build()  # the commit that adds the paper
    index.closed = store.listings = 0  # the build closed the index and listed the corpus
    after = run(session.ask("graph spectra laplacian"))
    assert after.index_generation == "g2"
    assert index.closed == 1 and store.listings == 1
    assert PAPER_C in after.sources
    run(session.ask("graph spectra laplacian"))
    assert index.closed == 1 and store.listings == 1  # and not again


def test_a_build_that_changes_nothing_does_not_make_a_new_generation():
    store = make_store()
    service, index, index_service, _ = build(store)
    session = AskSession(service)
    assert run(session.ask(QUESTION)).index_generation == "g1"
    assert index_service.build().committed is False
    index.closed = store.listings = 0
    assert run(session.ask(QUESTION)).index_generation == "g1"
    assert index.closed == 0 and store.listings == 0


def test_an_index_that_does_not_name_its_commits_is_reloaded_on_request_only():
    store = make_store()
    service, index, index_service, _ = build(store)
    index.commits = 0  # the fake then reports no generation, as an adapter that cannot tell
    session = AskSession(service)
    assert run(session.ask(QUESTION)).index_generation is None
    add_doc(store, PAPER_C, TEXT_C, name="c.txt")
    index_service.build()
    index.commits = 0
    index.closed = store.listings = 0
    run(session.ask(QUESTION))
    assert store.listings == 0 and index.closed == 0
    session.reload()
    assert index.closed == 1
    run(session.ask(QUESTION))
    assert store.listings == 1


def test_close_lets_go_of_the_index_and_the_next_question_loads_it_again():
    store = make_store()
    service, index, _, _ = build(store)
    session = AskSession(service)
    run(session.ask(QUESTION))
    session.close()
    assert index.closed == 1
    run(session.ask(QUESTION))
    assert store.listings == 2


def test_warm_up_searches_once_and_the_question_after_it_does_not_list_the_corpus_again():
    store = make_store()
    embedder = CountingEmbedder()
    service, index, _, _ = build(store, embedder)
    session = AskSession(service)
    run(session.warm_up())
    assert embedder.queries[-1:] == ["warm-up"] and index.keywords == [None]
    assert store.listings == 1
    run(session.ask(QUESTION))
    assert store.listings == 1


def test_warm_up_does_nothing_without_an_index_or_an_empty_corpus():
    store = make_store()
    qa = FakeQA()
    run(AskSession(AskService(store=store, qa=qa)).warm_up())
    assert store.listings == 0
    embedder = FakeEmbedder()
    index = FakeIndex(embedder_fingerprint=embedder.fingerprint)
    retrieval = Retrieval(embedder, index, None, 10, 3)
    service = AskService(store=InMemoryStore(), qa=FakeQA(), retrieval=lambda: retrieval)
    run(AskSession(service).warm_up())
    assert index.keywords == []


# ------------------------------------------------------------------ what a question returns


def test_the_answer_and_the_contexts_are_those_of_the_one_shot_ask():
    store = make_store()
    service, _, _, qa = build(store)
    one_shot = run(service.ask(QUESTION, lambda _: None))
    shot_contexts = qa.contexts
    result = run(AskSession(service).ask(QUESTION))
    assert result.answer == one_shot
    assert list(result.contexts) == shot_contexts == qa.contexts
    assert result.sources == tuple(dict.fromkeys(c.doc_id for c in result.contexts))
    assert result.warnings == ()


def test_with_the_additions_of_skip_summary_the_result_is_still_that_of_the_one_shot_ask():
    text = blocks.laid((230, blocks.FENCED, blocks.NEXT), (860, blocks.EQ_TAG, ""))
    _, qa, service = blocks.build(
        {blocks.A: [text]}, [(blocks.A, 0)], lookup=blocks.LOOKUP, circuit_lists=True
    )
    question = "In the lamp paper, what does Eq. (2) state?" + blocks.ASKED
    one_shot = run(service.ask(question, lambda _: None))
    shot_contexts = qa.contexts
    result = run(AskSession(service).ask(question))
    assert result.answer == one_shot and list(result.contexts) == shot_contexts == qa.contexts
    texts = [c.text for c in result.contexts]
    assert (
        len(texts) == 2 and f"{blocks.FENCED}\n\n{blocks.LIST}\n" in texts[0]
    )  # the block, the list
    assert texts[1].startswith(blocks.HEADER)  # and the chunk of the named equation
    assert result.sources == (blocks.A,)


def test_options_that_change_from_one_question_to_the_next_do_not_leak():
    text = blocks.laid((285, blocks.EQUATION, ""))
    whole = text[: text.index(blocks.EQUATION) + len(blocks.EQUATION)]
    store, qa, service = blocks.build({blocks.A: [text]}, [(blocks.A, 0)], default=False)
    index = service.retrieval().index
    plain = [blocks.at(store, blocks.A, 0)]
    session = AskSession(service)

    def contexts(options):
        result = run(session.ask(QUESTION, options))
        return [c.text for c in result.contexts]

    assert contexts(AskOptions(whole_blocks=True, keywords="never")) == [whole]
    assert contexts(AskOptions()) == [plain[0].text]  # the default is off again
    assert contexts(AskOptions(use_index=False)) == [] and qa.contexts is None
    assert contexts(AskOptions(whole_blocks=False, keywords="always")) == [plain[0].text]
    assert contexts(AskOptions(whole_blocks=True)) == [whole]
    assert index.keywords == ["never", None, "always", None]  # no question used another's choice


def test_the_options_reach_the_index_and_the_result_carries_the_warnings():
    store = make_store()
    service, index, _, _ = build(store)
    add_doc(store, PAPER_C, TEXT_C, name="c.txt")
    seen: list[str] = []
    session = AskSession(service)
    result = run(session.ask(QUESTION, AskOptions(keywords="never"), seen.append))
    assert index.keywords == ["never"]
    assert len(result.warnings) == 1 and "not in the index" in result.warnings[0]
    assert list(result.warnings) == seen


def test_a_question_that_does_not_use_the_index_has_no_contexts_and_no_sources():
    store = make_store()
    service, index, _, qa = build(store)
    result = run(AskSession(service).ask(QUESTION, AskOptions(use_index=False)))
    assert result.contexts == () and result.sources == () and result.index_generation is None
    assert index.keywords == [] and qa.contexts is None and len(qa.seen) == 2
    assert result.timings.retrieve_s == 0.0


def test_the_timings_split_the_wait_the_retrieval_and_the_answer():
    store = make_store()
    service, _, _, _ = build(store)
    ticks = itertools.count(0, 2)
    session = AskSession(service, clock=lambda: float(next(ticks)))
    result = run(session.ask(QUESTION))
    # clock reads: start 0, lock 2, before the answer 4, end 6
    assert (result.timings.retrieve_s, result.timings.answer_s, result.timings.wall_s) == (
        2.0,
        2.0,
        6.0,
    )


def test_the_warnings_of_the_corpus_listing_are_in_the_result_with_and_without_the_index():
    store = WarningStore()
    add_doc(store, PAPER_A, TEXT_A, name="a.txt")
    add_doc(store, PAPER_B, TEXT_B, name="b.txt")
    service, _, _, _ = build(store)
    seen: list[str] = []
    result = run(AskSession(service).ask(QUESTION, None, seen.append))
    assert result.warnings == ("listing: a warning",) and seen == ["listing: a warning"]
    plain = AskService(store=store, qa=FakeQA())
    seen = []
    result = run(AskSession(plain).ask(QUESTION, None, seen.append))
    assert result.warnings == ("listing: a warning",) and seen == ["listing: a warning"]


def test_the_phases_come_in_order_in_the_event_loop_and_the_search_is_done_before_the_answer():
    store = make_store()
    service, index, _, _ = build(store)
    session = AskSession(service)
    seen: list[tuple[str, int, bool]] = []
    loop_thread = []

    async def ask(options=None):
        loop_thread[:] = [threading.get_ident()]
        await session.ask(
            QUESTION,
            options,
            on_phase=lambda p: seen.append(
                (p, len(index.keywords), threading.get_ident() == loop_thread[0])
            ),
        )

    run(ask())
    assert seen == [("retrieving", 0, True), ("answering", 1, True)]
    seen.clear()
    run(ask(AskOptions(use_index=False)))
    assert seen == [("answering", 1, True)]  # the answerer retrieves for itself


def test_a_question_that_fails_before_the_answer_announces_no_answering_phase():
    store = make_store()
    service, _, _, _ = build(store)
    seen: list[str] = []
    with pytest.raises(ValueError, match="whole_blocks needs"):
        run(AskSession(service).ask(QUESTION, AskOptions(whole_blocks=True), on_phase=seen.append))
    assert seen == []  # the options are checked first
    empty = Retrieval(FakeEmbedder(), FakeIndex(), None, 10, 3)
    session = AskSession(AskService(store=InMemoryStore(), qa=FakeQA(), retrieval=lambda: empty))
    with pytest.raises(RuntimeError, match="no ingested documents"):
        run(session.ask(QUESTION, on_phase=seen.append))
    assert seen == ["retrieving"]


def test_an_option_the_question_cannot_use_is_a_value_error_and_the_session_goes_on():
    store = make_store()
    service, _, _, _ = build(store)
    session = AskSession(service)
    with pytest.raises(ValueError, match="whole_blocks needs"):
        run(session.ask(QUESTION, AskOptions(whole_blocks=True)))
    with pytest.raises(ValueError, match="keywords applies"):
        run(session.ask(QUESTION, AskOptions(use_index=False, keywords="never")))
    assert run(session.ask(QUESTION)).answer


# ------------------------------------------------------------------ errors do not end it


def test_a_failing_answer_is_raised_and_the_next_question_works():
    store = make_store()
    service, index, _, _ = build(store, qa=FailingOnce())
    session = AskSession(service)
    with pytest.raises(ConnectionError):
        run(session.ask(QUESTION))
    assert run(session.ask(QUESTION)).answer
    assert index.closed == 0


def test_an_index_that_cannot_answer_is_raised_and_the_listing_is_kept():
    store = make_store()
    _, index, _, _ = build(store)
    empty = FakeIndex(embedder_fingerprint=index.embedder_fingerprint)  # nothing committed
    retrieval = Retrieval(FakeEmbedder(), empty, None, 10, 3)
    session = AskSession(AskService(store=store, qa=FakeQA(), retrieval=lambda: retrieval))
    with pytest.raises(IndexNotReadyError, match="empty"):
        run(session.ask(QUESTION))
    with pytest.raises(IndexNotReadyError, match="empty"):
        run(session.ask(QUESTION))
    assert store.listings == 1


def test_a_corpus_without_documents_is_an_error_with_or_without_an_index():
    empty = InMemoryStore()
    retrieval = Retrieval(FakeEmbedder(), FakeIndex(), None, 10, 3)
    plain = AskService(store=empty, qa=FakeQA())
    indexed = AskService(store=empty, qa=FakeQA(), retrieval=lambda: retrieval)
    for service in (plain, indexed):
        with pytest.raises(RuntimeError, match="no ingested documents"):
            run(AskSession(service).ask(QUESTION))


# ------------------------------------------------------------------ concurrency


def test_retrieval_is_one_question_at_a_time():
    store = make_store()
    embedder = Gated()
    service, _, _, _ = build(store, embedder)
    embedder.release.set()  # the build above embedded documents; gate only the questions
    embedder.most = 0
    embedder.release.clear()
    session = AskSession(service)

    async def both():
        tasks = [asyncio.create_task(session.ask(QUESTION)) for _ in range(2)]
        await asyncio.to_thread(embedder.entered.wait, 5)
        await asyncio.sleep(0.2)  # the second would be inside by now if nothing held it back
        assert embedder.inside == 1
        embedder.release.set()
        return await asyncio.gather(*tasks)

    results = run(both())
    assert embedder.most == 1 and len(results) == 2


def test_answers_overlap_when_the_caller_asks_for_several_at_once():
    store = make_store()
    qa = SlowQA()
    service, _, _, _ = build(store, qa=qa)
    session = AskSession(service)

    async def both():
        return await asyncio.gather(session.ask(QUESTION), session.ask("graph spectra"))

    assert len(run(both())) == 2 and qa.most == 2


def test_the_blocking_reads_of_the_store_stay_off_the_event_loop_thread():
    text = blocks.laid((285, blocks.EQUATION, ""))
    store = ThreadStore()
    add_doc(store, blocks.A, [(text, "Part 0")], name="p.txt").title = "Lamp a quantum spectra"
    embedder = FakeEmbedder()
    index = blocks.FixedIndex(embedder_fingerprint=embedder.fingerprint)
    blocks.IndexService(
        store=store,
        embedder=embedder,
        index=index,
        chunk_chars=blocks.CHARS,
        overlap=blocks.OVERLAP,
        log=lambda _: None,
    ).build()
    index.answer = [blocks.at(store, blocks.A, 0)]
    retrieval = Retrieval(embedder, index, None, 100, 10)
    indexed = AskService(
        store=store, qa=FakeQA(), retrieval=lambda: retrieval, whole_blocks=blocks.BLOCKS
    )
    plain = AskService(store=store, qa=FakeQA())

    async def both():
        store.threads.clear()
        await AskSession(indexed).ask(QUESTION)  # lists, retrieves and completes the chunks
        indexed_threads = set(store.threads)
        store.threads.clear()
        await AskSession(plain).ask(QUESTION)  # lists the corpus and reads every paper
        return threading.get_ident(), indexed_threads, set(store.threads)

    loop, with_index, without_index = run(both())
    assert with_index and loop not in with_index
    assert without_index and loop not in without_index


# ------------------------------------------------------------------ a commit during a question


def test_a_commit_that_lands_during_a_question_is_seen_by_the_next_one():
    store = make_store()
    embedder = CommitsDuringSearch()
    service, index, index_service, _ = build(store, embedder)
    session = AskSession(service)
    question = "graph spectra laplacian"

    def land():
        add_doc(store, PAPER_C, TEXT_C, name="c.txt")
        index_service.build()  # commits generation g2 while the first question is retrieving
        index.closed = store.listings = 0

    embedder.before_search = land
    during = run(session.ask(question))
    assert during.index_generation == "g1"  # read before the search, as documented
    assert PAPER_C not in during.sources  # the question keeps the listing it started with
    assert any("no longer in the corpus" in w for w in during.warnings)
    after = run(session.ask(question))
    assert after.index_generation == "g2" and PAPER_C in after.sources
    assert index.closed == 1 and store.listings == 1  # one reload, at the next question


# ------------------------------------------------------------------ answer from given passages

OPENS = "Qubits decohere when they couple to the environment."
ENDS = "Gates act on the qubits one at a time."
LAST = "The proof uses two lemmas."
PAGES_B = [(f"{OPENS}\n{ENDS}", "Intro"), ("The second page opens here.\n" + LAST, "Proof")]
CUT = "one at a time.\n\nThe second page opens"  # the end of page 1, the break, the start of page 2


class WarningQA(FakeQA):
    async def ask(self, question, documents, warn, contexts=None):
        warn("the answerer cut the sources")
        return await super().ask(question, documents, warn, contexts)


def no_retrieval():
    raise AssertionError("the index must not be opened to answer from given passages")


def passage_store(store=None):
    store = store or InMemoryStore()
    add_doc(store, PAPER_A, TEXT_A, name="a.txt")
    add_doc(store, PAPER_B, PAGES_B, name="b.txt")
    return store


def given(qa=None, store=None, **kw):
    store = passage_store() if store is None else store
    qa = qa or FakeQA()
    return AskSession(AskService(store=store, qa=qa, retrieval=no_retrieval), **kw), qa, store


def test_given_passages_reach_the_answerer_in_order_and_unchanged():
    session, qa, _ = given()
    passages = [
        GivenPassage(PAPER_B, LAST),
        GivenPassage(PAPER_A, TEXT_A[0][0][:50]),
        GivenPassage(PAPER_B, OPENS),
    ]
    result = run(session.answer_from_passages("what is it", passages))
    assert [(c.doc_id, c.text) for c in qa.contexts] == [(p.doc_id, p.text) for p in passages]
    assert result.contexts == tuple(qa.contexts)
    assert result.sources == (PAPER_B, PAPER_A)  # by first appearance
    assert result.answer == "answer to 'what is it' from 2 docs"
    assert result.index_generation is None and result.warnings == ()
    assert qa.seen == ["b.txt", "a.txt"] and qa.markdown == ["", ""]  # manifests only


def test_a_given_passage_gets_the_pages_the_name_and_the_offset_of_its_text():
    session, _, _ = given()
    document = "".join(f"{t}\n\n" for t, _ in PAGES_B)
    result = run(
        session.answer_from_passages(
            "q",
            [
                GivenPassage(PAPER_B, ENDS),
                GivenPassage(PAPER_B, CUT),
                GivenPassage(PAPER_B, LAST),
            ],
        )
    )
    one, across, two = result.contexts
    assert (one.first_page, one.last_page, one.name) == (1, 1, f"{PAPER_B[:16]} pages 1-1")
    assert (across.first_page, across.last_page) == (1, 2)
    assert across.name == f"{PAPER_B[:16]} pages 1-2"
    assert (two.first_page, two.last_page, two.name) == (2, 2, f"{PAPER_B[:16]} pages 2-2")
    for chunk in result.contexts:
        assert document[chunk.start : chunk.start + len(chunk.text)] == chunk.text
        assert not chunk.is_reference


def test_white_space_may_differ_between_the_given_text_and_the_paper():
    session, qa, _ = given()
    spaced = "Qubits   decohere when they\ncouple  to the environment.\nGates act on"
    result = run(session.answer_from_passages("q", [GivenPassage(PAPER_B, spaced)]))
    (chunk,) = result.contexts
    assert chunk.text == spaced and qa.contexts[0].text == spaced  # kept as given
    assert (chunk.first_page, chunk.last_page, chunk.start) == (1, 1, 0)
    assert run(session.answer_from_passages("q", [GivenPassage(PAPER_B, f" {LAST}\n")])).contexts


def pages_of(passage, *pages):
    """The first and last page a passage is placed on in a paper of ``pages``."""
    store = InMemoryStore()
    add_doc(store, PAPER_B, [(text, "T") for text in pages], name="b.txt")
    session, _, _ = given(store=store)
    (chunk,) = run(session.answer_from_passages("q", [GivenPassage(PAPER_B, passage)])).contexts
    return chunk.first_page, chunk.last_page


def test_each_tier_is_tried_before_the_looser_ones():
    # tier 1 (as it is) before tier 2: the double space of page 1 would match "a  b" loosely
    assert pages_of("a b", "a  b first.", "a b second.") == (2, 2)
    # tier 2 (any white space) before tier 3: without the space, page 1's "alone" matches "al one"
    assert pages_of("al  one", "alone here.", "al one there.") == (2, 2)
    # and tier 3 when the first two find nothing
    assert pages_of("a_b", "a b first.", "a_b second.") == (2, 2)
    assert pages_of("a $b$", "a b first.", "ab second.") == (1, 1)


def test_the_white_space_tier_and_the_markup_tier_find_a_passage_across_a_page_break():
    loose = "one  at   a time.\n \nThe second page"
    assert pages_of(loose, *(t for t, _ in PAGES_B)) == (1, 2)
    pages = ("Before.\n$$\\begin{equation}\nE = m", "c^2\n\\tag{1}\n\\end{equation}$$ After.")
    assert pages_of("\\begin{equation} E=mc^2 \\end{equation}", *pages) == (1, 2)


def test_unicode_quotes_dashes_ligatures_and_page_markers_do_not_keep_a_passage_from_being_found():
    document_text = "Then \ufb01nal x \u2212 y = \u201cz\u201d \u2014 done.\nNext page begins."
    store = InMemoryStore()
    add_doc(store, PAPER_B, [(document_text, "T"), ("Second page.", "U")], name="b.txt")
    session, qa, _ = given(store=store)
    document = "".join(f"{t}\n\n" for t in (document_text, "Second page."))
    passages = [
        GivenPassage(PAPER_B, 'final x - y = "z" - done.'),
        GivenPassage(
            PAPER_B, "Next page begins.\n<!-- page 2 | method=text_layer -->\nSecond page"
        ),
    ]
    result = run(session.answer_from_passages("q", passages))
    assert result.warnings == ()
    assert [c.text for c in qa.contexts] == [p.text for p in passages]  # as given
    one, two = result.contexts
    assert document[one.start : one.start + 7] == "\ufb01nal x " and one.start == 5
    assert (one.first_page, one.last_page, two.first_page, two.last_page) == (1, 1, 1, 2)
    assert document[two.start :].startswith("Next page begins.")


def test_the_first_occurrence_of_a_text_is_the_one_placed():
    store = InMemoryStore()
    add_doc(store, PAPER_B, [("same words here", "A"), ("same words here", "B")], name="b.txt")
    session, _, _ = given(store=store)
    result = run(session.answer_from_passages("q", [GivenPassage(PAPER_B, "same words")]))
    assert (result.contexts[0].first_page, result.contexts[0].start) == (1, 0)


EQUATION_PAGES = [
    ("Page one has no equation.", "Intro"),
    ("Before it.\n\n$$\\begin{equation}\nE = m c^2\n\\tag{1}\n\\end{equation}$$After it.", "Body"),
]


def test_a_passage_is_found_without_the_markup_a_display_equation_carries():
    store = InMemoryStore()
    add_doc(store, PAPER_B, EQUATION_PAGES, name="b.txt")
    session, _, _ = given(store=store)
    bare = "\\begin{equation} E = m c^2 \\end{equation}"  # no $$, no \\tag{1}, other line breaks
    truncated = "E=mc^2 \\end{equation"  # a gold text may cut the closing brace
    result = run(
        session.answer_from_passages(
            "q", [GivenPassage(PAPER_B, bare), GivenPassage(PAPER_B, truncated)]
        )
    )
    document = "".join(f"{t}\n\n" for t, _ in EQUATION_PAGES)
    for chunk, given_text in zip(result.contexts, (bare, truncated), strict=True):
        assert chunk.text == given_text  # the given text, not the paper's
        assert (chunk.first_page, chunk.last_page) == (2, 2) and chunk.start >= len(document) // 3
    first = result.contexts[0]
    assert document[first.start :].startswith("E = m c^2\n")  # the first character matched
    assert result.warnings == ()


def test_the_markup_tier_does_not_match_other_text():
    store = InMemoryStore()
    add_doc(store, PAPER_B, EQUATION_PAGES, name="b.txt")
    session, _, _ = given(store=store)
    other = "\\begin{equation} E = m c^3 \\end{equation}"
    result = run(session.answer_from_passages("q", [GivenPassage(PAPER_B, other)]))
    assert len(result.warnings) == 1  # not found


def test_a_passage_that_no_tier_finds_cites_all_the_pages_of_its_paper_and_warns_once():
    session, qa, _ = given()
    seen: list[str] = []
    missing = GivenPassage(PAPER_B, "words no paper holds")
    blank = GivenPassage(PAPER_B, " \n ")
    found = GivenPassage(PAPER_B, LAST)
    result = run(session.answer_from_passages("q", [missing, found, blank], seen.append))
    assert [(c.first_page, c.last_page, c.name) for c in result.contexts] == [
        (1, 2, f"{PAPER_B[:16]} pages 1-2"),
        (2, 2, f"{PAPER_B[:16]} pages 2-2"),
        (1, 2, f"{PAPER_B[:16]} pages 1-2"),
    ]
    assert [c.text for c in qa.contexts] == [missing.text, found.text, blank.text]  # unchanged
    assert len(result.warnings) == 2 and list(result.warnings) == seen
    assert all(w.startswith(f"{UNPLACED_WARNING} {PAPER_B[:16]};") for w in seen)
    assert all(w.endswith("pages 1-2") for w in seen)


def test_a_paper_without_page_markers_cites_page_1_and_warns():
    store = InMemoryStore()
    manifest = add_doc(store, PAPER_B, [("x", "T")], name="b.txt")
    store.canonical[PAPER_B] = (manifest, "text without a page marker")
    session, qa, _ = given(store=store)
    result = run(session.answer_from_passages("q", [GivenPassage(PAPER_B, "text")]))
    (chunk,) = result.contexts
    assert (chunk.first_page, chunk.last_page, chunk.start) == (1, 1, 0)
    assert result.warnings == (f"{UNPLACED_WARNING} {PAPER_B[:16]}; cited with pages 1-1",)
    assert qa.contexts == [chunk]


@pytest.mark.parametrize(
    ("passages", "message"),
    [
        ([GivenPassage(PAPER_C, "anything")], f"paper {PAPER_C[:16]} is not in the corpus"),
        ([GivenPassage(PAPER_B, LAST), GivenPassage(PAPER_C, LAST)], f"paper {PAPER_C[:16]}"),
    ],
)
def test_an_unknown_paper_is_a_value_error_and_the_session_goes_on(passages, message):
    session, qa, _ = given()
    seen: list[str] = []
    with pytest.raises(ValueError, match=message):
        run(session.answer_from_passages("q", passages, on_phase=seen.append))
    assert qa.contexts is None and seen == []  # the answerer was not called
    assert run(session.answer_from_passages("q", [GivenPassage(PAPER_B, LAST)])).answer


def test_no_passages_asks_the_answerer_with_no_context_and_lists_nothing():
    session, qa, store = given(store=passage_store(CountingStore()))
    seen: list[str] = []
    result = run(session.answer_from_passages("q", [], on_phase=seen.append))
    assert qa.contexts == [] and qa.seen == []  # an empty list, not None (None retrieves)
    assert (
        result.answer == "answer to 'q' from 0 docs"
        and result.contexts == ()
        and result.sources == ()
    )
    assert result.warnings == () and result.index_generation is None
    assert seen == ["answering"] and store.listings == 0


class UnreadableStore(InMemoryStore):
    def markdown(self, doc):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")


def test_a_paper_that_cannot_be_read_is_raised_and_not_reported_as_a_missing_passage():
    session, qa, _ = given(store=passage_store(UnreadableStore()))
    seen: list[str] = []
    with pytest.raises(UnicodeDecodeError):
        run(session.answer_from_passages("q", [GivenPassage(PAPER_B, LAST)], seen.append))
    assert qa.contexts is None and seen == []


def test_the_phase_comes_once_in_the_event_loop_after_the_passages_are_placed():
    session, _, _ = given()
    seen: list[tuple[str, bool]] = []

    async def ask():
        loop = threading.get_ident()
        await session.answer_from_passages(
            "q",
            [GivenPassage(PAPER_B, LAST)],
            on_phase=lambda p: seen.append((p, threading.get_ident() == loop)),
        )

    run(ask())
    assert seen == [("answering", True)]


class NoLock:
    def __enter__(self):
        raise AssertionError("the retrieval lock must not be taken")

    def __exit__(self, *exc):
        return False

    acquire = __enter__


def test_the_retrieval_lock_is_not_taken():
    session, _, _ = given()
    session._lock = NoLock()
    assert run(session.answer_from_passages("q", [GivenPassage(PAPER_B, LAST)])).answer


def test_the_store_is_read_off_the_event_loop_thread():
    session, _, store = given(store=passage_store(ThreadStore()))

    async def ask():
        store.threads.clear()
        await session.answer_from_passages("q", [GivenPassage(PAPER_B, LAST)])
        return threading.get_ident(), set(store.threads)

    loop, threads = run(ask())
    assert threads and loop not in threads


def test_the_warnings_of_the_listing_and_of_the_answerer_are_in_the_result_and_given_to_warn():
    session, _, _ = given(WarningQA(), passage_store(WarningStore()))
    seen: list[str] = []
    result = run(session.answer_from_passages("q", [GivenPassage(PAPER_B, LAST)], seen.append))
    assert result.warnings == ("listing: a warning", "the answerer cut the sources")
    assert list(result.warnings) == seen


def test_the_timings_split_the_placing_from_the_answer():
    ticks = itertools.count(0, 2)
    session, _, _ = given(clock=lambda: float(next(ticks)))
    result = run(session.answer_from_passages("q", [GivenPassage(PAPER_B, LAST)]))
    # clock reads: start 0, before the answer 2, end 4
    assert (result.timings.retrieve_s, result.timings.answer_s, result.timings.wall_s) == (
        2.0,
        2.0,
        4.0,
    )
