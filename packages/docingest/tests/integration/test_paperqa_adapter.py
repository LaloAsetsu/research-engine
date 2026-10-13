import asyncio

import pytest

pytest.importorskip("paperqa")

import litellm
from paperqa.docs import Docs

from docingest.adapters.qa import paperqa
from docingest.adapters.qa.paperqa import PaperQAAnswerer, embedding_path, token_windows
from docingest.config import AppConfig
from docingest.domain.chunking import CHUNK_CHARS, OVERLAP, chunk_pages
from docingest.domain.models import DocumentManifest, SourceKind
from docingest.ports import StoredDocument


def test_token_windows_cover_long_chunks_within_limit():
    from paperqa.types import Doc, Text
    from transformers import AutoTokenizer

    try:
        tok = AutoTokenizer.from_pretrained(embedding_path(AppConfig()))
    except Exception:
        pytest.skip("embedder not in local HF cache")
    doc = Doc(docname="d", dockey="d", citation="c")
    long = " ".join(f"word{i}" for i in range(600))
    out = token_windows([Text(text=long, name="d pages 1-1", doc=doc)], tok, 254)
    assert len(out) > 1 and all(t.name == "d pages 1-1" for t in out)
    assert all(len(tok(t.text, add_special_tokens=False)["input_ids"]) <= 254 for t in out)
    assert out[0].text.startswith("word0") and out[-1].text.endswith("word599")


# ------------------------------------------------ answering from given contexts

REPLY = "Attention is a softmax over scaled dot products.\n\nRelevance Score: 9"


@pytest.fixture
def mock_llm(monkeypatch):
    """litellm answers every request with a canned reply: no server, no network."""
    real = paperqa.llm_params
    monkeypatch.setattr(paperqa, "llm_params", lambda cfg: {**real(cfg), "mock_response": REPLY})


@pytest.fixture
def qa_cfg():
    # "sparse" is PaperQA2's keyword embedder: nothing to download. A custom embedding is
    # also what keeps the pinned embedder's tokenizer out of these tests.
    return AppConfig.model_validate({"qa": {"llm": "openai/mock", "embedding": "sparse"}})


def chunks_of(doc_id, pages):
    return chunk_pages(doc_id, pages, chunk_chars=CHUNK_CHARS, overlap=OVERLAP)


def stored(letter: str, n_pages: int = 2, source_pages: int = 2) -> StoredDocument:
    manifest = DocumentManifest(
        doc_id=letter * 64,
        source_path="x",
        source_name=f"{letter}.pdf",
        source_kind=SourceKind.PDF,
        mime="application/pdf",
        size_bytes=1,
        n_pages=n_pages,
        source_pages=source_pages,
        max_pages=n_pages if n_pages < source_pages else None,
        pages=[],
        pipeline_version="t",
        config_hash="h",
        title=f"Paper {letter}",
    )
    return StoredDocument(manifest, f"mem://{letter}", canonical=n_pages == source_pages)


@pytest.fixture
def spy_evidence(monkeypatch):
    """Records what the Docs holds and whether retrieval is on when evidence is gathered."""
    seen = []
    original = Docs.aget_evidence

    async def spy(self, query, *args, **kwargs):
        settings = kwargs["settings"]
        seen.append(
            (list(self.texts), list(self.docs.values()), settings.answer.evidence_retrieval)
        )
        return await original(self, query, *args, **kwargs)

    monkeypatch.setattr(Docs, "aget_evidence", spy)
    return seen


@pytest.fixture
def llm_calls(monkeypatch):
    """The keyword arguments of every request litellm is asked to send."""
    sent = []
    original = litellm.acompletion

    async def spy(*args, **kwargs):
        sent.append(kwargs)
        return await original(*args, **kwargs)

    monkeypatch.setattr(litellm, "acompletion", spy)
    return sent


