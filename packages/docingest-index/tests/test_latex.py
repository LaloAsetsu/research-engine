import gc
import time

import pytest

from docingest_index.latex import _is_math, has_latex, latex_text


@pytest.mark.parametrize(
    ("question", "found"),
    [
        ("What is $mc$ here?", "mc"),
        (r"What is $\frac{n!}{(n/2)!(n/2+1)!}$?", r"\frac{n!}{(n/2)!(n/2+1)!}"),
        (r"¿De dónde sale $$\sum_{i=0}^{n} i$$ aquí?", r"\sum_{i=0}^{n} i"),
        (r"Explica \(a^2 + b^2\) por favor", "a^2 + b^2"),
        (r"Explica \[ E = mc^2 \] por favor", " E = mc^2 "),
        ("Where does $a, b, c$ come from?", "a, b, c"),
        (r"Qué es \frac{n!}{(n/2)!}?", r"\frac{n!}{(n/2)!}?"),
        (r"Qué es |\psi\rangle exactamente", r"|\psi\rangle"),
        (r"El estado \ket{0} del qubit", r"\ket{0}"),
        (r"La matriz \mathcal{H} \otimes \mathcal{K}", r"\mathcal{H} \otimes \mathcal{K}"),
        (r"Un factor 2\pi aparece", r"2\pi"),
        ("Qué es x_{i} y 10^{-3} en el texto", "x_{i} 10^{-3}"),
        (r"Explica \sum_{i=0}^{n} x_i en detalle", r"\sum_{i=0}^{n}"),
        (r"Compara $\alpha$ con \beta_{2} y x_i", r"\alpha \beta_{2}"),
        ("Cuánto es $dx$ aquí", "dx"),
        (r"Un factor (a+b)^{2} y v[0]^{2} y {a}^{2}", "(a+b)^{2} v[0]^{2} {a}^{2}"),
        ("Una matriz\n$$\nE = mc\n$$\nde energía", "\nE = mc\n"),
        (r"Compara \frac{a + b}{c} con 3", r"\frac{a + b}{c}"),
    ],
)
def test_latex_is_found_and_returned_without_the_prose_around_it(question, found):
    assert has_latex(question)
    assert latex_text(question) == found


@pytest.mark.parametrize(
    "question",
    [
        "What is the coherence time of a transmon qubit?",
        "¿Cuál es el tiempo de coherencia de un qubit transmon?",
        "",
        "It costs $5.",
        "It costs $5 and $10 today",
        "Cuesta $5 o $10 según el día",
        "Between $5 and 10$ euros",  # a stray dollar sign, with prose inside
        "The range is $5-$10",
        "Pay $ 5 now, or 5$ later",
        "Cuesta $ a o b$ hoy",  # the opening dollar sign is followed by a space
        "The rate is $ab-$5",  # the closing dollar sign is followed by a digit
        "A $5$ bill",  # only digits between the dollars
        r"Escaped \$5 and \$6 are money",
        r"Open C:\Users\me\Desktop\notes.txt",
        r"Open ..\data\raw\notes.txt",
        r"Open a/\foo and %APPDATA%\Microsoft\Teams",
        r"Open D:\Users",
        r"Open \\server\share\notes.txt",
        r"The regex \d+ and the escape \n",
        r"A bare \x and \t and \q",
        "A snake_case_name and my_variable",
        "The ratio 2^10 or 5_000",
        "x_ab and a_bc and a_b and i_j",
        "Cuánto es T_1 en un transmon y x^2 o x_i",  # a bare mark is a name or a variable
        "Emoticons n_n, T_T, o_O and x_x and ^_^",
        "Cuánto cuesta entre $5 y 10$ al mes",  # money in Spanish, with stray dollar signs
        "Pagué $5 o 6$ por el artículo",
        "Pagó $5, 6 y 7$ por el artículo",
        "Between $5 and $6, $7$",
        "Un $$ab cd ef$ raro",  # an opening $$ with no closing $$ does not open a single-dollar math
        r"Cuestan \$ab cd\$ euros",  # escaped dollar signs
        "Mezcla $ab$$cd$ rara",  # a closing dollar sign right before another one
        "Pagó $una vez$ el total",  # a word of three letters
        "Pagó $1.000 y 2.000$ en total",  # thousands separators
        "Pagó $1,5 o 2,5$ en total",  # decimal commas
        "Pagó $5 y 6$ en total",
        "Pagó $5 de 10$ en total",  # a word of two letters
        "Pagó $5 y 6.$ en total",  # a full stop or a semicolon after a number
        "Pagó $5; 6 y 7$ en total",
        "Qué es $x$ aquí",  # too few tokens to rank anything
        "Qué es $T_1$ aquí",
        "Cuando $f(x)$ crece",
        "Si $k = 2$ entonces",
        "Si $a, b$ son enteros",
        "Qué es x_{i} en el texto",
        "El valor 10^{-3} es pequeño",
        "A {brace} and a {nested {brace}} are not math",
        "A $ alone and a ^ alone and a _ alone",
        r"Cites \[1\] and \[2\]",
    ],
)
def test_ordinary_prose_has_no_latex(question):
    assert not has_latex(question)
    assert latex_text(question) == ""


