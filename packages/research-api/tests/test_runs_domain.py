"""The lines of a run file and the closed-book prompt, against the qa-run/1 schema itself."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from qa_bench.formats import QA_RUN, check_line

from research_api.domain.ask import AskAnswer, AskContext, AskTimings, Citation
from research_api.domain.jobs import ArmSpec
from research_api.domain.runs import (
    BARE_PROMPT_MD5,
    AnswerError,
    ModelFacts,
    RunAnswer,
    RunHeaderInput,
    Usage,
    bare_messages,
    header_record,
    item_record,
    partial_text,
)

NOW = datetime(2026, 10, 23, 1, 2, 3, tzinfo=UTC)
DOC = "ab" * 32


def header(**changes: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "run_id": "bare-demo-v1-abcd1234",
        "created": NOW,
        "started": NOW,
        "set_name": "demo/v1",
        "set_md5": "c" * 32,
        "corpus": "demo-corpus",
        "arm": ArmSpec("bare"),
        "model": ModelFacts("m", "label", "rev-1"),
        "repeats": 2,
        "notes": "hello",
        "runner": "research-api 0.1.0",
    }
    fields.update(changes)
    return header_record(RunHeaderInput(**fields))  # type: ignore[arg-type]


def test_the_closed_book_prompt_is_the_one_of_r_281() -> None:
    # B2 records this md5 for the C0 and C1 arms of the closed-book run (R-281).
    assert BARE_PROMPT_MD5.startswith("9ebdae30")
    system, user = bare_messages("  What is T1?  ")
    assert system.startswith("Answer in a direct and concise tone.")
    assert "Question: What is T1?\n" in user
    assert user.endswith("Answer (about 200 words, but can be longer):")
    assert 'reply "I cannot answer."' in user


@pytest.mark.parametrize("arm", ["bare", "harness", "oracle"])
def test_a_header_is_a_valid_qa_run_header(arm: str) -> None:
    record = header(arm=ArmSpec(arm, "v", {"bm25": "never"} if arm == "harness" else {}))
    assert check_line(QA_RUN, record, header=True) == []
    assert record["concurrency"] == 1 and record["repeats"] == 2
    assert record["set"] == {"name": "demo/v1", "md5": "c" * 32}


def test_a_closed_book_run_searches_no_corpus_and_names_its_prompt() -> None:
    record = header(prompt_md5=BARE_PROMPT_MD5)
    assert record["corpus"] == {"id": "none"}
    assert record["arm"] == {"name": "bare", "prompt_md5": BARE_PROMPT_MD5}  # type: ignore[comparison-overlap]
    assert record["model"] == {  # type: ignore[comparison-overlap]
        "id": "m",
        "revision": "rev-1",
        "endpoint": "label",
        "temperature": 0.0,
        "max_tokens": 1024,
    }


def test_a_harness_run_names_the_corpus_of_its_set_and_keeps_its_overrides() -> None:
    record = header(arm=ArmSpec("harness", "no index", {"use_index": False}))
    assert record["corpus"] == {"id": "demo-corpus"}
    assert record["arm"] == {  # type: ignore[comparison-overlap]
        "name": "harness",
        "variant": "no index",
        "overrides": {"use_index": False},
    }
    assert "temperature" not in record["model"]  # type: ignore[operator]


def test_a_set_with_no_corpus_gives_an_unknown_one_and_the_end_is_written_when_there_is_one() -> (
    None
):
    assert header(arm=ArmSpec("oracle"), corpus=None)["corpus"] == {"id": "unknown"}
    assert "ended" not in header()
    ended = header(ended=NOW)
    assert ended["ended"] == NOW.isoformat()
    assert check_line(QA_RUN, ended, header=True) == []


def test_the_notes_of_a_header_say_how_the_run_went() -> None:
    record = header(interleaved_asks=2, partial="Cancelled at t with 1 of 4 answers")
    assert (
        record["notes"] == "hello Interleaved asks: 2. Partial: Cancelled at t with 1 of 4 answers."
    )
    assert partial_text(1, 4, "Cancelled", NOW) == (
        "Cancelled at 2026-10-23T01:02:03+00:00 with 1 of 4 answers"
    )


def ask_answer() -> AskAnswer:
    return AskAnswer(
        answer="J.",
        references_text="1. (abababababababab pages 1-1): Paper.",
        citations=(Citation("abababababababab pages 1-1", DOC, 1, 1),),
        contexts=(AskContext(DOC, "abababababababab pages 1-1", 0, 1, 1, False, "text"),),
        sources=(DOC,),
        warnings=("a warning",),
        index_generation="g1",
        timings=AskTimings(0.5, 1.5, 2.0),
    )


def test_an_ask_answer_becomes_a_valid_item_with_its_citations_and_contexts() -> None:
    record = item_record("DP-001", 2, RunAnswer.from_ask(ask_answer()))
    assert check_line(QA_RUN, record, header=False) == []
    assert record["citations"][0] == {  # type: ignore[index]
        "key": "abababababababab pages 1-1",
        "doc_id": DOC,
        "first_page": 1,
        "last_page": 1,
    }
    assert record["contexts"][0]["text"] == "text"  # type: ignore[index]
    assert record["timings"] == {"wall_s": 2.0, "retrieve_s": 0.5, "answer_s": 1.5}
    assert "usage" not in record and "error" not in record and "answer_prompt_md5" not in record
    assert "score" not in record["contexts"][0]  # type: ignore[index]


def test_a_closed_book_item_has_usage_a_prompt_and_no_contexts() -> None:
    answer = RunAnswer(
        "text", timings=AskTimings(0.0, 1.0, 1.0), usage=Usage(1, 100, None), prompt_md5="f" * 32
    )
    record = item_record("q", 1, answer)
    assert check_line(QA_RUN, record, header=False) == []
    assert record["usage"] == {"llm_requests": 1, "prompt_tokens": 100}
    assert record["answer_prompt_md5"] == "f" * 32 and record["contexts"] == []


def test_an_empty_usage_is_left_out() -> None:
    assert "usage" not in item_record("q", 1, RunAnswer("t", usage=Usage()))


def test_a_question_that_failed_is_a_valid_item_with_an_error_and_no_answer() -> None:
    record = item_record("q", 1, RunAnswer.failed(AnswerError("timeout", "too slow"), 3.0))
    assert check_line(QA_RUN, record, header=False) == []
    assert record["answer"] is None
    assert record["error"] == {"kind": "timeout", "message": "too slow"}
    assert record["timings"]["wall_s"] == 3.0  # type: ignore[index]
    assert "timings" not in item_record("q", 1, RunAnswer.failed(AnswerError("other", "x")))
