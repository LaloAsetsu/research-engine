"""The vendored matcher through the functions the scorer uses, on made-up expressions."""

import pytest

from qa_bench.score import mathnorm as mn


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (r"\frac{a}{b}", "a/b"),
        (r"\ket{0}", "|0>"),
        ("α²", r"\alpha^2"),
        ("ħω", r"\hbar\omega"),
        (r"\left(a+b\right)", "(a+b)"),
        (r"\langle a|b\rangle", "<a|b>"),
        (r"\ketbra{a}{b}", "|a><b|"),
        (r"\mathrm{CNOT}", "CNOT"),
        (r"x \cdot y", "xy"),
        (r"\begin{pmatrix}1&0\\0&1\end{pmatrix}", "1,0,0,1"),
    ],
)
def test_one_expression_written_two_ways_has_one_canonical_form(left, right):
    assert mn.canon(left) == mn.canon(right)


def test_an_expression_key_is_exact_when_its_form_is_inside_the_answer():
    key = {"type": "expr", "value": r"\frac{a}{b}"}
    assert mn.match_expr(key, "so the ratio $a/b$ follows") == {"exact": True, "symbol_recall": 1.0}


def test_an_alternative_spelling_of_the_key_can_make_it_exact():
    key = {"type": "expr", "value": "x+y", "alts": ["y+x"]}
    assert mn.match_expr(key, "we get y+x")["exact"] is True


def test_symbol_recall_is_the_share_of_the_keys_symbols_in_the_answer():
    key = {"type": "expr", "value": "a+b+c+d"}
    assert mn.match_expr(key, "a b c only") == {"exact": False, "symbol_recall": 0.75}
    assert mn.symbols(r"\sigma_{z}\otimes I") == frozenset({"sigma", "z", "otimes", "I"})


@pytest.mark.parametrize(
    ("text", "values"),
    [
        ("1,5 GHz", (1.5, 15.0)),  # a decimal comma, or thousands: both are offered
        ("2.5x10^-3 s", (0.0025,)),
        ("1.5e-3", (0.0015,)),
        (r"1.5 \times 10^{-3}", (0.0015,)),
        ("12,345 units", (12345.0,)),
    ],
)
def test_numbers_are_read_with_exponents_and_decimal_commas(text, values):
    assert sorted(mn.numbers(text)) == sorted(values)


def test_number_words_leave_out_compounds_and_scales():
    text = "Twenty-two and thirty two, treinta y dos, two hundred, dos mil, eight, ocho"
    assert mn.number_words(text) == frozenset({8})


def test_the_word_route_and_the_digit_route_are_separate_functions():
    key = {"type": "num", "value": 8, "rel": 0.02}
    assert mn.match_num_digits(key, "eight gates") is False
    assert mn.match_num_word(key, "eight gates") is True
    assert mn.match_num_digits(key, "8 gates") is True
    assert mn.match_num_word({"type": "num", "value": 8.5, "rel": 0.02}, "eight") is False


def test_the_upstream_match_key_still_reads_number_words_but_the_scorer_does_not_call_it():
    key = {"type": "num", "value": 8, "rel": 0.02}
    assert mn.match_key(key, "eight") == {"hit": True, "lenient": True, "word_only": True}
    assert mn.match_key({"type": "text", "value": "abc"}, "xx abc yy") == {
        "hit": True,
        "lenient": True,
    }
    assert mn.match_key({"type": "expr", "value": "a+b"}, "no")["hit"] is False
