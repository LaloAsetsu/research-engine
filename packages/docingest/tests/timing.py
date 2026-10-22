"""Checks on how work grows with its input, built to survive a loaded machine.

``assert_linear`` is for the tests that guard against quadratic scanning: it runs the same
function on a small and on a four times larger input and fails only when the larger one costs
far more than the input alone explains. It measures CPU time of this process, so waiting for a
busy CPU is not counted, and it protects against the rest of the noise in three ways:

* the two sizes are measured one after the other, round by round, so a burst of load slows
  both and not only the one that happened to run during it;
* the least disturbed run of each size is kept (the minimum over the rounds);
* a failed check is measured again, up to ``attempts`` times in all, and fails only when every
  attempt fails. Quadratic work costs about 16 times as much on four times the input, so it
  fails every attempt; a passing run costs nothing extra.

The cyclic garbage collector is off during the runs: with a big heap (other tests of a session
import PaperQA2) collections cost more per allocation than with a small one, which would make a
linear scan look slower on the larger input.

``timeout_notes`` is for the converter tests that used to check a wall-clock bound on a pandoc
run: the converter records a timeout in the warnings of the result, which does not depend on how
fast the machine is. It names the timeouts that have a note, but not every timeout has one (a
paper whose pieces all time out ends with the first error as its only note), so those tests also
assert ``not conv.degraded``, which the converter sets for every timeout that ends in a result and
is the signal that covers the rest.
"""

import gc
import time
from collections.abc import Callable
from typing import Any

MIN_ROUNDS = 3  # a check that passes after this many rounds stops there


def run_job(job: Callable[[], object]) -> None:
    """``fn`` of :func:`assert_linear` for inputs that are already a function to call."""
    job()


def assert_linear(
    fn: Callable[[Any], object],
    small: Any,
    big: Any,
    *,
    factor: float = 8,
    floor: float = 0.05,
    ceiling: float | None = None,
    rounds: int = 5,
    attempts: int = 3,
    clock: Callable[[], float] = time.process_time,
) -> None:
    """Fail unless ``fn(big)`` costs less than ``max(factor * cost of fn(small), floor)``.

    ``big`` should be four times ``small``: ``factor`` 8 then leaves room for twice the
    expected cost of linear work, and 16 times (quadratic) is far outside it. ``floor`` keeps a
    timer-sized ``small`` from making the ratio noise. ``ceiling`` is an absolute limit in
    seconds on ``fn(big)``, for the tests that also pin the cost of the old slow code.

    Each attempt runs ``fn(small)`` then ``fn(big)`` up to ``rounds`` times and compares the
    fastest of each; it stops as soon as the check passes from :data:`MIN_ROUNDS` rounds on.
    The test fails after ``attempts`` failed attempts, and the message holds every measurement.
    """
    seen: list[str] = []
    collecting = gc.isenabled()
    gc.collect()
    gc.disable()
    try:
        for attempt in range(1, attempts + 1):
            best_small = best_big = float("inf")
            for done in range(1, rounds + 1):
                best_small = min(best_small, _cpu(fn, small, clock))
                best_big = min(best_big, _cpu(fn, big, clock))
                if done >= min(MIN_ROUNDS, rounds) and _fits(
                    best_small, best_big, factor, floor, ceiling
                ):
                    return
            seen.append(f"attempt {attempt}: small {best_small:.4f} s, big {best_big:.4f} s")
    finally:
        if collecting:
            gc.enable()
    limit = f"big < max({factor:g} * small, {floor:g} s)"
    if ceiling is not None:
        limit += f" and big < {ceiling:g} s"
    raise AssertionError(
        f"not linear, {limit} failed {attempts} times in a row: " + "; ".join(seen)
    )


def _cpu(fn: Callable[[Any], object], arg: Any, clock: Callable[[], float]) -> float:
    start = clock()
    fn(arg)
    return clock() - start


def _fits(small: float, big: float, factor: float, floor: float, ceiling: float | None) -> bool:
    return big < max(factor * small, floor) and (ceiling is None or big < ceiling)


def timeout_notes(conversion) -> list[str]:
    """The notes of a ``Conversion`` that say pandoc ran out of time: it timed out, or the
    time budget shared by the pieces of a paper was spent before it could run. They are in the
    warnings of the paper and in those of each segment it replaced.

    This is not every timeout: a piece with no plain text, a paper whose pieces all time out and
    a reference list recovered after a timeout leave other notes or none. ``conversion.degraded``
    is the signal that covers them; use this to say which runs timed out, next to it and not instead."""
    notes = [*conversion.warnings, *(w for s in conversion.segments for w in s.warnings)]
    return [n for n in notes if "timed out" in n or "no time left" in n]
