"""Limits are checked from the description, before any array exists (memory via tracemalloc)."""

import tracemalloc

import numpy as np
import pytest
from circuits import circuit, op

from quantum_sim import (
    MAX_OPS,
    Circuit,
    LimitExceededError,
    engine,
)

pytestmark = pytest.mark.engine_pending

MiB = 2**20
# A refusal must stay far below one statevector of 20 wires (16 MiB).
BUDGET = 4 * MiB


def peak_while(call) -> tuple[int, str]:
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        with pytest.raises(LimitExceededError) as error:
            call()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak, str(error.value)


def test_statevector_of_21_wires_is_refused_before_allocation():
    peak, message = peak_while(lambda: engine.statevector(circuit(21, op("h", 1))))
    assert peak < BUDGET
    assert "21" in message and "20" in message


def test_probabilities_of_21_wires_are_refused_before_allocation():
    peak, _ = peak_while(lambda: engine.probabilities(circuit(21, op("h", 1))))
    assert peak < BUDGET


def test_unitary_of_11_wires_is_refused_before_allocation():
    peak, message = peak_while(lambda: engine.unitary(circuit(11, op("h", 1))))
    assert peak < BUDGET
    assert "11" in message and "10" in message


def test_equal_of_21_wire_circuits_is_refused_before_allocation():
    a = circuit(21, op("h", 1))
    peak, _ = peak_while(lambda: engine.equal(a, a))
    assert peak < BUDGET


def test_more_than_10000_ops_are_refused_before_allocation():
    many = Circuit(20, tuple(op("h", 1 + k % 20) for k in range(MAX_OPS + 1)))
    peak, message = peak_while(lambda: engine.statevector(many))
    assert peak < BUDGET
    assert "10000" in message.replace(",", "").replace("_", "")
    with pytest.raises(LimitExceededError):
        engine.unitary(Circuit(1, many.ops[: MAX_OPS + 1]))


def test_exactly_10000_ops_are_accepted():
    ops = tuple(op("x", 1) for _ in range(MAX_OPS))  # an even number of X: the identity
    np.testing.assert_allclose(engine.statevector(Circuit(1, ops)), [1, 0], atol=1e-9)


def test_statevector_of_20_wires_is_accepted():
    state = engine.statevector(circuit(20, op("x", 1), op("h", 20)))
    assert state.shape == (2**20,)
    assert state.dtype == np.complex128
    assert abs(state[2**19] - 2**-0.5) < 1e-12
    assert abs(state[2**19 + 1] - 2**-0.5) < 1e-12


def test_unitary_of_10_wires_is_accepted():
    u = engine.unitary(circuit(10, op("x", 10)))
    assert u.shape == (2**10, 2**10)
    assert u[1, 0] == 1