def test_given_contexts_are_the_only_evidence(mock_llm, qa_cfg, spy_evidence):
    a, b = stored("a"), stored("b", n_pages=1, source_pages=3)
    contexts = [
        *chunks_of(a.manifest.doc_id, {1: "Attention is a softmax over scaled dot products."}),
        *chunks_of(b.manifest.doc_id, {1: "A second paper."}),
        *chunks_of(a.manifest.doc_id, {2: "More on attention heads."}),
    ]
    # The Markdown is not read: only the manifests are needed, and papers may be in any order.
    documents = [(b, ""), (a, "")]
    answer = asyncio.run(
        PaperQAAnswerer(qa_cfg).ask("What is attention?", documents, print, contexts=contexts)
    )
    [(texts, docs, retrieval)] = spy_evidence
    assert retrieval is False
    # Added per paper in order of first appearance, each chunk as given.
    assert [(t.name, t.text) for t in texts] == [
        (contexts[0].name, contexts[0].text),
        (contexts[2].name, contexts[2].text),
        (contexts[1].name, contexts[1].text),
    ]
    assert [d.dockey for d in docs] == [a.manifest.doc_id, b.manifest.doc_id]
    assert "Paper a" in docs[0].citation
    assert docs[1].citation.endswith(", pages 1-1 of 3")  # a partial run says so
    assert answer.startswith("Question: What is attention?")


@pytest.mark.parametrize("temperature", [0.0, 0.4])
def test_every_request_carries_the_temperature(mock_llm, llm_calls, spy_evidence, temperature):
    cfg = AppConfig.model_validate(
        {"qa": {"llm": "openai/mock", "embedding": "sparse", "temperature": temperature}}
    )
    a = stored("a")
    contexts = [
        *chunks_of(a.manifest.doc_id, {1: "Attention is a softmax."}),
        *chunks_of(a.manifest.doc_id, {2: "Heads run in parallel."}),
    ]
    asyncio.run(PaperQAAnswerer(cfg).ask("What is attention?", [(a, "")], print, contexts=contexts))
    # one summary per context, then the answer
    assert len(llm_calls) == len(contexts) + 1
    assert {call["temperature"] for call in llm_calls} == {temperature}


@pytest.fixture
def pinned_embedder(monkeypatch):
    """The default config (the pinned MiniLM); every construction of an embedder is counted
    and returns a sparse one, so nothing is downloaded or loaded."""
    from paperqa import Settings, SparseEmbeddingModel

    built = []

    def get_embedding_model(self):
        built.append(self.embedding)
        return SparseEmbeddingModel()

    monkeypatch.setattr(Settings, "get_embedding_model", get_embedding_model)
    monkeypatch.setattr(paperqa, "embedding_path", lambda cfg: "pinned-minilm")
    monkeypatch.setattr(PaperQAAnswerer, "_splitter", lambda self: None)  # needs the tokenizer
    return built


def test_given_contexts_never_build_the_embedder(mock_llm, spy_evidence, pinned_embedder):
    cfg = AppConfig.model_validate({"qa": {"llm": "openai/mock"}})  # default embedder
    a = stored("a")
    contexts = [
        *chunks_of(a.manifest.doc_id, {1: "Attention is a softmax."}),
        *chunks_of(a.manifest.doc_id, {2: "Heads run in parallel."}),
    ]
    answer = asyncio.run(
        PaperQAAnswerer(cfg).ask("What is attention?", [(a, "")], print, contexts=contexts)
    )
    assert pinned_embedder == []
    [(texts, _docs, retrieval)] = spy_evidence
    assert retrieval is False
    assert all(t.embedding is None for t in texts)
    assert answer.startswith("Question: What is attention?")


def test_contexts_answer_is_the_same_with_or_without_vectors(mock_llm, qa_cfg):
    a, b = stored("a"), stored("b")
    contexts = [
        *chunks_of(a.manifest.doc_id, {1: "Attention is a softmax over scaled dot products."}),
        *chunks_of(b.manifest.doc_id, {1: "A second paper."}),
    ]
    documents = [(a, ""), (b, "")]
    answer = asyncio.run(PaperQAAnswerer(qa_cfg).ask("What?", documents, print, contexts=contexts))

    class WithVectors(PaperQAAnswerer):
        """The previous behaviour: the given texts are embedded as they are added."""

        async def _docs_from_contexts(self, contexts, documents, settings):
            from paperqa import SparseEmbeddingModel

            docs = await super()._docs_from_contexts(contexts, documents, settings)
            vectors = await SparseEmbeddingModel().embed_documents([t.text for t in docs.texts])
            for text, vector in zip(docs.texts, vectors, strict=True):
                text.embedding = vector
            return docs

    with_vectors = asyncio.run(
        WithVectors(qa_cfg).ask("What?", documents, print, contexts=contexts)
    )
    assert answer == with_vectors


