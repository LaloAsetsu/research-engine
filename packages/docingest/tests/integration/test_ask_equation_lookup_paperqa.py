"""The whole ask path with a mocked LLM: the answer prompt holds the chunk of the equation number
the question names, under a header that names its paper, only when ``[qa] skip_summary`` is on.
Real ``Container``, ``AskService`` and ``PaperQAAnswerer``; fake store, embedder and index;
invented papers."""

import asyncio

import pytest

pytest.importorskip("paperqa")

import litellm
from fakes import FakeEmbedder, FixedIndex, InMemoryStore, add_doc

from docingest.adapters.qa import paperqa
from docingest.adapters.qa.paperqa import PaperQAAnswerer
from docingest.application.index import IndexService, document_chunks
from docingest.bootstrap import Container
from docingest.config import AppConfig
from docingest.domain.text import split_pages

REPLY = "Eq. (2) of the lamp paper is a sum."
CHARS, OVERLAP = 300, 40
IDS = {letter: letter * 64 for letter in "abcdef"}
SENTENCE = (
    "An equation number belongs to the paper it is printed in: for a question about the "
    "Eq. (n) of one paper, do not use the Eq. (n) of another."
)

SERVER_400 = (
    "This model's maximum context length is 16384 tokens. However, you requested 1024 output "
    "tokens and your prompt contains at least 15361 input tokens, for a total of at least 16385 "
    "tokens. Please reduce the length of the input prompt or the number of requested output "
    "tokens. (parameter=input_tokens, value=15361)"
)


