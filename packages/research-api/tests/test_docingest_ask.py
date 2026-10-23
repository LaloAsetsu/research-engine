"""The docingest adapter: the engine on a real ``AskSession``, error wording, the paper reader."""

from __future__ import annotations

import asyncio
import hashlib
import threading
from pathlib import Path
from typing import Any

import pytest
from docingest.adapters.store.filesystem import FilesystemStore
from docingest.application.ask import AskResult, AskService, AskSession, AskTimings
from docingest.domain.chunking import Chunk
from docingest.domain.errors import (
    IndexMismatchError,
    IndexNotReadyError,
    InvalidConfigError,
    NotConfiguredError,
    RetrievalError,
)
from docingest.domain.models import (
    DocumentManifest,
    PageMethod,
    PageRecord,
    SourceKind,
    SourceMetadata,
)
from docingest.ports import StoredDocument
from helpers import arun

from research_api.adapters.docingest_ask import (
    DocingestAskEngine,
    DocingestDocumentReader,
    OfflineAskEngine,
    OfflineDocumentReader,
    open_docingest,
)
from research_api.adapters.docingest_ask.documents import PAGES_KEPT
from research_api.adapters.docingest_ask.errors import (
    REASON_CONFIG,
    REASON_INDEX,
    REASON_SLOW,
    REASON_START,
    classify,
    start_reason,
)
from research_api.domain.ask import AskOptions, AskQuestion
from research_api.domain.errors import (
    ConflictError,
    InvalidInputError,
    NotFoundError,
    UnavailableError,
    UpstreamError,
)
from research_api.ports.ask import EngineHealth

# --- a corpus on disk ---------------------------------------------------------------------


