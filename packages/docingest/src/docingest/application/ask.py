"""Use case: answer a question over the normalized corpus.

Without an index the question goes to the ``QuestionAnswerer`` with the whole corpus, which
retrieves its own evidence. With a ``Retrieval`` configured the evidence is retrieved here in
two stages and handed over as ``contexts``:

1. embed the question once, search the chunk index (dense, plus BM25 for English questions and for
   questions with LaTeX, as the index adapter is set up) for the top ``candidates``;
2. rerank those with the cross-encoder, a stable sort by score, and keep the first ``contexts``.
   With no reranker the first ``contexts`` of the first stage are kept as they are. If
   ``max_chunks_per_paper`` is set, the cap is applied here, to the reranked order and before the
   cut to ``contexts``: each paper keeps its best chunks and the next papers' chunks move up.
   Capping the first stage instead, before the reranker has judged the chunks, dropped gold
   evidence in the measurements.

The answerer then summarizes exactly those chunks and writes the cited answer. When the answerer
reads the chunks as they are (``circuit_lists``, set from ``[qa] skip_summary``) and
``[qa] whole_blocks`` is on (``whole_blocks``, off by default), the chunks are completed first: a
chunk that starts or stops inside a display equation, a code block or a table gets the rest of
that block, and the caption that follows it, from the paper's text (see ``domain.blocks``). Then
each circuit code block in a chunk gets its gate list as a paragraph right after it (see
``domain.circuits``): the chunks themselves, the index and the stored Markdown are not changed.
In the same case (``equation_lookup``), a question that names an equation number ("Eq. (2)") is
given the chunk of each of the first papers among the retrieved ones that holds that number,
under a header that names the paper (see ``domain.equations``): equation numbers are local to a
paper, and no search can weigh a number that every paper has.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from ..domain.blocks import Kind, complete, partial_note, whole_blocks
from ..domain.chunking import NAME_LENGTH, PAGE_BREAK, Chunk
from ..domain.circuits import CodeBlock, add_gate_lists, circuit_code_blocks
from ..domain.equations import (
    MIN_TITLE_WORDS,
    EquationSpan,
    canonical,
    chunk_for,
    covers,
    equation_header,
    equation_references,
    find_equations,
    lead_sentence_start,
    title_overlap,
)
from ..domain.errors import IndexMismatchError, IndexNotReadyError, RetrievalError
from ..domain.text import split_pages
from ..ports import (
    ChunkIndex,
    DocumentStore,
    Embedder,
    KeywordMode,
    QuestionAnswerer,
    Reranker,
    StoredDocument,
)
from .index import document_chunks

BUILD_HINT = "run `docingest index build`"
# The equation lookup adds at most this many chunks for one number, and this many in all. A
# number every paper has is a guess about which paper is meant: the first two papers of the
# ranking are the ones the retrieval is surest of, and four chunks are the 40% of a ten-chunk
# prompt that a reader of two numbers (two each) can use before they crowd out the evidence.
MAX_PER_NUMBER = 2
MAX_ADDED = 4
# A cut block is completed whole when it, with its caption, is at most this many chunks long.
# 99.6% of the 40,609 blocks of 1,249 papers are within three chunks; the rest is a very long
# equation or table, whose remaining text would crowd out the other chunks of the prompt.
BLOCK_CHUNKS = 3


@dataclass(frozen=True)
class Retrieval:
    """The two-stage retrieval in front of the answerer."""

    embedder: Embedder
    index: ChunkIndex
    reranker: Reranker | None  # None: the first stage's order is final
    # Hits the first stage returns. Hits of papers that left the corpus are dropped afterwards,
    # so on an index that was not pruned the reranker may see fewer.
    candidates: int
    contexts: int  # chunks handed to the answerer
    # At most this many of the contexts from one paper, applied after reranking and before the
    # cut to ``contexts``; 0 is no cap.
    max_chunks_per_paper: int = 0

    def __post_init__(self) -> None:
        if self.candidates < 1 or self.contexts < 1:
            raise ValueError("candidates and contexts must be at least 1")
        if self.max_chunks_per_paper < 0:
            raise ValueError("max_chunks_per_paper must be at least 0")


@dataclass(frozen=True)
class EquationLookup:
    """What the equation lookup needs to cut a paper like the index did and to keep the answer
    prompt as long as it was."""

    chunk_chars: int  # [qa] chunk_chars
    overlap: int  # [qa] overlap
    sources: int  # [qa] answer_max_sources: the chunks the answer prompt carries at most


@dataclass(frozen=True)
class WholeBlocks:
    """What the completion of cut blocks needs to bound itself and to know which chunks the
    answer prompt carries."""

    chunk_chars: int  # [qa] chunk_chars: the caption of a block is at most this long, a block
    # is completed up to ``BLOCK_CHUNKS`` times this
    sources: int  # [qa] answer_max_sources: only these first chunks are completed


class AskService:
    def __init__(
        self,
        *,
        store: DocumentStore,
        qa: QuestionAnswerer,
        retrieval: Callable[[], Retrieval] | None = None,
        circuit_lists: bool = False,
        equation_lookup: EquationLookup | None = None,
        whole_blocks: WholeBlocks | None = None,
    ):
        """``retrieval`` is called only for a question that uses the index, so a configuration
        problem with it does not stop one that does not (``use_index=False``).
        ``circuit_lists`` adds a gate list after each circuit code block of the retrieved chunks;
        it is for an answerer that reads the chunks as they are (``[qa] skip_summary``), as a
        summary would not keep the list. ``equation_lookup`` adds the chunk of an equation number
        that the question names (see ``with_equation_lookup``); it is for the same answerer, and
        so is ``whole_blocks``, which completes the display equations, code blocks and tables
        that a chunk cuts (see ``with_whole_blocks``) and is also opt in (``[qa] whole_blocks``)."""
        self.store = store
        self.qa = qa
        self._retrieval = retrieval
        self._circuit_lists = circuit_lists
        self._equation_lookup = equation_lookup
        self._whole_blocks = whole_blocks

    @property
    def has_index(self) -> bool:
        """Whether an index is configured: the default path of ``ask`` then uses it."""
        return self._retrieval is not None

    async def ask(
        self,
        question: str,
        warn: Callable[[str], None] = print,
        *,
        use_index: bool = True,
        keywords: KeywordMode | None = None,
    ) -> str:
        """``use_index=False`` answers with the answerer's own retrieval even when an index is
        configured. ``keywords`` overrides the index's keyword setting for this question."""
        documents, warnings = self.store.corpus()
        for w in warnings:
            warn(w)
        if not documents:
            raise RuntimeError("no ingested documents; run `docingest ingest` first")
        if self._retrieval is None or not use_index:
            if keywords is not None:
                raise ValueError("keywords applies to the index, which this question does not use")
            return await self.qa.ask(
                question, [(d, self.store.markdown(d)) for d in documents], warn
            )
        retrieval = self._retrieval()
        try:
            contexts, sources = retrieve(retrieval, question, documents, warn, keywords)
        finally:
            retrieval.index.close()
        labels: Labels = {}
        notes: Notes = {}
        if self._whole_blocks is not None:  # first: the lookup sees the completed chunks
            contexts, notes = with_whole_blocks(
                contexts, sources, self.store, warn, blocks=self._whole_blocks
            )
        if self._equation_lookup is not None:
            contexts, labels = with_equation_lookup(
                contexts,
                sources,
                question,
                self.store,
                warn,
                lookup=self._equation_lookup,
                completed=self._whole_blocks is not None,
            )
            kept = {c.doc_id for c in contexts}
            sources = [d for d in sources if d.manifest.doc_id in kept]
        if self._circuit_lists:
            contexts = with_gate_lists(contexts, sources, self.store, warn)
        # last: a note and a header move the text, and the gate lists read it by offset
        if notes:
            contexts = with_block_notes(contexts, notes)
        if labels:
            contexts = with_equation_headers(contexts, labels, sources)
        # The answerer needs only the manifests of the papers the chunks come from.
        return await self.qa.ask(question, [(d, "") for d in sources], warn, contexts=contexts)