def pad(n: int, letter: str) -> str:
    return (f"{letter}lamp " * (n // 6 + 1))[:n]


def paper_page(letter: str) -> str:
    """1,500 characters of the paper's own words with its Eq. (2) at 360, in the second chunk."""
    equation = f"$$ H_{{{letter}}} = \\sum_i s_i \\tag{{2}}$$"
    return pad(360, letter) + equation + pad(1500 - 360 - len(equation), letter)


def header(letter: str) -> str:
    return f"Equation (2) of paper {IDS[letter][:16]}, Lamp {letter} quantum spectra resonators:"


@pytest.fixture
def llm_calls(monkeypatch):
    sent = []
    real = paperqa.llm_params
    monkeypatch.setattr(paperqa, "llm_params", lambda cfg: {**real(cfg), "mock_response": REPLY})
    original = litellm.acompletion

    async def spy(*args, **kwargs):
        sent.append(kwargs)
        return await original(*args, **kwargs)

    monkeypatch.setattr(litellm, "acompletion", spy)
    return sent


@pytest.fixture
def server_rejects(monkeypatch, llm_calls):
    """The first ``n`` answer requests get the server's context-length 400."""
    original = litellm.acompletion

    def rejects(n: int):
        seen = []

        async def spy(*args, **kwargs):
            seen.append(kwargs["messages"][-1]["content"])
            if len(seen) <= n:
                raise litellm.ContextWindowExceededError(
                    message=f"ContextWindowExceededError: OpenAIException - {SERVER_400}",
                    model="mock",
                    llm_provider="openai",
                )
            return await original(*args, **kwargs)

        monkeypatch.setattr(litellm, "acompletion", spy)
        return seen

    return rejects


def ask(
    skip_summary: bool,
    question: str = "In the lamp paper on quantum spectra of resonators, what does Eq. (2) state?",
    picks: str = "ab",
    *,
    sources: int = 10,
    warn=lambda _: None,
) -> str:
    """``picks``: the retrieved chunks, one letter each: the lead chunk of that paper, and for a
    letter that comes again its chunk at 520 (so ``"abcc"`` is a, b, c0, c520)."""
    cfg = AppConfig.model_validate(
        {
            "qa": {
                "llm": "openai/mock",
                "embedding": "sparse",
                "skip_summary": skip_summary,
                "chunk_chars": CHARS,
                "overlap": OVERLAP,
                "answer_max_sources": sources,
            }
        }
    )
    store = InMemoryStore()
    for letter in sorted(set(picks)):
        add_doc(
            store, IDS[letter], [(paper_page(letter), "Part")], name=f"{letter}.txt"
        ).title = f"Lamp {letter} quantum spectra resonators"
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
    chunks = {
        letter: {
            c.start: c
            for c in document_chunks(doc, split_pages(store.markdown(doc)), CHARS, OVERLAP)
        }
        for letter in set(picks)
        for doc in store.corpus()[0]
        if doc.manifest.doc_id == IDS[letter]
    }
    seen: set[str] = set()
    for letter in picks:
        index.answer.append(chunks[letter][520 if letter in seen else 0])
        seen.add(letter)
    parts = {"store": store, "embedder": embedder, "index": index, "qa": PaperQAAnswerer(cfg)}
    return asyncio.run(Container(cfg, log=lambda _: None, overrides=parts).ask.ask(question, warn))


def prompts(llm_calls) -> list[str]:
    return [call["messages"][-1]["content"] for call in llm_calls]


def test_with_skip_summary_each_equation_is_in_the_prompt_under_the_header_of_its_paper(llm_calls):
    ask(skip_summary=True)
    [prompt] = prompts(llm_calls)  # no summary requests: one request, the answer
    for letter in "ab":
        assert prompt.count(header(letter)) == 1
        assert prompt.count(f"H_{{{letter}}} = \\sum_i s_i \\tag{{2}}") == 1
    # each equation is right after its own header and before the next paper's
    order = [
        prompt.index(header("a")),
        prompt.index("H_{a} = "),
        prompt.index(header("b")),
        prompt.index("H_{b} = "),
    ]
    assert order == sorted(order)
    assert prompt.index(header("a")) < prompt.index("blamp")  # a's chunks come before b's


def test_the_header_is_followed_by_the_chunk_as_the_index_holds_it(llm_calls):
    ask(skip_summary=True, picks="a")
    [prompt] = prompts(llm_calls)
    start = prompt.index(header("a"))
    # the chunk that starts at 260 of the paper's text, from its first character to its last
    chunk = paper_page("a")[260:560]
    assert prompt[start + len(header("a")) :].startswith("\n\n" + chunk)


def test_without_skip_summary_no_request_holds_the_header_or_the_added_chunk(llm_calls):
    ask(skip_summary=False)
    assert len(llm_calls) > 1  # a summary for each chunk, then the answer
    text = " ".join(str(call["messages"]) for call in llm_calls)
    assert "Equation (2) of paper" not in text and "H_{a} =" not in text


def test_a_question_that_does_not_name_the_paper_gets_no_equation_chunk_and_no_header(llm_calls):
    # every paper has an Eq. (2): with no title in common the prompt is as without the lookup
    ask(skip_summary=True, question="In some paper on circuits, what does Eq. (2) state?")
    [prompt] = prompts(llm_calls)
    assert "Equation (" not in prompt and "H_{a} =" not in prompt and "H_{b} =" not in prompt


def test_a_question_without_a_number_has_no_header(llm_calls):
    ask(
        skip_summary=True,
        question="In the lamp paper on quantum spectra of resonators, what is the lamp?",
    )
    [prompt] = prompts(llm_calls)
    assert "Equation (" not in prompt and "H_{a} =" not in prompt


def test_the_answer_prompt_carries_the_sentence_about_equation_numbers_once_after_the_context(
    llm_calls,
):
    ask(skip_summary=True)
    [prompt] = prompts(llm_calls)
    assert paperqa.COPY_RULE.count(SENTENCE) == 1 and prompt.count(SENTENCE) == 1
    assert prompt.index("H_{b} = ") < prompt.index(SENTENCE) < prompt.index("Answer (")


def test_the_copy_rule_sentence_has_no_curly_brace_and_sits_before_the_refusal_sentence():
    rule = paperqa.COPY_RULE
    assert "{" not in SENTENCE and "}" not in SENTENCE
    assert rule.index("into another's. ") < rule.index(SENTENCE) < rule.index("say so instead")


def test_the_sentence_is_not_in_a_prompt_that_summaries_wrote(llm_calls):
    ask(skip_summary=False)
    assert not any(SENTENCE in str(call["messages"]) for call in llm_calls)


def test_the_prompt_is_as_long_as_before_when_chunks_were_replaced(llm_calls):
    # ten retrieved chunks and ten places: the two added chunks took the places of two chunks
    # whose papers have another
    ask(skip_summary=True, picks="abccddeeff")
    [prompt] = prompts(llm_calls)
    keys = prompt.split("Valid Keys: ")[1].split("\n")[0].split(", ")
    assert len(keys) == 10
    assert header("a") in prompt and header("b") in prompt
    # the two places came from papers that had a second chunk: e and f are still in the prompt
    assert "elamp" in prompt and "flamp" in prompt


def test_a_retry_with_fewer_sources_drops_the_lowest_ranked_chunks_and_keeps_the_best_papers(
    llm_calls, server_rejects
):
    attempts = server_rejects(2)
    warnings: list[str] = []
    ask(skip_summary=True, picks="abccddeeff", warn=warnings.append)
    first, second, third = attempts
    assert header("a") in first and header("b") in first
    # five sources: a0, a's equation, b0, b's equation, c0
    assert header("a") in second and header("b") in second and "clamp" in second
    assert "dlamp" not in second
    # two sources: a0 and a's equation
    assert header("a") in third and header("b") not in third and "blamp" not in third
    assert warnings == [
        "answer prompt too long for the model; retried with 5 sources",
        "answer prompt too long for the model; retried with 2 sources",
    ]


def test_a_failing_lookup_still_answers_and_says_so(llm_calls):
    warnings: list[str] = []

    class Broken(InMemoryStore):
        broken = False

        def markdown(self, doc):
            if self.broken:
                raise OSError("no document.md")
            return super().markdown(doc)

    store = Broken()
    for letter in "ab":
        add_doc(
            store, IDS[letter], [(paper_page(letter), "Part")], name=f"{letter}.txt"
        ).title = f"Lamp {letter} quantum spectra resonators"
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
    docs = {d.manifest.doc_id: d for d in store.corpus()[0]}
    index.answer = [
        document_chunks(docs[IDS[x]], split_pages(store.markdown(docs[IDS[x]])), CHARS, OVERLAP)[0]
        for x in "ab"
    ]
    store.broken = True
    cfg = AppConfig.model_validate(
        {
            "qa": {
                "llm": "openai/mock",
                "embedding": "sparse",
                "skip_summary": True,
                "chunk_chars": CHARS,
                "overlap": OVERLAP,
                "answer_max_sources": 10,
            }
        }
    )
    parts = {"store": store, "embedder": embedder, "index": index, "qa": PaperQAAnswerer(cfg)}
    answer = asyncio.run(
        Container(cfg, log=lambda _: None, overrides=parts).ask.ask(
            "In the lamp paper on quantum spectra of resonators, what does Eq. (2) state?",
            warnings.append,
        )
    )
    [prompt] = prompts(llm_calls)
    assert answer and "Equation (2) of paper" not in prompt
    assert sorted(warnings) == sorted(
        f"equation lookup skipped for {IDS[x][:16]}: no document.md" for x in "ab"
    )
