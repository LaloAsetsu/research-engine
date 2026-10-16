"""Short constructors for test circuits and a hypothesis strategy for random ones."""

from __future__ import annotations

import math

import numpy as np
from hypothesis import strategies as st
from naive import FIXED, PARAMETRIC, TWO_WIRE

from quantum_sim import Circuit, Op

GATE_NAMES = sorted([*FIXED, *PARAMETRIC])


def op(gate: str, *wires: int, c=(), o=(), p=()) -> Op:
    return Op(gate, wires, controls=tuple(c), open_controls=tuple(o), params=tuple(p))


def cx(control: int, target: int) -> Op:
    return op("x", target, c=(control,))


def cz(control: int, target: int) -> Op:
    return op("z", target, c=(control,))


def circuit(n: int, *ops: Op) -> Circuit:
    return Circuit(n, ops)


angles = st.floats(min_value=-2 * math.pi, max_value=2 * math.pi, allow_nan=False)


@st.composite
def random_ops(draw, n: int, max_ops: int = 12) -> list[Op]:
    """Ops on ``n`` wires: every gate, random targets, closed and open controls, angles."""
    ops = []
    for _ in range(draw(st.integers(0, max_ops))):
        gate = draw(st.sampled_from([g for g in GATE_NAMES if n >= 2 or g not in TWO_WIRE]))
        k = 2 if gate in TWO_WIRE else 1
        wires = draw(st.permutations(range(1, n + 1)))
        targets, free = wires[:k], wires[k:]
        n_controls = draw(st.integers(0, len(free)))
        n_closed = draw(st.integers(0, n_controls))
        params = ()
        if gate == "rk":
            params = (draw(st.integers(1, 6)),)
        elif gate in PARAMETRIC:
            params = (draw(angles),)
        ops.append(
            op(
                gate,
                *targets,
                c=free[:n_closed],
                o=free[n_closed:n_controls],
                p=params,
            )
        )
    return ops


@st.composite
def random_circuits(draw, min_wires: int = 1, max_wires: int = 6, max_ops: int = 12) -> Circuit:
    n = draw(st.integers(min_wires, max_wires))
    return Circuit(n, tuple(draw(random_ops(n, max_ops))))


@st.composite
def random_states(draw, n: int):
    """A normalized state on ``n`` wires (seeded by hypothesis)."""
    seed = draw(st.integers(0, 2**32 - 1))
    rng = np.random.default_rng(seed)
    v = rng.normal(size=2**n) + 1j * rng.normal(size=2**n)
    return v / np.linalg.norm(v)