def doc_id(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


def manifest(
    name: str,
    *,
    kind: SourceKind = SourceKind.PDF,
    pages: int = 2,
    title: str | None = None,
    metadata: SourceMetadata | None = None,
    section_titles: bool = False,
) -> DocumentManifest:
    return DocumentManifest(
        doc_id=doc_id(name),
        source_path=f"/in/{name}",
        source_name=name,
        source_kind=kind,
        mime="application/pdf",
        size_bytes=10,
        n_pages=pages,
        source_pages=pages,
        pages=[
            PageRecord(
                index=i,
                method=PageMethod.LATEX if kind is SourceKind.LATEX else PageMethod.TEXT_LAYER,
                n_chars=10,
                seconds=0.0,
                title=f"Section {i + 1}" if section_titles else None,
            )
            for i in range(pages)
        ],
        pipeline_version="test",
        config_hash="h",
        title=title,
        metadata=metadata,
    )


def markdown(*texts: str, method: str = "text_layer") -> str:
    return "".join(f"<!-- page {i} | method={method} -->\n{t}\n\n" for i, t in enumerate(texts, 1))


@pytest.fixture
def store(tmp_path: Path) -> FilesystemStore:
    store = FilesystemStore(tmp_path / "normalized")
    store.save(
        manifest("alpha.pdf", title="Alpha study of qubits"), markdown("alpha one", "alpha two")
    )
    store.save(
        manifest(
            "beta.pdf",
            metadata=SourceMetadata(
                title="Beta metadata title",
                authors=["Ada Example", "Bo Sample"],
                year=2024,
                doi="10.1000/x",
                arxiv_id="2401.00001",
                url="https://example.org/beta",
            ),
        ),
        markdown("beta one $x^2$", "beta two"),
    )
    store.save(manifest("gamma-source-name.pdf"), markdown("gamma one", "gamma two"))
    store.save(
        manifest("delta.tex", kind=SourceKind.LATEX, pages=3, title="Delta", section_titles=True),
        markdown("intro", "method", "results", method="latex"),
    )
    return store


class CountingStore:
    """A store that counts how often its listing and its files are read."""

    def __init__(self, inner: FilesystemStore) -> None:
        self.inner, self.listings, self.reads = inner, 0, 0

    def corpus(self):
        self.listings += 1
        return self.inner.corpus()

    def markdown(self, doc):
        self.reads += 1
        return self.inner.markdown(doc)


class Clock:
    def __init__(self) -> None:
        self.at = 0.0

    def __call__(self) -> float:
        return self.at


def reader_for(store: Any, **kwargs: Any) -> DocingestDocumentReader:
    return DocingestDocumentReader(lambda: store, **kwargs)


def test_the_listing_is_sorted_by_title_with_the_fallbacks(store: FilesystemStore) -> None:
    found = reader_for(store).search("", 50)
    assert [d.title for d in found.items] == [
        "Alpha study of qubits",
        "Beta metadata title",  # the title of the metadata when the manifest has none
        "Delta",
        "gamma-source-name.pdf",  # the file name when nothing else is there
    ]
    assert found.total == 4
    by_name = {d.source_name: d for d in found.items}
    assert by_name["delta.tex"].page_kind == "section" and by_name["delta.tex"].pages == 3
    assert by_name["alpha.pdf"].page_kind == "page" and by_name["alpha.pdf"].source_kind == "pdf"
    assert by_name["alpha.pdf"].short_id == doc_id("alpha.pdf")[:16]


def test_search_by_part_of_a_title_or_an_id_prefix_with_a_limit(store: FilesystemStore) -> None:
    reader = reader_for(store)
    assert [d.source_name for d in reader.search("QUBITS", 10).items] == ["alpha.pdf"]
    assert [d.source_name for d in reader.search(doc_id("beta.pdf")[:10], 10).items] == ["beta.pdf"]
    assert reader.search("zzz", 10).total == 0
    limited = reader.search("", 2)
    assert len(limited.items) == 2 and limited.total == 4


def test_a_paper_has_its_metadata_and_pages(store: FilesystemStore) -> None:
    reader = reader_for(store)
    beta = reader.get(doc_id("beta.pdf")[:16])
    assert beta.authors == ("Ada Example", "Bo Sample") and beta.year == 2024
    assert (beta.doi, beta.arxiv_id, beta.url) == (
        "10.1000/x",
        "2401.00001",
        "https://example.org/beta",
    )
    assert [(p.page, p.method) for p in beta.pages] == [(1, "text_layer"), (2, "text_layer")]
    alpha = reader.get(doc_id("alpha.pdf"))
    assert (alpha.authors, alpha.year, alpha.doi) == ((), None, None)
    delta = reader.get(doc_id("delta.tex")[:20])
    assert [(p.page, p.title) for p in delta.pages] == [
        (1, "Section 1"),
        (2, "Section 2"),
        (3, "Section 3"),
    ]


def test_a_page_is_the_text_between_its_markers(store: FilesystemStore) -> None:
    reader = reader_for(store)
    page = reader.page(doc_id("beta.pdf")[:16], 1)
    assert (page.page, page.pages, page.kind, page.markdown) == (1, 2, "page", "beta one $x^2$")
    assert reader.page(doc_id("beta.pdf"), 2).markdown == "beta two"
    section = reader.page(doc_id("delta.tex")[:16], 3)
    assert (section.kind, section.title, section.markdown) == ("section", "Section 3", "results")


def test_a_missing_paper_or_page_is_not_found(store: FilesystemStore) -> None:
    reader = reader_for(store)
    with pytest.raises(NotFoundError):
        reader.get("f" * 16)
    with pytest.raises(NotFoundError):
        reader.page(doc_id("beta.pdf"), 3)
    with pytest.raises(NotFoundError):
        reader.page(doc_id("beta.pdf"), 0)


class ListStore:
    """Documents in memory, for a corpus the filesystem store cannot hold (two ids that share
    their first 16 characters would share a folder)."""

    def __init__(self, documents: list[StoredDocument]) -> None:
        self.documents = documents

    def corpus(self) -> tuple[list[StoredDocument], list[str]]:
        return self.documents, []

    def markdown(self, doc: StoredDocument) -> str:
        return markdown("x")


def test_an_ambiguous_prefix_is_a_conflict() -> None:
    first = manifest("one.pdf").model_copy(update={"doc_id": "ab" * 8 + "0" * 48})
    second = manifest("two.pdf").model_copy(update={"doc_id": "ab" * 8 + "1" * 48})
    reader = reader_for(
        ListStore([StoredDocument(first, "/a", True), StoredDocument(second, "/b", True)])
    )
    with pytest.raises(ConflictError):
        reader.get("ab" * 8)
    assert reader.get("ab" * 8 + "0" * 3).summary.doc_id == first.doc_id
    assert reader.page(second.doc_id, 1).markdown == "x"


def test_a_paper_has_one_page_count_whatever_its_markers_say() -> None:
    """The listing, the detail and a page all count the manifest's pages. Here the text holds a
    marker for page 1 only, while the manifest lists two pages."""
    paper = manifest("short-text.pdf", pages=2)
    reader = reader_for(ListStore([StoredDocument(paper, "/a", True)]))
    assert reader.search("", 5).items[0].pages == 2
    assert reader.get(paper.doc_id).summary.pages == 2
    assert reader.page(paper.doc_id, 1).pages == 2
    with pytest.raises(NotFoundError):  # a page the manifest does not list is not served
        reader.page(paper.doc_id, 3)


def test_the_text_of_a_paper_that_went_missing_is_not_found(
    store: FilesystemStore, tmp_path: Path
) -> None:
    reader = reader_for(store)
    reader.search("", 1)  # list it
    (tmp_path / "normalized" / doc_id("alpha.pdf")[:16] / "document.md").unlink()
    with pytest.raises(NotFoundError, match="not available"):
        reader.page(doc_id("alpha.pdf"), 1)


def test_the_text_of_a_paper_that_is_not_utf8_is_not_found(
    store: FilesystemStore, tmp_path: Path
) -> None:
    reader = reader_for(store)
    reader.search("", 1)
    (tmp_path / "normalized" / doc_id("alpha.pdf")[:16] / "document.md").write_bytes(
        b"<!-- page 1 | method=text_layer -->\n\xff\xfe caf\xe9\n"
    )
    with pytest.raises(NotFoundError, match="not available"):
        reader.page(doc_id("alpha.pdf"), 1)


class BrokenListing:
    def corpus(self) -> tuple[list[StoredDocument], list[str]]:
        raise OSError("the papers folder is gone")

    def markdown(self, doc: StoredDocument) -> str:
        raise AssertionError("not reached")


def test_a_papers_folder_that_cannot_be_read_is_a_retry_later_not_a_crash() -> None:
    reader = reader_for(BrokenListing())
    for read in (
        lambda: reader.search("", 5),
        lambda: reader.get("a" * 16),
        lambda: reader.page("a" * 16, 1),
    ):
        with pytest.raises(UnavailableError) as raised:
            read()
        assert raised.value.retry_after_s and "gone" not in raised.value.detail


def test_the_listing_is_kept_for_a_while_and_read_again_after(store: FilesystemStore) -> None:
    counted, clock = CountingStore(store), Clock()
    reader = reader_for(counted, monotonic=clock, ttl_s=30)
    for _ in range(3):
        reader.search("", 10)
        reader.get(doc_id("alpha.pdf"))
    assert counted.listings == 1
    clock.at = 29.9
    reader.search("", 10)
    assert counted.listings == 1
    clock.at = 30.0
    reader.search("", 10)
    assert counted.listings == 2


def test_the_pages_of_a_paper_are_split_once_for_all_its_pages(store: FilesystemStore) -> None:
    counted = CountingStore(store)
    reader = reader_for(counted)
    for page in (1, 2, 1, 2):
        reader.page(doc_id("beta.pdf"), page)
    assert counted.reads == 1
    reader.page(doc_id("alpha.pdf"), 1)
    assert counted.reads == 2


def test_only_a_few_papers_keep_their_pages(tmp_path: Path) -> None:
    store = FilesystemStore(tmp_path / "n")
    names = [f"paper{i}.pdf" for i in range(PAGES_KEPT + 2)]
    for name in names:
        store.save(manifest(name), markdown("a", "b"))
    counted = CountingStore(store)
    reader = reader_for(counted)
    for name in names:
        reader.page(doc_id(name), 1)
    assert len(reader._pages) == PAGES_KEPT
    before = counted.reads
    reader.page(doc_id(names[0]), 1)  # the first one was let go
    assert counted.reads == before + 1
    reader.page(doc_id(names[-1]), 1)  # the last one is still there
    assert counted.reads == before + 1


def test_the_store_is_opened_on_first_use_not_before(store: FilesystemStore) -> None:
    opened: list[int] = []

    def open_store() -> FilesystemStore:
        opened.append(1)
        return store

    reader = DocingestDocumentReader(open_store)
    assert opened == []
    reader.search("", 1)
    reader.search("", 1)
    assert opened == [1]


def test_two_first_requests_open_the_store_once(store: FilesystemStore) -> None:
    opened: list[int] = []
    inside = threading.Barrier(2, timeout=5)

    def open_store() -> FilesystemStore:
        opened.append(1)
        threading.Event().wait(0.05)  # long enough for the other request to arrive
        return store

    reader = DocingestDocumentReader(open_store)
    threads = [threading.Thread(target=lambda: (inside.wait(), reader._store)) for _ in "ab"]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert opened == [1]


# --- the engine ---------------------------------------------------------------------------

QUESTION = "What limits T1?"
ID_A = doc_id("alpha.pdf")


class Answerer:
    """The question answerer docingest calls: keeps what it was asked, returns a formatted text."""

    def __init__(self, text: str | None = None, error: Exception | None = None) -> None:
        self.text, self.error, self.seen = text, error, []

    async def ask(self, question, documents, warn, contexts=None):
        self.seen.append((question, [d.manifest.doc_id for d, _ in documents], contexts))
        if self.error:
            raise self.error
        return (
            self.text
            or f"Question: {question}\n\nBecause of loss.\n\nReferences\n\n1. ({ID_A[:16]} pages 1-2): Alpha."
        )


def real_session(store: FilesystemStore, answerer: Answerer) -> AskSession:
    return AskSession(AskService(store=store, qa=answerer))


@arun
async def test_a_real_session_without_the_index_goes_through_the_adapter(
    store: FilesystemStore,
) -> None:
    answerer = Answerer()
    engine = DocingestAskEngine(lambda: real_session(store, answerer))
    phases: list[str] = []
    answer = await engine.ask(AskQuestion(QUESTION, AskOptions(use_index=False)), phases.append)
    assert phases == ["answering"]
    assert answer.answer == "Because of loss."
    assert [(c.key, c.doc_id) for c in answer.citations] == [
        (f"{ID_A[:16]} pages 1-2", ID_A[:16])  # nothing names the full id without the index
    ]
    assert answer.references_text.startswith(f"1. ({ID_A[:16]} pages 1-2)")
    assert answer.contexts == () and answer.sources == () and answer.index_generation is None
    assert answer.timings.wall_s >= 0
    assert answerer.seen[0][0] == QUESTION and len(answerer.seen[0][1]) == 4  # the whole corpus
    assert engine.health().state == "ready"


class StubSession:
    """Returns what the real session returns for a question that used the index."""

    def __init__(self, result: AskResult | None = None, error: Exception | None = None) -> None:
        self.result, self.error = result, error
        self.calls: list[tuple[str, Any]] = []
        self.warmed = self.closed = 0

    async def ask(self, question, options=None, warn=None, on_phase=None):
        self.calls.append((question, options))
        if on_phase:
            on_phase("retrieving")
            on_phase("answering")
        if self.error:
            raise self.error
        return self.result

    async def warm_up(self) -> None:
        self.warmed += 1
        if self.error:
            raise self.error

    def close(self) -> None:
        self.closed += 1


def indexed_result() -> AskResult:
    chunk = Chunk(ID_A, f"{ID_A[:16]} pages 1-2", "chunk text", 1, 2, True, 40)
    return AskResult(
        answer=f"Question: {QUESTION}\n\nLoss ({ID_A[:16]} pages 1-2).\n\nReferences\n\n"
        f"1. ({ID_A[:16]} pages 1-2): Alpha.\n",
        contexts=(chunk,),
        sources=(ID_A,),
        warnings=("1 of 4 documents are not in the index",),
        index_generation="gen-7",
        timings=AskTimings(1.5, 4.5, 6.0),
    )


@arun
async def test_the_result_of_an_indexed_question_is_split_like_a_run_item() -> None:
    session = StubSession(indexed_result())
    engine = DocingestAskEngine(lambda: session)  # type: ignore[arg-type]
    phases: list[str] = []
    options = AskOptions(use_index=True, bm25="always", whole_blocks=False)
    answer = await engine.ask(AskQuestion(QUESTION, options), phases.append)
    assert phases == ["retrieving", "answering"]
    docingest_options = session.calls[0][1]
    assert (docingest_options.use_index, docingest_options.keywords) == (True, "always")
    assert docingest_options.whole_blocks is False
    assert answer.answer == f"Loss ({ID_A[:16]} pages 1-2)."
    assert [(c.doc_id, c.first_page, c.last_page) for c in answer.citations] == [(ID_A, 1, 2)]
    (context,) = answer.contexts
    assert (context.doc_id, context.name, context.start, context.is_reference) == (
        ID_A,
        f"{ID_A[:16]} pages 1-2",
        40,
        True,
    )
    assert context.text == "chunk text"
    assert answer.sources == (ID_A,) and answer.warnings == (
        "1 of 4 documents are not in the index",
    )
    assert answer.index_generation == "gen-7"
    assert (answer.timings.retrieve_s, answer.timings.answer_s, answer.timings.wall_s) == (
        1.5,
        4.5,
        6.0,
    )
    assert engine.health() == EngineHealth("ready", None, "gen-7")


@arun
async def test_the_session_opens_once_in_a_worker_thread() -> None:
    opened: list[int] = []
    session = StubSession(indexed_result())

    def open_session() -> StubSession:
        opened.append(1)
        return session

    engine = DocingestAskEngine(open_session)  # type: ignore[arg-type]
    assert opened == [] and engine.health().state == "warming_up"
    await engine.warm_up()
    await engine.ask(AskQuestion(QUESTION), lambda _: None)
    await engine.ask(AskQuestion(QUESTION), lambda _: None)
    assert opened == [1] and session.warmed == 1
    engine.close()
    assert session.closed == 1


def test_closing_an_engine_that_never_opened_does_nothing() -> None:
    DocingestAskEngine(StubSession).close()  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("error", "state", "reason"),
    [
        (
            IndexNotReadyError("the index is empty; run `docingest index build`"),
            "unavailable",
            REASON_INDEX,
        ),
        (IndexMismatchError("another embedder /secret/path"), "unavailable", REASON_INDEX),
        (NotConfiguredError("[adapters] index is x"), "unavailable", REASON_CONFIG),
        (InvalidConfigError("[index] contexts"), "unavailable", REASON_CONFIG),
        (FileNotFoundError("/private/path/config.toml"), "unavailable", REASON_CONFIG),
        (ImportError("No module named paperqa"), "unavailable", REASON_CONFIG),
        (RuntimeError("something else /private/path"), "unavailable", REASON_START),
    ],
)
@arun
async def test_a_warm_up_that_fails_is_unavailable_with_a_fixed_reason(
    error: Exception, state: str, reason: str, caplog: pytest.LogCaptureFixture
) -> None:
    engine = DocingestAskEngine(lambda: StubSession(error=error))  # type: ignore[arg-type]
    await engine.warm_up()
    health = engine.health()
    assert (health.state, health.reason) == (state, reason)
    assert "private" not in (health.reason or "") and "secret" not in (health.reason or "")
    assert "private" not in caplog.text and "secret" not in caplog.text  # the message stays out
    assert any(getattr(r, "event", "") == "ask.warmup_failed" for r in caplog.records)


