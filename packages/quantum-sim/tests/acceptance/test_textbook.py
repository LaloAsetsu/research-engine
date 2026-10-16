"""Textbook circuits, end to end: the conventions together."""

import cmath
import math

import numpy as np
import pytest
from circuits import circuit, cx, cz, op
from naive import Z, basis, literal


def test_bell_state(sim):
    state = sim.statevector(circuit(2, op("h", 1), cx(1, 2)))
    np.testing.assert_allclose(state, np.array([1, 0, 0, 1]) / math.sqrt(2), atol=1e-12)


@pytest.mark.parametrize("label", ["00", "01", "10", "11"])
def test_the_four_bell_states(sim, label):
    state = sim.statevector(circuit(2, op("h", 1), cx(1, 2)), label)
    b0, b1 = int(label[0]), int(label[1])
    expected = (basis(f"0{b1}") + (-1) ** b0 * basis(f"1{1 - b1}")) / math.sqrt(2)
    np.testing.assert_allclose(state, expected, atol=1e-12)


@pytest.mark.parametrize("n", [3, 4, 5])
def test_ghz_state_by_a_chain_and_by_a_star_of_cnots(sim, n):
    expected = (basis("0" * n) + basis("1" * n)) / math.sqrt(2)
    chain = circuit(n, op("h", 1), *(cx(w, w + 1) for w in range(1, n)))
    star = circuit(n, op("h", 1), *(cx(1, w) for w in range(2, n + 1)))
    np.testing.assert_allclose(sim.statevector(chain), expected, atol=1e-12)
    np.testing.assert_allclose(sim.statevector(star), expected, atol=1e-12)


def test_swap_is_three_alternating_cnots(sim):
    three = sim.unitary(circuit(2, cx(1, 2), cx(2, 1), cx(1, 2)))
    np.testing.assert_allclose(three, literal("swap"), atol=1e-12)
    np.testing.assert_allclose(three, sim.unitary(circuit(2, op("swap", 1, 2))), atol=1e-12)


def test_cnot_is_hadamard_conjugated_cz(sim):
    composed = sim.unitary(circuit(2, op("h", 2), cz(1, 2), op("h", 2)))
    np.testing.assert_allclose(composed, sim.unitary(circuit(2, cx(1, 2))), atol=1e-12)


def toffoli_nielsen_chuang(a: int, b: int, c: int):
    """Nielsen and Chuang, Fig. 4.9: Toffoli from 6 CNOTs, 7 T or T^dagger and 2 H."""
    return [
        op("h", c),
        cx(b, c),
        op("tdg", c),
        cx(a, c),
        op("t", c),
        cx(b, c),
        op("tdg", c),
        cx(a, c),
        op("t", b),
        op("t", c),
        op("h", c),
        cx(a, b),
        op("t", a),
        op("tdg", b),
        cx(a, b),
    ]


def test_toffoli_decomposition_is_exact(sim):
    ops = toffoli_nielsen_chuang(1, 2, 3)
    assert sum(1 for o in ops if o.controls) == 6
    assert sum(1 for o in ops if o.gate in ("t", "tdg")) == 7
    decomposed = sim.unitary(circuit(3, *ops))
    np.testing.assert_allclose(
        decomposed, sim.unitary(circuit(3, op("x", 3, c=(1, 2)))), atol=1e-12
    )


def test_toffoli_decomposition_on_permuted_wires(sim):
    decomposed = sim.unitary(circuit(4, *toffoli_nielsen_chuang(4, 1, 3)))
    np.testing.assert_allclose(
        decomposed, sim.unitary(circuit(4, op("x", 3, c=(4, 1)))), atol=1e-12
    )


@pytest.mark.parametrize("gamma", [0.0, 0.37, -1.2, math.pi / 3])
def test_zz_evolution_compiles_to_cnot_rz_cnot(sim, gamma):
    compiled = sim.unitary(circuit(2, cx(1, 2), op("rz", 2, p=(2 * gamma,)), cx(1, 2)))
    zz = np.kron(Z, Z)
    exact = math.cos(gamma) * np.eye(4) - 1j * math.sin(gamma) * zz  # exp(-i gamma Z⊗Z)
    np.testing.assert_allclose(compiled, exact, atol=1e-12)
    np.testing.assert_allclose(
        compiled, sim.unitary(circuit(2, op("rzz", 1, 2, p=(2 * gamma,)))), atol=1e-12
    )


def qft3(with_swap: bool):
    ops = [
        op("h", 1),
        op("rk", 1, c=(2,), p=(2,)),
        op("rk", 1, c=(3,), p=(3,)),
        op("h", 2),
        op("rk", 2, c=(3,), p=(2,)),
        op("h", 3),
    ]
    return circuit(3, *ops, *([op("swap", 1, 3)] if with_swap else []))


def dft(n: int) -> np.ndarray:
    size = 2**n
    omega = cmath.exp(2j * math.pi / size)
    return np.array([[omega ** (j * k) for k in range(size)] for j in range(size)]) / math.sqrt(
        size
    )


def bit_reversal(n: int) -> np.ndarray:
    size = 2**n
    perm = np.zeros((size, size))
    for k in range(size):
        perm[int(format(k, f"0{n}b")[::-1], 2), k] = 1
    return perm


def test_qft_on_3_wires_is_the_dft_with_the_final_swap(sim):
    np.testing.assert_allclose(sim.unitary(qft3(with_swap=True)), dft(3), atol=1e-12)


def test_qft_on_3_wires_without_the_swap_is_the_bit_reversed_dft(sim):
    np.testing.assert_allclose(
        sim.unitary(qft3(with_swap=False)), bit_reversal(3) @ dft(3), atol=1e-12
    )


def test_phase_kickback_marks_the_control(sim):
    # |+>|-> through a CNOT becomes |->|->.
    c = circuit(2, op("h", 1), op("x", 2), op("h", 2), cx(1, 2), op("h", 1))
    state = sim.statevector(c)
    np.testing.assert_allclose(np.abs(state) ** 2, [0, 0, 0.5, 0.5], atol=1e-12)