def test_without_contexts_the_embedder_is_built_once(mock_llm, pinned_embedder):
    cfg = AppConfig.model_validate({"qa": {"llm": "openai/mock"}})
    a = stored("a")
    markdown = "<!-- page 1 | method=text_layer -->\nAttention is a softmax.\n\n"
    asyncio.run(PaperQAAnswerer(cfg).ask("What is attention?", [(a, markdown)], print))
    assert pinned_embedder == ["st-pinned-minilm"]


def test_no_contexts_is_not_an_empty_corpus(qa_cfg):
    with pytest.raises(ValueError, match="contexts is empty"):
        asyncio.run(PaperQAAnswerer(qa_cfg).ask("q?", [(stored("a"), "")], print, contexts=[]))


def test_contexts_from_a_paper_that_was_not_given_are_refused(qa_cfg):
    contexts = chunks_of("c" * 64, {1: "Orphan."})
    with pytest.raises(ValueError, match="not in documents"):
        asyncio.run(
            PaperQAAnswerer(qa_cfg).ask("q?", [(stored("a"), "")], print, contexts=contexts)
        )


def test_without_contexts_paperqa_still_chunks_and_retrieves_itself(mock_llm, qa_cfg, spy_evidence):
    a = stored("a")
    pages = {1: "Attention is a softmax over scaled dot products.", 2: "Heads run in parallel."}
    markdown = "".join(f"<!-- page {n} | method=text_layer -->\n{t}\n\n" for n, t in pages.items())
    asyncio.run(PaperQAAnswerer(qa_cfg).ask("What is attention?", [(a, markdown)], print))
    [(texts, _docs, retrieval)] = spy_evidence
    assert retrieval is True
    assert [t.name for t in texts] == [c.name for c in chunks_of(a.manifest.doc_id, pages)]


# ------------------------------------------------------ [qa] skip_summary (chunks, not summaries)

EQUATION = (
    "Where $d$ is the dimension, $$\\begin{equation} d(n)=\\frac{n!}{(n/2)!(n/2+1)!}"
    "=O(\\frac{2^n}{n^{3/2}}) + (a + 1000) \\end{equation}$$ holds."
)


def skip_cfg(skip: bool, **qa) -> AppConfig:
    return AppConfig.model_validate(
        {"qa": {"llm": "openai/mock", "embedding": "sparse", "skip_summary": skip, **qa}}
    )


def prompts(llm_calls) -> list[str]:
    """The user message of every request, in the order they were sent."""
    return [call["messages"][-1]["content"] for call in llm_calls]


def ask_chunks(cfg, letters, texts, question="What is d(n)?"):
    """Ask over one single-page chunk per letter, retrieved in the given order."""
    docs = [(stored(letter), "") for letter in letters]
    contexts = [
        chunks_of(stored(letter).manifest.doc_id, {1: text})[0]
        for letter, text in zip(letters, texts, strict=True)
    ]
    return asyncio.run(PaperQAAnswerer(cfg).ask(question, docs, print, contexts=contexts)), contexts


def test_skip_summary_gives_the_answer_model_the_chunk_text_verbatim(mock_llm, llm_calls):
    answer, _ = ask_chunks(skip_cfg(True), "a", [EQUATION])
    assert len(llm_calls) == 1  # the answer; no summary request
    [prompt] = prompts(llm_calls)
    assert EQUATION in prompt  # $$ ... $$ untouched, and "(a + 1000)" not taken for a citation
    assert answer.startswith("Question: What is d(n)?")
    assert {call["temperature"] for call in llm_calls} == {0.0}


def test_skip_summary_false_still_summarizes_every_chunk(mock_llm, llm_calls):
    texts = [EQUATION, "A second passage."]
    ask_chunks(skip_cfg(False), "ab", texts)
    assert len(llm_calls) == len(texts) + 1  # one summary per chunk, then the answer
    answer_prompt = prompts(llm_calls)[-1]
    assert (
        REPLY.split("\n", maxsplit=1)[0] in answer_prompt
    )  # the summaries are what the answer reads
    assert EQUATION not in answer_prompt and "$$" not in answer_prompt


def test_skip_summary_is_off_by_default(mock_llm, llm_calls, qa_cfg):
    assert qa_cfg.qa.skip_summary is False
    ask_chunks(qa_cfg, "ab", ["First passage.", "Second passage."])
    assert len(llm_calls) == 3


