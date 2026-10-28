"""QuestionAnswerer adapter: PaperQA2 over the normalized corpus (``qa`` extra).

The LLM is any litellm model: by default the pinned local Qwen3-VL snapshot served
by ``mlx_vlm.server`` (OpenAI-compatible), or e.g. ``ollama/llama3.1``. Embeddings
default to a pinned sentence-transformers snapshot; any PaperQA embedding string
(e.g. ``ollama/mxbai-embed-large``) can replace it.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from pathlib import Path

from ...config import DEFAULT_LLM_BASE, AppConfig
from ...domain.chunking import NAME_LENGTH, Chunk
from ...domain.models import AnswerModel
from ...domain.text import split_pages
from ...ports import StoredDocument
from ..models.huggingface import resolve


def _quiet_litellm() -> None:
    import logging

    import litellm

    litellm.suppress_debug_info = True  # local models have no price entry -> noisy warnings
    logging.getLogger("LiteLLM").setLevel(logging.CRITICAL)


def llm_path(cfg: AppConfig) -> str:
    """Local snapshot path of the pinned QA LLM (default: the [ocr] model)."""
    rev = cfg.qa.llm_revision or cfg.ocr.revision
    assert rev is not None
    return resolve(cfg.ocr.repo_id, rev)


def embedding_path(cfg: AppConfig) -> str:
    return resolve(cfg.qa.embedding_repo_id, cfg.qa.embedding_revision)


def llm_params(cfg: AppConfig) -> dict:
    """litellm parameters of the QA LLM.

    The default model is the pinned local snapshot served by scripts/serve_llm.sh (which
    loads the same snapshot path, so the request's model id matches what it serves and
    nothing is fetched from `main`): the local server's address and a placeholder key.
    Any other model goes to ``llm_base`` if set, else to its provider's own endpoint,
    with the provider's key from the environment as litellm reads it. The placeholder is
    used only for an ``openai/`` model when ``OPENAI_API_KEY`` is unset: a keyless local
    OpenAI-compatible server (LM Studio, vLLM) still needs a non-empty key in the client,
    but a real key must never be overridden. ``[qa] temperature`` is sent on every request.
    """
    q = cfg.qa
    custom = os.environ.get("DOCINGEST_LLM") or q.llm
    if custom is None:
        return {
            "model": f"openai/{llm_path(cfg)}",
            "api_base": q.llm_base or DEFAULT_LLM_BASE,
            "api_key": "sk-local",  # mlx_vlm.server checks no key
            "max_tokens": 1024,
            "temperature": q.temperature,
        }
    params: dict = {"model": custom, "max_tokens": 1024, "temperature": q.temperature}
    if q.llm_base:
        params["api_base"] = q.llm_base
    if custom.startswith("openai/") and not os.environ.get("OPENAI_API_KEY"):
        params["api_key"] = "sk-local"
    return params


def answer_model(cfg: AppConfig) -> AnswerModel:
    """The model ``llm_params`` selects, without its address or key: the pinned model's
    repository and revision, or the litellm string of ``DOCINGEST_LLM`` or ``[qa] llm``."""
    custom = os.environ.get("DOCINGEST_LLM") or cfg.qa.llm
    if custom is not None:
        return AnswerModel(name=custom, revision=None)
    rev = cfg.qa.llm_revision or cfg.ocr.revision
    assert rev is not None
    return AnswerModel(name=cfg.ocr.repo_id, revision=rev)


def local_settings(cfg: AppConfig):
    from paperqa import Settings

    _quiet_litellm()
    q = cfg.qa
    llm_cfg = {"model_list": [{"model_name": "local", "litellm_params": llm_params(cfg)}]}
    embedding = os.environ.get("DOCINGEST_EMBEDDING") or q.embedding or f"st-{embedding_path(cfg)}"
    s = Settings(
        llm="local",
        llm_config=llm_cfg,
        summary_llm="local",
        summary_llm_config=llm_cfg,
        embedding=embedding,
    )
    s.parsing.use_doc_details = False  # no network metadata lookups
    s.parsing.multimodal = False
    s.parsing.reader_config = {"chunk_chars": q.chunk_chars, "overlap": q.overlap}
    s.answer.evidence_k = q.evidence_k
    s.answer.answer_max_sources = q.answer_max_sources
    s.answer.max_concurrent_requests = q.max_concurrent_requests
    return s


def paperqa_doc(stored: StoredDocument):
    from paperqa.types import Doc

    m = stored.manifest
    partial = "" if m.complete else f", pages 1-{m.n_pages} of {m.source_pages}"
    return Doc(docname=m.doc_id[:NAME_LENGTH], dockey=m.doc_id, citation=m.citation() + partial)


def token_windows(texts, tokenizer, max_tokens: int, overlap_tokens: int = 32):
    """Re-split chunks longer than the embedder's window so no text goes unembedded."""
    from paperqa.types import Text

    out = []
    for t in texts:
        enc = tokenizer(t.text, add_special_tokens=False, return_offsets_mapping=True)
        offsets = enc["offset_mapping"]
        if len(offsets) <= max_tokens:
            out.append(t)
            continue
        step = max_tokens - overlap_tokens
        for start in range(0, len(offsets), step):
            window = offsets[start : start + max_tokens]
            piece = t.text[window[0][0] : window[-1][1]]
            out.append(Text(text=piece, name=t.name, doc=t.doc))  # keeps its page-range name
            if start + max_tokens >= len(offsets):
                break
    return out


