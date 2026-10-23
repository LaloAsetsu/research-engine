"""The two bootstrap engines against the scripts they stand for."""

import random

import pytest
from test_npcompat import DIFFS60

from qa_bench.formats import load_schema
from qa_bench.score.bootstrap import ENGINES, NumpyPcg64, PythonRandom


def reference_python_boot(diffs):
    """The comparison script of the mathcirc results, as it was written."""
    rng = random.Random(1)
    n = len(diffs)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(2000))
    return round(means[int(0.025 * 2000)], 4), round(means[int(0.975 * 2000) - 1], 4)


@pytest.mark.parametrize("n", [1, 2, 5, 30, 101])
@pytest.mark.parametrize("kind", ["int", "float"])
def test_python_random_engine_equals_the_reference_script(n, kind):
    rnd = random.Random(11)
    diffs = [rnd.choice([-1, 0, 1]) if kind == "int" else rnd.random() - 0.4 for _ in range(n)]
    assert PythonRandom().ci(diffs) == reference_python_boot(diffs)


def test_the_engine_names_are_the_ones_the_score_schema_allows():
    schema = load_schema("qa-score/1")
    allowed = schema["$defs"]["header"]["properties"]["bootstrap"]["properties"]["engine"]["enum"]
    assert sorted(ENGINES) == sorted(allowed)
    assert all(ENGINES[name].name == name for name in ENGINES)


def test_means_of_the_python_engine_are_exact_means():
    engine = PythonRandom()
    assert engine.mean([0.1, 0.2, 0.3]) == pytest.approx(0.2)
    assert engine.diff_mean([1, 0, 0, 0]) == 0.25


def test_the_same_values_in_another_order_can_give_another_interval():
    """Why the scorer fixes the order of the questions: the resamples index into the list."""
    rnd = random.Random(5)
    values = [rnd.random() for _ in range(40)]
    forward = PythonRandom().ci(values)
    shuffled = values[:]
    rnd.shuffle(shuffled)
    assert PythonRandom().ci(shuffled) != forward


def test_one_value_has_a_zero_width_interval_in_both_engines():
    assert PythonRandom().ci([0.5]) == (0.5, 0.5)
    assert NumpyPcg64().ci([0.5]) == (0.5, 0.5)


def test_a_repeated_call_with_equal_values_gives_the_same_interval():
    engine = PythonRandom()
    assert engine.ci([1.0, 0.0, 0.0]) == engine.ci([1.0, 0.0, 0.0])
    other = NumpyPcg64()
    assert other.ci([1.0, 0.0, 0.0]) == other.ci([1.0, 0.0, 0.0])


def test_numpy_engine_gives_the_interval_numpy_gives():
    """Values taken from numpy 2.5.3: default_rng(1) resamples of 60 differences."""
    engine = NumpyPcg64()
    assert engine.ci([float(x) for x in DIFFS60]) == (-0.2667, 0.05)
    assert engine.diff_mean(DIFFS60) == -0.1
    assert engine.mean([1.0, 0.0, 0.0, 0.0]) == 0.25


@pytest.mark.parametrize("n", [2, 5, 30, 150])
def test_numpy_engine_equals_numpy(n):
    np = pytest.importorskip("numpy")
    rnd = random.Random(n)
    diffs = [rnd.random() - 0.4 for _ in range(n)]
    means = np.array(diffs)[np.random.default_rng(1).integers(0, n, size=(2000, n))].mean(axis=1)
    low, high = np.percentile(means, [2.5, 97.5])
    assert NumpyPcg64().ci(diffs) == (round(float(low), 4), round(float(high), 4))


def test_the_two_engines_are_different_generators():
    rnd = random.Random(2)
    diffs = [rnd.random() - 0.4 for _ in range(40)]
    assert PythonRandom().ci(diffs) != NumpyPcg64().ci(diffs)