class HangingSession(StubSession):
    async def warm_up(self) -> None:
        await asyncio.Event().wait()  # an embedder that takes the connection and never answers


@arun
async def test_a_warm_up_that_never_ends_becomes_unavailable_and_a_question_recovers_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = HangingSession(indexed_result())
    engine = DocingestAskEngine(lambda: session, warm_up_limit_s=0.05)  # type: ignore[arg-type]
    assert engine.health().state == "warming_up"
    await engine.warm_up()
    assert engine.health() == EngineHealth("unavailable", REASON_SLOW)
    assert any(getattr(r, "event", "") == "ask.warmup_failed" for r in caplog.records)
    await engine.ask(AskQuestion(QUESTION), lambda _: None)
    assert engine.health().state == "ready"


@arun
async def test_the_warm_up_limit_covers_building_the_session() -> None:
    def slow() -> StubSession:
        threading.Event().wait(0.3)
        return StubSession(indexed_result())

    engine = DocingestAskEngine(slow, warm_up_limit_s=0.05)  # type: ignore[arg-type]
    await engine.warm_up()
    assert engine.health() == EngineHealth("unavailable", REASON_SLOW)


@arun
async def test_a_warm_up_within_the_limit_is_ready() -> None:
    engine = DocingestAskEngine(lambda: StubSession(indexed_result()), warm_up_limit_s=5)  # type: ignore[arg-type]
    await engine.warm_up()
    assert engine.health().state == "ready"