COPY_RULE = (
    "Take every circuit, gate, qubit index and equation only from the context above, "
    "and copy it exactly, keeping its LaTeX. "
    "Keep each one with the paper it comes from: do not move a gate or a term from one "
    "paper's circuit or equation into another's. "
    "An equation number belongs to the paper it is printed in: for a question about the "
    "Eq. (n) of one paper, do not use the Eq. (n) of another. "
    "If the context does not show a gate or a term, say so instead of guessing."
)
"""Added to PaperQA2's answer prompt when the model reads raw chunks. A summary is written by a
model; a raw circuit or equation is not, and an answer model that reads the circuits of several
papers at once may lend a gate from one to another, or answer a question about a paper's Eq. (n)
with the Eq. (n) of another. The text is pinned here and must hold no curly brace: PaperQA2 fills
the prompt with ``str.format``."""

_RULE_ANCHOR = "{prior_answer_prompt}"


def with_copy_rule(qa_prompt: str) -> str:
    """``qa_prompt`` (PaperQA2's answer template) with ``COPY_RULE`` as its last instruction,
    just before the answer. Every other instruction, the citation format included, is kept."""
    if qa_prompt.count(_RULE_ANCHOR) != 1:
        raise ValueError("PaperQA2's answer prompt changed: cannot place the copy rule in it")
    return qa_prompt.replace(_RULE_ANCHOR, f"{COPY_RULE}\n\n{_RULE_ANCHOR}")


def keep_chunks_verbatim(settings, contexts: list[Chunk]) -> None:
    """Make the answer model read ``contexts`` as retrieved instead of PaperQA2's summaries.

    Four things need to change together. ``evidence_skip_summary`` makes each context the
    chunk text. PaperQA2 still strips any parenthesis that holds a letter and a four-digit
    number from it, which deletes ``(a + 1000)`` from an equation, so that is switched off.
    And with no summary score every context scores 5, so PaperQA2's default order (score, then
    chunk name) would pick the ``answer_max_sources`` chunks by paper id: a serializer ranks
    the contexts by their place in ``contexts`` (the reranked order) before applying the same
    ``answer_max_sources`` cut and prompt layout as PaperQA2. Last, the answer prompt gets
    ``COPY_RULE``: the model reads raw circuits and equations, with no summary in between.
    """
    settings.answer.evidence_skip_summary = True
    settings.answer.skip_evidence_citation_strip = True
    settings.prompts.qa = with_copy_rule(settings.prompts.qa)
    rank: dict[str, int] = {}
    for i, chunk in enumerate(contexts):
        rank.setdefault(chunk.text, i)
    # model_copy is shallow: `default.answer` is `settings.answer`, so a lower answer_max_sources
    # set later (the retry in `PaperQAAnswerer.ask`) reaches the cut below.
    default = settings.model_copy(update={"custom_context_serializer": None})

    async def by_retrieval_rank(*, settings, contexts, question, pre_str):
        ranked = [
            c.model_copy(update={"score": len(rank) + 1 - rank.get(c.text.text, len(rank))})
            for c in contexts
        ]
        return await default.context_serializer(ranked, question, pre_str)

    settings.custom_context_serializer = by_retrieval_rank