def retrieve(
    retrieval: Retrieval,
    question: str,
    documents: list[StoredDocument],
    warn: Callable[[str], None],
    keywords: KeywordMode | None = None,
) -> tuple[list[Chunk], list[StoredDocument]]:
    """The chunks to answer from, best first, and the documents they come from (in order of
    first appearance). Raises ``IndexMismatchError`` or ``IndexNotReadyError`` when the index
    cannot answer for this corpus and embedder."""
    by_id = {d.manifest.doc_id: d for d in documents}
    _check_ready(retrieval, by_id, warn)
    vector = retrieval.embedder.embed_query(question)
    hits = retrieval.index.search(question, vector, retrieval.candidates, keywords=keywords)
    chunks = [h.chunk for h in hits if h.chunk.doc_id in by_id]
    if len(chunks) < len(hits):
        warn(
            f"{len(hits) - len(chunks)} of {len(hits)} retrieved chunks belong to documents that "
            f"are no longer in the corpus and were left out ({BUILD_HINT} to prune them)"
        )
    if not hits:  # a rebuild would not change this: the indexed papers have no chunks
        raise RetrievalError("the index found no passage for this question")
    if not chunks:
        raise IndexNotReadyError(
            f"every retrieved chunk is from a document that left the corpus; {BUILD_HINT}"
        )
    if retrieval.reranker is not None:
        scores = retrieval.reranker.rerank(question, chunks)
        if len(scores) != len(chunks):
            raise RetrievalError(
                f"the reranker returned {len(scores)} scores for {len(chunks)} chunks"
            )
        # stable by score: ties keep the first-stage order, as in the measured run
        order = sorted(range(len(chunks)), key=lambda i: (-scores[i], i))
        chunks = [chunks[i] for i in order]
    top = cap_per_paper(chunks, retrieval.max_chunks_per_paper)[: retrieval.contexts]
    seen: dict[str, StoredDocument] = {}
    for chunk in top:
        seen.setdefault(chunk.doc_id, by_id[chunk.doc_id])
    return top, list(seen.values())


