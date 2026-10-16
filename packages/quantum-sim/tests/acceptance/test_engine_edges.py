"""Edges of the engine that the acceptance suite does not reach: argument types, error order,
the details of equality, and checks that pin the numbers the algorithms promise."""

import math

import naive
import numpy as np
import pytest
from circuits import circuit, cx, op

from quantum_sim import (
    MAX_OPS,
    MEASURE,
    RANDOM_STATES,
    Circuit,
    InvalidCircuitError,
    InvalidStateError,
    LimitExceededError,
    Op,
    UnknownGateError,
    UnsupportedOperationError,
    engine,
    gates,
    validate,
)

# --- validate ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(Circuit(True), id="n_wires-is-a-bool"),
        pytest.param(Circuit(2.0), id="n_wires-is-a-float"),  # type: ignore[arg-type]
        pytest.param(circuit(2, Op("h", (True,))), id="wire-is-a-bool"),
        pytest.param(circuit(2, Op("h", (1.0,))), id="wire-is-a-float"),  # type: ignore[arg-type]
        pytest.param(circuit(2, Op("h", ("1",))), id="wire-is-a-string"),  # type: ignore[arg-type]
        pytest.param(circuit(2, op("rz", 1, p=(True,))), id="angle-is-a-bool"),
        pytest.param(circuit(2, op("rz", 1, p=(1j,))), id="angle-is-complex"),  # type: ignore[arg-type]
        pytest.param(circuit(2, op("rz", 1, p=("1",))), id="angle-is-a-string"),  # type: ignore[arg-type]
        pytest.param(circuit(2, op("rz", 1, p=(10**400,))), id="angle-too-large-for-a-float"),
        pytest.param(circuit(2, op("rk", 1, p=(-1,))), id="rk-negative"),
        pytest.param(circuit(2, op("rk", 1, p=(-(10**400),))), id="rk-huge-negative"),
        pytest.param(circuit(2, op("rk", 1, p=(1e400,))), id="rk-infinite"),
        pytest.param(circuit(2, "notanop"), id="op-is-a-string"),  # type: ignore[arg-type]
        pytest.param(circuit(2, None), id="op-is-none"),  # type: ignore[arg-type]
        pytest.param(circuit(2, op("rk", 1, p=(math.nan,))), id="rk-nan"),
        pytest.param(circuit(2, Op(MEASURE, (1,), params=(0.1,))), id="measure-with-angle"),
        pytest.param(circuit(2, Op(MEASURE, (1,), open_controls=(2,))), id="measure-open-control"),
        pytest.param(circuit(2, Op(MEASURE, ())), id="measure-nothing"),
        pytest.param(circuit(3, Op("x", (1,), classical_controls=(1,))), id="classical-is-target"),
        pytest.param(
            circuit(3, Op("x", (1,), classical_controls=(9,))), id="classical-out-of-range"
        ),
    ],
)
def test_more_malformed_circuits(bad):
    with pytest.raises(InvalidCircuitError):
        validate(bad)


@pytest.mark.parametrize("not_a_circuit", [None, "h", 3, (1, ()), Op("h", (1,))])
def test_something_that_is_not_a_circuit_is_refused(not_a_circuit):
    with pytest.raises(InvalidCircuitError):
        validate(not_a_circuit)  # type: ignore[arg-type]


def test_the_gate_name_must_be_a_string():
    with pytest.raises(UnknownGateError):
        validate(circuit(1, Op(["h"], (1,))))  # type: ignore[arg-type]


def test_rk_takes_a_whole_number_given_as_a_float():
    validate(circuit(1, op("rk", 1, p=(3.0,))))
    np.testing.assert_allclose(gates.matrix("rk", (3.0,)), gates.matrix("t"), atol=1e-12)


def test_a_huge_rk_is_the_identity_and_costs_nothing():
    np.testing.assert_allclose(gates.matrix("rk", (10**9,)), np.eye(2), atol=1e-15)
    np.testing.assert_allclose(gates.matrix("rk", (60,)), np.eye(2), atol=1e-15)


