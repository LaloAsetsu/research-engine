"""Controlled gates: the gate acts only when its controls hold, and controls add to each other.

Guards the two silent bugs of the first proposal (a controlled gate other than X acting
unconditionally, and an extra control replacing the gate's own).
"""

import cmath
import itertools
import math

import numpy as np
import pytest
from circuits import circuit, cx, cz, op
from naive import H, basis, diag, literal

from quantum_sim import Circuit

THETA = 0.9


def controlled(gate: np.ndarray) -> np.ndarray:
    """|0><0| ⊗ I + |1><1| ⊗ G: control on the first (most significant) wire."""
    out = np.eye(2 * len(gate), dtype=complex)
    out[len(gate) :, len(gate) :] = gate
    return out


def control_below(gate: np.ndarray) -> np.ndarray:
    """The same controlled gate with the control on wire 2 and the target on wire 1."""
    swap = literal("swap")
    return swap @ controlled(gate) @ swap


CASES = [
    ("z", (), diag(1, -1)),
    ("s", (), diag(1, 1j)),
    ("h", (), H),
    ("ry", (THETA,), literal("ry", (THETA,))),
    ("p", (THETA,), diag(1, cmath.exp(1j * THETA))),
    ("rz", (THETA,), diag(cmath.exp(-1j * THETA / 2), cmath.exp(1j * THETA / 2))),
    ("sx", (), literal("sx")),
    ("rk", (3,), literal("t")),
]


@pytest.mark.parametrize(("name", "params", "gate"), CASES, ids=[c[0] for c in CASES])
def test_controlled_gate_on_all_four_basis_inputs(sim, name, params, gate):
    u = sim.unitary(circuit(2, op(name, 2, c=(1,), p=params)))
    np.testing.assert_allclose(u, controlled(gate), atol=1e-12)
    for label in ("00", "01"):
        np.testing.assert_allclose(u @ basis(label), basis(label), atol=1e-12)
    below = sim.unitary(circuit(2, op(name, 1, c=(2,), p=params)))
    np.testing.assert_allclose(below, control_below(gate), atol=1e-12)


def test_controlled_rz_is_not_controlled_p(sim):
    crz = sim.unitary(circuit(2, op("rz", 2, c=(1,), p=(THETA,))))
    cp = sim.unitary(circuit(2, op("p", 2, c=(1,), p=(THETA,))))
    assert np.max(np.abs(crz - cp)) > 0.1
    # Not even up to a global phase: the |00> entry fixes the phase to 1.
    assert abs(crz[0, 0] - cp[0, 0]) < 1e-12
    assert abs(crz[3, 3] - cp[3, 3]) > 0.1


def test_uncontrolled_rz_and_p_differ_by_a_global_phase_only(sim):
    rz = sim.unitary(circuit(1, op("rz", 1, p=(THETA,))))
    p = sim.unitary(circuit(1, op("p", 1, p=(THETA,))))
    np.testing.assert_allclose(rz, cmath.exp(-1j * THETA / 2) * p, atol=1e-12)


def test_cz_is_symmetric(sim):
    np.testing.assert_allclose(
        sim.unitary(circuit(2, cz(1, 2))), sim.unitary(circuit(2, cz(2, 1))), atol=1e-12
    )
    np.testing.assert_allclose(sim.unitary(circuit(2, cz(1, 2))), diag(1, 1, 1, -1), atol=1e-12)


def test_open_control_acts_on_0(sim):
    u = sim.unitary(circuit(2, op("x", 2, o=(1,))))
    np.testing.assert_allclose(u @ basis("00"), basis("01"), atol=1e-12)
    np.testing.assert_allclose(u @ basis("01"), basis("00"), atol=1e-12)
    np.testing.assert_allclose(u @ basis("10"), basis("10"), atol=1e-12)
    np.testing.assert_allclose(u @ basis("11"), basis("11"), atol=1e-12)