Notes = dict[
    tuple[str, int], tuple[Kind, ...]
]  # (doc id, chunk start) to the blocks it shows part of


def with_whole_blocks(
    contexts: list[Chunk],
    sources: list[StoredDocument],
    store: DocumentStore,
    warn: Callable[[str], None],
    *,
    blocks: WholeBlocks,
) -> tuple[list[Chunk], Notes]:
    """``contexts`` with each display equation, code block and table that a chunk starts or
    stops inside completed from the paper's text, and the notes for ``with_block_notes``.

    Only the chunks the answer prompt carries (the first ``blocks.sources``) are looked at, and
    a paper's Markdown is read once, when its first chunk comes. A cut block is read with the
    caption that follows it (see ``domain.blocks``) and the chunk is extended to the block's start
    or end, as far as no other chunk of the prompt holds the text already: an overlap that two
    neighbouring chunks share is never added to either, and the best ranked of two chunks next
    to the missing text takes it (``domain.blocks.complete``). A block with its caption of more
    than ``BLOCK_CHUNKS`` chunks is completed without the caption, and one longer than that is
    left as it is; the chunks that hold part of it get a note. The chunks keep their order, and
    an extended chunk keeps what it was (its text is the paper's text at its new offsets, its
    pages and name follow the pages it now spans). A chunk whose text is not the paper's text at
    its offset (the index may be older than the Markdown) is left alone and holds nothing.

    Like the gate lists this never stops an answer: if anything fails for a paper or a chunk the
    chunks stay as they are and ``warn`` is called once for that paper."""
    window = contexts[: max(1, blocks.sources)]
    by_id = {d.manifest.doc_id: d for d in sources}
    papers: dict[str, list[int]] = {}  # the places of the window, by paper
    for i, chunk in enumerate(window):
        if chunk.doc_id in by_id:
            papers.setdefault(chunk.doc_id, []).append(i)
    out = list(contexts)
    notes: Notes = {}
    for doc_id, places in papers.items():
        try:
            pages = split_pages(store.markdown(by_id[doc_id]))
            document = "".join(text + PAGE_BREAK for text in pages.values())
            held = [i for i in places if _is_text_at(document, out[i])]
            plans = complete(
                [(out[i].start, out[i].start + len(out[i].text)) for i in held],
                whole_blocks(document, caption_max=blocks.chunk_chars),
                limit=BLOCK_CHUNKS * blocks.chunk_chars,
            )
            page_starts = _page_starts(pages)
        except Exception as e:
            warn(f"whole blocks skipped for {doc_id[:NAME_LENGTH]}: {e}")
            continue
        warned = False
        for i, plan in zip(held, plans, strict=True):
            chunk = out[i]
            try:
                if (plan.start, plan.stop) != (chunk.start, chunk.start + len(chunk.text)):
                    chunk = _extended(chunk, document, plan.start, plan.stop, page_starts)
                    out[i] = chunk
                if plan.partial:
                    notes[(doc_id, chunk.start)] = plan.partial
            except Exception as e:
                if not warned:
                    warned = True
                    warn(f"whole blocks skipped for {doc_id[:NAME_LENGTH]}: {e}")
    return out, notes