def test_rk_takes_an_integer_of_any_size():
    for k in (10**400, 2**2048, np.int64(7)):
        validate(circuit(1, op("rk", 1, p=(k,))))
    np.testing.assert_allclose(gates.matrix("rk", (10**400,)), np.eye(2), atol=1e-15)
    np.testing.assert_allclose(engine.unitary(circuit(1, op("rk", 1, p=(10**400,)))), np.eye(2))


def test_a_huge_number_is_shortened_in_the_error_message():
    with pytest.raises(InvalidCircuitError) as info:
        validate(circuit(2, op("rz", 1, p=(10**400,))))
    assert len(str(info.value)) < 120
    with pytest.raises(InvalidCircuitError) as info:
        validate(circuit(2, op("rk", 1, p=(-(10**400),))))
    assert len(str(info.value)) < 120


def test_numpy_integers_and_floats_are_accepted_as_wires_and_angles():
    c = circuit(2, op("rz", int(np.int64(2)), p=(float(np.float32(0.5)),)))
    validate(c)
    validate(Circuit(2, (Op("rz", (np.int64(2),), params=(np.float64(0.5),)),)))  # type: ignore[arg-type]


def test_errors_name_the_operation():
    c = circuit(2, op("h", 1), op("h", 1), op("h", 7))
    with pytest.raises(InvalidCircuitError, match="operation 3"):
        validate(c)
    with pytest.raises(UnknownGateError, match="operation 1"):
        validate(circuit(2, Op("cnot", (1,))))


def test_the_op_limit_counts_every_op_and_is_checked_before_the_ops_are_read():
    ops = tuple(Op("nonsense", (1,)) for _ in range(MAX_OPS + 1))
    with pytest.raises(LimitExceededError, match="10,000"):
        validate(Circuit(1, ops))


def test_gates_refuse_what_validate_refuses():
    with pytest.raises(UnknownGateError):
        gates.matrix(None)  # type: ignore[arg-type]
    with pytest.raises(InvalidCircuitError):
        gates.matrix("rz", ("0.1",))  # type: ignore[arg-type]


# --- the state ---------------------------------------------------------------------------------


@pytest.mark.parametrize("state", [["a", "b"], [[1, 0], [0, 0]], [1, 0, 0, 0, 0]])
def test_input_states_that_are_not_a_vector_of_numbers(state):
    with pytest.raises(InvalidStateError):
        engine.statevector(circuit(1, op("h", 1)), state)


def test_a_state_with_nan_is_refused():
    with pytest.raises(InvalidStateError):
        engine.statevector(circuit(1), [math.nan, 0])


def test_an_input_state_with_norm_inside_the_tolerance_is_accepted():
    engine.statevector(circuit(1), [1 + 5e-10, 0])


def test_the_input_state_is_not_modified_by_probabilities_either():
    state = np.array([0, 1, 0, 0], dtype=complex)
    engine.probabilities(circuit(2, op("x", 1)), state)
    np.testing.assert_array_equal(state, [0, 1, 0, 0])


def test_probabilities_take_an_input_state_and_numpy_wires():
    c = circuit(2, op("x", 2))
    np.testing.assert_allclose(engine.probabilities(c, "10", wires=np.array([1, 2])), [0, 0, 0, 1])
    np.testing.assert_allclose(engine.probabilities(c, [0, 0, 1, 0]), [0, 0, 0, 1], atol=1e-12)


@pytest.mark.parametrize("wires", [(), [True], [1.0], ["1"], 5, 1.0, object()])
def test_probabilities_refuse_wires_that_are_not_wire_numbers(wires):
    with pytest.raises(InvalidCircuitError):
        engine.probabilities(circuit(2), wires=wires)


def test_probabilities_of_a_complex_state_use_both_parts():
    state = [0, 1j / math.sqrt(2), 0, 1 / math.sqrt(2)]
    np.testing.assert_allclose(engine.probabilities(circuit(2), state), [0, 0.5, 0, 0.5])


def test_statevector_returns_a_new_array_even_without_ops():
    state = np.array([0, 1], dtype=complex)
    out = engine.statevector(circuit(1), state)
    out[0] = 5
    np.testing.assert_array_equal(state, [0, 1])


