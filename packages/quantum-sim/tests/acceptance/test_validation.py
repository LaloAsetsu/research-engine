"""Malformed circuits and input states are refused with a typed error, never guessed at."""

import math

import numpy as np
import pytest
from circuits import circuit, cx, op

from quantum_sim import (
    MEASURE,
    Circuit,
    InvalidCircuitError,
    InvalidStateError,
    Op,
    UnknownGateError,
    engine,
    validate,
)


def test_a_valid_circuit_passes():
    assert validate(circuit(3, op("h", 1), cx(1, 3), op("rzz", 2, 3, c=(1,), p=(0.2,)))) is None


@pytest.mark.parametrize("name", ["H", "CNOT", "cx", "toffoli", "u3", "r", "U_1", ""])
def test_unknown_gate_names_are_refused(name):
    c = circuit(2, Op(name, (1,)))
    with pytest.raises(UnknownGateError, match="gate"):
        validate(c)
    with pytest.raises(UnknownGateError):
        engine.statevector(c)


INVALID = [
    pytest.param(Circuit(0), id="no-wires"),
    pytest.param(circuit(2, op("h", 0)), id="wire-0"),
    pytest.param(circuit(2, op("h", 3)), id="wire-above-n"),
    pytest.param(circuit(2, op("h", -1)), id="negative-wire"),
    pytest.param(circuit(2, cx(1, 1)), id="control-is-target"),
    pytest.param(circuit(3, op("x", 3, c=(1,), o=(1,))), id="open-and-closed-on-one-wire"),
    pytest.param(circuit(3, op("x", 3, c=(1, 1))), id="control-twice"),
    pytest.param(circuit(2, op("swap", 1, 1)), id="two-wire-gate-on-one-wire"),
    pytest.param(circuit(2, op("swap", 1)), id="too-few-targets"),
    pytest.param(circuit(2, op("h", 1, 2)), id="too-many-targets"),
    pytest.param(circuit(2, op("rz", 1)), id="missing-angle"),
    pytest.param(circuit(2, op("h", 1, p=(0.5,))), id="unexpected-angle"),
    pytest.param(circuit(2, op("rz", 1, p=(math.nan,))), id="nan-angle"),
    pytest.param(circuit(2, op("rz", 1, p=(math.inf,))), id="infinite-angle"),
    pytest.param(circuit(2, op("rk", 1, p=(0,))), id="rk-0"),
    pytest.param(circuit(2, op("rk", 1, p=(2.5,))), id="rk-not-integer"),
    pytest.param(circuit(2, Op(MEASURE, (1,), controls=(2,))), id="controlled-measure"),
    pytest.param(circuit(2, Op(MEASURE, (1, 2))), id="measure-two-wires"),
]


@pytest.mark.parametrize("c", INVALID)
def test_malformed_circuits_are_refused(c):
    with pytest.raises(InvalidCircuitError):
        validate(c)
    with pytest.raises(InvalidCircuitError):
        engine.statevector(c)


def test_probabilities_refuse_bad_wires():
    c = circuit(2, op("h", 1))
    for wires in [(0,), (3,), (1, 1)]:
        with pytest.raises(InvalidCircuitError):
            engine.probabilities(c, wires=wires)


BAD_STATES = [
    pytest.param("01", id="label-too-short"),
    pytest.param("0102", id="label-not-binary"),
    pytest.param("|010>", id="ket-text-is-not-read"),
    pytest.param([1, 0, 0], id="amplitudes-wrong-length"),
    pytest.param([1, 1, 0, 0, 0, 0, 0, 0], id="not-normalized"),
    pytest.param(np.zeros(8), id="zero-vector"),
]


@pytest.mark.parametrize("state", BAD_STATES)
def test_bad_input_states_are_refused(state):
    with pytest.raises(InvalidStateError):
        engine.statevector(circuit(3, op("h", 1)), state)


def test_amplitude_input_is_accepted_and_not_modified():
    state = np.array([0, 1j, 0, 0], dtype=complex)
    out = engine.statevector(circuit(2, op("x", 1)), state)
    np.testing.assert_allclose(out, [0, 0, 0, 1j], atol=1e-12)
    np.testing.assert_allclose(state, [0, 1j, 0, 0])
    np.testing.assert_allclose(
        engine.statevector(circuit(2, op("x", 1)), [0, 1, 0, 0]), [0, 0, 0, 1], atol=1e-12
    )


def test_default_input_is_all_zeros():
    out = engine.statevector(circuit(3))
    np.testing.assert_allclose(out, [1, 0, 0, 0, 0, 0, 0, 0], atol=1e-12)
