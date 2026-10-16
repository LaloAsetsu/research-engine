"""Version 1 measures at the end of a wire only, and reports outcome probabilities."""

import math

import numpy as np
import pytest
from circuits import circuit, cx, op

from quantum_sim import MEASURE, Circuit, Op, UnsupportedOperationError, engine, validate

pytestmark = pytest.mark.engine_pending


def measure(wire: int) -> Op:
    return Op(MEASURE, (wire,))


BELL = (op("h", 1), cx(1, 2))


def test_end_of_wire_measurement_gives_the_outcome_probabilities():
    c = circuit(2, *BELL, measure(1), measure(2))
    validate(c)
    np.testing.assert_allclose(engine.probabilities(c), [0.5, 0, 0, 0.5], atol=1e-12)


def test_statevector_is_the_state_before_the_final_measurements():
    c = circuit(2, *BELL, measure(1), measure(2))
    np.testing.assert_allclose(
        engine.statevector(c), np.array([1, 0, 0, 1]) / math.sqrt(2), atol=1e-12
    )


def test_default_wires_are_the_measured_ones_in_increasing_order():
    c = circuit(3, op("x", 3), measure(3), op("h", 2), measure(1))
    np.testing.assert_allclose(engine.probabilities(c), [0, 1, 0, 0], atol=1e-12)


def test_without_measurements_every_wire_is_reported():
    np.testing.assert_allclose(
        engine.probabilities(circuit(2, op("x", 2))), [0, 1, 0, 0], atol=1e-12
    )


def test_probabilities_follow_the_order_of_the_wires_given():
    c = circuit(2, op("x", 1))
    np.testing.assert_allclose(engine.probabilities(c, wires=(1, 2)), [0, 0, 1, 0], atol=1e-12)
    np.testing.assert_allclose(engine.probabilities(c, wires=(2, 1)), [0, 1, 0, 0], atol=1e-12)
    np.testing.assert_allclose(engine.probabilities(c, wires=(2,)), [1, 0], atol=1e-12)


def test_a_gate_on_another_wire_after_a_measurement_is_allowed():
    c = circuit(2, op("x", 1), measure(1), op("h", 2))
    validate(c)
    np.testing.assert_allclose(engine.probabilities(c, wires=(1, 2)), [0, 0, 0.5, 0.5], atol=1e-12)


MID_CIRCUIT = [
    pytest.param(circuit(1, measure(1), op("h", 1)), id="gate-after-measure"),
    pytest.param(circuit(2, measure(1), cx(1, 2)), id="control-after-measure"),
    pytest.param(circuit(2, measure(1), op("x", 2, o=(1,))), id="open-control-after-measure"),
    pytest.param(circuit(2, measure(2), op("swap", 1, 2)), id="two-wire-gate-after-measure"),
    pytest.param(circuit(1, measure(1), measure(1)), id="measured-twice"),
    pytest.param(
        Circuit(2, (measure(1), Op("x", (2,), classical_controls=(1,)))),
        id="classically-controlled",
    ),
]


@pytest.mark.parametrize("c", MID_CIRCUIT)
def test_mid_circuit_and_classically_controlled_ops_are_refused(c):
    with pytest.raises(UnsupportedOperationError):
        validate(c)
    with pytest.raises(UnsupportedOperationError):
        engine.statevector(c)
    with pytest.raises(UnsupportedOperationError):
        engine.probabilities(c)


def test_unitary_refuses_measurements_but_takes_the_unitary_part():
    c = circuit(2, *BELL, measure(1), measure(2))
    with pytest.raises(UnsupportedOperationError):
        engine.unitary(c)
    with pytest.raises(UnsupportedOperationError):
        engine.equal(c, c)
    assert engine.unitary(c.without_measurements()).shape == (4, 4)
