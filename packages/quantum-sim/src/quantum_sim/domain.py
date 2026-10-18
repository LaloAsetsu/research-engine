"""Circuits, the gate table, the conventions, the limits and the errors. Standard library only.

A circuit is a number of wires and a sequence of operations applied left to right. Wires are
numbered from 1 (the top wire of a drawing, the most significant bit of a basis label) in every
user-facing structure; only the engine counts from 0. ``docs/conventions.md`` is the same
table in prose.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from numbers import Integral, Real
from typing import Literal

# --- Errors ------------------------------------------------------------------------------------


class QuantumSimError(Exception):
    """Base class of every error this package raises on purpose."""


class InvalidCircuitError(QuantumSimError, ValueError):
    """The circuit is malformed: a wire out of range or used twice, a wrong number of target
    wires or parameters for the gate, a parameter that is not finite, wires of different
    circuits that do not match."""


class UnknownGateError(InvalidCircuitError):
    """The gate name is not in ``GATES`` (names are exact and lowercase; nothing is guessed)."""


class UnsupportedOperationError(QuantumSimError):
    """The circuit is well formed but uses something version 1 does not simulate: an operation
    on a wire after its measurement, a classically controlled operation, or a measurement where
    a unitary is asked for."""


class LimitExceededError(QuantumSimError):
    """The request is larger than a limit (``MAX_STATEVECTOR_WIRES``, ``MAX_UNITARY_WIRES``,
    ``MAX_OPS``). Raised from the circuit's description, before any array is created."""


class InvalidStateError(QuantumSimError, ValueError):
    """The input state has the wrong length, is not normalized, or is a malformed basis label."""


class AngleError(QuantumSimError, ValueError):
    """An angle expression is not in the grammar, divides by zero, overflows, or lacks a value
    for one of its symbols."""


class LabelError(QuantumSimError, ValueError):
    """A gate label is not one the resolver knows. ``ambiguous`` is True when the label is a
    known family whose meaning depends on the paper (``U_1``, ``R(\\theta)``, ...): it is refused
    and never mapped."""

    def __init__(self, message: str, *, ambiguous: bool = False) -> None:
        super().__init__(message)
        self.ambiguous = ambiguous


# --- Limits ------------------------------------------------------------------------------------

MAX_STATEVECTOR_WIRES = 20  # 16 MiB of complex128
MAX_UNITARY_WIRES = 10  # 16 MiB of complex128
MAX_OPS = 10_000
RANDOM_STATES = 8  # states used by `equal` for 11 to 20 wires
ATOL = 1e-9  # tolerance of `equal` and of the input-state norm

# --- Gate table --------------------------------------------------------------------------------

MEASURE = "measure"


@dataclass(frozen=True)
class GateSpec:
    """One named gate: how many target wires and parameters it takes, and its matrix as text.

    Multi-wire matrices are written in the order of the op's ``wires``: the first target wire is
    the most significant bit, as everywhere else.
    """

    name: str
    n_targets: int
    n_params: int
    definition: str


_SPECS = (
    GateSpec("id", 1, 0, "I = [[1, 0], [0, 1]]"),
    GateSpec("h", 1, 0, "H = (1/sqrt 2) [[1, 1], [1, -1]]"),
    GateSpec("x", 1, 0, "X = [[0, 1], [1, 0]]"),
    GateSpec("y", 1, 0, "Y = [[0, -i], [i, 0]]"),
    GateSpec("z", 1, 0, "Z = [[1, 0], [0, -1]]"),
    GateSpec("s", 1, 0, "S = P(pi/2) = diag(1, i)"),
    GateSpec("sdg", 1, 0, "S^dagger = diag(1, -i)"),
    GateSpec("t", 1, 0, "T = P(pi/4) = diag(1, e^{i pi/4})"),
    GateSpec("tdg", 1, 0, "T^dagger = diag(1, e^{-i pi/4})"),
    GateSpec("sx", 1, 0, "sqrt(X) = (1/2) [[1+i, 1-i], [1-i, 1+i]]"),
    GateSpec("sxdg", 1, 0, "sqrt(X)^dagger = (1/2) [[1-i, 1+i], [1+i, 1-i]]"),
    GateSpec("rx", 1, 1, "R_x(theta) = exp(-i theta X / 2)"),
    GateSpec("ry", 1, 1, "R_y(theta) = exp(-i theta Y / 2)"),
    GateSpec("rz", 1, 1, "R_z(theta) = exp(-i theta Z / 2) = diag(e^{-i theta/2}, e^{i theta/2})"),
    GateSpec("p", 1, 1, "P(theta) = diag(1, e^{i theta})"),
    GateSpec("rk", 1, 1, "R_k = diag(1, e^{2 pi i / 2^k}), k a positive integer (the QFT's R_k)"),
    GateSpec("rxx", 2, 1, "R_xx(theta) = exp(-i theta X⊗X / 2)"),
    GateSpec("ryy", 2, 1, "R_yy(theta) = exp(-i theta Y⊗Y / 2)"),
    GateSpec("rzz", 2, 1, "R_zz(theta) = exp(-i theta Z⊗Z / 2)"),
    GateSpec("swap", 2, 0, "SWAP: |ab> -> |ba>"),
    GateSpec("iswap", 2, 0, "iSWAP = [[1, 0, 0, 0], [0, 0, i, 0], [0, i, 0, 0], [0, 0, 0, 1]]"),
)

