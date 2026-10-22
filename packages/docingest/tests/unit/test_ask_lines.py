"""The JSON-lines front of AskSession: parsing, formatting and the loop, on fakes."""

import asyncio
import json

import pytest
from fakes import FakeEmbedder, FakeIndex, FakeQA, FakeReranker, InMemoryStore, add_doc

from docingest.application.ask import AskOptions, AskService, AskSession, Retrieval
from docingest.application.index import IndexService
from docingest.entrypoints.ask_lines import InvalidRequest, parse_request, serve_lines

PAPER_A, PAPER_B = "a" * 64, "b" * 64
TEXT_A = [("Attention weights use a softmax over scaled dot products. " * 4, "Method")]
TEXT_B = [("Qubits decohere when they couple to the environment. " * 4, "Intro")]


def session(qa=None):
    store = InMemoryStore()
    add_doc(store, PAPER_A, TEXT_A, name="a.txt")
    add_doc(store, PAPER_B, TEXT_B, name="b.txt")
    embedder = FakeEmbedder()
    index = FakeIndex(embedder_fingerprint=embedder.fingerprint)
    IndexService(
        store=store, embedder=embedder, index=index, chunk_chars=60, overlap=10, log=lambda _: None
    ).build()
    retrieval = Retrieval(embedder, index, FakeReranker(), 50, 2)
    qa = qa or FakeQA()
    service = AskService(store=store, qa=qa, retrieval=lambda: retrieval)
    return AskSession(service), index, qa


def answers(lines, **kw):
    sess, index, qa = session(**kw)
    out: list[str] = []
    asyncio.run(serve_lines(sess, lines, out.append))
    return [json.loads(line) for line in out], index, qa


def line(**fields):
    return json.dumps(fields)


# ------------------------------------------------------------------ parsing


def test_a_request_with_only_a_question_uses_the_defaults():
    assert parse_request(line(question="why")) == (None, "why", AskOptions(), False)


def test_every_field_of_a_request_is_read():
    request = parse_request(
        line(
            question="why",
            id="q1",
            use_index=False,
            bm25="never",
            whole_blocks=True,
            return_contexts=True,
        )
    )
    assert request == ("q1", "why", AskOptions(False, "never", True), True)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not json", "not JSON"),
        ("[1]", "JSON object"),
        (line(), "question is required"),
        (line(question="  "), "question is required"),
        (line(question=3), "question is required"),
        (line(question="q", id=3), "id must be a string"),
        (line(question="q", use_index="yes"), "true or false"),
        (line(question="q", return_contexts=1), "true or false"),
        (line(question="q", bm25="sometimes"), "bm25 must be one of"),
        (line(question="q", whole_blocks="on"), "true, false or null"),
        (line(question="q", queston="typo"), "unknown field queston"),
    ],
)
def test_a_line_that_is_not_a_request_says_why(text, message):
    with pytest.raises(InvalidRequest, match=message):
        parse_request(text)


# ------------------------------------------------------------------ the loop


def test_one_answer_line_per_request_in_order_with_the_id_echoed():
    out, index, _ = answers(
        [
            line(question="how do qubits decohere", id="one"),
            "\n",
            line(question="attention softmax", id="two", bm25="never"),
        ]
    )
    assert [o["id"] for o in out] == ["one", "two"]
    assert all(o["error"] is None and o["answer"].startswith("answer to") for o in out)
    assert out[0]["sources"] and out[0]["index_generation"] == "g1"
    assert set(out[0]["timings"]) == {"retrieve_s", "answer_s", "wall_s"}
    assert "contexts" not in out[0]
    assert index.keywords == [None, "never"] and index.closed == 1  # the build's close only


def test_contexts_come_with_return_contexts():
    out, _, qa = answers([line(question="how do qubits decohere", return_contexts=True)])
    [context, *_] = out[0]["contexts"]
    assert set(context) == {
        "doc_id",
        "name",
        "first_page",
        "last_page",
        "start",
        "is_reference",
        "text",
    }
    assert [c["text"] for c in out[0]["contexts"]] == [c.text for c in qa.contexts]


def test_a_bad_line_and_a_failing_question_give_error_lines_and_the_loop_goes_on():
    class Down(FakeQA):
        async def ask(self, question, documents, warn, contexts=None):
            if "down" in question:
                raise ConnectionError("the model server is down")
            return await super().ask(question, documents, warn, contexts)

    out, _, _ = answers(
        [
            "{broken",
            line(question="q", id="x", queston=1),
            line(question="the server is down", id="d"),
            line(question="how do qubits decohere", id="ok"),
        ],
        qa=Down(),
    )
    assert [o["error"] is not None for o in out] == [True, True, True, False]
    assert out[0]["error"]["kind"] == "InvalidRequest" and out[0]["id"] is None
    assert out[1]["id"] == "x" and "unknown field" in out[1]["error"]["message"]
    assert out[2]["id"] == "d" and out[2]["error"] == {
        "kind": "ConnectionError",
        "message": "the model server is down",
    }
    assert out[2]["answer"] is None and out[2]["timings"] is None
    assert out[3]["id"] == "ok" and out[3]["answer"]


def test_an_option_the_question_cannot_use_is_an_error_line():
    out, _, _ = answers([line(question="q", whole_blocks=True), line(question="how do qubits")])
    assert (
        out[0]["error"]["kind"] == "ValueError"
        and "whole_blocks needs" in out[0]["error"]["message"]
    )
    assert out[1]["error"] is None


def test_the_end_of_the_input_ends_the_loop_and_empty_input_gives_nothing():
    out, _, _ = answers([])
    assert out == []
