"""Key-fact matching and the per-answer metrics."""

import hashlib
from pathlib import Path

import pytest
from scoredata import DOC_A, DOC_B, answer, expr_key, num_key, question, text_key

from qa_bench.score import keys, mathnorm
from qa_bench.score.keys import MATHNORM_CODE_MD5, Match, key_scores, match_key


def test_the_vendored_matcher_is_the_code_of_version_2():
    text = Path(mathnorm.__file__).read_text(encoding="utf-8")
    code = text[text.index('"""', 3) + 3 :]
    assert hashlib.md5(code.encode("utf-8")).hexdigest() == MATHNORM_CODE_MD5


@pytest.mark.parametrize(
    ("value", "text", "expected"),
    [
        ("sigma_z", r"the coupling $\sigma_{z}$ survives", True),
        ("sigma_z", "the coupling \u03c3_z survives", True),
        ("sigma_z", r"the coupling $\sigma_{x}$ is dropped", False),
        ("R_z", "the gate R _ { z } is virtual", True),
        ("R_z", r"the gate $R_{zz}(\theta)$", False),
        ("Bell pair", "a BELL, pair state", True),
        ("Phi^+", "the phase phi", False),
        ("Phi^+", r"$\Phi^{+}$", True),
    ],
)
def test_text_keys_ignore_latex_braces_greek_spelling_and_case(value, text, expected):
    assert match_key(text_key(value), text, number_words=False) == Match(expected, expected)


def test_text_keys_try_their_alternatives():
    key = text_key("dephasing", alts=["desfase"])
    assert match_key(key, "solo el desfase importa", number_words=False).exact


@pytest.mark.parametrize(
    ("value", "text", "hit"),
    [
        (8, "an 8 MHz coupling", True),
        (-8, "an 8 MHz coupling", True),  # signs are often dropped in prose
        (8, "about 8.1 MHz", True),  # inside the relative tolerance of 2%
        (8, "about 9 MHz", False),
        (1.5e-3, "1.5e-3 s", True),
        (1.5e-3, "1.5 x 10^-3 s", True),
        (8, "wire 18", False),
    ],
)
def test_number_keys_match_digits_within_the_tolerance(value, text, hit):
    assert match_key(num_key(value), text, number_words=False).exact is hit


def test_a_number_word_does_not_count_unless_asked_and_is_flagged_either_way():
    key = num_key(3)
    off = match_key(key, "it needs three gates", number_words=False)
    on = match_key(key, "it needs three gates", number_words=True)
    assert off == Match(False, False, True)
    assert on == Match(True, True, True)
    assert match_key(key, "it needs tres puertas", number_words=False).word_only
    assert match_key(key, "it needs 3 gates", number_words=True) == Match(True, True, False)
    assert match_key(key, "3 gates, three of them", number_words=False) == Match(True, True, False)


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (1, "one gate"),
        (21, "twenty-one gates"),
        (2, "twenty-two gates"),
        (2, "two hundred"),
        (2.5, "two"),
    ],
)
def test_number_words_outside_two_to_twenty_are_never_read(value, text):
    assert not match_key(num_key(value), text, number_words=True).exact


def test_expression_keys_are_exact_by_canonical_form_and_lenient_by_symbol_recall():
    key = expr_key(r"\frac{a}{b}")
    assert match_key(key, "the ratio $a/b$ is small", number_words=False) == Match(True, True)
    near = match_key(expr_key("a+b+c+d+e"), "among a b c d e", number_words=False)
    assert near == Match(False, True)
    assert match_key(expr_key("a+b+c+d+e"), "unrelated words", number_words=False) == Match(
        False, False
    )


def _score(keys_, text, *, category="a", number_words=False, **extra):
    q = question("Q-1", keys_, category=category)
    return key_scores(q, answer("Q-1", text, **extra), number_words=number_words)


def test_all_keys_and_the_key_share():
    metrics, rows = _score([text_key("entropy"), num_key(8)], "The entropy is about 8 units.")
    assert metrics["all_keys"] is True
    assert metrics["key_share"] == 1.0
    metrics, rows = _score([text_key("entropy"), num_key(8)], "The entropy is 9.")
    assert (metrics["all_keys"], metrics["key_share"]) == (False, 0.5)
    assert [(r["i"], r["exact"]) for r in rows] == [(0, True), (1, False)]


def test_a_key_record_names_the_paper_of_a_key_that_has_one():
    _, rows = _score([text_key("entropy", paper=DOC_A)], "entropy")
    assert rows[0]["paper"] == DOC_A


def test_number_words_off_is_the_main_column_and_the_words_columns_show_the_rest():
    metrics, rows = _score([num_key(3)], "It needs three gates.")
    assert metrics["all_keys"] is False
    assert metrics["key_share"] == 0.0
    assert metrics["all_keys_words"] is True
    assert metrics["all_keys_lenient_words"] is True
    assert metrics["key_share_words"] == 1.0
    assert metrics["key_share_lenient_words"] == 1.0
    assert metrics["num_word_only"] is True
    assert rows[0]["word_only"] is True
    assert rows[0]["exact"] is False