CONTEXT_OVERFLOW = "maximum context length"
"""The words of the HTTP 400 that an OpenAI-compatible server (vLLM 0.30) sends when the prompt and
the reply do not fit its window: ``This model's maximum context length is 16384 tokens. However,
you requested 1024 output tokens and your prompt contains ...``. OpenAI's own API says the same."""


def is_context_overflow(error: BaseException) -> bool:
    """Whether ``error``, or the error it wraps, is the server's context-length 400.

    litellm raises it as a ``BadRequestError`` (a ``ContextWindowExceededError``), and PaperQA2's
    client wraps it in ``AllModelsExhaustedError``, so the chain is followed. Any other 400 is
    not one: it does not mention the context length, and fewer sources would not cure it."""
    from litellm.exceptions import BadRequestError

    exc: BaseException | None = error
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, BadRequestError) and CONTEXT_OVERFLOW in str(exc).lower():
            return True
        exc = getattr(exc, "last_exc", None) or exc.__cause__
    return False


_RETRY_PENDING: ContextVar[bool] = ContextVar("docingest_retry_pending", default=False)


class _OverflowFilter(logging.Filter):
    """Hides the overflow records of the ask that has a retry pending, and only of that ask:
    the logger is shared by every ask in the process, the context variable is not."""

    def filter(self, record: logging.LogRecord) -> bool:
        error = record.exc_info[1] if record.exc_info else None
        return not (_RETRY_PENDING.get() and error is not None and is_context_overflow(error))


@contextmanager
def _overflow_is_retried() -> Iterator[None]:
    """PaperQA2's client logs every failed request with its traceback, which would print a
    traceback for an overflow that the retry then cures. Only that record is held back; the
    exception itself is not touched and is raised if nothing is left to retry."""
    log, hide = logging.getLogger("lmi.llms"), _OverflowFilter()
    log.addFilter(hide)
    token = _RETRY_PENDING.set(True)
    try:
        yield
    finally:
        _RETRY_PENDING.reset(token)
        log.removeFilter(hide)


def without_repeated_text(contexts: list[Chunk]) -> list[Chunk]:
    """``contexts`` without a chunk whose text an earlier (better ranked) one already has.
    PaperQA2 keys a context by its text, so two such chunks would share one citation key; a
    summary differs per paper, a verbatim chunk does not."""
    first: dict[str, Chunk] = {}
    for chunk in contexts:
        first.setdefault(chunk.text, chunk)
    return list(first.values())