def _is_text_at(document: str, chunk: Chunk) -> bool:
    """Whether ``chunk`` is the paper's text at its offset."""
    return document[chunk.start : chunk.start + len(chunk.text)] == chunk.text


def _page_starts(pages: dict[int, str]) -> list[tuple[int, int]]:
    """``(offset, page number)`` of each page in the pages joined as the chunker joins them."""
    starts, at = [], 0
    for number, text in pages.items():
        starts.append((at, number))
        at += len(text) + len(PAGE_BREAK)
    return starts


def _extended(
    chunk: Chunk, document: str, start: int, stop: int, page_starts: list[tuple[int, int]]
) -> Chunk:
    """``chunk`` as the paper's text from ``start`` to ``stop``, with the pages it now spans."""
    offsets = [offset for offset, _ in page_starts]

    def page_at(offset: int) -> int:
        return page_starts[max(0, bisect_right(offsets, offset) - 1)][1]

    first = min(chunk.first_page, page_at(start))
    last = max(chunk.last_page, page_at(stop - 1))
    name = chunk.name
    if (first, last) != (chunk.first_page, chunk.last_page):
        name = f"{chunk.doc_id[:NAME_LENGTH]} pages {first}-{last}"
    return replace(
        chunk,
        name=name,
        text=document[start:stop],
        first_page=first,
        last_page=last,
        start=start,
    )


def with_block_notes(contexts: list[Chunk], notes: Notes) -> list[Chunk]:
    """``contexts`` with the note of ``domain.blocks.partial_note`` and a blank line before the
    text of each chunk in ``notes``. Only ``text`` changes."""
    return [
        replace(c, text=f"{partial_note(notes[key])}\n\n{c.text}")
        if (key := (c.doc_id, c.start)) in notes
        else c
        for c in contexts
    ]


def with_gate_lists(
    contexts: list[Chunk],
    sources: list[StoredDocument],
    store: DocumentStore,
    warn: Callable[[str], None],
) -> list[Chunk]:
    """``contexts`` with the gate list of each circuit code block added after the block (see
    ``domain.circuits.add_gate_lists``). A chunk that holds the end of a block that began in the
    chunk before is given the list of the whole block, read from the paper's Markdown. The
    Markdown of a paper is read once, and only when one of its chunks holds a code fence; a chunk
    with none is returned as it is.

    The list is an addition to the answer, so it never stops one: if anything fails for a chunk
    (the Markdown cannot be read, a circuit the reader chokes on) the chunk is kept as it is and
    ``warn`` is called once for that paper."""
    by_id = {d.manifest.doc_id: d for d in sources}
    read: dict[str, tuple[str, list[CodeBlock]]] = {}
    warned: set[str] = set()
    out: list[Chunk] = []
    for chunk in contexts:
        if "```" not in chunk.text or chunk.doc_id not in by_id:
            out.append(chunk)
            continue
        try:
            if chunk.doc_id not in read:
                # the text the chunker cut: the pages joined as chunk_pages joins them
                pages = split_pages(store.markdown(by_id[chunk.doc_id]))
                document = "".join(text + PAGE_BREAK for text in pages.values())
                read[chunk.doc_id] = (document, circuit_code_blocks(document))
            document, blocks = read[chunk.doc_id]
            text = add_gate_lists(chunk.text, chunk.start, document, blocks)
        except Exception as e:
            if chunk.doc_id not in warned:
                warned.add(chunk.doc_id)
                warn(f"gate list skipped for {chunk.doc_id[:NAME_LENGTH]}: {e}")
            out.append(chunk)
            continue
        out.append(replace(chunk, text=text) if text != chunk.text else chunk)
    return out


@dataclass
class EquationLabel:
    """What a chunk of ``with_equation_lookup`` is labelled for: the numbers it holds, and those
    among them whose equation starts before the chunk (``partial``)."""

    tags: list[str] = field(default_factory=list)
    partial: set[str] = field(default_factory=set)


Labels = dict[tuple[str, int], EquationLabel]  # (doc id, chunk start) to what the header says


