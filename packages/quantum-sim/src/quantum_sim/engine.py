"""Statevector and unitary simulation, outcome probabilities and equality up to global phase.

Every function validates the circuit (``domain.validate``) and checks the limits from the
circuit's description before it creates any array, so a refused request costs no memory. A gate
is applied with ``numpy.tensordot`` over its wires: the state is a tensor with one axis per wire
(axis 0 is wire 1, the most significant bit), the controls select a slice of it, and the gate's
matrix contracts the target axes of that slice (O(2^n) per gate on a state, O(4^n) on a unitary).
"""

from __future__ import annotations

import cmath
import math
import re
from collections.abc import Sequence
from numbers import Integral, Real

import numpy as np
from numpy.typing import ArrayLike, NDArray

from quantum_sim.domain import (
    ATOL,
    MAX_STATEVECTOR_WIRES,
    MAX_UNITARY_WIRES,
    RANDOM_STATES,
    Circuit,
    Equality,
    InvalidCircuitError,
    InvalidStateError,
    LimitExceededError,
    Op,
    UnsupportedOperationError,
    validate,
)
from quantum_sim.gates import matrix

StateInput = str | ArrayLike | None
"""``None`` for |0...0>, a basis label such as ``"0110"`` (wire 1 first), or ``2**n``
amplitudes whose norm is 1 within ``ATOL``."""

_BASIS_LABEL = re.compile(r"[01]+")


def _check_request(circuit: Circuit, max_wires: int) -> None:
    """Validate the circuit and refuse a size above ``max_wires``, creating no array."""
    validate(circuit)
    if circuit.n_wires > max_wires:
        raise LimitExceededError(
            f"{circuit.n_wires} wires requested; the limit for this request is {max_wires}"
        )


def _apply(tensor: NDArray[np.complex128], op: Op) -> None:
    """Apply ``op`` in place to ``tensor``, whose first axes are the wires (axis 0 is wire 1).

    Axes after the wires (a batch of states, or the columns of a unitary) are left alone.
    """
    controls = {w - 1: 1 for w in op.controls} | {w - 1: 0 for w in op.open_controls}
    targets = [w - 1 for w in op.wires]
    selection: list[int | slice] = [controls.get(axis, slice(None)) for axis in range(tensor.ndim)]
    section = tensor[tuple(selection)]  # a view; the control axes are gone from it
    in_section = [axis - sum(c < axis for c in controls) for axis in targets]
    k = len(targets)
    gate = matrix(op.gate, op.params).reshape((2,) * (2 * k))
    updated = np.tensordot(gate, section, axes=(list(range(k, 2 * k)), in_section))
    section[...] = np.moveaxis(updated, list(range(k)), in_section)


def _run(circuit: Circuit, tensor: NDArray[np.complex128]) -> None:
    for op in circuit.ops:
        if not op.is_measurement:
            _apply(tensor, op)


def _initial_state(n_wires: int, input_state: StateInput) -> NDArray[np.complex128]:
    """The input as a new complex128 vector of ``2**n_wires`` amplitudes."""
    if input_state is None:
        state = np.zeros(2**n_wires, dtype=np.complex128)
        state[0] = 1
        return state
    if isinstance(input_state, str):
        if len(input_state) != n_wires or not _BASIS_LABEL.fullmatch(input_state):
            raise InvalidStateError(
                f"{input_state!r} is not a basis label of {n_wires} bits (wire 1 first)"
            )
        state = np.zeros(2**n_wires, dtype=np.complex128)
        state[int(input_state, 2)] = 1
        return state
    try:
        state = np.array(input_state, dtype=np.complex128)
    except (TypeError, ValueError) as error:
        raise InvalidStateError(f"the input state is not a list of amplitudes: {error}") from None
    if state.shape != (2**n_wires,):
        raise InvalidStateError(
            f"expected {2**n_wires} amplitudes for {n_wires} wires, got shape {state.shape}"
        )
    norm = float(np.linalg.norm(state))
    if not abs(norm - 1) <= ATOL:
        raise InvalidStateError(f"the input state has norm {norm}, not 1 (within {ATOL})")
    return state


def statevector(circuit: Circuit, input_state: StateInput = None) -> NDArray[np.complex128]:
    """The state after ``circuit`` acts on ``input_state``, as ``2**n_wires`` amplitudes.

    Amplitude ``k`` belongs to the basis label of ``k`` in binary with wire 1 as the most
    significant bit. Measurements (at the end of their wires) are not applied: the result is
    the state just before them.

    Raises ``LimitExceededError`` above ``MAX_STATEVECTOR_WIRES`` wires or ``MAX_OPS``
    operations, ``InvalidStateError`` for a bad input state, and whatever
    ``domain.validate`` raises.
    """
    _check_request(circuit, MAX_STATEVECTOR_WIRES)
    state = _initial_state(circuit.n_wires, input_state)
    _run(circuit, state.reshape((2,) * circuit.n_wires))
    return state


