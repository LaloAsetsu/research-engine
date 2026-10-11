"""The retry ladder of the OCR adapters: what is asked after each kind of bad output."""

import pytest

from docingest.adapters.ocr.ladder import Attempt, Generation, LimitRefused, run_ladder
from docingest.adapters.ocr.profiles import attempts, profile_for
from docingest.domain.ocr_guards import OcrGuards
from docingest.domain.ocr_guards import page_area_sq_in as area

G = OcrGuards()
LETTER = area(1275, 1650, 150)
GOOD = "A qubit is described by two complex amplitudes. " * 3
LOOP = "and then the model kept going " * 20
OVERLONG = "word " * 40000
CUT = "".join(f"row {i} value {i * 3}\n" for i in range(300)) + "row cut"  # a long table


def run(replies, *, profile="markdown", max_tokens=4096, guards=G, temperature=0.0, penalty=1.05):
    """Drive the ladder with scripted ``(text, finish)`` replies; returns (asked, picked)."""
    asked: list[Attempt] = []

    def generate(attempt, n):
        asked.append(attempt)
        assert n == len(asked) - 1
        return replies[len(asked) - 1]

    out, k = run_ladder(
        profile_for(profile),
        guards,
        temperature=temperature,
        penalty=penalty,
        max_tokens=max_tokens,
        area_sq_in=LETTER,
        generate=generate,
        read=lambda r: Generation(*r),
    )
    assert out == replies[: len(asked)]
    return asked, k


def test_a_good_page_is_one_attempt():
    asked, k = run([(GOOD, "stop")])
    assert asked == [Attempt(0.0, 1.05, 4096)] and k == 0


def test_a_loop_is_retried_once_with_a_stricter_setting():
    asked, k = run([(LOOP, "length"), (GOOD, "stop")])
    assert asked == [Attempt(0.0, 1.05, 4096), Attempt(0.2, 1.15, 4096)]  # same budget
    assert k == 1


def test_a_loop_that_survives_the_retries_is_not_retried_again():
    asked, k = run([(LOOP, "length")] * 3 + [(GOOD, "stop")])
    assert [a.temperature for a in asked] == [0.0, 0.2, 0.5] and k == 2  # no fourth try


def test_the_third_rung_can_still_rescue_a_page():
    asked, k = run([(LOOP, "length"), (LOOP, "length"), (GOOD, "stop")])
    assert [a.penalty for a in asked] == [1.05, 1.15, 1.25] and k == 2


def test_text_too_long_for_the_page_gets_the_stricter_retry_not_more_room():
    asked, _ = run([(OVERLONG, "length"), (GOOD, "stop")])
    assert asked[1] == Attempt(0.2, 1.15, 4096)


def test_a_dense_page_that_hit_the_limit_is_retried_with_more_room_and_the_same_setting():
    asked, k = run([(CUT, "length"), (GOOD, "stop")])
    assert asked == [Attempt(0.0, 1.05, 4096), Attempt(0.0, 1.05, 8192)] and k == 1


def test_a_page_still_truncated_with_more_room_ends_the_ladder_on_the_longer_answer():
    cut = CUT
    longer = CUT + "".join(f"row {i} value {i * 3}\n" for i in range(300, 600))
    asked, k = run([(cut, "length"), (longer, "length"), (GOOD, "stop")])
    assert asked == [Attempt(0.0, 1.05, 4096), Attempt(0.0, 1.05, 8192)]  # no third attempt
    assert k == 1
    # The longer of two cut-off answers wins whichever comes later.
    asked, k = run([(longer, "length"), (cut, "length"), (GOOD, "stop")])
    assert k == 0


def test_cut_off_answers_of_a_stricter_rung_are_compared_by_length_too():
    # Three cut-off answers (olmOCR-style ladder or a limit that cannot be raised): longest.
    texts = [CUT[:2000], CUT, CUT[:1000]]
    _, k = run([(t, "length") for t in texts], max_tokens=16000)
    assert k == 1


def test_a_truncated_page_beats_a_loop_and_a_good_one_beats_both():
    cut = CUT
    assert run([(LOOP, "stop"), (cut, "length"), (cut, "length")])[1] == 2
    # the truncated page is still a page
    assert run([(cut, "length"), (LOOP, "stop"), (LOOP, "stop")])[1] == 0
    assert run([(GOOD, "stop")])[1] == 0


