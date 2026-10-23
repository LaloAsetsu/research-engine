"""The paired bootstrap, with the two random generators the earlier result files were made with.

Both engines resample the questions (cluster ``id``) 2,000 times with seed 1 and read the 95%
interval from the sorted resample means. They differ in the generator and the arithmetic, so they
give different last digits:

``python_random``
    ``random.Random(1)`` restarted for every call, the means of ``statistics.mean`` for a group and
    the sorted resample means at positions 50 and 1949 (the mathcirc comparisons).
``numpy_pcg64``
    ``default_rng(1).integers(0, n, size=(2000, n))``, row means with numpy's pairwise sum and
    ``numpy.percentile`` (the end-to-end and closed-book comparisons), written in pure Python in
    :mod:`qa_bench.score.npcompat`.

The order of the values is the order of the question-set file, never the order of a run file: the
resamples index into the list, so another order moves the last digit.
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Sequence
from typing import Protocol

from qa_bench.score import npcompat

RESAMPLES = 2000
SEED = 1
LEVEL = 0.95
DIGITS = 4
# Written out, not computed from LEVEL: 0.05 / 2 * 100 is 2.5000000000000004 in floating point.
LOW_INDEX, HIGH_INDEX = 50, 1949  # of the sorted means: int(0.025 * 2000), int(0.975 * 2000) - 1
LOW_PERCENT, HIGH_PERCENT = 2.5, 97.5


class Engine(Protocol):
    """What the scorer needs from a bootstrap engine."""

    name: str
    description: str

    def mean(self, values: Sequence[float]) -> float: ...

    def diff_mean(self, diffs: Sequence[float]) -> float: ...

    def ci(self, diffs: Sequence[float]) -> tuple[float, float]: ...


class PythonRandom:
    """``random.Random(1)`` and sorted means, as the mathcirc comparison script did."""

    name = "python_random"
    description = (
        "random.Random(1) restarted per group and metric, 2,000 resamples of the questions, "
        "95% interval = sorted resample means [50] and [1949], means by statistics.mean"
    )

    def __init__(self) -> None:
        self._indexes: dict[int, list[list[int]]] = {}
        self._cache: dict[tuple[float, ...], tuple[float, float]] = {}

    def _rows(self, n: int) -> list[list[int]]:
        if n not in self._indexes:
            rng = random.Random(SEED)
            self._indexes[n] = [[rng.randrange(n) for _ in range(n)] for _ in range(RESAMPLES)]
        return self._indexes[n]

    def mean(self, values: Sequence[float]) -> float:
        return statistics.mean(values)

    def diff_mean(self, diffs: Sequence[float]) -> float:
        return statistics.mean(diffs)

    def ci(self, diffs: Sequence[float]) -> tuple[float, float]:
        key = tuple(float(x) for x in diffs)
        if key not in self._cache:
            n = len(key)
            get = key.__getitem__
            means = sorted(sum(map(get, row)) / n for row in self._rows(n))
            lo, hi = means[LOW_INDEX], means[HIGH_INDEX]
            self._cache[key] = (round(lo, DIGITS), round(hi, DIGITS))
        return self._cache[key]


class NumpyPcg64:
    """numpy's ``default_rng(1)`` stream and ``percentile``, without numpy."""

    name = "numpy_pcg64"
    description = (
        "numpy default_rng(1).integers(0, n, size=(2000, n)) and numpy.percentile(means, "
        "[2.5, 97.5]) re-implemented in pure Python bit for bit (qa_bench.score.npcompat); "
        "arm means sum/len, difference means by numpy's pairwise sum"
    )

    def __init__(self) -> None:
        self._indexes: dict[int, list[list[int]]] = {}
        self._cache: dict[tuple[float, ...], tuple[float, float]] = {}

    def _rows(self, n: int) -> list[list[int]]:
        if n not in self._indexes:
            self._indexes[n] = npcompat.integers_matrix(SEED, n, RESAMPLES, n)
        return self._indexes[n]

    def mean(self, values: Sequence[float]) -> float:
        return sum(values) / len(values)

    def diff_mean(self, diffs: Sequence[float]) -> float:
        return npcompat.mean_numpy([float(x) for x in diffs])

    def ci(self, diffs: Sequence[float]) -> tuple[float, float]:
        key = tuple(float(x) for x in diffs)
        if key not in self._cache:
            n = len(key)
            if n == 1:
                self._cache[key] = (round(key[0], DIGITS), round(key[0], DIGITS))
            else:
                means = [npcompat.mean_numpy([key[j] for j in row]) for row in self._rows(n)]
                lo = npcompat.percentile_linear(means, LOW_PERCENT)
                hi = npcompat.percentile_linear(means, HIGH_PERCENT)
                self._cache[key] = (round(lo, DIGITS), round(hi, DIGITS))
        return self._cache[key]


ENGINES: dict[str, type[PythonRandom] | type[NumpyPcg64]] = {
    PythonRandom.name: PythonRandom,
    NumpyPcg64.name: NumpyPcg64,
}
