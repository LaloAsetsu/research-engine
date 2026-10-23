"""Retrieval ranks, cost and the short-answer F1 of QASPER."""

import pytest
from scoredata import answer, question

from qa_bench.score.answers import (
    cost_scores,
    normalize_answer,
    qasper_scores,
    retrieval_scores,
    token_f1,
)


def test_normalize_drops_case_punctuation_and_articles():
    assert normalize_answer("The Cat, a dog!") == "cat dog"


def test_token_f1_is_the_squad_score():
    assert token_f1("cat sat", "the cat sat on the mat") == pytest.approx(2 / 3)
    assert token_f1("x", "y") == 0.0
    assert token_f1("up down", "down up") == 1.0
    assert token_f1("go go", "go") == pytest.approx(2 / 3)


def qasper_question(*refs):
    return question("Q-1", None, ext={"refs": [{"answer": r} for r in refs]})


def test_qasper_takes_the_best_reference():
    q = qasper_question("the cat sat on the mat", "cat sat")
    scores = qasper_scores(q, answer("Q-1", "long text", ext={"short": "cat sat"}))
    assert scores == {"answer_f1": 1.0, "answer_good": True, "refused": False}


def test_qasper_good_means_f1_of_at_least_half():
    q = qasper_question("one two three four")
    assert qasper_scores(q, answer("Q-1", "x", ext={"short": "one two"}))["answer_good"] is True
    assert qasper_scores(q, answer("Q-1", "x", ext={"short": "one"}))["answer_good"] is False


def test_qasper_refusal_is_the_phrase_in_the_long_answer():
    q = qasper_question("yes")
    scores = qasper_scores(q, answer("Q-1", "I Cannot Answer this.", ext={"short": "no"}))
    assert scores["refused"] is True


def test_qasper_without_a_short_answer_scores_none():
    q = qasper_question("yes")
    assert qasper_scores(q, answer("Q-1", "x")) == {
        "answer_f1": None,
        "answer_good": None,
        "refused": None,
    }


def test_retrieval_hits_are_ranks_at_or_above_the_threshold():
    ext = {"retrieval": {"hybrid_lenient": 7, "rerank_lenient": 1, "rerank_strict": 2}}
    scores = retrieval_scores(answer("Q-1", "x", ext=ext))
    assert scores == {"psg10": True, "psg50": True, "rr1": True, "rr1_strict": False}


def test_a_rank_beyond_the_threshold_or_missing_is_a_miss():
    ext = {"retrieval": {"hybrid_lenient": 30, "rerank_lenient": None}}
    scores = retrieval_scores(answer("Q-1", "x", ext=ext))
    assert scores == {"psg10": False, "psg50": True, "rr1": False, "rr1_strict": False}


def test_a_run_without_ranks_scores_none():
    expected = dict.fromkeys(("psg10", "psg50", "rr1", "rr1_strict"))
    assert retrieval_scores(answer("Q-1", "x")) == expected
    assert retrieval_scores(answer("Q-1", "x", ext={"retrieval": None})) == expected


def test_cost_reads_timings_usage_and_the_last_llm_call():
    calls = [
        {"prompt_tokens": 100, "end": 1.0},
        {"prompt_tokens": 900, "end": 5.0},
        {"completion_tokens": 3},
    ]
    item = answer(
        "Q-1",
        "x",
        usage={"prompt_tokens": 1000, "completion_tokens": 50, "llm_requests": 2},
        ext={"llm_calls": calls},
    )
    assert cost_scores(item) == {
        "wall_s": 2.0,
        "prompt_tokens": 1000,
        "completion_tokens": 50,
        "llm_requests": 2,
        "answer_prompt_tokens": 900,
        "error": False,
    }


def test_an_error_is_flagged_and_missing_fields_are_none():
    item = {"id": "Q-1", "repeat": 1, "answer": None, "contexts": [], "error": {"kind": "other"}}
    scores = cost_scores(item)
    assert scores["error"] is True
    assert scores["wall_s"] is None
    assert scores["answer_prompt_tokens"] is None
