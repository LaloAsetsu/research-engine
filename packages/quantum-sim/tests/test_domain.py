"""The domain types: plain frozen data, built the same way by hand and from JSON."""

import dataclasses
import json

import pytest

from quantum_sim import (
    CONVENTIONS,
    GATES,
    MAX_OPS,
    MAX_STATEVECTOR_WIRES,
    MAX_UNITARY_WIRES,
    MEASURE,
    RANDOM_STATES,
    Circuit,
    Equality,
    InvalidCircuitError,
    InvalidStateError,
    LimitExceededError,
    Op,
    QuantumSimError,
    UnknownGateError,
    UnsupportedOperationError,
)


def test_op_stores_lists_as_tuples_and_compares_by_value():
    from_json = Op(
        **json.loads(
            '{"gate": "x", "wires": [3], "controls": [1], "open_controls": [2], "params": []}'
        )
    )
    by_hand = Op("x", (3,), controls=(1,), open_controls=(2,))
    assert from_json == by_hand
    assert hash(from_json) == hash(by_hand)
    assert from_json.all_wires == (3, 1, 2)
    assert not from_json.is_measurement


def test_op_and_circuit_are_frozen():
    op = Op("h", (1,))
    with pytest.raises(dataclasses.FrozenInstanceError):
        op.gate = "x"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        Circuit(1).n_wires = 2  # type: ignore[misc]


def test_circuit_measured_wires_and_unitary_part():
    ops = [Op("h", (1,)), Op(MEASURE, (3,)), Op(MEASURE, (1,))]
    c = Circuit(3, ops)  # a list is stored as a tuple
    assert c.ops == tuple(ops)
    assert c.measured_wires == (1, 3)
    assert c.without_measurements() == Circuit(3, (Op("h", (1,)),))
    assert Circuit(2).ops == ()
    assert Circuit(2).measured_wires == ()


def test_errors_share_one_base_and_value_errors_stay_value_errors():
    for error in (
        InvalidCircuitError,
        UnknownGateError,
        UnsupportedOperationError,
        LimitExceededError,
        InvalidStateError,
    ):
        assert issubclass(error, QuantumSimError)
    assert issubclass(UnknownGateError, InvalidCircuitError)
    assert issubclass(InvalidCircuitError, ValueError)
    assert issubclass(InvalidStateError, ValueError)


def test_equality_is_true_exactly_when_equal():
    assert Equality(True, False, 0.5, 0.0, "arrays")
    assert not Equality(False, False, 0.0, 1.0, "unitary")


def test_gate_table_shapes():
    assert all(spec.name == name for name, spec in GATES.items())
    assert {n for n, s in GATES.items() if s.n_targets == 2} == {
        "rxx",
        "ryy",
        "rzz",
        "swap",
        "iswap",
    }
    assert {n for n, s in GATES.items() if s.n_params == 1} == {
        "rx",
        "ry",
        "rz",
        "p",
        "rk",
        "rxx",
        "ryy",
        "rzz",
    }
    assert MEASURE not in GATES
    assert all(name == name.lower() for name in GATES)


def test_conventions_state_the_approved_choices():
    assert "most significant" in CONVENTIONS["wire_order"]
    assert "1-based" in CONVENTIONS["wire_numbers"]
    assert "exp(-i theta sigma_a / 2)" in CONVENTIONS["rotations"]
    assert "diag(1, e^{i theta})" in CONVENTIONS["phase"]
    assert "2 pi i / 2^k" in CONVENTIONS["qft_rk"]
    assert "global phase" in CONVENTIONS["equality"]
    assert "end of a wire" in CONVENTIONS["measurement"]


def test_the_limits_are_the_approved_ones():
    assert (MAX_STATEVECTOR_WIRES, MAX_UNITARY_WIRES, MAX_OPS, RANDOM_STATES) == (20, 10, 10_000, 8)
