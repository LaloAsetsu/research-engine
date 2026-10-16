"""The gate table: every matrix against a literal, unitarity, and the textbook identities."""

import cmath
import math

import numpy as np
import pytest
from naive import FIXED, PARAMETRIC, TWO_WIRE, H, X, Y, Z, diag, literal

from quantum_sim import GATES, InvalidCircuitError, UnknownGateError, gates

ANGLES = (0.0, 0.3, -1.1, math.pi / 2, math.pi, 2.5, -3 * math.pi / 4, 2 * math.pi)
PI = math.pi


def test_the_naive_table_covers_the_gate_table_exactly():
    assert set(FIXED) | set(PARAMETRIC) == set(GATES)
    for name, spec in GATES.items():
        assert (name in PARAMETRIC) == (spec.n_params == 1)
        assert (name in TWO_WIRE) == (spec.n_targets == 2)


# Values worked out by hand, independent of the literal table in naive.py.
HAND = [
    ("h", (), np.array([[1, 1], [1, -1]]) / math.sqrt(2)),
    ("x", (), [[0, 1], [1, 0]]),
    ("y", (), [[0, -1j], [1j, 0]]),
    ("z", (), [[1, 0], [0, -1]]),
    ("s", (), [[1, 0], [0, 1j]]),
    ("sdg", (), [[1, 0], [0, -1j]]),
    ("t", (), [[1, 0], [0, (1 + 1j) / math.sqrt(2)]]),
    ("tdg", (), [[1, 0], [0, (1 - 1j) / math.sqrt(2)]]),
    ("sx", (), [[0.5 + 0.5j, 0.5 - 0.5j], [0.5 - 0.5j, 0.5 + 0.5j]]),
    ("rx", (PI,), [[0, -1j], [-1j, 0]]),
    ("ry", (PI,), [[0, -1], [1, 0]]),
    ("rz", (PI,), [[-1j, 0], [0, 1j]]),
    ("rx", (PI / 2,), np.array([[1, -1j], [-1j, 1]]) / math.sqrt(2)),
    ("ry", (PI / 2,), np.array([[1, -1], [1, 1]]) / math.sqrt(2)),
    ("p", (PI,), [[1, 0], [0, -1]]),
    ("p", (PI / 2,), [[1, 0], [0, 1j]]),
    ("rk", (1,), [[1, 0], [0, -1]]),
    ("rk", (2,), [[1, 0], [0, 1j]]),
    ("rzz", (PI,), np.diag([-1j, 1j, 1j, -1j])),
    ("rxx", (PI,), [[0, 0, 0, -1j], [0, 0, -1j, 0], [0, -1j, 0, 0], [-1j, 0, 0, 0]]),
    ("ryy", (PI,), [[0, 0, 0, 1j], [0, 0, -1j, 0], [0, -1j, 0, 0], [1j, 0, 0, 0]]),
    ("swap", (), [[1, 0, 0, 0], [0, 0, 1, 0], [0, 1, 0, 0], [0, 0, 0, 1]]),
    ("iswap", (), [[1, 0, 0, 0], [0, 0, 1j, 0], [0, 1j, 0, 0], [0, 0, 0, 1]]),
]


@pytest.mark.parametrize(("name", "params", "expected"), HAND, ids=lambda v: str(v)[:12])
def test_matrix_equals_the_hand_value_exactly_with_its_global_phase(sim, name, params, expected):
    np.testing.assert_allclose(sim.matrix(name, params), np.asarray(expected), atol=1e-12)


def all_gates():
    for name in GATES:
        if name == "rk":
            yield from ((name, (k,)) for k in range(1, 7))
        elif GATES[name].n_params:
            yield from ((name, (a,)) for a in ANGLES)
        else:
            yield name, ()


@pytest.mark.parametrize(("name", "params"), list(all_gates()))
def test_every_gate_is_unitary_and_equals_the_conventions_literal(sim, name, params):
    m = sim.matrix(name, params)
    size = 4 if name in TWO_WIRE else 2
    assert m.shape == (size, size)
    assert m.dtype == np.complex128
    np.testing.assert_allclose(m @ m.conj().T, np.eye(size), atol=1e-12)
    np.testing.assert_allclose(m, literal(name, params), atol=1e-12)