@pytest.mark.parametrize("mark", ["=", "+", "<", ">", "|", "^", "_", "{", "}", "\\"])
def test_each_math_character_makes_dollar_signs_math_by_itself(mark):
    # "speed" and "fast" alone would be prose (a word of three letters)
    assert latex_text(f"Cuánto es $speed {mark} fast$ aquí") == f"speed {mark} fast"
    assert latex_text("Cuánto es $speed and fast$ aquí") == ""


def test_a_dollar_amount_next_to_real_math_does_not_hide_the_math():
    question = r"Costs $5, and then $\ket{0}$ is prepared"
    assert latex_text(question) == r"\ket{0}"
    # the money before the math is not mistaken for an opening dollar sign
    assert latex_text("I paid $5 for $xy$") == "xy"


def test_an_unclosed_brace_or_delimiter_does_not_fail():
    assert latex_text(r"Qué es \frac{n!") == r"\frac"  # the command is still there
    assert latex_text(r"Qué es $\frac{n") == r"$\frac"
    assert latex_text(r"Un \( sin cerrar") == ""


def test_a_long_question_with_no_latex_is_cheap():
    assert latex_text("palabra " * 20_000) == ""
    assert latex_text("$ " * 5_000) == ""


def _cpu(question: str) -> float:
    start = time.process_time()  # CPU time: waiting for a busy CPU is not counted
    latex_text(question)
    return time.process_time() - start


def _assert_linear(small: str, big: str, factor: float = 8, floor: float = 0.05) -> None:
    """Fail unless the CPU time of `big` is under max(factor * that of `small`, floor).

    The same method as docingest's tests/timing.py (packages do not import each other's
    tests): the two sizes run one after the other, round by round, so a burst of load slows
    both; the fastest run of each is kept (from the third round on, a pass stops the test);
    a failed check is measured again, and the test fails only when all 3 attempts fail.
    Quadratic work costs 16 times as much on 4 times the input, in every attempt. The cyclic
    collector is off during the runs, so a big heap does not make a linear scan look slower."""
    seen = []
    collecting = gc.isenabled()
    gc.collect()  # as in timing.py: start from a clean heap, then keep the collector off
    gc.disable()
    try:
        for _ in range(3):
            fastest_small = fastest_big = float("inf")
            for done in range(1, 6):
                fastest_small = min(fastest_small, _cpu(small))
                fastest_big = min(fastest_big, _cpu(big))
                if done >= 3 and fastest_big < max(factor * fastest_small, floor):
                    return
            seen.append((round(fastest_small, 4), round(fastest_big, 4)))
    finally:
        if collecting:
            gc.enable()
    raise AssertionError(f"not linear in 3 attempts, (small, big) seconds: {seen}")


# Each opener repeated must stay unclosed, or the question is just a row of closed pairs: "$a" and
# "$$a" repeated close each other at once, and a "$" opener cannot reach past the next "$", so no
# amount of them is quadratic. "{a " alone holds no character that starts LaTeX, so a command
# before the brace is what makes the scanner look at it.
@pytest.mark.parametrize("opener", [r"\(a", r"\[a", r"\frac{a "])
def test_unclosed_delimiters_cost_time_in_proportion_to_the_question(opener):
    # Quadrupling the question must cost far less than 16x (quadratic scanning). Only the ratio
    # of two runs is checked, in CPU time of this process, so other load on the machine barely
    # moves it; the floor keeps a timer-sized `small` from making the ratio noise.
    _assert_linear(opener * 10_000 + " fin", opener * 40_000 + " fin")


@pytest.mark.parametrize(
    "inside",
    [
        "n - 1",
        "x - 1",
        "a - 2",
        "2 x",
        "x 2",
        "3 n",
        "0.5 n",
        "x 2y",
        "a, 2",
        "k = 2",
        "x",
        "2 \\pi",
    ],
)
def test_a_number_next_to_a_variable_is_math_not_money(inside):
    assert _is_math(inside)


@pytest.mark.parametrize(
    "inside",
    [
        "5 y 10",
        "5 o 6",
        "5, 6 y 7",
        "1.000 y 2.000",
        "1,5 o 2,5",
        "5 a 10",
        "5 de 10",
        "5; 6 y 7",
        "5 y 6.",
        "5 and 10",
        "5",
        "10 000",
    ],
)
def test_two_numbers_and_short_words_are_money(inside):
    assert not _is_math(inside)


@pytest.mark.parametrize(
    ("question", "found"),
    [
        ("Qué es $n - 1 - m - 2$ aquí", "n - 1 - m - 2"),  # not money: one-letter words and numbers
        ("Qué es $2 xy$ aquí", "2 xy"),
        ("Qué es $ab 2y$ aquí", "ab 2y"),
        ("Qué es $a b c$ aquí", "a b c"),  # three tokens
        ("Qué es $dx$ aquí", "dx"),  # one token of two letters
        (r"Qué es \hat{H} aquí", r"\hat{H}"),
        ("Qué es $a$ y $b$ y $c$ aquí", "a b c"),  # the floor counts the whole question
    ],
)
def test_the_latex_of_a_question_needs_a_word_or_three_tokens(question, found):
    assert latex_text(question) == found


@pytest.mark.parametrize(
    "question",
    [
        "Qué es $a b$ aquí",
        "Qué es $a$ aquí",
        "Qué es $2$ y $3$ aquí",
        "Qué es $a_1$ aquí",
        "Qué es $+$ aquí",
    ],
)
def test_too_little_latex_is_nothing(question):
    assert latex_text(question) == ""
