"""The pure-Python numpy routines against values taken from numpy 2.5.3, and against numpy itself
when it is installed (it is not a dependency of qa-bench)."""

import hashlib
import random
from array import array

import pytest

from qa_bench.score import npcompat

# SeedSequence(seed).generate_state(4, uint64), from numpy 2.5.3
SEED_STATES = {
    0: [15793235383387715774, 12390638538380655177, 2361836109651742017, 3188717715514472916],
    1: [7434755675892716031, 10007452063617845036, 5266248848659119178, 15045448681910192573],
    12345: [13091511679009522556, 13538552136045918767, 7269824232120749972, 4520223790601600371],
    2**32: [5836529245451711556, 8811601478698894690, 4323349454413245209, 13668837544996263139],
    2**130 + 5: [
        8786577384290153012,
        8783918975595767503,
        16280013672054783631,
        3934764579093188190,
    ],
    2**70 + 3: [
        4943007236101998820,
        12091938953640079810,
        11493795787252505438,
        8139457820393360652,
    ],
}
# default_rng(seed).integers(0, high, size=(rows, cols)): sha256 of the int64 bytes, first six values
INTEGERS = [
    ((1, 101, 50, 101), "1deb82aa873bf6916eb1420a44ff771f7fafc355c64906002b93d819a0c9394e", [47, 51, 76, 95, 3, 14]),
    ((1, 2, 20, 50), "8e65cdb72c49c8f68f22bd7832b8b7111f2c56371e40b0805915937a122e0a38", [0, 1, 1, 1, 0, 0]),
    ((1, 336, 10, 336), "41499370d793cceebd60100be7d695687aed927db636d69403832d16efde1e05", [158, 171, 253, 319, 11, 48]),
    ((5, 1000, 10, 100), "a52716b6bccb728eb0b4e250d4303aea2ff46cb92f18814260ec755d29acfba1", [670, 805, 22, 807, 468, 515]),
    ((1, 2**31 + 17, 3, 50), "da56e5646d1842a4d00af26e8ec0314f22441562f66967f85086129f8773269c", [1016164999, 1621709887, 2041105261, 1767258102, 535214424, 1866217522]),
    ((9, 2**32 - 2, 3, 50), "b32b1b3c1c530039eddc9e92001009ca9da2008020060ec7a964558b81ca81d6", [1810532128, 3737691868, 4127654942, 1231870531, 491168701, 2590501577]),
]  # fmt: skip
VALUES9 = [0.15444662376869212, 0.7161198827881962, 0.6602565151913709, 0.14297899792423718, 0.8828328336570754, 0.9675447826663839, 0.21958783080191968, 0.9525041289189863, 0.3982568747172719]  # fmt: skip
DIFFS60 = [0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, -1, 0, -1, 0, 1, 0, 0, -1, 0, 0, 1, 1, 0, 1, -1, -1, 0, -1, -1, 0, 0, -1, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 0, 0, -1, 0, -1, 0, 0, -1, 0, -1, -1, 0, -1, 1, 0, -1]  # fmt: skip


@pytest.mark.parametrize("seed", SEED_STATES)
def test_the_seed_sequence_state_is_numpys(seed):
    assert npcompat.seed_sequence_state(seed, 4) == SEED_STATES[seed]


@pytest.mark.parametrize(("shape", "digest", "head"), INTEGERS)
def test_the_integer_matrix_is_numpys(shape, digest, head):
    rows = npcompat.integers_matrix(*shape)
    flat = [x for row in rows for x in row]
    assert flat[:6] == head
    assert hashlib.sha256(array("q", flat).tobytes()).hexdigest() == digest


def test_the_range_the_algorithm_covers_is_checked():
    for high in (1, 2**32):
        with pytest.raises(ValueError, match="2 <= high"):
            npcompat.integers_matrix(1, high, 1, 1)


def test_mean_and_percentile_of_a_vector_of_300_are_numpys():
    rnd = random.Random(7)
    values = [rnd.random() - 0.4 for _ in range(300)]
    assert npcompat.mean_numpy(values) == 0.08741762296096789
    assert npcompat.percentile_linear(values, 2.5) == -0.3737668510747435
    assert npcompat.percentile_linear(values, 97.5) == 0.5740711427028081


def test_mean_and_percentile_of_a_short_vector_are_numpys():
    assert npcompat.mean_numpy(VALUES9) == 0.566058718937126
    assert npcompat.percentile_linear(VALUES9, 33.3) == 0.3382240759617135


def test_the_percentile_of_resample_means_of_a_difference_vector_is_numpys():
    rows = npcompat.integers_matrix(1, 60, 2000, 60)
    means = [npcompat.mean_numpy([float(DIFFS60[j]) for j in row]) for row in rows]
    assert npcompat.percentile_linear(means, 2.5) == -0.26666666666666666
    assert npcompat.percentile_linear(means, 97.5) == 0.05
    assert npcompat.mean_numpy([float(x) for x in DIFFS60]) == -0.1


def test_a_sum_of_a_short_run_is_added_in_order_and_a_long_one_in_halves():
    assert npcompat.pairwise_sum([0.1, 0.2, 0.3]) == (0.1 + 0.2) + 0.3
    values = [0.1 * i for i in range(400)]
    assert npcompat.pairwise_sum(values) == pytest.approx(sum(values), rel=1e-12)


def test_the_percentile_of_one_value_is_that_value():
    assert npcompat.percentile_linear([0.25], 97.5) == 0.25


def test_against_numpy_on_many_seeds_sizes_and_arrays():
    np = pytest.importorskip("numpy")
    for seed in (0, 1, 2, 7, 12345, 2**32 - 1, 2**40 + 5):
        theirs = [int(x) for x in np.random.SeedSequence(seed).generate_state(4, np.uint64)]
        assert npcompat.seed_sequence_state(seed, 4) == theirs
    for seed, high, rows, cols in [
        (1, 40, 30, 40),
        (1, 3, 20, 7),
        (123, 12, 40, 12),
        (1, 2**20, 5, 100),
    ]:
        ours = npcompat.integers_matrix(seed, high, rows, cols)
        assert ours == np.random.default_rng(seed).integers(0, high, size=(rows, cols)).tolist()
    rnd = random.Random(7)
    for n in (2, 3, 7, 100, 336, 2001):
        vals = [rnd.choice([rnd.random(), rnd.randint(-5, 5) / 7, 0.0, 1.0]) for _ in range(n)]
        for q in (2.5, 97.5, 50, 0, 100, 33.3):
            assert npcompat.percentile_linear(vals, q) == float(np.percentile(np.array(vals), q))
    for n in (1, 5, 8, 9, 100, 127, 128, 129, 300, 1000):
        vals = [rnd.random() * rnd.choice([1, 1e-3, 1e3]) - 0.4 for _ in range(n)]
        assert npcompat.mean_numpy(vals) == float(np.array(vals).mean())