def _unused_embedding():
    from paperqa import EmbeddingModel

    class UnusedEmbedding(EmbeddingModel):
        """Placeholder for the argument PaperQA2 insists on when nothing is retrieved: without
        it, ``aquery`` would build the configured embedder (the local MiniLM, seconds to load)."""

        name: str = "unused"

        async def embed_documents(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("answering from given contexts embeds nothing")

    return UnusedEmbedding()


class PaperQAAnswerer:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg

    def _splitter(self):
        """Token re-splitter for the pinned sentence-transformers embedder, else None."""
        if self.cfg.qa.embedding or os.environ.get("DOCINGEST_EMBEDDING"):
            return None  # custom embedder: its window is unknown here
        from transformers import AutoTokenizer

        emb_dir = Path(embedding_path(self.cfg))
        tokenizer = AutoTokenizer.from_pretrained(emb_dir)
        st_cfg = json.loads((emb_dir / "sentence_bert_config.json").read_text())
        max_tokens = st_cfg["max_seq_length"] - 2  # [CLS] and [SEP]
        return lambda texts: token_windows(texts, tokenizer, max_tokens)

    async def ask(
        self,
        question: str,
        documents: list[tuple[StoredDocument, str]],
        warn: Callable[[str], None],
        contexts: list[Chunk] | None = None,
    ) -> str:
        if contexts is not None and not contexts:
            raise ValueError("contexts is empty: nothing to answer from")
        settings = local_settings(self.cfg)
        if contexts is None:
            if self.cfg.qa.skip_summary:
                warn(
                    "[qa] skip_summary applies to chunks from the index; PaperQA2's own "
                    "retrieval has no retrieval order to keep, so its evidence is summarized"
                )
            embedding_model = settings.get_embedding_model()  # load the embedder once
            docs = await self._docs_from_corpus(documents, settings, embedding_model)
        else:
            # Nothing is retrieved from given contexts, so nothing is embedded either.
            embedding_model = _unused_embedding()
            if self.cfg.qa.skip_summary:
                contexts = without_repeated_text(contexts)
            docs = await self._docs_from_contexts(contexts, documents, settings)
            settings.answer.evidence_retrieval = False  # use exactly these chunks
            if self.cfg.qa.skip_summary:
                keep_chunks_verbatim(settings, contexts)
        verbatim = contexts is not None and self.cfg.qa.skip_summary
        sources = min(settings.answer.answer_max_sources, len(contexts or ()))
        while True:
            try:
                with _overflow_is_retried() if verbatim and sources > 1 else nullcontext():
                    session = await docs.aquery(
                        question, settings=settings, embedding_model=embedding_model
                    )
                return session.formatted_answer
            except Exception as exc:
                # Only the verbatim prompt grows with the chunks (a summary is about 100 words),
                # and it makes no LLM call before the answer, so a retry repeats nothing.
                if not (verbatim and sources > 1 and is_context_overflow(exc)):
                    raise
                sources //= 2
                # keep_chunks_verbatim's serializer cuts to this limit, best ranked first
                settings.answer.answer_max_sources = sources
                noun = "source" if sources == 1 else "sources"
                warn(f"answer prompt too long for the model; retried with {sources} {noun}")

    async def _docs_from_corpus(self, documents, settings, embedding_model):
        from paperqa import Docs
        from paperqa.readers import chunk_pdf
        from paperqa.types import ParsedMetadata, ParsedText

        split = self._splitter()
        rc = settings.parsing.reader_config
        docs = Docs()
        for stored, markdown in documents:
            pages = split_pages(markdown)
            parsed = ParsedText(
                # chunk_pdf concatenates pages as-is: keep a break so words don't fuse
                content={str(n): text + "\n\n" for n, text in pages.items()},
                metadata=ParsedMetadata(
                    parsing_libraries=["docingest"],
                    total_parsed_text_length=sum(map(len, pages.values())),
                ),
            )
            doc = paperqa_doc(stored)
            # Page-aware chunks: citations point at page ranges ("pages 3-4").
            texts = chunk_pdf(parsed, doc, chunk_chars=rc["chunk_chars"], overlap=rc["overlap"])
            if split:
                texts = split(texts)
            await docs.aadd_texts(texts, doc, settings=settings, embedding_model=embedding_model)
        return docs

    async def _docs_from_contexts(self, contexts, documents, settings):
        """A ``Docs`` holding exactly ``contexts``: with nothing else in it, nothing else can
        be retrieved. Chunks go in as given, without the embedder's token re-split and
        without vectors: PaperQA2 only reads them to retrieve, which is off on this path."""
        from paperqa import Docs
        from paperqa.types import Text

        stored = {s.manifest.doc_id: s for s, _ in documents}
        by_paper: dict[str, list[Chunk]] = {}  # papers in order of first appearance
        for chunk in contexts:
            by_paper.setdefault(chunk.doc_id, []).append(chunk)
        settings.parsing.defer_embedding = True  # aadd_texts skips the embedder
        docs = Docs()
        for doc_id, chunks in by_paper.items():
            if doc_id not in stored:
                raise ValueError(f"contexts come from {doc_id[:NAME_LENGTH]}, not in documents")
            doc = paperqa_doc(stored[doc_id])
            texts = [Text(text=c.text, name=c.name, doc=doc) for c in chunks]
            await docs.aadd_texts(texts, doc, settings=settings)
        return docs