# --- gates under controls, in sizes the acceptance suite does not use ---------------------------


def test_target_and_controls_interleaved_on_6_wires_match_the_naive_builder():
    c = circuit(
        6,
        op("ry", 4, c=(6, 1), o=(3,), p=(0.7,)),
        op("rxx", 5, 2, c=(4,), o=(6,), p=(-1.3,)),
        op("iswap", 6, 1, o=(2, 3)),
        op("sx", 3, c=(5,)),
        cx(2, 6),
    )
    np.testing.assert_allclose(engine.unitary(c), naive.unitary(c), atol=1e-12)


def test_two_wire_gate_with_wires_in_descending_order():
    # Every two-wire gate in the table is symmetric in its two wires, so this guards the
    # bookkeeping of the axes (a target above or below the other, controls in between).
    c = circuit(3, op("iswap", 3, 1), op("rzz", 3, 2, p=(0.4,)))
    np.testing.assert_allclose(engine.unitary(c), naive.unitary(c), atol=1e-12)


# --- equality ----------------------------------------------------------------------------------


def test_equal_takes_a_matrix_on_either_side_and_keeps_the_order_of_the_phase():
    c = circuit(2, op("p", 2, p=(0.8,)))
    target = np.diag([1, np.exp(0.8j), 1, np.exp(0.8j)])
    left = engine.equal(c, 1j * target)
    right = engine.equal(1j * target, c)
    assert left.equal and right.equal
    assert abs(left.phase + math.pi / 2) < 1e-12
    assert abs(right.phase - math.pi / 2) < 1e-12
    assert left.method == right.method == "unitary"


def test_equal_compares_lists_and_integer_arrays():
    assert engine.equal([1, 0], [1, 0]).exact
    assert engine.equal(np.array([[0, 1], [1, 0]]), [[0, 1j], [1j, 0]])


def test_equal_of_a_phase_and_zero_arrays():
    zeros = np.zeros(4)
    assert engine.equal(zeros, zeros).exact
    assert not engine.equal(np.array([0, 0, 1, 0]), zeros)
    assert not engine.equal(zeros, np.array([0, 0, 1, 0]))
    assert engine.equal(zeros, zeros).phase == 0


def test_equal_with_nan_is_false():
    assert not engine.equal(np.array([1, math.nan]), np.array([1, 0]))


@pytest.mark.parametrize("atol", [math.nan, math.inf, 0, 0.0, -1e-9, True, "1e-9", None])
def test_equal_refuses_a_tolerance_that_is_not_a_positive_finite_number(atol):
    with pytest.raises(ValueError, match="atol"):
        engine.equal(np.ones(2), np.ones(2), atol=atol)
    with pytest.raises(ValueError, match="atol"):
        engine.equal(circuit(1), circuit(1), atol=atol)


def test_equal_refuses_empty_arrays():
    with pytest.raises(ValueError, match="empty"):
        engine.equal(np.zeros(0), np.zeros(0))


def test_equal_refuses_what_is_not_an_array():
    with pytest.raises(TypeError):
        engine.equal("abc", np.zeros(3))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        engine.equal([[1], [1, 2]], [[1], [1, 2]])
    with pytest.raises(TypeError):
        engine.equal(circuit(1), np.zeros((2, 2, 2)))
    with pytest.raises(TypeError):
        engine.equal(np.zeros(2), circuit(1))


def test_equal_of_a_circuit_and_a_matrix_of_the_wrong_size():
    with pytest.raises(ValueError, match="shape"):
        engine.equal(circuit(2), np.eye(2))
    with pytest.raises(ValueError, match="shape"):
        engine.equal(np.eye(8), circuit(2))


def test_equal_checks_the_circuit_before_it_builds_anything():
    big = circuit(11, op("h", 1))
    with pytest.raises(LimitExceededError):
        engine.equal(big, np.eye(2**11))
    with pytest.raises(UnsupportedOperationError):
        engine.equal(Circuit(2, (Op(MEASURE, (1,)),)), np.eye(4))
    with pytest.raises(InvalidCircuitError):
        engine.equal(circuit(2, op("h", 9)), np.eye(4))


