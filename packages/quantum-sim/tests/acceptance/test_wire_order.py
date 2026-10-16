"""Wire 1 is the top wire and the most significant bit; wires are numbered from 1."""

import itertools

import numpy as np
import pytest
from circuits import circuit, cx, op
from naive import basis


def labels(n):
    return ["".join(bits) for bits in itertools.product("01", repeat=n)]


def flip(label: str, wire: int) -> str:
    bits = list(label)
    bits[wire - 1] = "1" if bits[wire - 1] == "0" else "0"
    return "".join(bits)


def test_x_on_wire_1_of_2_maps_00_to_10(sim):
    state = sim.statevector(circuit(2, op("x", 1)))
    np.testing.assert_allclose(state, [0, 0, 1, 0], atol=1e-12)


def test_x_on_wire_2_of_2_maps_00_to_01(sim):
    state = sim.statevector(circuit(2, op("x", 2)))
    np.testing.assert_allclose(state, [0, 1, 0, 0], atol=1e-12)


def test_x_on_the_last_wire_of_5_is_amplitude_1(sim):
    state = sim.statevector(circuit(5, op("x", 5)))
    assert np.argmax(np.abs(state)) == 1


def test_a_basis_label_input_is_read_wire_1_first(sim):
    state = sim.statevector(circuit(3, op("id", 1)), "100")
    np.testing.assert_allclose(state, basis("100"), atol=1e-12)
    assert np.argmax(np.abs(state)) == 4


def test_hadamard_on_wire_1_of_2(sim):
    state = sim.statevector(circuit(2, op("h", 1)))
    np.testing.assert_allclose(state, np.array([1, 0, 1, 0]) / np.sqrt(2), atol=1e-12)


@pytest.mark.parametrize(("control", "target"), [(1, 3), (3, 1), (1, 2), (2, 1), (2, 3), (3, 2)])
def test_cnot_on_every_basis_state_of_3_wires(sim, control, target):
    for label in labels(3):
        expected = flip(label, target) if label[control - 1] == "1" else label
        state = sim.statevector(circuit(3, cx(control, target)), label)
        np.testing.assert_allclose(state, basis(expected), atol=1e-12, err_msg=label)


@pytest.mark.parametrize(("control", "target"), [(1, 4), (4, 1), (2, 4), (4, 2)])
def test_non_adjacent_and_reversed_cnot_on_4_wires(sim, control, target):
    u = sim.unitary(circuit(4, cx(control, target)))
    for label in labels(4):
        expected = flip(label, target) if label[control - 1] == "1" else label
        np.testing.assert_allclose(u @ basis(label), basis(expected), atol=1e-12, err_msg=label)


def test_swap_on_non_adjacent_wires_in_either_order(sim):
    for a, b in [(1, 3), (3, 1)]:
        u = sim.unitary(circuit(3, op("swap", a, b)))
        for label in labels(3):
            swapped = list(label)
            swapped[a - 1], swapped[b - 1] = label[b - 1], label[a - 1]
            np.testing.assert_allclose(u @ basis(label), basis("".join(swapped)), atol=1e-12)


def test_iswap_phases_follow_the_wires_it_exchanges(sim):
    u = sim.unitary(circuit(3, op("iswap", 1, 3)))
    np.testing.assert_allclose(u @ basis("100"), 1j * basis("001"), atol=1e-12)
    np.testing.assert_allclose(u @ basis("110"), 1j * basis("011"), atol=1e-12)
    np.testing.assert_allclose(u @ basis("101"), basis("101"), atol=1e-12)
    np.testing.assert_allclose(u @ basis("010"), basis("010"), atol=1e-12)


def test_unitary_columns_are_the_images_of_basis_states(sim):
    c = circuit(3, op("h", 1), cx(1, 3), op("t", 2), op("ry", 2, p=(0.7,)))
    u = sim.unitary(c)
    for k, label in enumerate(labels(3)):
        np.testing.assert_allclose(u[:, k], sim.statevector(c, label), atol=1e-12)
