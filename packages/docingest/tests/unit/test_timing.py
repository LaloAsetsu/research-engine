"""The helpers of ``tests/timing.py``, run against a fake clock: the cost of a call is the number
the call is given, so no test here depends on how fast the machine is."""

import gc

import pytest
from timing import assert_linear, timeout_notes

from docingest.domain.models import PageMethod
from docingest.ports.converters import Conversion, Segment


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def scripted(costs):
    """``(clock, fn, calls)``: ``fn(tag)`` costs the next number of ``costs`` (the last one
    repeats), and ``calls`` collects the tags in the order they ran."""
    clock, calls = Clock(), []
    it = iter(costs)
    last = [0.0]

    def fn(tag):
        calls.append(tag)
        last[0] = next(it, last[0])
        clock.now += last[0]

    return clock, fn, calls


def by_size(cost_of):
    """``fn`` whose cost is ``cost_of(input)``, with the clock it advances."""
    clock = Clock()

    def fn(n):
        clock.now += cost_of(n)

    return clock, fn


def test_linear_work_passes_after_three_rounds_and_runs_small_then_big_each_time():
    clock, fn = by_size(lambda n: n)
    calls = []

    def traced(n):
        calls.append(n)
        fn(n)

    assert_linear(traced, 1.0, 4.0, clock=clock)
    assert calls == [1.0, 4.0] * 3


def test_quadratic_work_fails_every_attempt_and_the_message_holds_all_of_them():
    clock, fn = by_size(lambda n: n * n)
    calls = []

    def traced(n):
        calls.append(n)
        fn(n)

    with pytest.raises(AssertionError) as caught:
        assert_linear(traced, 1.0, 4.0, clock=clock)
    assert len(calls) == 3 * 5 * 2  # three attempts of five rounds
    message = str(caught.value)
    assert [f"attempt {i}:" in message for i in (1, 2, 3)] == [True] * 3
    assert "small 1.0000 s, big 16.0000 s" in message


def test_work_that_grows_a_little_more_than_linearly_still_fails():
    clock, fn = by_size(lambda n: n * (1 + n))  # 2 for 1, 20 for 4: ten times
    with pytest.raises(AssertionError):
        assert_linear(fn, 1.0, 4.0, clock=clock)


def test_a_burst_of_load_that_hits_only_the_big_runs_of_one_attempt_is_retried():
    # the first attempt: every big run costs 16 (a burst); after it the machine is quiet
    costs = [1.0, 16.0] * 5 + [1.0, 4.0] * 3
    clock, fn, calls = scripted(costs)
    assert_linear(fn, "small", "big", clock=clock)
    assert calls == ["small", "big"] * 8


def test_one_disturbed_round_does_not_count_because_the_fastest_run_of_each_size_is_kept():
    costs = [1.0, 16.0, 1.0, 4.0, 1.0, 4.0]
    clock, fn, calls = scripted(costs)
    assert_linear(fn, "small", "big", clock=clock)
    assert len(calls) == 6


def test_the_floor_replaces_a_ratio_of_timer_sized_runs():
    clock, fn = by_size(lambda n: n)
    assert_linear(fn, 0.001, 0.04, clock=clock)  # 40 times, but under the floor
    with pytest.raises(AssertionError):
        assert_linear(fn, 0.001, 0.06, clock=clock)
    assert_linear(fn, 0.001, 0.06, floor=0.1, clock=clock)


def test_the_ceiling_fails_a_run_that_is_in_proportion_but_too_slow_in_itself():
    clock, fn = by_size(lambda n: n)
    assert_linear(fn, 5.0, 20.0, clock=clock)
    with pytest.raises(AssertionError, match="big < 10 s"):
        assert_linear(fn, 5.0, 20.0, ceiling=10, clock=clock)


def test_the_collector_is_off_during_the_runs_and_back_as_it_was_after():
    seen = []
    clock, fn = by_size(lambda n: n)

    def watching(n):
        seen.append(gc.isenabled())
        fn(n)

    assert gc.isenabled()
    assert_linear(watching, 1.0, 4.0, clock=clock)
    assert gc.isenabled() and seen == [False] * 6
    gc.disable()
    try:
        assert_linear(watching, 1.0, 4.0, clock=clock)
        assert not gc.isenabled()
    finally:
        gc.enable()


def test_the_collector_comes_back_when_the_check_fails_or_the_function_raises():
    clock, fn = by_size(lambda n: n * n)
    with pytest.raises(AssertionError):
        assert_linear(fn, 1.0, 4.0, clock=clock)
    assert gc.isenabled()

    def boom(_):
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        assert_linear(boom, 1.0, 4.0, clock=clock)
    assert gc.isenabled()


def test_timeout_notes_finds_a_timeout_in_the_paper_or_in_one_of_its_segments():
    plain = Segment("a", warnings=("pandoc exited with code 64: stub",))
    slow = Segment("b", method=PageMethod.LATEX_PLAINTEXT, warnings=("pandoc timed out after 2s",))
    late = Segment("c", warnings=("no time left for this run",))
    conv = Conversion(
        [plain, slow, late], PageMethod.LATEX, "pandoc", warnings=["pandoc timed out after 9s"]
    )
    assert timeout_notes(conv) == [
        "pandoc timed out after 9s",
        "pandoc timed out after 2s",
        "no time left for this run",
    ]
    assert timeout_notes(Conversion([plain], PageMethod.LATEX, "pandoc", warnings=["x"])) == []