def with_equation_lookup(
    contexts: list[Chunk],
    sources: list[StoredDocument],
    question: str,
    store: DocumentStore,
    warn: Callable[[str], None],
    *,
    lookup: EquationLookup,
    completed: bool = False,
) -> tuple[list[Chunk], Labels]:
    """``contexts`` with the chunk that holds each equation number the question names, and the
    labels for ``with_equation_headers``.

    Only the papers among the chunks the answer prompt carries (the first ``lookup.sources``)
    are searched, in the order of their best chunk, and for each number the first
    ``MAX_PER_NUMBER`` of them that tag it, at most ``MAX_ADDED`` chunks in all (the first pick
    of every number comes before the second of any). A paper's Markdown is read when the first
    of its numbers is needed, once. The chunk is cut like the index cut the paper: the one that
    holds the tag whole and most of its equation. If the equation starts before that chunk, the
    chunk is extended back to the start of the equation when that is at most ``chunk_chars``
    away, and otherwise it is labelled as the end of the equation. A chunk of the prompt that
    holds the whole equation, and whose text is still the paper's text at its offset (the index
    may be older than the Markdown), is not added again: it only gets its label.

    The prompt keeps its size. An added chunk goes right behind the best chunk of its paper, so
    that it lasts as long as the paper does when the answerer retries with fewer sources, and it
    takes the place of the lowest ranked chunk of the ``lookup.sources`` places of the prompt
    once those are full: never the best one, never one that is labelled, and a chunk whose paper
    keeps another chunk before one that is the only chunk of its paper (a paper leaves the
    prompt only when nothing else can give a place). Where that leaves no place, the later picks
    are not added. A chunk with an old text at the offset of an added chunk is replaced by it,
    except, with ``completed``, one whose text only goes on after the added chunk's text (a
    chunk that ``with_whole_blocks`` completed at its end). Chunks beyond the prompt stay as
    they are.

    Like the gate lists this never stops an answer: if anything fails for a paper (its Markdown
    cannot be read) nothing is added for it and ``warn`` is called once for that paper."""
    numbers = [canonical(n) for n in equation_references(question)]
    if not numbers or not contexts:
        return contexts, {}
    capacity = max(1, lookup.sources)  # places in the prompt
    window = contexts[:capacity]
    by_id = {d.manifest.doc_id: d for d in sources}
    papers = _Papers(by_id, numbers, question, store, warn=warn, lookup=lookup)
    picked, labels = _pick_equations(numbers, window, papers, completed)
    # the largest number of picks that has places: the free ones, the places of the chunks that
    # an added chunk replaces at its own offset, and the lowest ranked ones that may go
    keep, stale, victims = len(picked), set(), []
    while True:
        starts = {(d, c.start) for d, c in picked[:keep]}
        stale = {i for i, c in enumerate(window) if (c.doc_id, c.start) in starts}
        free = max(0, capacity - len(window)) + len(stale)
        lowest_first = [
            i
            for i in range(len(window) - 1, 0, -1)
            if i not in stale and (window[i].doc_id, window[i].start) not in labels
        ]
        if keep <= free + len(lowest_first):
            victims = _lowest_ranked(window, lowest_first, stale, keep - free)
            break
        keep -= 1
    for doc_id, chunk in picked[keep:]:  # no place for these: no label either
        labels.pop((doc_id, chunk.start), None)
    gone = stale | set(victims)
    behind: dict[int, list[Chunk]] = {}  # the added chunks, by the place of their paper's best
    for doc_id, chunk in picked[:keep]:
        behind.setdefault(next(i for i, c in enumerate(window) if c.doc_id == doc_id), []).append(
            chunk
        )
    out: list[Chunk] = []
    for i, chunk in enumerate(window):
        if i not in gone:
            out.append(chunk)
        out.extend(behind.get(i, ()))
    return out + contexts[capacity:], labels


def _lowest_ranked(
    window: list[Chunk], lowest_first: list[int], stale: set[int], need: int
) -> list[int]:
    """The ``need`` places to take from ``lowest_first``, lowest ranked first, each from a paper
    that still has another chunk in the window; only when there is none, the lowest ranked."""
    left = Counter(c.doc_id for i, c in enumerate(window) if i not in stale)
    candidates = list(lowest_first)
    taken: list[int] = []
    for _ in range(max(0, need)):
        at = next((i for i in candidates if left[window[i].doc_id] > 1), candidates[0])
        candidates.remove(at)
        left[window[at].doc_id] -= 1
        taken.append(at)
    return taken


