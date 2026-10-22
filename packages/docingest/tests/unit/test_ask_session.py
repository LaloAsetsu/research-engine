"""AskSession with fakes: the index stays open between questions and is reloaded when, and only
when, it has a new commit; the structured result; one retrieval at a time."""

import asyncio
import itertools
import threading

import pytest
import test_ask_whole_blocks as blocks
from fakes import FakeEmbedder, FakeIndex, FakeQA, FakeReranker, InMemoryStore, add_doc

from docingest.application.ask import AskOptions, AskService, AskSession, Retrieval
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