@arun
async def test_a_session_that_cannot_be_built_is_unavailable() -> None:
    def broken() -> StubSession:
        raise ImportError("No module named 'paperqa'")

    engine = DocingestAskEngine(broken)  # type: ignore[arg-type]
    await engine.warm_up()
    assert engine.health().reason == REASON_CONFIG
    with pytest.raises(UnavailableError, match=r"`qa` extra"):
        await engine.ask(AskQuestion(QUESTION), lambda _: None)


@arun
async def test_the_engine_recovers_when_the_index_is_built_later() -> None:
    session = StubSession(error=IndexNotReadyError("empty"))
    engine = DocingestAskEngine(lambda: session)  # type: ignore[arg-type]
    await engine.warm_up()
    assert engine.health().state == "unavailable"
    session.error, session.result = None, indexed_result()
    await engine.ask(AskQuestion(QUESTION), lambda _: None)
    assert engine.health().state == "ready" and engine.health().index_generation == "gen-7"


@arun
async def test_a_failed_question_says_what_to_do_and_marks_the_engine_only_when_it_is_the_index() -> (
    None
):
    session = StubSession(error=IndexNotReadyError("the index is empty"))
    engine = DocingestAskEngine(lambda: session)  # type: ignore[arg-type]
    with pytest.raises(UnavailableError) as raised:
        await engine.ask(AskQuestion(QUESTION), lambda _: None)
    assert raised.value.retry_after_s == 30 and "index" in raised.value.detail
    assert engine.health().state == "unavailable"

    session.error = ConnectionError("model server /v1 refused")
    engine._health = EngineHealth("ready")  # back to a known state
    with pytest.raises(UnavailableError) as down:
        await engine.ask(AskQuestion(QUESTION), lambda _: None)
    assert "refused" not in down.value.detail
    assert engine.health().state == "ready"  # a server that is down may be back for the next one