@dataclass
class _Paper:
    """What one paper gives: its text (the pages joined as the chunker joins them) and, for each
    asked number it tags, where it is, the chunk to give and whether that chunk has the whole
    equation."""

    document: str = ""
    hits: dict[str, tuple[EquationSpan, Chunk, bool]] = field(default_factory=dict)

    def holds(self, chunk: Chunk) -> bool:
        """Whether ``chunk`` is the paper's text at its offset."""
        return self.document[chunk.start : chunk.start + len(chunk.text)] == chunk.text


class _Papers:
    """The equations of the papers that were asked for, each paper read once and only if some
    number reaches it."""

    def __init__(
        self,
        by_id: dict[str, StoredDocument],
        numbers: list[str],
        question: str,
        store: DocumentStore,
        *,
        warn: Callable[[str], None],
        lookup: EquationLookup,
    ):
        self.by_id = by_id
        self.numbers = numbers
        self.question = question
        self.store = store
        self.warn = warn
        self.lookup = lookup
        self._found: dict[str, _Paper] = {}

    def named(self, doc_id: str) -> bool:
        """Whether the paper looks like the one the question asks about: its title shares at
        least ``MIN_TITLE_WORDS`` content words with the question. Every paper has an Eq. (n), so
        a paper that does not pass gives nothing."""
        m = self.by_id[doc_id].manifest
        title = m.title or (m.metadata.title if m.metadata else None)
        return title_overlap(self.question, title) >= MIN_TITLE_WORDS

    def get(self, doc_id: str) -> _Paper:
        """The paper's hits for the asked numbers. A paper that cannot be read has none."""
        if doc_id not in self._found:
            self._found[doc_id] = _Paper()
            try:
                self._found[doc_id] = self._read(doc_id)
            except Exception as e:
                self._found[doc_id] = _Paper()
                self.warn(f"equation lookup skipped for {doc_id[:NAME_LENGTH]}: {e}")
        return self._found[doc_id]

    def _read(self, doc_id: str) -> _Paper:
        doc = self.by_id[doc_id]
        pages = split_pages(self.store.markdown(doc))
        document = "".join(text + PAGE_BREAK for text in pages.values())
        paper = _Paper(document)
        spans = find_equations(document, self.numbers)
        if not spans:
            return paper
        chunks = document_chunks(doc, pages, self.lookup.chunk_chars, self.lookup.overlap)
        starts = []  # (offset, page number) of each page in the joined text
        at = 0
        for number, text in pages.items():
            starts.append((at, number))
            at += len(text) + len(PAGE_BREAK)
        for number, span in spans.items():
            if (chunk := chunk_for(chunks, span)) is None:
                continue
            whole = covers(chunk, span)
            # back to the sentence before the equation (it names what the equation gives), or
            # failing that to the equation itself, when that is at most chunk_chars before the chunk
            lead = lead_sentence_start(document, span.start, self.lookup.chunk_chars)
            for target in (lead, span.start):
                if target < chunk.start and chunk.start - target <= self.lookup.chunk_chars:
                    first = max(n for offset, n in starts if offset <= target)
                    chunk = replace(
                        chunk,
                        name=f"{doc_id[:NAME_LENGTH]} pages {first}-{chunk.last_page}",
                        text=document[target : chunk.start + len(chunk.text)],
                        first_page=first,
                        start=target,
                    )
                    whole = True
                    break
            paper.hits[number] = (span, chunk, whole)
        return paper


