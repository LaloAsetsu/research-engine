"""The whole ask path with a mocked LLM: the answer prompt holds the gate list after a circuit
code block only when ``[qa] skip_summary`` is on. Real ``Container``, ``AskService`` and
``PaperQAAnswerer``; fake store, embedder and index; an invented paper."""

import asyncio

import pytest

pytest.importorskip("paperqa")

import litellm
from fakes import FakeEmbedder, FakeIndex, InMemoryStore, add_doc

from docingest.adapters.qa import paperqa
from docingest.adapters.qa.paperqa import PaperQAAnswerer
from docingest.application.index import IndexService
from docingest.bootstrap import Container
from docingest.config import AppConfig
from docingest.domain.chunking import PAGE_BREAK
from docingest.domain.circuits import gate_list

PAPER = "a" * 64
CIRCUIT = (
    "\\begin{quantikz}\n"
    "\\lstick{$\\ket{a}$} & \\gate{H} & \\ctrl{1} & \\qw \\\\\n"
    "\\lstick{$\\ket{b}$} & \\qw & \\targ{} & \\meter{}\n"
    "\\end{quantikz}"
)
CZ = "\\begin{quantikz}\n& \\ctrl{1} & \\qw \\\\\n& \\ctrl{-1} & \\qw\n\\end{quantikz}"
BLOCK = f"``` latex\n{CIRCUIT}\n```"
LIST = gate_list(CIRCUIT).text
LIST_CZ = gate_list(CZ).text
REPLY = "The first wire gets a Hadamard gate."


def pad(n: int) -> str:
    return ("lamp " * (n // 5 + 1))[:n]


PAGE_1 = f"Pair drawn here.\n\n{BLOCK}\n\nFig 1."
# a second block that the first chunk cuts and the second chunk ends
PAGE_2 = pad(240 - len(PAGE_1) - len(PAGE_BREAK) - 1) + "\n" + f"``` latex\n{CZ}\n```\n\nFig 2."
PAGE_3 = f"Display math: $${CZ}$$ ends. " + pad(200)


@pytest.fixture
def llm_calls(monkeypatch):
    """The keyword arguments of every request sent to the (mocked) model."""
    sent = []
    real = paperqa.llm_params
    monkeypatch.setattr(paperqa, "llm_params", lambda cfg: {**real(cfg), "mock_response": REPLY})
    original = litellm.acompletion

    async def spy(*args, **kwargs):
        sent.append(kwargs)
        return await original(*args, **kwargs)

    monkeypatch.setattr(litellm, "acompletion", spy)
    return sent


def ask(skip_summary: bool, question: str = "lamp pair figure") -> str:
    cfg = AppConfig.model_validate(
        {
            "qa": {
                "llm": "openai/mock",
                "embedding": "sparse",
                "skip_summary": skip_summary,
                "chunk_chars": 300,
                "overlap": 40,
                "answer_max_sources": 20,
            }
        }
    )
    store = InMemoryStore()
    pages = [(PAGE_1, "A"), (PAGE_2, "B"), (PAGE_3, "C")]
    add_doc(store, PAPER, pages, name="p.txt")
    embedder = FakeEmbedder()
    index = FakeIndex(embedder_fingerprint=embedder.fingerprint)
    IndexService(
        store=store, embedder=embedder, index=index, chunk_chars=300, overlap=40, log=lambda _: None
    ).build()
    parts = {"store": store, "embedder": embedder, "index": index, "qa": PaperQAAnswerer(cfg)}
    container = Container(cfg, log=lambda _: None, overrides=parts)
    return asyncio.run(container.ask.ask(question, lambda _: None))


def prompts(llm_calls) -> list[str]:
    return [call["messages"][-1]["content"] for call in llm_calls]


def test_with_skip_summary_the_answer_prompt_has_the_list_right_after_the_block(llm_calls):
    ask(skip_summary=True)
    [prompt] = prompts(llm_calls)  # no summary requests: one request, the answer
    assert f"{BLOCK}\n\n{LIST}\n" in prompt
    assert prompt.index(LIST) < prompt.index("Fig 1.")  # between the block and its caption


def test_the_block_that_starts_in_the_previous_chunk_has_the_list_of_the_whole_block(llm_calls):
    ask(skip_summary=True)
    [prompt] = prompts(llm_calls)
    # the chunk that ends the second block starts inside it, and still gets the whole list
    assert f"```\n\n{LIST_CZ}\n\n\nFig 2." in prompt
    assert prompt.count(LIST_CZ) == 1  # the chunk that cuts the block does not get it


def test_a_circuit_in_display_math_gets_no_list(llm_calls):
    ask(skip_summary=True)
    [prompt] = prompts(llm_calls)
    math = prompt[prompt.index("Display math:") :]
    assert "Gate list" not in math.split("ends.")[0]


def test_without_skip_summary_no_request_holds_a_gate_list(llm_calls):
    ask(skip_summary=False)
    assert len(llm_calls) > 1  # a summary for each chunk, then the answer
    assert not any("Gate list" in text for call in llm_calls for text in [str(call["messages"])])


def test_the_copy_rule_and_the_chunk_order_are_unchanged_by_the_lists(llm_calls):
    ask(skip_summary=True)
    [prompt] = prompts(llm_calls)
    assert paperqa.COPY_RULE in prompt
    assert prompt.index(LIST) < prompt.index(paperqa.COPY_RULE)
    assert prompt.index("Answer (") > prompt.index(paperqa.COPY_RULE)