def unitary(circuit: Circuit) -> NDArray[np.complex128]:
    """The ``2**n x 2**n`` matrix of ``circuit``, rows and columns indexed as in
    ``statevector``; ``unitary(c) @ v`` equals ``statevector(c, v)``.

    Raises ``LimitExceededError`` above ``MAX_UNITARY_WIRES`` wires or ``MAX_OPS``
    operations, ``UnsupportedOperationError`` if the circuit has a measurement (use
    ``Circuit.without_measurements``), and whatever ``domain.validate`` raises.
    """
    _check_unitary_request(circuit)
    n, size = circuit.n_wires, 2**circuit.n_wires
    result = np.eye(size, dtype=np.complex128)
    _run(circuit, result.reshape((2,) * n + (size,)))
    return result


def _check_wires(wires: Sequence[int], n_wires: int) -> list[int]:
    try:
        chosen = list(wires)
    except TypeError:
        raise InvalidCircuitError(
            f"wires must be a sequence of wire numbers, got {wires!r}"
        ) from None
    if not chosen:
        raise InvalidCircuitError("probabilities needs at least one wire")
    for wire in chosen:
        if isinstance(wire, bool) or not isinstance(wire, Integral) or not 1 <= wire <= n_wires:
            raise InvalidCircuitError(f"wire {wire!r} is not an integer from 1 to {n_wires}")
    if len(set(chosen)) != len(chosen):
        raise InvalidCircuitError(f"wires {chosen} repeat a wire")
    return chosen


def probabilities(
    circuit: Circuit, input_state: StateInput = None, wires: Sequence[int] | None = None
) -> NDArray[np.float64]:
    """Outcome probabilities of measuring ``wires`` after ``circuit``, as ``2**len(wires)``
    numbers that sum to 1.

    Outcome ``k`` is the binary of ``k`` over ``wires`` in the order given (the first wire
    given is the most significant bit). ``wires=None`` means the measured wires of the circuit
    in increasing order, or every wire if it has no measurement.

    Raises ``InvalidCircuitError`` for a wire out of range or given twice, no wire at all, or
    ``wires`` that is not a sequence, and what ``statevector`` raises.
    """
    _check_request(circuit, MAX_STATEVECTOR_WIRES)
    n = circuit.n_wires
    if wires is None:
        chosen = list(circuit.measured_wires or range(1, n + 1))
    else:
        chosen = _check_wires(wires, n)
    state = _initial_state(n, input_state)
    _run(circuit, state.reshape((2,) * n))
    weights = (state.real**2 + state.imag**2).reshape((2,) * n)
    kept = sorted(chosen)
    summed = weights.sum(axis=tuple(axis for axis in range(n) if axis + 1 not in chosen))
    ordered = np.transpose(summed, [kept.index(w) for w in chosen])
    return np.ascontiguousarray(ordered).reshape(-1)


def _phase_of(a: NDArray[np.complex128], b: NDArray[np.complex128]) -> float:
    """phi in (-pi, pi] with a ~ e^{i phi} b, read at the largest entry of b."""
    flat_a, flat_b = a.reshape(-1), b.reshape(-1)
    k = int(np.argmax(np.abs(flat_b)))
    product = flat_a[k] * flat_b[k].conjugate()
    phase = float(np.angle(product)) if product != 0 else 0.0
    return math.pi if phase <= -math.pi else phase


def _deviations(
    a: NDArray[np.complex128], b: NDArray[np.complex128], phase: float
) -> tuple[float, float]:
    """max |a - e^{i phase} b| and max |a - b|."""
    return (
        float(np.max(np.abs(a - cmath.exp(1j * phase) * b))),
        float(np.max(np.abs(a - b))),
    )


def _compare(a: NDArray[np.complex128], b: NDArray[np.complex128], atol: float, method: str):
    if a.shape != b.shape:
        raise ValueError(f"arrays of different shape cannot be equal: {a.shape} and {b.shape}")
    if a.size == 0:
        raise ValueError("cannot compare empty arrays")
    phase = _phase_of(a, b)
    deviation, plain = _deviations(a, b, phase)
    return Equality(deviation < atol, plain < atol, phase, deviation, method)  # type: ignore[arg-type]


def _image(circuit: Circuit, state: NDArray[np.complex128]) -> NDArray[np.complex128]:
    """``state`` after ``circuit``, computed in place on ``state``."""
    _run(circuit, state.reshape((2,) * circuit.n_wires))
    return state