def test_equal_refuses_a_bad_circuit_whatever_the_other_one_is():
    with pytest.raises(UnknownGateError):
        engine.equal(circuit(2, Op("cnot", (1,))), circuit(2))
    with pytest.raises(UnknownGateError):
        engine.equal(circuit(2), circuit(2, Op("cnot", (1,))))
    with pytest.raises(UnsupportedOperationError):
        engine.equal(circuit(2), Circuit(2, (Op(MEASURE, (1,)),)))


def test_the_unitary_comparison_reaches_10_wires_and_random_states_start_at_11():
    ten = circuit(10, op("h", 1), cx(1, 10))
    assert engine.equal(ten, ten).method == "unitary"
    eleven = circuit(11, op("h", 1), cx(1, 11))
    assert engine.equal(eleven, eleven).method == "random-states"


def test_21_wires_are_refused_for_a_pair_of_circuits_with_mismatched_wires_last():
    with pytest.raises(InvalidCircuitError):
        engine.equal(circuit(21), circuit(22))


def test_random_state_equality_matches_a_reference_run_of_the_same_recipe():
    """8 states from default_rng(seed), a phase from the first image, the worst deviation."""
    n, seed = 11, 5
    a = circuit(n, op("z", n, c=tuple(range(1, n))), op("t", 4))
    b = circuit(n, op("s", 4))
    rng = np.random.default_rng(seed)
    worst = 0.0
    phase = None
    for index in range(RANDOM_STATES):
        v = rng.normal(size=2**n) + 1j * rng.normal(size=2**n)
        v /= np.linalg.norm(v)
        ia, ib = engine.statevector(a, v), engine.statevector(b, v)
        k = int(np.argmax(np.abs(ib)))
        if index == 0:
            phase = np.angle(ia[k] * np.conj(ib[k]))
        worst = max(worst, float(np.max(np.abs(ia - np.exp(1j * phase) * ib))))
    result = engine.equal(a, b, seed=seed)
    assert result.method == "random-states"
    assert abs(result.max_deviation - worst) < 1e-12
    assert abs(result.phase - phase) < 1e-12
    assert result.max_deviation != engine.equal(a, b, seed=seed + 1).max_deviation


def test_a_global_phase_between_circuits_of_11_wires_is_reported_and_is_not_exact():
    a = circuit(11, op("rz", 3, p=(0.5,)), op("rz", 3, p=(0.5,)))
    b = circuit(11, op("p", 3, p=(1.0,)))
    result = engine.equal(a, b)
    assert result.equal and not result.exact
    assert abs(result.phase + 0.5) < 1e-9
    assert result.max_deviation < 1e-12


def test_random_states_see_a_difference_on_a_quarter_of_the_state():
    # Differs from the identity only where wires 1 and 2 are both 1, a quarter of every random
    # state; the deviation is about twice an amplitude, far above the tolerance.
    a = circuit(11, op("z", 2, c=(1,)))
    result = engine.equal(a, circuit(11))
    assert not result and result.max_deviation > 0.01


def test_the_phase_stays_in_the_half_open_range_when_it_rounds_to_minus_pi():
    a = np.array([complex(-1, -1e-300), 0])
    result = engine.equal(a, np.array([1, 0]))
    assert result.equal
    assert result.phase == math.pi


# --- the order of a gate's own wires -------------------------------------------------------------


def test_the_first_target_wire_is_the_more_significant_bit_of_the_gate_matrix(monkeypatch):
    """Every two-wire gate in the table is symmetric in its wires, so the order of the targets
    cannot be seen through the table; a matrix that is not symmetric (CNOT, control first)
    shows it."""
    cnot = np.eye(4, dtype=complex)[[0, 1, 3, 2]]
    monkeypatch.setattr(engine, "matrix", lambda name, params: cnot)
    for first, second, controls in [(3, 1, ()), (1, 3, ()), (2, 4, (1,)), (4, 1, (3,))]:
        got = engine.unitary(circuit(4, op("swap", first, second, c=controls)))
        expected = naive.unitary(circuit(4, op("x", second, c=(first, *controls))))
        np.testing.assert_allclose(got, expected, atol=1e-12)