GATES: Mapping[str, GateSpec] = {spec.name: spec for spec in _SPECS}
"""Every gate the simulator knows, by its exact name. Any gate takes any number of controls."""

CONVENTIONS: Mapping[str, str] = {
    "source": "Nielsen and Chuang, Quantum Computation and Quantum Information",
    "wire_order": (
        "wire 1 is the top wire of the drawing and the most significant bit: amplitude k "
        "belongs to the basis label |w1 w2 ... wn> that is k in binary (opposite of Qiskit)"
    ),
    "wire_numbers": "1-based in every user-facing structure; 0-based only inside the engine",
    "rotations": "R_a(theta) = exp(-i theta sigma_a / 2) for a in x, y, z",
    "two_qubit_rotations": "R_aa(theta) = exp(-i theta sigma_a ⊗ sigma_a / 2) for a in x, y, z",
    "phase": "P(theta) = diag(1, e^{i theta}); S = P(pi/2); T = P(pi/4)",
    "sqrt_x": "sqrt(X) = (1/2) [[1+i, 1-i], [1-i, 1+i]]",
    "qft_rk": "R_k = diag(1, e^{2 pi i / 2^k})",
    "controls": (
        "a closed control acts on |1>, an open control on |0>; any gate takes any number of "
        "controls, and controls are never part of the gate's own matrix"
    ),
    "equality": (
        "equal up to global phase: max |a - e^{i phi} b| < 1e-9, with phi taken from the "
        "largest entry of b; exact equality is reported too"
    ),
    "input_state": "|0...0> unless the request gives a basis label or a list of amplitudes",
    "step_order": "operations apply left to right, in the order of Circuit.ops",
    "measurement": (
        "version 1: computational-basis measurement at the end of a wire only; no operation "
        "may touch a wire after its measurement, and classically controlled operations are "
        "refused"
    ),
}

# --- Circuits ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Op:
    """One operation: a gate from ``GATES`` (or ``MEASURE``) on target wires, with controls.

    ``wires`` are the target wires in the order the gate's matrix uses. ``controls`` act on |1>,
    ``open_controls`` on |0>. ``classical_controls`` are measured wires whose outcome would
    control the operation; version 1 refuses them. Lists are stored as tuples, so an op built
    from JSON compares equal to one built by hand.
    """

    gate: str
    wires: tuple[int, ...]
    controls: tuple[int, ...] = ()
    open_controls: tuple[int, ...] = ()
    params: tuple[float, ...] = ()
    classical_controls: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        for name in ("wires", "controls", "open_controls", "params", "classical_controls"):
            object.__setattr__(self, name, tuple(getattr(self, name)))

    @property
    def is_measurement(self) -> bool:
        return self.gate == MEASURE

    @property
    def all_wires(self) -> tuple[int, ...]:
        """Targets, then closed controls, then open controls (classical controls excluded)."""
        return self.wires + self.controls + self.open_controls


@dataclass(frozen=True)
class Circuit:
    """``n_wires`` wires and ``ops`` applied left to right."""

    n_wires: int
    ops: tuple[Op, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "ops", tuple(self.ops))

    @property
    def measured_wires(self) -> tuple[int, ...]:
        """The wires with a measurement, in increasing order."""
        return tuple(sorted({w for op in self.ops if op.is_measurement for w in op.wires}))

    def without_measurements(self) -> Circuit:
        """The same circuit with its measurements dropped (its unitary part)."""
        return Circuit(self.n_wires, tuple(op for op in self.ops if not op.is_measurement))


def _is_int(value: object) -> bool:
    return isinstance(value, Integral) and not isinstance(value, bool)


def _is_finite_real(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, Real):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # an int too large for a float
        return False


def _short(value: object) -> str:
    """``repr(value)`` cut to 40 characters, so a huge integer does not fill an error message."""
    text = repr(value)
    return text if len(text) <= 40 else text[:37] + "..."


def check_params(name: str, params: Sequence[float]) -> None:
    """Raise ``InvalidCircuitError`` unless ``params`` fit gate ``name`` (a key of ``GATES``).

    The count must match the gate, every parameter must be a finite real number, and the ``rk``
    parameter must be a positive integer of any size (a float with an integral value such as
    ``3.0`` counts, since JSON does not tell them apart).
    """
    spec = GATES[name]
    if len(params) != spec.n_params:
        raise InvalidCircuitError(
            f"gate {name!r} takes {spec.n_params} parameter(s), got {len(params)}"
        )
    for value in params:
        if not (name == "rk" and _is_int(value)) and not _is_finite_real(value):
            raise InvalidCircuitError(f"gate {name!r}: {_short(value)} is not a finite real number")
    if name == "rk":
        k = params[0]
        if not (_is_int(k) or float(k).is_integer()) or k < 1:
            raise InvalidCircuitError(f"gate 'rk' takes a positive integer k, got {_short(k)}")