@arun
async def test_a_surprise_is_raised_as_it_is_for_the_desk_to_log() -> None:
    surprise = KeyError("boom")
    engine = DocingestAskEngine(lambda: StubSession(error=surprise))  # type: ignore[arg-type]
    with pytest.raises(KeyError):
        await engine.ask(AskQuestion(QUESTION), lambda _: None)


@arun
async def test_cancelling_a_question_is_not_swallowed() -> None:
    class Slow(StubSession):
        async def ask(self, *args: Any, **kwargs: Any) -> AskResult:
            await asyncio.sleep(3600)
            raise AssertionError

    engine = DocingestAskEngine(Slow)  # type: ignore[arg-type]
    task = asyncio.ensure_future(engine.ask(AskQuestion(QUESTION), lambda _: None))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# --- what a member is told ----------------------------------------------------------------


class APIConnectionError(Exception):
    """Named like litellm's: the adapter matches by name, so it needs no litellm."""


class BadRequestError(Exception):
    pass


class ContextWindowExceededError(BadRequestError):
    pass


@pytest.mark.parametrize(
    ("error", "kind", "retry"),
    [
        (IndexNotReadyError("x"), UnavailableError, 30),
        (IndexMismatchError("x"), UnavailableError, None),
        (NotConfiguredError("x"), UnavailableError, None),
        (InvalidConfigError("x"), UnavailableError, None),
        (RetrievalError("the index found no passage for this question"), InvalidInputError, None),
        (RetrievalError("the reranker returned 3 scores LEAK"), UpstreamError, None),
        (
            RuntimeError("no ingested documents; run `docingest ingest` first"),
            UnavailableError,
            None,
        ),
        (
            ValueError("whole_blocks needs [qa] skip_summary: only an answerer"),
            InvalidInputError,
            None,
        ),
        (ValueError("keywords applies to the index"), InvalidInputError, None),
        (ImportError("paperqa"), UnavailableError, None),
        (ConnectionError("refused LEAK"), UnavailableError, 15),
        (TimeoutError("slow LEAK"), UnavailableError, 15),
        (
            APIConnectionError("http://127.0.0.1:8001 prompt: my question LEAK"),
            UnavailableError,
            15,
        ),
        (BadRequestError("maximum context length: my question LEAK"), UpstreamError, None),
        (ContextWindowExceededError("too long: my question LEAK"), UpstreamError, None),
    ],
)
def test_each_failure_has_words_that_leave_the_library_message_out(
    error: Exception, kind: type, retry: int | None
) -> None:
    told = classify(error)
    assert isinstance(told, kind)
    assert getattr(told, "retry_after_s", None) == retry
    detail = told.detail
    assert detail and "my question" not in detail and "127.0.0.1" not in detail
    assert "LEAK" not in detail


