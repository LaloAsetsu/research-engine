"""The fast engine against the naive builder on random circuits (1 to 6 wires, every gate,
random targets, closed and open controls, random angles)."""

import naive
import numpy as np
from circuits import random_circuits, random_states
from hypothesis import given, settings
from hypothesis import strategies as st

from quantum_sim import engine

RANDOM = settings(max_examples=300, deadline=None)


@RANDOM
@given(random_circuits())
def test_unitary(circuit):
    np.testing.assert_allclose(engine.unitary(circuit), naive.unitary(circuit), atol=1e-10)


@RANDOM
@given(st.data())
def test_statevector_on_a_random_input(data):
    circuit = data.draw(random_circuits())
    state = data.draw(random_states(circuit.n_wires))
    np.testing.assert_allclose(
        engine.statevector(circuit, state), naive.unitary(circuit) @ state, atol=1e-10
    )


@RANDOM
@given(st.data())
def test_statevector_on_a_basis_label(data):
    circuit = data.draw(random_circuits())
    label = data.draw(st.text("01", min_size=circuit.n_wires, max_size=circuit.n_wires))
    np.testing.assert_allclose(
        engine.statevector(circuit, label), naive.statevector(circuit, label), atol=1e-10
    )


@RANDOM
@given(st.data())
def test_probabilities_of_a_subset_of_wires(data):
    circuit = data.draw(random_circuits())
    n = circuit.n_wires
    wires = data.draw(st.permutations(range(1, n + 1)))[: data.draw(st.integers(1, n))]
    full = np.abs(naive.statevector(circuit)) ** 2
    expected = np.zeros(2 ** len(wires))
    for index, p in enumerate(full):
        bits = format(index, f"0{n}b")
        expected[int("".join(bits[w - 1] for w in wires), 2)] += p
    got = engine.probabilities(circuit, wires=wires)
    np.testing.assert_allclose(got, expected, atol=1e-10)
    assert abs(got.sum() - 1) < 1e-10


@settings(max_examples=100, deadline=None)
@given(random_circuits(max_wires=5))
def test_equal_finds_a_circuit_equal_to_its_own_unitary(circuit):
    assert engine.equal(circuit, naive.unitary(circuit)).equal
