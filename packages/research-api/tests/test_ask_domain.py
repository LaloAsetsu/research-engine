"""The rule that reads a formatted answer, and the life of one ask."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from research_api.domain.ask import (
    AskAnswer,
    AskFailure,
    AskRecord,
    AskState,
    AskTimings,
    EventKind,
    read_formatted_answer,
)
from research_api.domain.documents import is_doc_id, page_kind_of, resolve_doc_id
from research_api.domain.errors import ConflictError, NotFoundError

A = "a" * 16
B = "b" * 16
FULL_A = A + "1" * 48
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def formatted(body: str, *entries: str, question: str | None = None) -> str:
    head = f"Question: {question}\n\n" if question is not None else ""
    refs = "\n".join(entries)
    return f"{head}{body}\n\nReferences\n\n{refs}\n" if entries else f"{head}{body}\n"


def test_an_answer_is_split_from_its_references() -> None:
    text = formatted(
        f"Loss limits T1 ({A} pages 2-3).",
        f"1. ({A} pages 2-3): Some Paper (2025).",
        f"2. ({B} pages 5-5): Other Paper (2024).",
    )
    read = read_formatted_answer(text, full_ids=[FULL_A])
    assert read.answer == f"Loss limits T1 ({A} pages 2-3)."
    assert read.references_text.startswith(f"1. ({A} pages 2-3)")
    assert [(c.key, c.doc_id, c.first_page, c.last_page) for c in read.citations] == [
        (f"{A} pages 2-3", FULL_A, 2, 3),  # the full id is taken from the sources
        (f"{B} pages 5-5", B, 5, 5),  # an id nobody names stays as printed
    ]


def test_a_paper_cited_for_two_ranges_has_two_citations() -> None:
    text = formatted("x", f"1. ({A} pages 1-1): P.", f"2. ({A} pages 4-6): P.")
    assert [c.key for c in read_formatted_answer(text).citations] == [
        f"{A} pages 1-1",
        f"{A} pages 4-6",
    ]


def test_the_question_line_is_dropped_only_when_it_is_this_question() -> None:
    text = formatted("The answer.", f"1. ({A} pages 1-1): P.", question="What is T1?")
    assert read_formatted_answer(text, question="What is T1?").answer == "The answer."
    assert read_formatted_answer(text, question="Another?").answer.startswith("Question: What")
    assert read_formatted_answer(text).answer.startswith("Question: What")


def test_a_multi_line_question_is_dropped_whole() -> None:
    question = "First line\n\nsecond paragraph"
    text = formatted("The answer.", f"1. ({A} pages 1-1): P.", question=question)
    assert read_formatted_answer(text, question=question).answer == "The answer."


def test_an_answer_without_references_is_all_answer() -> None:
    read = read_formatted_answer("Only text.\n")
    assert (read.answer, read.references_text, read.citations) == ("Only text.", "", ())


def test_a_references_heading_the_model_wrote_stays_in_the_answer() -> None:
    body = "Intro.\n\nReferences\n\n- a list the model wrote, with no citation form"
    read = read_formatted_answer(body + "\n")
    assert read.answer == body
    assert read.citations == ()


def test_only_the_last_references_list_is_the_list() -> None:
    body = "Intro.\n\nReferences\n\nnot a citation"
    text = formatted(body, f"1. ({A} pages 1-2): P.")
    read = read_formatted_answer(text)
    assert read.answer == body
    assert [c.key for c in read.citations] == [f"{A} pages 1-2"]


@pytest.mark.parametrize(
    "entry",
    [
        f"1. ({A[:15]} pages 1-2): short id",
        f"1. ({A.upper()} pages 1-2): upper case",
        f"1. ({A} page 1-2): wrong word",
        f"({A} pages 1-2): no number",
    ],
)
def test_entries_that_do_not_read_like_citations_are_ignored(entry: str) -> None:
    assert read_formatted_answer(formatted("x", entry)).citations == ()


def test_doc_id_rules() -> None:
    assert is_doc_id("a" * 16) and is_doc_id("a" * 64)
    assert not is_doc_id("a" * 15) and not is_doc_id("a" * 65)
    assert not is_doc_id("A" * 16) and not is_doc_id("g" * 16)
    assert not is_doc_id("../../etc/passwd")
    assert page_kind_of("latex") == "section"
    assert page_kind_of("pdf") == "page"


def test_a_short_id_names_one_paper_or_it_is_refused() -> None:
    ids = ["1" * 16 + "a" * 48, "1" * 16 + "b" * 48, "2" * 64]
    assert resolve_doc_id(ids, "2" * 16) == "2" * 64
    assert resolve_doc_id(ids, ids[0]) == ids[0]
    with pytest.raises(ConflictError):
        resolve_doc_id(ids, "1" * 16)
    with pytest.raises(NotFoundError):
        resolve_doc_id(ids, "3" * 16)


# --- the record ---------------------------------------------------------------------------


def answer() -> AskAnswer:
    return AskAnswer("a", "", (), (), (), (), None, AskTimings(0.0, 0.0, 0.0))


def record() -> AskRecord:
    return AskRecord("id", "ana", NOW)


def kinds(r: AskRecord) -> list[EventKind]:
    return [e.kind for e in r.events]


def test_a_normal_ask_goes_queued_retrieving_answering_answer() -> None:
    r = record()
    r.waiting(0)
    r.start()
    r.entered("retrieving")
    r.entered("answering")
    r.finish(answer(), NOW)
    assert kinds(r) == [
        EventKind.QUEUED,
        EventKind.RETRIEVING,
        EventKind.ANSWERING,
        EventKind.ANSWER,
    ]
    assert [e.id for e in r.events] == [0, 1, 2, 3]
    assert r.state is AskState.DONE and r.final and r.answer is not None
    assert r.position is None and r.finished_at == NOW


def test_the_position_is_said_again_only_when_it_changes() -> None:
    r = record()
    assert r.waiting(2) is not None
    assert r.waiting(2) is None
    assert r.waiting(1) is not None
    assert r.position == 1
    r.start()
    assert r.waiting(0) is None  # running: nothing is queued any more
    assert r.position is None


def test_a_phase_is_not_repeated_and_not_taken_when_over() -> None:
    r = record()
    r.start()
    assert r.entered("answering") is not None
    assert r.entered("answering") is None
    assert r.phase == "answering"
    r.fail(AskFailure("unavailable", 503, "down"), NOW)
    assert r.entered("retrieving") is None
    assert r.failure is not None and r.failure.status == 503 and r.state is AskState.FAILED


def test_cancel_while_queued_ends_it_at_once() -> None:
    r = record()
    r.waiting(1)
    event = r.cancel(NOW)
    assert event is not None and event.kind is EventKind.CANCELLED
    assert r.state is AskState.CANCELLED and r.final
    assert r.cancel(NOW) is None  # a second cancel does nothing
    assert r.finish(answer(), NOW) is None and r.answer is None


def test_cancel_while_running_drops_the_answer_that_arrives_later() -> None:
    r = record()
    r.start()
    r.entered("answering")
    assert r.cancel(NOW + timedelta(seconds=1)) is not None
    assert r.dropped and r.state is AskState.CANCELLED
    assert r.finish(answer(), NOW) is None
    assert r.fail(AskFailure("internal", 500, "x"), NOW) is None
    assert kinds(r)[-1] is EventKind.CANCELLED
    assert r.answer is None and r.failure is None


def test_a_finished_ask_cannot_be_cancelled_or_failed() -> None:
    r = record()
    r.start()
    r.finish(answer(), NOW)
    assert r.cancel(NOW) is None
    assert r.fail(AskFailure("internal", 500, "x"), NOW) is None
    assert r.state is AskState.DONE