def test_number_words_on_count_in_the_main_columns_and_stay_flagged():
    metrics, rows = _score([num_key(3)], "It needs three gates.", number_words=True)
    assert metrics["all_keys"] is True
    assert metrics["all_keys_words"] is True
    assert metrics["num_word_only"] is True
    assert rows[0]["exact"] is True


def test_digits_are_never_word_only():
    metrics, rows = _score([num_key(3)], "It needs 3 gates and three more.")
    assert metrics["all_keys"] is True
    assert metrics["num_word_only"] is False
    assert rows[0]["word_only"] is False


LENIENT_ONLY = "the relation among a b c d e holds"


def test_a_lenient_f_hit_without_a_gold_paper_is_flagged_and_not_counted():
    metrics, rows = _score([expr_key("a+b+c+d+e")], LENIENT_ONLY, category="f")
    assert metrics["all_keys"] is False
    assert metrics["all_keys_lenient"] is False
    assert metrics["lenient_unchecked"] is True
    assert rows[0]["unchecked"] is True
    assert rows[0]["lenient"] is False
    assert metrics["all_keys_lenient_words"] is False


def test_a_lenient_f_hit_counts_when_the_answer_cites_a_gold_paper():
    in_text, _ = _score(
        [expr_key("a+b+c+d+e")], f"{LENIENT_ONLY} ({DOC_A[:16]} pages 2-2)", category="f"
    )
    assert in_text["all_keys_lenient"] is True
    assert in_text["lenient_unchecked"] is False
    citation = {"key": "k", "doc_id": DOC_A, "first_page": 1, "last_page": 1}
    in_list, _ = _score([expr_key("a+b+c+d+e")], LENIENT_ONLY, category="f", citations=[citation])
    assert in_list["all_keys_lenient"] is True


def test_a_citation_of_another_paper_does_not_count():
    other = {"key": "k", "doc_id": DOC_B, "first_page": 1, "last_page": 1}
    metrics, _ = _score([expr_key("a+b+c+d+e")], LENIENT_ONLY, category="f", citations=[other])
    assert metrics["lenient_unchecked"] is True


def test_other_categories_keep_the_plain_lenient_rule_and_an_exact_f_hit_is_untouched():
    metrics, rows = _score([expr_key("a+b+c+d+e")], LENIENT_ONLY, category="c")
    assert metrics["all_keys_lenient"] is True
    assert "unchecked" not in rows[0]
    exact, rows = _score([expr_key("a+b+c+d+e")], "it is $a+b+c+d+e$", category="f")
    assert exact["all_keys"] is True
    assert exact["lenient_unchecked"] is False


def test_the_funnel_stages_need_the_text_the_model_was_given():
    ext = {
        "given": [{"text": "entropy appears here"}],
        "answer_prompt": "prompt with entropy and 8",
    }
    metrics, rows = _score([text_key("entropy"), num_key(8)], "entropy", ext=ext)
    assert metrics["chunks"] == 0.5
    assert metrics["evidence"] == 1.0
    assert [r["in_contexts"] for r in rows] == [True, False]


def test_summaries_stand_in_for_the_answer_prompt():
    ext = {"given": [{"text": "x"}], "summaries": ["entropy", None]}
    metrics, _ = _score([text_key("entropy")], "entropy", ext=ext)
    assert metrics["evidence"] == 1.0


def test_stages_are_none_when_the_run_kept_no_text():
    metrics, rows = _score([text_key("entropy")], "entropy", ext={"given": [{"doc_id": DOC_A}]})
    assert metrics["chunks"] is None
    assert metrics["evidence"] is None
    assert metrics["math_spans"] is None
    assert metrics["unsupported_math"] is None
    assert "in_contexts" not in rows[0]


def test_math_in_the_answer_that_neither_chunks_nor_prompt_hold_is_unsupported():
    ext = {"given": [{"text": "we have $x_1+x_2=y$ here"}], "answer_prompt": "prompt"}
    metrics, _ = _score([text_key("k")], "Both $x_1+x_2=y$ and $z_1+z_2=w$ hold.", ext=ext)
    assert metrics["math_spans"] == 2
    assert metrics["unsupported_math"] == 1


def test_short_math_spans_do_not_count():
    text = "the $x$ and $y$ and $abc+def$ and \\(p^2+q^2\\) and \\[a=b+c\\]"
    assert keys.math_spans(text) == ["abc+def", "p^2+q^2"]


@pytest.mark.parametrize(
    ("text", "cannot"),
    [
        ("I cannot answer this.", True),
        ("The paper does not specify the value.", True),
        ("El articulo no se especifica.", True),
        ("No se menciona el valor.", True),
        ("La respuesta no lo menciona.", True),
        ("The value is 8.", False),
    ],
)
def test_the_cannot_answer_pattern(text, cannot):
    metrics, _ = _score([text_key("zzz")], text)
    assert metrics["cannot_answer"] is cannot


def test_an_empty_or_missing_answer_scores_nothing_without_failing():
    metrics, rows = _score([text_key("entropy")], None)
    assert (metrics["all_keys"], metrics["answer_chars"]) == (False, 0)
    assert rows[0]["exact"] is False