def _compare_on_random_states(a: Circuit, b: Circuit, atol: float, seed: int) -> Equality:
    """Both circuits on ``RANDOM_STATES`` random states, one phase for all of them.

    The phase is read at the largest entry of the first image of ``b``; the states are handled
    one at a time, so two states are in memory, not eight.
    """
    rng = np.random.default_rng(seed)
    size = 2**a.n_wires
    phase = 0.0
    deviation = plain = 0.0
    for index in range(RANDOM_STATES):
        state = rng.normal(size=size) + 1j * rng.normal(size=size)
        state /= np.linalg.norm(state)
        image_a, image_b = _image(a, state.copy()), _image(b, state)
        if index == 0:
            phase = _phase_of(image_a, image_b)
        dev, exact_dev = _deviations(image_a, image_b, phase)
        deviation, plain = max(deviation, dev), max(plain, exact_dev)
    return Equality(deviation < atol, plain < atol, phase, deviation, "random-states")


def _check_unitary_request(circuit: Circuit) -> None:
    _check_request(circuit, MAX_UNITARY_WIRES)
    if circuit.measured_wires:
        raise UnsupportedOperationError(
            "a unitary is not defined with measurements; use Circuit.without_measurements()"
        )


def _as_array(value: object) -> NDArray[np.complex128]:
    try:
        return np.asarray(value, dtype=np.complex128)
    except (TypeError, ValueError) as error:
        raise TypeError(f"cannot compare a {type(value).__name__} as an array: {error}") from None


def _equal_circuits(a: Circuit, b: Circuit, atol: float, seed: int) -> Equality:
    for circuit in (a, b):
        validate(circuit)
        if circuit.measured_wires:
            raise UnsupportedOperationError(
                "circuits with measurements cannot be compared; use Circuit.without_measurements()"
            )
    if a.n_wires != b.n_wires:
        raise InvalidCircuitError(
            f"circuits on {a.n_wires} and {b.n_wires} wires cannot be compared"
        )
    if a.n_wires > MAX_STATEVECTOR_WIRES:
        raise LimitExceededError(
            f"{a.n_wires} wires requested; the limit for comparing circuits is "
            f"{MAX_STATEVECTOR_WIRES}"
        )
    if a.n_wires > MAX_UNITARY_WIRES:
        return _compare_on_random_states(a, b, atol, seed)
    return _compare(unitary(a), unitary(b), atol, "unitary")


def _equal_circuit_and_matrix(
    circuit: Circuit, target: NDArray[np.complex128], atol: float, circuit_first: bool
) -> Equality:
    if target.ndim != 2:
        raise TypeError(
            "a circuit is compared with a 2**n x 2**n matrix or another circuit, "
            f"not with an array of {target.ndim} dimension(s)"
        )
    _check_unitary_request(circuit)
    size = 2**circuit.n_wires
    if target.shape != (size, size):
        raise ValueError(
            f"a circuit of {circuit.n_wires} wires is a {size} x {size} matrix, "
            f"but the array has shape {target.shape}"
        )
    own = unitary(circuit)
    return _compare(*((own, target) if circuit_first else (target, own)), atol, "unitary")


def _check_atol(atol: object) -> None:
    if isinstance(atol, bool) or not isinstance(atol, Real) or not math.isfinite(atol) or atol <= 0:
        raise ValueError(f"atol must be a finite number greater than 0, got {atol!r}")


def equal(
    a: Circuit | ArrayLike, b: Circuit | ArrayLike, *, atol: float = ATOL, seed: int = 0
) -> Equality:
    """Whether ``a`` and ``b`` are equal up to a global phase (see ``domain.Equality``).

    - Two arrays of the same shape (states or matrices): compared directly (method
      ``"arrays"``); arrays of different shapes raise ``ValueError``.
    - A circuit and a ``2**n x 2**n`` matrix: the circuit's unitary against the matrix
      (``"unitary"``).
    - Two circuits on the same number of wires: their unitaries up to ``MAX_UNITARY_WIRES``
      wires (``"unitary"``); from there to ``MAX_STATEVECTOR_WIRES``, both applied to
      ``RANDOM_STATES`` random states drawn with ``seed``, with one global phase for all of
      them (``"random-states"``); above that, ``LimitExceededError``. Circuits with different
      numbers of wires raise ``InvalidCircuitError``.

    Circuits with measurements raise ``UnsupportedOperationError``; anything else raises
    ``TypeError``. An ``atol`` that is not a finite number greater than 0 raises ``ValueError``.
    """
    _check_atol(atol)
    if isinstance(a, Circuit) and isinstance(b, Circuit):
        return _equal_circuits(a, b, atol, seed)
    if isinstance(a, Circuit):
        return _equal_circuit_and_matrix(a, _as_array(b), atol, circuit_first=True)
    if isinstance(b, Circuit):
        return _equal_circuit_and_matrix(b, _as_array(a), atol, circuit_first=False)
    return _compare(_as_array(a), _as_array(b), atol, "arrays")