@pytest.mark.parametrize("theta", ANGLES)
def test_rotations_are_exponentials_of_paulis(sim, theta):
    c, s = math.cos(theta / 2), math.sin(theta / 2)
    for name, pauli in (("rx", X), ("ry", Y), ("rz", Z)):
        np.testing.assert_allclose(
            sim.matrix(name, (theta,)), c * np.eye(2) - 1j * s * pauli, atol=1e-12
        )
    for name, pauli in (("rxx", X), ("ryy", Y), ("rzz", Z)):
        pp = np.kron(pauli, pauli)
        np.testing.assert_allclose(
            sim.matrix(name, (theta,)), c * np.eye(4) - 1j * s * pp, atol=1e-12
        )


def test_identities_of_the_fixed_gates(sim):
    m = sim.matrix
    np.testing.assert_allclose(m("s") @ m("s"), Z, atol=1e-12)
    np.testing.assert_allclose(m("t") @ m("t"), m("s"), atol=1e-12)
    np.testing.assert_allclose(m("sx") @ m("sx"), X, atol=1e-12)
    np.testing.assert_allclose(m("sdg"), m("s").conj().T, atol=1e-12)
    np.testing.assert_allclose(m("tdg"), m("t").conj().T, atol=1e-12)
    np.testing.assert_allclose(m("sxdg"), m("sx").conj().T, atol=1e-12)
    np.testing.assert_allclose(m("h") @ m("x") @ m("h"), Z, atol=1e-12)
    np.testing.assert_allclose(m("h") @ m("z") @ m("h"), X, atol=1e-12)
    np.testing.assert_allclose(m("h") @ m("y") @ m("h"), -Y, atol=1e-12)
    np.testing.assert_allclose(m("h"), H, atol=1e-12)
    np.testing.assert_allclose(m("id"), np.eye(2), atol=1e-12)
    np.testing.assert_allclose(m("iswap") @ m("iswap"), np.diag([1, -1, -1, 1]), atol=1e-12)


def test_phase_gates_are_p_at_the_textbook_angles(sim):
    m = sim.matrix
    np.testing.assert_allclose(m("s"), m("p", (PI / 2,)), atol=1e-12)
    np.testing.assert_allclose(m("t"), m("p", (PI / 4,)), atol=1e-12)
    np.testing.assert_allclose(m("z"), m("p", (PI,)), atol=1e-12)
    for k in range(1, 7):
        np.testing.assert_allclose(m("rk", (k,)), diag(1, cmath.exp(2j * PI / 2**k)), atol=1e-12)
    np.testing.assert_allclose(m("rk", (3,)), m("t"), atol=1e-12)


@pytest.mark.parametrize("theta", ANGLES)
def test_rz_is_p_times_a_global_phase(sim, theta):
    np.testing.assert_allclose(
        sim.matrix("rz", (theta,)),
        cmath.exp(-1j * theta / 2) * sim.matrix("p", (theta,)),
        atol=1e-12,
    )


def test_matrix_returns_a_new_array_each_call(sim):
    first = sim.matrix("x")
    first[0, 0] = 7
    assert sim.matrix("x")[0, 0] == 0


@pytest.mark.parametrize("name", ["H", "cnot", "cx", "u3", "measure", "", "R_z"])
def test_unknown_names_are_refused_not_guessed(name):
    with pytest.raises(UnknownGateError):
        gates.matrix(name, ())


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("h", (0.1,)),
        ("rz", ()),
        ("rz", (0.1, 0.2)),
        ("rz", (math.nan,)),
        ("p", (math.inf,)),
        ("rk", (0,)),
        ("rk", (-2,)),
        ("rk", (1.5,)),
    ],
)
def test_wrong_parameters_are_refused(name, params):
    with pytest.raises(InvalidCircuitError):
        gates.matrix(name, params)