def _check_wires(op: Op, position: int, n_wires: int) -> None:
    groups = (op.wires, op.controls, op.open_controls, op.classical_controls)
    seen: set[int] = set()
    for wire in (w for group in groups for w in group):
        if not _is_int(wire) or not 1 <= wire <= n_wires:
            raise InvalidCircuitError(
                f"operation {position}: wire {wire!r} is not an integer from 1 to {n_wires}"
            )
        if wire in seen:
            raise InvalidCircuitError(f"operation {position}: wire {wire} is used twice")
        seen.add(wire)


def _check_op(op: Op, position: int, n_wires: int) -> None:
    """Everything about one op that does not depend on the other ops."""
    if op.is_measurement:
        if len(op.wires) != 1 or op.controls or op.open_controls or op.params:
            raise InvalidCircuitError(
                f"operation {position}: a measurement has one wire, no controls and no parameters"
            )
    elif not isinstance(op.gate, str) or op.gate not in GATES:
        raise UnknownGateError(
            f"operation {position}: unknown gate {op.gate!r} (names are exact and lowercase)"
        )
    else:
        spec = GATES[op.gate]
        if len(op.wires) != spec.n_targets:
            raise InvalidCircuitError(
                f"operation {position}: gate {op.gate!r} acts on {spec.n_targets} wire(s), "
                f"got {len(op.wires)}"
            )
        try:
            check_params(op.gate, op.params)
        except InvalidCircuitError as error:
            raise InvalidCircuitError(f"operation {position}: {error}") from None
    _check_wires(op, position, n_wires)


def validate(circuit: Circuit) -> None:
    """Raise if ``circuit`` cannot be simulated; return None if it can (size limits aside).

    Checks, from the description only:

    - ``circuit`` is a ``Circuit``, ``n_wires`` is an int of at least 1, and ``len(ops) <= MAX_OPS``
      (``InvalidCircuitError``, ``LimitExceededError``);
    - every op is an ``Op`` (``InvalidCircuitError``);
    - each gate name is in ``GATES`` or is ``MEASURE`` (``UnknownGateError``);
    - each op has the gate's number of target wires and of parameters, every parameter is a
      finite real number, and the ``rk`` parameter is a positive integer
      (``InvalidCircuitError``);
    - every wire (targets, controls, open controls, classical controls) is an int from 1 to
      ``n_wires``, and no wire appears twice in one op (``InvalidCircuitError``);
    - a measurement has one target, no controls and no parameters (``InvalidCircuitError``);
    - no op touches a wire after that wire's measurement, and no op has classical controls
      (``UnsupportedOperationError``).

    The wire-count limits depend on what is asked for, so the engine checks them.
    """
    if not isinstance(circuit, Circuit):
        raise InvalidCircuitError(f"expected a Circuit, got {type(circuit).__name__}")
    if not _is_int(circuit.n_wires) or circuit.n_wires < 1:
        raise InvalidCircuitError(f"a circuit needs at least 1 wire, got {circuit.n_wires!r}")
    if len(circuit.ops) > MAX_OPS:
        raise LimitExceededError(
            f"{len(circuit.ops):,} operations; the limit is {MAX_OPS:,} per circuit"
        )
    measured: set[int] = set()
    for position, op in enumerate(circuit.ops, start=1):
        if not isinstance(op, Op):
            raise InvalidCircuitError(
                f"operation {position}: expected an Op, got {type(op).__name__}"
            )
        _check_op(op, position, circuit.n_wires)
        if op.classical_controls:
            raise UnsupportedOperationError(
                f"operation {position}: classically controlled operations are not simulated "
                "in version 1"
            )
        for wire in op.all_wires:
            if wire in measured:
                raise UnsupportedOperationError(
                    f"operation {position}: wire {wire} is used after its measurement; version 1 "
                    "measures at the end of a wire only"
                )
        if op.is_measurement:
            measured.add(op.wires[0])


# --- Results -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Equality:
    """The answer of ``engine.equal``. True in a boolean context when ``equal`` is.

    ``phase`` is phi in (-pi, pi] with a ≈ e^{i phi} b, taken from the largest entry of b (of the
    first random state's image, for ``"random-states"``).
    ``max_deviation`` is max |a - e^{i phi} b| over the compared entries; ``equal`` is
    ``max_deviation < atol`` and ``exact`` is max |a - b| < atol. ``method`` says what was
    compared: the two arrays given, the two unitaries, or the images of ``RANDOM_STATES``
    random states.
    """

    equal: bool
    exact: bool
    phase: float
    max_deviation: float
    method: Literal["arrays", "unitary", "random-states"]

    def __bool__(self) -> bool:
        return self.equal