def test_skip_summary_keeps_citations_and_page_provenance(monkeypatch):
    from paperqa.utils import encode_id

    question = "What is d(n)?"
    key = "pqac-" + encode_id(question + EQUATION[:500], 8)  # PaperQA2's key for this chunk
    real = paperqa.llm_params
    reply = f"It grows like a Catalan number ({key})."
    monkeypatch.setattr(paperqa, "llm_params", lambda cfg: {**real(cfg), "mock_response": reply})
    answer, [chunk] = ask_chunks(skip_cfg(True), "a", [EQUATION], question)
    assert f"({chunk.name})" in answer  # the key became the cited chunk name with its pages
    assert chunk.name.endswith("pages 1-1")
    assert "References" in answer and "Paper a" in answer
    assert key not in answer


def test_skip_summary_keeps_the_retrieval_order_for_the_source_cut(mock_llm, llm_calls):
    # PaperQA2 alone would order equal scores by chunk name, i.e. by paper id (a, b, c...).
    letters, texts = "edcba", [f"Passage {n} of paper {x}." for n, x in enumerate("edcba")]
    ask_chunks(skip_cfg(True, answer_max_sources=3), letters, texts)
    [prompt] = prompts(llm_calls)
    assert [t in prompt for t in texts] == [True, True, True, False, False]
    assert prompt.index(texts[0]) < prompt.index(texts[1]) < prompt.index(texts[2])


def test_skip_summary_answer_max_sources_decides_how_many_chunks_are_read(mock_llm, llm_calls):
    letters, texts = "abcd", [f"Passage {n}." for n in range(4)]
    ask_chunks(skip_cfg(True, answer_max_sources=4), letters, texts)
    assert all(t in prompts(llm_calls)[-1] for t in texts)


def test_skip_summary_reads_a_text_that_two_papers_share_once(mock_llm, llm_calls):
    # PaperQA2 keys a context by its text: the same text twice would be one key twice.
    text = "A license notice that two papers repeat."
    letters, texts = "abc", [text, "Own passage of b.", text]
    ask_chunks(skip_cfg(True, answer_max_sources=3), letters, texts)
    [prompt] = prompts(llm_calls)
    assert prompt.count(text) == 1 and "Own passage of b." in prompt
    assert "Paper a" in prompt and "Paper c" not in prompt  # the better ranked copy is kept
    keys = prompt.split("Valid Keys: ")[1].split("\n")[0].split(", ")
    assert len(keys) == len(set(keys)) == 2


def test_skip_summary_off_keeps_every_chunk_even_with_the_same_text(mock_llm, llm_calls):
    ask_chunks(skip_cfg(False), "ab", ["Same text.", "Same text."])
    assert len(llm_calls) == 3  # a summary per chunk, then the answer


def test_skip_summary_without_contexts_warns_and_keeps_the_summaries(mock_llm, llm_calls):
    cfg = skip_cfg(True)
    a = stored("a")
    markdown = "<!-- page 1 | method=text_layer -->\nAttention is a softmax.\n\n"
    warnings: list[str] = []
    asyncio.run(PaperQAAnswerer(cfg).ask("What is attention?", [(a, markdown)], warnings.append))
    assert len(warnings) == 1 and "skip_summary" in warnings[0]
    assert len(llm_calls) == 2  # one summary, then the answer: PaperQA2's own retrieval
    assert "Attention is a softmax." not in prompts(llm_calls)[-1]


def test_skip_summary_off_is_silent_without_contexts(mock_llm, qa_cfg):
    a = stored("a")
    markdown = "<!-- page 1 | method=text_layer -->\nAttention is a softmax.\n\n"
    warnings: list[str] = []
    asyncio.run(PaperQAAnswerer(qa_cfg).ask("What is attention?", [(a, markdown)], warnings.append))
    assert warnings == []


# ------------------------------------------------------ the copy rule of the verbatim answer prompt

RULE = (
    "Take every circuit, gate, qubit index and equation only from the context above, "
    "and copy it exactly, keeping its LaTeX. "
    "Keep each one with the paper it comes from: do not move a gate or a term from one "
    "paper's circuit or equation into another's. "
    "If the context does not show a gate or a term, say so instead of guessing."
)
CIRCUIT = "\\begin{quantikz} \\lstick{$q_0$} & \\gate{H} & \\ctrl{1} & \\qw \\\\ & & \\gate{Z} & \\qw \\end{quantikz}"