@pytest.mark.parametrize(
    "error",
    [
        KeyError("x"),
        ValueError("some other value"),
        RuntimeError("some other runtime"),
        OSError("disk"),
    ],
)
def test_other_errors_are_surprises(error: Exception) -> None:
    assert classify(error) is None


def test_start_reasons_are_fixed_text() -> None:
    assert start_reason(IndexNotReadyError("x")) == REASON_INDEX
    assert start_reason(KeyError("/a/path")) == REASON_START
    assert start_reason(TimeoutError("/a/path")) == REASON_SLOW


# --- without a configuration ---------------------------------------------------------------


@arun
async def test_the_offline_engine_says_why_it_cannot_answer() -> None:
    engine = OfflineAskEngine()
    await engine.warm_up()
    assert engine.health().state == "unavailable" and "docingest configuration" in (
        engine.health().reason or ""
    )
    with pytest.raises(UnavailableError):
        await engine.ask(AskQuestion(QUESTION), lambda _: None)
    engine.close()


def test_the_offline_reader_has_no_papers() -> None:
    reader = OfflineDocumentReader()
    assert reader.search("x", 5).total == 0
    with pytest.raises(NotFoundError):
        reader.get("a" * 16)
    with pytest.raises(NotFoundError):
        reader.page("a" * 16, 1)


def test_open_docingest_reads_the_settings_and_opens_nothing(tmp_path: Path) -> None:
    config = tmp_path / "docingest.toml"
    config.write_text(f'output_dir = "{tmp_path / "normalized"}"\n', encoding="utf-8")
    engine, reader = open_docingest(config)
    assert isinstance(engine, DocingestAskEngine) and isinstance(reader, DocingestDocumentReader)
    assert engine.health().state == "warming_up" and engine._session is None
    assert reader._opened is None


def test_open_docingest_refuses_a_file_that_is_not_settings(tmp_path: Path) -> None:
    config = tmp_path / "docingest.toml"
    config.write_text("this is = = not toml", encoding="utf-8")
    with pytest.raises(ValueError, match=r"(?i)toml|expected|invalid"):
        open_docingest(config)
    with pytest.raises(FileNotFoundError):
        open_docingest(tmp_path / "missing.toml")
