"""The lines of a run file and the closed-book prompt, against the qa-run/1 schema itself."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from qa_bench.formats import QA_RUN, check_line

from research_api.domain.ask import (
    UNPLACED_WARNING,
    AskAnswer,
    AskContext,
    AskTimings,
    Citation,
    GoldPassage,
)
from research_api.domain.jobs import ArmSpec
from research_api.domain.runs import (
    BARE_PROMPT_MD5,
    ORACLE_TIMINGS_NOTE,
    AnswerError,
    ModelFacts,
    RunAnswer,
    RunHeaderInput,
    Usage,
    bare_messages,
    gold_passages,
    header_record,
    item_record,
    oracle_gaps,
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


# --- the oracle arm ---------------------------------------------------------------------------

OTHER = "cd" * 32


def gold_item(item_id: str, *papers: tuple[str, list[dict[str, object]]]) -> dict[str, object]:
    return {
        "id": item_id,
        "gold_papers": [{"doc_id": doc, "passages": passages} for doc, passages in papers],
    }


def test_the_gold_passages_are_the_md_texts_in_set_order() -> None:
    item = gold_item(
        "q",
        (DOC, [{"md_text": "first"}, {"source_text": "only a quote"}, {"md_text": "second"}]),
        (OTHER, [{"md_text": "third"}]),
    )
    assert gold_passages(item) == (
        GoldPassage(DOC, "first"),
        GoldPassage(DOC, "second"),
        GoldPassage(OTHER, "third"),
    )


def test_a_passage_without_md_text_is_left_out_of_the_gold_passages() -> None:
    item = gold_item("q", (DOC, [{"source_text": "a quote"}, {"block_index": 3}]))
    assert gold_passages(item) == ()


def test_the_oracle_gaps_are_the_ids_of_the_items_with_a_gold_passage_without_a_text() -> None:
    items = [
        gold_item("a", (DOC, [{"md_text": "x"}])),
        gold_item("b", (DOC, [{"source_text": "x"}])),
        gold_item("c", (DOC, [{"block_index": 3}])),
        gold_item("d", (DOC, [{"source_text": "x"}, {"md_text": "y"}])),  # one passage is enough
        gold_item("e", (DOC, [{"md_text": "x"}]), (OTHER, [{"pdf_page": 2}])),
    ]
    assert oracle_gaps(items) == ["b", "c", "d", "e"]
    assert oracle_gaps(items[:1]) == [] and oracle_gaps([]) == []


@pytest.mark.parametrize(
    "item",
    [
        {"id": "q"},
        {"id": "q", "gold_papers": None},
        {"id": "q", "gold_papers": []},
        gold_item("q", (DOC, [])),  # a paper with no passage lacks no text
    ],
)
def test_a_question_with_no_gold_passage_is_no_gap_and_gets_no_passage(
    item: dict[str, object],
) -> None:
    assert oracle_gaps([item]) == [] and gold_passages(item) == ()


def oracle_answer(*warnings: str, truncated: bool = False) -> AskAnswer:
    return AskAnswer(
        answer="J.",
        references_text="",
        citations=(),
        contexts=(AskContext(DOC, "abababababababab pages 2-3", 4, 2, 3, False, "gold text"),),
        sources=(DOC,),
        warnings=warnings,
        index_generation=None,
        timings=AskTimings(0.4, 1.5, 1.9),
        truncated=truncated,
    )


def test_an_oracle_item_says_what_its_retrieve_time_is_and_that_nothing_was_cut() -> None:
    record = item_record("DP-001", 1, RunAnswer.from_oracle(oracle_answer("1 of 4 not in index")))
    assert check_line(QA_RUN, record, header=False) == []
    assert record["usage"] == {"truncated": False}
    assert record["ext"] == {"timings_note": ORACLE_TIMINGS_NOTE}
    assert "not a retrieval time" in ORACLE_TIMINGS_NOTE
    assert record["timings"]["retrieve_s"] == 0.4  # type: ignore[index]


def test_an_oracle_item_whose_passage_was_not_placed_says_so_and_a_cut_is_recorded() -> None:
    unplaced = f"{UNPLACED_WARNING} abababababababab; cited with pages 1-6"
    answer = oracle_answer("another warning", unplaced, truncated=True)
    record = item_record("DP-001", 1, RunAnswer.from_oracle(answer))
    assert check_line(QA_RUN, record, header=False) == []
    assert record["ext"] == {"timings_note": ORACLE_TIMINGS_NOTE, "warnings": [unplaced]}
    assert record["usage"] == {"truncated": True}


def test_only_an_oracle_item_has_ext_and_a_truncated_flag() -> None:
    record = item_record("q", 1, RunAnswer.from_ask(ask_answer()))
    assert "ext" not in record and "usage" not in record