def test_the_copy_rule_text_is_pinned():
    assert paperqa.COPY_RULE == RULE
    assert "{" not in RULE and "}" not in RULE  # PaperQA2 fills the prompt with str.format


def test_skip_summary_adds_the_copy_rule_to_the_answer_prompt(mock_llm, llm_calls):
    ask_chunks(skip_cfg(True), "ab", [CIRCUIT, EQUATION])
    [prompt] = prompts(llm_calls)
    assert prompt.count(RULE) == 1
    assert CIRCUIT in prompt and EQUATION in prompt  # the rule is in the prompt, not in the chunks
    assert (
        prompt.index(EQUATION) < prompt.index(RULE) < prompt.index("Answer (")
    )  # after the context


def test_skip_summary_false_has_no_copy_rule_in_any_request(mock_llm, llm_calls):
    ask_chunks(skip_cfg(False), "ab", [CIRCUIT, EQUATION])
    assert len(llm_calls) == 3  # two summaries and the answer
    assert not any(RULE in call["messages"][-1]["content"] for call in llm_calls)
    assert not any("copy it exactly" in str(call["messages"]) for call in llm_calls)


def test_copy_rule_changes_nothing_else_in_the_answer_request(mock_llm, llm_calls, monkeypatch):
    from paperqa.prompts import default_system_prompt

    texts = [CIRCUIT, EQUATION, "A third passage."]
    ask_chunks(skip_cfg(True), "abc", texts)
    monkeypatch.setattr(paperqa, "with_copy_rule", lambda prompt: prompt)
    ask_chunks(skip_cfg(True), "abc", texts)
    ruled, plain = llm_calls
    assert RULE in ruled["messages"][-1]["content"] and RULE not in plain["messages"][-1]["content"]
    assert (
        ruled["messages"][-1]["content"].replace(f"{RULE}\n\n", "")
        == plain["messages"][-1]["content"]
    )
    assert ruled["messages"][0] == plain["messages"][0]  # the system prompt
    assert ruled["messages"][0]["content"] == default_system_prompt  # untouched, PaperQA2's own
    assert {k: v for k, v in ruled.items() if k != "messages"} == {
        k: v for k, v in plain.items() if k != "messages"
    }


def test_copy_rule_keeps_the_citation_format_and_the_refusal_phrase(mock_llm, llm_calls):
    from paperqa.prompts import CANNOT_ANSWER_PHRASE, CITATION_KEY_CONSTRAINTS

    ask_chunks(skip_cfg(True), "a", [CIRCUIT])
    [prompt] = prompts(llm_calls)
    assert CITATION_KEY_CONSTRAINTS in prompt
    assert f'reply "{CANNOT_ANSWER_PHRASE}."' in prompt
    key = prompt.split("Valid Keys: ")[1].split("\n")[0]
    assert key.startswith("pqac-") and f"{key}: {CIRCUIT}\nFrom Paper a" in prompt


def test_copy_rule_is_not_added_without_contexts(mock_llm, llm_calls):
    a = stored("a")
    markdown = "<!-- page 1 | method=text_layer -->\nAttention is a softmax.\n\n"
    asyncio.run(PaperQAAnswerer(skip_cfg(True)).ask("What is attention?", [(a, markdown)], print))
    assert not any(RULE in call["messages"][-1]["content"] for call in llm_calls)


def test_copy_rule_goes_before_the_answer_and_nowhere_else():
    from paperqa.prompts import qa_prompt

    ruled = paperqa.with_copy_rule(qa_prompt)
    assert ruled.count(RULE) == 1
    assert ruled.replace(f"{RULE}\n\n", "") == qa_prompt
    assert ruled.index(RULE) > ruled.index("{context}")
    assert ruled.index(RULE) < ruled.index("{prior_answer_prompt}")
    assert ruled.index("extraneous information") < ruled.index(RULE)  # the last instruction
    assert f"information.\n\n{RULE}\n\n{{prior_answer_prompt}}Answer (" in ruled


@pytest.mark.parametrize(
    "template", ["No placeholder.", "{prior_answer_prompt} twice {prior_answer_prompt}"]
)
def test_copy_rule_refuses_an_answer_prompt_it_cannot_place_it_in(template):
    with pytest.raises(ValueError, match="answer prompt changed"):
        paperqa.with_copy_rule(template)
