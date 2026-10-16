"""The whole ask path with a mocked LLM: the answer prompt holds the whole display equation, code
block or table that a retrieved chunk cuts, only when ``[qa] skip_summary`` and ``[qa] whole_blocks``
are both on. Real
``Container``, ``AskService`` and ``PaperQAAnswerer``; fake store, embedder and index; an invented
paper."""

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
from docingest.domain.circuits import gate_list
from docingest.domain.text import split_pages

REPLY = "The circuit prepares a pair."
CHARS, OVERLAP = 300, 40
PAPER = "a" * 64
CIRCUIT = (
    "\\begin{quantikz}\n& \\gate{H} & \\ctrl{1} & \\qw \\\\\n& \\qw & \\targ{} & \\meter{TAILTOKEN}\n"
    "\\end{quantikz}"
)
BLOCK = f"``` latex\n{CIRCUIT}\n```"
LIST = gate_list(CIRCUIT).text
CAPTION = "Fig. 1. The circuit that prepares the pair."
LONG_TABLE = "| a | b |\n|---|---|\n" + "| 1 | 2 |\n" * 200 + "| 3 | 4 |"
SERVER_400 = (
    "This model's maximum context length is 16384 tokens. However, you requested 1024 output "
    "tokens and your prompt contains at least 15361 input tokens, for a total of at least 16385 "
    "tokens. Please reduce the length of the input prompt or the number of requested output "
    "tokens. (parameter=input_tokens, value=15361)"
)


def pad(n: int, letter: str = "a") -> str:
    return (f"{letter}lamp " * (n // 6 + 1))[:n]


def page_text(table: bool = False) -> str:
    """The circuit starts at 235 and ends past the first chunk's 300, its caption after it."""
    head = pad(233) + "\n\n" + BLOCK + "\n\n" + CAPTION + "\n\n"
    rest = LONG_TABLE if table else pad(200, "b")
    return head + rest + "\n\n" + pad(1500, "c")


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
    starts=(0,),
    *,
    whole_blocks: bool = True,
    text: str | None = None,
    sources: int = 10,
    warn=lambda _: None,
    store_cls=InMemoryStore,
    broken: bool = False,
) -> str:
    cfg = AppConfig.model_validate(
        {
            "qa": {
                "llm": "openai/mock",
                "embedding": "sparse",
                "skip_summary": skip_summary,
                "whole_blocks": whole_blocks,
                "chunk_chars": CHARS,
                "overlap": OVERLAP,
                "answer_max_sources": sources,
            }
        }
    )
    store = store_cls()
    add_doc(store, PAPER, [(text or page_text(), "Part")], name="p.txt").title = "Lamp pair paper"
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
    doc = store.corpus()[0][0]
    chunks = {
        c.start: c for c in document_chunks(doc, split_pages(store.markdown(doc)), CHARS, OVERLAP)
    }
    index.answer = [chunks[s] for s in starts]
    if broken:
        store.broken = True  # type: ignore[attr-defined]
    parts = {"store": store, "embedder": embedder, "index": index, "qa": PaperQAAnswerer(cfg)}
    return asyncio.run(
        Container(cfg, log=lambda _: None, overrides=parts).ask.ask(
            "What does the pair circuit do?", warn
        )
    )


class Breakable(InMemoryStore):
    broken = False

    def markdown(self, doc):
        if self.broken:
            raise OSError("no document.md")
        return super().markdown(doc)


def prompts(llm_calls) -> list[str]:
    return [call["messages"][-1]["content"] for call in llm_calls]


def test_the_fixture_cuts_the_circuit_in_the_first_chunk():
    text = page_text()
    assert text.index(BLOCK) < CHARS < text.index(BLOCK) + len(BLOCK)
    assert "TAILTOKEN" not in text[:CHARS]


def test_with_skip_summary_the_prompt_holds_the_rest_of_the_circuit_its_list_and_its_caption(
    llm_calls,
):
    ask(skip_summary=True)
    [prompt] = prompts(llm_calls)  # no summary requests: one request, the answer
    assert "TAILTOKEN" in prompt
    assert (
        f"{BLOCK}\n\n{LIST}\n\n\n{CAPTION}" in prompt
    )  # list right after the fence, then the caption
    assert prompt.count("Gate list") == 1


def test_without_skip_summary_no_request_holds_the_rest_of_the_circuit(llm_calls):
    ask(skip_summary=False)
    assert len(llm_calls) > 1  # a summary for each chunk, then the answer
    text = " ".join(str(call["messages"]) for call in llm_calls)
    assert "TAILTOKEN" not in text and "Gate list" not in text


def test_with_the_key_off_the_prompt_is_the_one_the_gate_lists_alone_give(llm_calls, monkeypatch):
    import docingest.application.ask as ask_module

    ask(skip_summary=True, whole_blocks=False)
    [off] = prompts(llm_calls)
    assert "TAILTOKEN" not in off and CAPTION not in off
    assert off.count("Gate list") == 0  # the circuit is cut: the gate list needs its closing fence

    def never(*_args, **_kwargs):
        raise AssertionError("the completion must not run with the key off")

    monkeypatch.setattr(ask_module, "with_whole_blocks", never)
    monkeypatch.setattr(ask_module, "with_block_notes", never)
    ask(skip_summary=True, whole_blocks=False)
    assert prompts(llm_calls)[1] == off  # and nothing else differs


def test_the_key_is_off_by_default(llm_calls):
    cfg = AppConfig.model_validate({"qa": {"skip_summary": True}})
    assert cfg.qa.whole_blocks is False
    assert (
        Container(cfg, log=lambda _: None, overrides={"store": InMemoryStore()}).ask._whole_blocks
        is None
    )


def test_the_prompt_grows_by_the_added_characters_only(llm_calls, monkeypatch):
    import docingest.application.ask as ask_module

    text = page_text()
    ask(skip_summary=True)
    monkeypatch.setattr(ask_module, "with_whole_blocks", lambda c, *a, **k: (c, {}))
    ask(skip_summary=True)  # the same question with the circuit left cut
    full, cut = prompts(llm_calls)
    caption_end = text.index(CAPTION) + len(CAPTION)
    list_text = f"\n\n{LIST}\n"
    assert len(full) - len(cut) == (caption_end - CHARS) + len(list_text)


def test_a_table_over_three_chunks_is_shown_as_it_was_with_a_note(llm_calls):
    ask(skip_summary=True, text=page_text(table=True), starts=(260,))
    [prompt] = prompts(llm_calls)
    assert (
        "Note: this passage shows only part of a longer table; the rest is not in this passage."
        in prompt
    )
    assert prompt.count("| 3 | 4 |") == 0  # the end of the table is not there


def test_a_retry_with_fewer_sources_keeps_the_completed_best_chunk(llm_calls, server_rejects):
    attempts = server_rejects(2)
    warnings: list[str] = []
    ask(skip_summary=True, starts=(0, 520, 780, 1040), warn=warnings.append)
    assert len(attempts) == 3
    assert all("TAILTOKEN" in a for a in attempts)  # the best chunk is the completed one
    assert warnings == [
        "answer prompt too long for the model; retried with 2 sources",
        "answer prompt too long for the model; retried with 1 source",
    ]


def test_a_failing_completion_still_answers_and_says_so(llm_calls):
    warnings: list[str] = []
    answer = ask(skip_summary=True, store_cls=Breakable, broken=True, warn=warnings.append)
    [prompt] = prompts(llm_calls)
    assert answer and "TAILTOKEN" not in prompt
    assert f"whole blocks skipped for {PAPER[:16]}: no document.md" in warnings