def test_an_empty_answer_is_retried_once():
    asked, k = run([("", "stop"), (GOOD, "stop")])
    assert len(asked) == 2 and k == 1


def test_an_empty_answer_twice_stays_empty():
    asked, k = run([("", "stop")] * 3)
    assert len(asked) == 3 and k == 2


def test_the_default_ladder_is_the_configured_attempt_and_two_stricter():
    profile = profile_for("markdown")
    assert attempts(profile, 0.0, 1.05) == [(0.0, 1.05), (0.2, 1.15), (0.5, 1.25)]
    assert attempts(profile, 0.3, None, OcrGuards(max_attempts=1)) == [(0.3, None)]
    assert attempts(profile, 0.0, 1.05, OcrGuards(max_attempts=2))[-1] == (0.2, 1.15)


def test_a_profile_with_its_own_ladder_keeps_it():
    olmocr = profile_for("olmocr")
    assert len(attempts(olmocr, 0.0, 1.05)) == len(olmocr.ladder)
    front_matter = (
        "---\nprimary_language: en\nis_rotation_valid: True\nrotation_correction: 0\n---\n"
    )
    bad = ("no front matter here", "stop")
    good = (front_matter + GOOD, "stop")
    asked, k = run([bad, bad, good, bad], profile="olmocr", max_tokens=8000)
    assert [a.temperature for a in asked] == [0.1, 0.1, 0.2] and k == 2


def test_an_output_the_profile_calls_invalid_beats_a_loop():
    bad = ("no front matter, but " + GOOD, "stop")
    assert run([(LOOP, "stop"), bad, *[bad] * 6], profile="olmocr", max_tokens=8000)[1] == 7


@pytest.mark.parametrize("limit", [8192, 16000])
def test_no_extra_room_when_the_limit_is_already_generous(limit):
    cut = CUT
    asked, _ = run([(cut, "length"), (GOOD, "stop")], max_tokens=limit)
    assert asked[1].max_tokens == limit


def test_a_refused_raised_limit_ends_the_ladder_with_the_best_answer_so_far():
    asked = []

    def generate(attempt, n):
        asked.append(attempt)
        if attempt.max_tokens > 4096:
            raise LimitRefused("context too small")
        return (CUT, "length")

    out, k = run_ladder(
        profile_for("markdown"),
        G,
        temperature=0.0,
        penalty=1.05,
        max_tokens=4096,
        area_sq_in=LETTER,
        generate=generate,
        read=lambda r: Generation(*r),
    )
    assert [a.max_tokens for a in asked] == [4096, 8192]
    assert out == [(CUT, "length")] and k == 0


def test_a_refusal_of_the_first_attempt_is_not_swallowed():
    def generate(attempt, n):
        raise LimitRefused("no")

    with pytest.raises(LimitRefused):
        run_ladder(
            profile_for("markdown"),
            G,
            temperature=0.0,
            penalty=1.05,
            max_tokens=4096,
            area_sq_in=LETTER,
            generate=generate,
            read=lambda r: Generation(*r),
        )


def test_a_page_of_unknown_size_has_no_length_bound():
    asked, k = run_unsized([(" ".join(f"w{i}" for i in range(40000)), "stop")])
    assert len(asked) == 1 and k == 0


def run_unsized(replies):
    asked: list[Attempt] = []

    def generate(attempt, n):
        asked.append(attempt)
        return replies[len(asked) - 1]

    _, k = run_ladder(
        profile_for("markdown"),
        G,
        temperature=0.0,
        penalty=1.05,
        max_tokens=4096,
        area_sq_in=None,
        generate=generate,
        read=lambda r: Generation(*r),
    )
    return asked, k


def test_a_dot_leader_runaway_gets_the_stricter_retry_never_the_larger_limit():
    # Old (measured): the first entry then ". . . ." up to 8192 tokens, retried with twice
    # max_tokens, 350 s, nothing kept. A loop at the limit is retried with a stricter sampling.
    runaway = "10.1.3 The circuit " + ". " * 4000
    asked, k = run(
        [(runaway, "length"), ("10.1.3 The circuit .... 367\n10.2 Factoring .... 369", "stop")]
    )
    assert asked == [Attempt(0.0, 1.05, 4096), Attempt(0.2, 1.15, 4096)] and k == 1