def _pick_equations(
    numbers: list[str], window: list[Chunk], papers: _Papers, completed: bool
) -> tuple[list[tuple[str, Chunk]], Labels]:
    """The chunks to add, best pick first, as ``(doc id, chunk)``, and the labels of every chunk
    that gets a header: the added ones and the ones of ``window`` that hold the equation."""
    order = [
        d
        for d in dict.fromkeys(c.doc_id for c in window if c.doc_id in papers.by_id)
        if papers.named(d)
    ]
    picked: list[tuple[str, Chunk]] = []
    added: set[tuple[str, int]] = set()
    labels: Labels = {}
    cursor = dict.fromkeys(numbers, 0)
    for _ in range(MAX_PER_NUMBER):
        for number in numbers:
            while cursor[number] < len(order):
                doc_id = order[cursor[number]]
                cursor[number] += 1
                paper = papers.get(doc_id)
                if (hit := paper.hits.get(number)) is None:
                    continue
                span, chunk, whole = hit
                # a chunk of the prompt that holds all of the equation, as the paper has it now
                there = next(
                    (
                        c
                        for c in window
                        if c.doc_id == doc_id and covers(c, span) and paper.holds(c)
                    ),
                    None,
                )
                key = (doc_id, there.start if there else chunk.start)
                if there is None and key not in added:
                    twin = next((c for c in window if (c.doc_id, c.start) == key), None)
                    # a twin with an old text is replaced; with completed blocks, one that only
                    # goes on after the chunk's text (a block completed at its end) is the same
                    if twin is None or (
                        not twin.text.startswith(chunk.text)
                        if completed
                        else twin.text != chunk.text
                    ):
                        if len(picked) >= MAX_ADDED:
                            break
                        picked.append((doc_id, chunk))
                    added.add(key)
                label = labels.setdefault(key, EquationLabel())
                label.tags.append(span.tag)
                if there is None and not whole:
                    label.partial.add(span.tag)
                break
    return picked, labels


def with_equation_headers(
    contexts: list[Chunk], labels: Labels, sources: list[StoredDocument]
) -> list[Chunk]:
    """``contexts`` with the header of ``domain.equations.equation_header`` and a blank line
    before the text of each chunk in ``labels``. Only ``text`` changes."""
    by_id = {d.manifest.doc_id: d.manifest for d in sources}
    out: list[Chunk] = []
    for chunk in contexts:
        label = labels.get((chunk.doc_id, chunk.start))
        if label is None or chunk.doc_id not in by_id:
            out.append(chunk)
            continue
        m = by_id[chunk.doc_id]
        title = m.title or (m.metadata.title if m.metadata else None) or m.source_name
        header = equation_header(label.tags, chunk.doc_id, title, label.partial)
        out.append(replace(chunk, text=f"{header}\n\n{chunk.text}"))
    return out


def cap_per_paper(chunks: list[Chunk], cap: int) -> list[Chunk]:
    """``chunks`` without the ones beyond the first ``cap`` of each paper, in the same order.
    ``cap`` 0 keeps everything."""
    if not cap:
        return chunks
    kept: list[Chunk] = []
    per_paper: dict[str, int] = {}
    for chunk in chunks:
        if per_paper.get(chunk.doc_id, 0) < cap:
            per_paper[chunk.doc_id] = per_paper.get(chunk.doc_id, 0) + 1
            kept.append(chunk)
    return kept


def _check_ready(
    retrieval: Retrieval, corpus: dict[str, StoredDocument], warn: Callable[[str], None]
) -> None:
    index = retrieval.index
    held, configured = index.embedder_fingerprint, retrieval.embedder.fingerprint
    if held != configured:
        raise IndexMismatchError(
            f"the index holds vectors of embedder {held!r} but the configured one is "
            f"{configured!r}; restore the [embedder] settings or use another [index] dir, "
            f"then {BUILD_HINT}"
        )
    # Before every question, so it reads what the last commit left for search, not every
    # stored document (``stats()`` and ``keys()``, which ``index status`` and ``build`` use).
    state = index.committed()
    if not state.documents:
        stats = index.stats()  # only to word the error; no answer follows, so a full scan is fine
        if stats.documents == 0:
            raise IndexNotReadyError(f"the index is empty; {BUILD_HINT}")
        raise IndexNotReadyError(
            f"the index holds {stats.documents} documents but none is committed, so none can "
            f"be searched (an earlier build was interrupted); {BUILD_HINT}"
        )
    searchable = state.documents
    if state.pending:
        # Rare, so the full scan is fine. A document that is stored but not committed is only
        # pending, not missing. ``stats()`` has the last word on pending: it also sets aside a
        # shard whose header cannot be read, which the file names alone cannot tell.
        searchable = index.keys().keys()
        if index.stats().pending:
            warn(
                f"the index has changes that are not committed yet and are not searched; "
                f"{BUILD_HINT}"
            )
    missing = len(corpus.keys() - searchable)
    if missing:
        warn(
            f"{missing} of {len(corpus)} documents of the corpus are not in the index and cannot "
            f"be used for this answer; {BUILD_HINT}"
        )