def test_open_control_equals_x_conjugated_closed_control(sim):
    open_ = sim.unitary(circuit(2, op("ry", 1, o=(2,), p=(THETA,))))
    closed = sim.unitary(circuit(2, op("x", 2), op("ry", 1, c=(2,), p=(THETA,)), op("x", 2)))
    np.testing.assert_allclose(open_, closed, atol=1e-12)


def test_mixed_closed_and_open_controls(sim):
    u = sim.unitary(circuit(3, op("x", 3, c=(1,), o=(2,))))
    for label in ("000", "001", "010", "011", "110", "111"):
        np.testing.assert_allclose(u @ basis(label), basis(label), atol=1e-12)
    np.testing.assert_allclose(u @ basis("100"), basis("101"), atol=1e-12)
    np.testing.assert_allclose(u @ basis("101"), basis("100"), atol=1e-12)


def test_two_controls_on_x_is_toffoli(sim):
    u = sim.unitary(circuit(3, op("x", 3, c=(1, 2))))
    expected = np.eye(8, dtype=complex)
    expected[[6, 7]] = expected[[7, 6]]
    np.testing.assert_allclose(u, expected, atol=1e-12)


def test_controls_add_up_three_controls_on_z(sim):
    u = sim.unitary(circuit(4, op("z", 4, c=(1, 2, 3))))
    expected = np.ones(16, dtype=complex)
    expected[15] = -1
    np.testing.assert_allclose(u, np.diag(expected), atol=1e-12)
    # The same gate with the target in the middle: the diagonal is the same, CZ-like gates
    # are symmetric in all their wires.
    np.testing.assert_allclose(
        sim.unitary(circuit(4, op("z", 2, c=(4, 1, 3)))), np.diag(expected), atol=1e-12
    )


def test_controlled_controlled_rz_acts_only_on_11(sim):
    u = sim.unitary(circuit(3, op("rz", 2, c=(1, 3), p=(THETA,))))
    rz = literal("rz", (THETA,))
    for label in ("000", "001", "100", "010", "011", "110"):
        np.testing.assert_allclose(u @ basis(label), basis(label), atol=1e-12)
    np.testing.assert_allclose(u @ basis("101"), rz[0, 0] * basis("101"), atol=1e-12)
    np.testing.assert_allclose(u @ basis("111"), rz[1, 1] * basis("111"), atol=1e-12)


def test_controlled_swap_is_fredkin(sim):
    u = sim.unitary(circuit(3, op("swap", 2, 3, c=(1,))))
    for bits in itertools.product("01", repeat=3):
        label = "".join(bits)
        expected = label[0] + label[2] + label[1] if label[0] == "1" else label
        np.testing.assert_allclose(u @ basis(label), basis(expected), atol=1e-12)


def test_controlled_two_wire_rotation(sim):
    u = sim.unitary(circuit(3, op("rzz", 1, 3, c=(2,), p=(THETA,))))
    m, p = cmath.exp(-1j * THETA / 2), cmath.exp(1j * THETA / 2)
    expected = np.diag([1, 1, m, p, 1, 1, p, m])
    np.testing.assert_allclose(u, expected, atol=1e-12)


def test_an_extra_control_on_a_cnot_is_a_toffoli_not_a_cnot(sim):
    toffoli = sim.unitary(circuit(3, op("x", 3, c=(1, 2))))
    cnot_from_2 = sim.unitary(circuit(3, cx(2, 3)))
    assert np.max(np.abs(toffoli - cnot_from_2)) > 0.5


def test_controlled_global_phase_becomes_a_relative_phase(sim):
    # rz(2 pi) = -I: uncontrolled it is a global phase, controlled it is CZ-like.
    u = sim.unitary(circuit(2, op("rz", 2, c=(1,), p=(2 * math.pi,))))
    np.testing.assert_allclose(u, diag(1, 1, -1, -1), atol=1e-12)


def test_circuit_with_no_ops_is_the_identity(sim):
    np.testing.assert_allclose(sim.unitary(Circuit(3)), np.eye(8), atol=1e-12)
