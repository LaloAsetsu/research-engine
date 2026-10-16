"""Equality up to global phase, for arrays and for circuits up to 20 wires."""

import cmath
import math

import numpy as np
import pytest
from circuits import circuit, cx, cz, op

from quantum_sim import Equality, InvalidCircuitError, engine

THETA = 0.8


def test_arrays_equal_up_to_a_global_phase():
    a = np.array([1, 1j, 0, -1]) / math.sqrt(3)
    result = engine.equal(cmath.exp(0.7j) * a, a)
    assert isinstance(result, Equality)
    assert result
    assert result.equal and not result.exact
    assert abs(result.phase - 0.7) < 1e-12
    assert result.max_deviation < 1e-12
    assert result.method == "arrays"


def test_identical_arrays_are_exactly_equal_with_phase_0():
    u = np.eye(4)
    result = engine.equal(u, u)
    assert result.equal and result.exact
    assert abs(result.phase) < 1e-12


def test_the_phase_is_taken_from_the_largest_entry_and_lies_in_minus_pi_to_pi():
    b = np.array([0.1, 0.995])
    b = b / np.linalg.norm(b)
    result = engine.equal(-b, b)
    assert result.equal
    assert abs(abs(result.phase) - math.pi) < 1e-12
    assert -math.pi < result.phase <= math.pi


def test_different_arrays_are_not_equal():
    result = engine.equal(np.array([1, 0]), np.array([0, 1]))
    assert not result
    assert not result.exact
    assert result.max_deviation > 0.5


def test_a_relative_phase_is_not_a_global_phase():
    a = np.array([1, 1]) / math.sqrt(2)
    b = np.array([1, -1]) / math.sqrt(2)
    assert not engine.equal(a, b)


def test_arrays_of_different_shapes_raise():
    with pytest.raises(ValueError, match="shape"):
        engine.equal(np.eye(2), np.eye(4))


def test_the_tolerance_is_1e_9_by_default_and_can_be_set():
    a = np.array([1.0, 0.0])
    b = np.array([1.0, 5e-9])
    assert not engine.equal(a, b)
    assert engine.equal(a, b, atol=1e-8)


def test_circuits_up_to_10_wires_compare_unitaries():
    rz = circuit(3, op("rz", 2, p=(THETA,)))
    p = circuit(3, op("p", 2, p=(THETA,)))
    result = engine.equal(rz, p)
    assert result.equal and not result.exact
    assert result.method == "unitary"
    assert abs(result.phase + THETA / 2) < 1e-12


def test_controlled_rz_and_controlled_p_circuits_are_not_equal():
    crz = circuit(2, op("rz", 2, c=(1,), p=(THETA,)))
    cp = circuit(2, op("p", 2, c=(1,), p=(THETA,)))
    assert not engine.equal(crz, cp)


def test_a_circuit_against_a_target_matrix():
    cnot = np.eye(4)[[0, 1, 3, 2]]
    assert engine.equal(circuit(2, op("h", 2), cz(1, 2), op("h", 2)), cnot).method == "unitary"
    assert engine.equal(circuit(2, op("h", 2), cz(1, 2), op("h", 2)), cnot)
    assert not engine.equal(circuit(2, cx(2, 1)), cnot)


def test_circuits_on_different_numbers_of_wires_raise():
    with pytest.raises(InvalidCircuitError):
        engine.equal(circuit(2, op("h", 1)), circuit(3, op("h", 1)))


def test_a_circuit_against_a_state_vector_is_a_type_error():
    with pytest.raises(TypeError):
        engine.equal(circuit(1, op("h", 1)), np.array([1, 0]))


def swap_by_cnots(n: int, a: int, b: int):
    return circuit(n, cx(a, b), cx(b, a), cx(a, b))


@pytest.mark.parametrize("n", [11, 15, 20])
def test_circuits_of_11_to_20_wires_compare_random_states(n):
    result = engine.equal(swap_by_cnots(n, 1, n), circuit(n, op("swap", 1, n)))
    assert result.equal and result.exact
    assert result.method == "random-states"


def test_random_states_find_a_difference_on_one_basis_state_only():
    n = 14
    ccz = circuit(n, op("z", n, c=tuple(range(1, n))))  # differs from I on |1...1> only
    result = engine.equal(ccz, circuit(n))
    assert not result
    assert result.method == "random-states"


def test_random_states_use_one_global_phase_for_every_state():
    n = 12
    rz = circuit(n, op("rz", 5, p=(THETA,)))
    p = circuit(n, op("p", 5, p=(THETA,)))
    result = engine.equal(rz, p)
    assert result.equal and not result.exact
    assert abs(result.phase + THETA / 2) < 1e-9
    crz = circuit(n, op("rz", 5, c=(1,), p=(THETA,)))
    cp = circuit(n, op("p", 5, c=(1,), p=(THETA,)))
    assert not engine.equal(crz, cp)


def test_random_state_equality_is_reproducible_by_seed():
    n = 11
    a, b = circuit(n, op("t", 3)), circuit(n, op("rz", 3, p=(math.pi / 4,)))
    first, second = engine.equal(a, b, seed=7), engine.equal(a, b, seed=7)
    assert first == second
