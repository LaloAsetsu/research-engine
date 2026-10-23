"""Pure-Python copies of the two numpy routines that the numpy bootstrap engine depends on.

An earlier end-to-end scorer drew its bootstrap resamples with
``numpy.random.default_rng(1).integers(0, n, size=(2000, n))`` and read the interval with
``numpy.percentile(means, [2.5, 97.5])``. To give the same numbers without numpy (the scoring
package imports the standard library only), this module re-implements, bit for bit:

* ``SeedSequence(entropy)`` into ``PCG64`` (XSL-RR 128/64) into buffered 32-bit draws into
  Lemire's bounded integers (numpy's ``bit_generator.pyx``, ``pcg64.c`` and ``distributions.c``);
* the ``linear`` percentile and numpy's pairwise float64 summation (``mean`` of a vector).

numpy gives its ``Generator`` no stream guarantee across versions, so what this module matches is
the stream of numpy 2.x as checked against 2.5.3: ``tests/test_npcompat.py`` pins fixed values
taken from numpy and, when numpy is installed, compares many more.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

M32 = 0xFFFFFFFF
M64 = (1 << 64) - 1
M128 = (1 << 128) - 1
INIT_A, MULT_A = 0x43B0D7E5, 0x931E8875
INIT_B, MULT_B = 0x8B51F9DD, 0x58F38DED
MIX_MULT_L, MIX_MULT_R = 0xCA01F9DD, 0x4973F715
XSHIFT = 16
POOL = 4
PCG_MULT = (0x2360ED051FC65DA4 << 64) | 0x4385DF649FCCF645
LOOP_MAX = 128  # numpy sums blocks of up to this many elements with eight accumulators


def _hashmix(value: int, hash_const: list[int]) -> int:
    value ^= hash_const[0]
    hash_const[0] = (hash_const[0] * MULT_A) & M32
    value = (value * hash_const[0]) & M32
    return value ^ (value >> XSHIFT)


def _mix(x: int, y: int) -> int:
    r = (MIX_MULT_L * x - MIX_MULT_R * y) & M32
    return r ^ (r >> XSHIFT)


def _entropy_words(entropy: int) -> list[int]:
    if entropy == 0:
        return [0]
    words: list[int] = []
    while entropy:
        words.append(entropy & M32)
        entropy >>= 32
    return words


def seed_sequence_state(entropy: int, n_words64: int) -> list[int]:
    """``SeedSequence(entropy).generate_state(n_words64, uint64)`` as Python ints."""
    ent = _entropy_words(entropy)
    pool = [0] * POOL
    hash_const = [INIT_A]
    for i in range(POOL):
        pool[i] = _hashmix(ent[i] if i < len(ent) else 0, hash_const)
    for i_src in range(POOL):
        for i_dst in range(POOL):
            if i_src != i_dst:
                pool[i_dst] = _mix(pool[i_dst], _hashmix(pool[i_src], hash_const))
    for i_src in range(POOL, len(ent)):
        for i_dst in range(POOL):
            pool[i_dst] = _mix(pool[i_dst], _hashmix(ent[i_src], hash_const))
    hash_b = INIT_B
    words32: list[int] = []
    for i in range(n_words64 * 2):
        v = pool[i % POOL] ^ hash_b
        hash_b = (hash_b * MULT_B) & M32
        v = (v * hash_b) & M32
        words32.append(v ^ (v >> XSHIFT))
    return [words32[2 * i] | (words32[2 * i + 1] << 32) for i in range(n_words64)]


class PCG64:
    """``numpy.random.PCG64(seed)`` with the 32-bit buffering of ``Generator.integers``."""

    def __init__(self, seed: int) -> None:
        v = seed_sequence_state(seed, 4)
        initstate = (v[0] << 64) | v[1]
        initseq = (v[2] << 64) | v[3]
        self.inc = ((initseq << 1) | 1) & M128
        self.state = 0
        self._step()
        self.state = (self.state + initstate) & M128
        self._step()
        self.has32 = False
        self.buf32 = 0

    def _step(self) -> None:
        self.state = (self.state * PCG_MULT + self.inc) & M128

    def next64(self) -> int:
        self._step()
        s = self.state
        x = (s >> 64) ^ (s & M64)
        rot = s >> 122
        return ((x >> rot) | (x << ((-rot) & 63))) & M64

    def next32(self) -> int:
        if self.has32:
            self.has32 = False
            return self.buf32
        n = self.next64()
        self.has32 = True
        self.buf32 = n >> 32
        return n & M32

    def bounded(self, rng: int) -> int:
        """One integer in ``[0, rng]`` by Lemire's method (``0 < rng < 2**32 - 1``)."""
        excl = rng + 1
        m = self.next32() * excl
        left = m & M32
        if left < excl:
            threshold = (M32 - rng) % excl
            while left < threshold:
                m = self.next32() * excl
                left = m & M32
        return m >> 32


def integers_matrix(seed: int, high: int, rows: int, cols: int) -> list[list[int]]:
    """``default_rng(seed).integers(0, high, size=(rows, cols))`` as a list of rows."""
    rng = high - 1
    if rng <= 0 or rng >= M32:
        raise ValueError("npcompat covers 2 <= high < 2**32 only")
    g = PCG64(seed)
    return [[g.bounded(rng) for _ in range(cols)] for _ in range(rows)]


def percentile_linear(values: Sequence[float], q: float) -> float:
    """``numpy.percentile(values, q)`` with the default ``linear`` method; ``q`` in 0..100."""
    v = sorted(values)
    n = len(v)
    virtual = (n - 1) * (q / 100)
    prev = math.floor(virtual)
    nxt = min(prev + 1, n - 1)
    gamma = virtual - prev
    a, b = v[prev], v[nxt]
    diff = b - a
    lerp = a + diff * gamma
    if gamma >= 0.5:
        lerp = b - diff * (1 - gamma)
    return lerp


def pairwise_sum(a: Sequence[float], lo: int = 0, n: int | None = None) -> float:
    """numpy's float64 pairwise summation of ``a[lo:lo + n]`` (a contiguous reduction)."""
    if n is None:
        n = len(a) - lo
    if n < 8:
        res = 0.0
        for i in range(lo, lo + n):
            res += a[i]
        return res
    if n <= LOOP_MAX:
        r = [a[lo + j] for j in range(8)]
        i = 8
        end = n - (n % 8)
        while i < end:
            for j in range(8):
                r[j] += a[lo + i + j]
            i += 8
        res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]))
        while i < n:
            res += a[lo + i]
            i += 1
        return res
    n2 = n // 2
    n2 -= n2 % 8
    return pairwise_sum(a, lo, n2) + pairwise_sum(a, lo + n2, n - n2)


def mean_numpy(values: Sequence[float]) -> float:
    """``numpy.mean`` of a float64 vector: the pairwise sum divided by the length."""
    return pairwise_sum(values) / len(values)
