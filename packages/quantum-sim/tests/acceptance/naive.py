"""A second, deliberately slow implementation that the engine is checked against.

Gate matrices are literals written from docs/conventions.md, not taken from
``quantum_sim.gates``. A controlled gate is built on its own wires as
``(I - P) ⊗ I + P ⊗ G`` with ``P`` the Kronecker product of the control projectors, padded
with identities by a Kronecker product, and moved to the circuit's wire order by a
permutation matrix. Nothing here uses ``tensordot`` or reshapes a state.
"""

from __future__ import annotations

import cmath
import math
from collections.abc import Sequence
from functools import reduce

import numpy as np

from quantum_sim import Circuit, Op

I2 = np.eye(2, dtype=complex)
X = np.array([[0, 1], [1, 0]], dtype=complex)
Y = np.array([[0, -1j], [1j, 0]], dtype=complex)
Z = np.array([[1, 0], [0, -1]], dtype=complex)
H = np.array([[1, 1], [1, -1]], dtype=complex) / math.sqrt(2)
KET0 = np.array([[1, 0], [0, 0]], dtype=complex)  # |0><0|
KET1 = np.array([[0, 0], [0, 1]], dtype=complex)  # |1><1|


def diag(*entries: complex) -> np.ndarray:
    return np.diag(np.array(entries, dtype=complex))


def literal(name: str, params: Sequence[float] = ()) -> np.ndarray:  # noqa: PLR0911
    """The matrix of a gate, typed in from the conventions table."""
    e = cmath.exp
    if name in FIXED:
        assert not params
        return FIXED[name].copy()
    (theta,) = params
    c, s = math.cos(theta / 2), math.sin(theta / 2)
    match name:
        case "rx":
            return np.array([[c, -1j * s], [-1j * s, c]], dtype=complex)
        case "ry":
            return np.array([[c, -s], [s, c]], dtype=complex)
        case "rz":
            return diag(e(-1j * theta / 2), e(1j * theta / 2))
        case "p":
            return diag(1, e(1j * theta))
        case "rk":
            return diag(1, e(2j * math.pi / 2**theta))
        case "rxx":
            return np.array(
                [
                    [c, 0, 0, -1j * s],
                    [0, c, -1j * s, 0],
                    [0, -1j * s, c, 0],
                    [-1j * s, 0, 0, c],
                ],
                dtype=complex,
            )
        case "ryy":
            return np.array(
                [
                    [c, 0, 0, 1j * s],
                    [0, c, -1j * s, 0],
                    [0, -1j * s, c, 0],
                    [1j * s, 0, 0, c],
                ],
                dtype=complex,
            )
        case "rzz":
            m, p = e(-1j * theta / 2), e(1j * theta / 2)
            return diag(m, p, p, m)
    raise KeyError(name)


FIXED = {
    "id": I2,
    "h": H,
    "x": X,
    "y": Y,
    "z": Z,
    "s": diag(1, 1j),
    "sdg": diag(1, -1j),
    "t": diag(1, cmath.exp(1j * math.pi / 4)),
    "tdg": diag(1, cmath.exp(-1j * math.pi / 4)),
    "sx": np.array([[1 + 1j, 1 - 1j], [1 - 1j, 1 + 1j]], dtype=complex) / 2,
    "sxdg": np.array([[1 - 1j, 1 + 1j], [1 + 1j, 1 - 1j]], dtype=complex) / 2,
    "swap": np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], dtype=complex),
    "iswap": np.array([[1, 0, 0, 0], [0, 0, 1j, 0], [0, 1j, 0, 0], [0, 0, 0, 1]], dtype=complex),
}

PARAMETRIC = ("rx", "ry", "rz", "p", "rk", "rxx", "ryy", "rzz")
TWO_WIRE = ("rxx", "ryy", "rzz", "swap", "iswap")


def kron(*factors: np.ndarray) -> np.ndarray:
    return reduce(np.kron, factors, np.eye(1, dtype=complex))


def permutation(n: int, order: Sequence[int]) -> np.ndarray:
    """The matrix that takes a basis state written with its bits in ``order`` (wire numbers,
    one per position) to the same state written in wire order 1..n."""
    size = 2**n
    perm = np.zeros((size, size), dtype=complex)
    for column in range(size):
        bits = [(column >> (n - 1 - position)) & 1 for position in range(n)]
        by_wire = dict(zip(order, bits, strict=True))
        row = sum(by_wire[w] << (n - w) for w in range(1, n + 1))
        perm[row, column] = 1
    return perm


def embed(n: int, op: Op) -> np.ndarray:
    """The full ``2**n x 2**n`` matrix of one op."""
    gate = literal(op.gate, op.params)
    projector = kron(*([KET1] * len(op.controls) + [KET0] * len(op.open_controls)))
    n_controls = len(op.controls) + len(op.open_controls)
    local = kron(np.eye(2**n_controls) - projector, np.eye(len(gate))) + kron(projector, gate)
    order = [*op.controls, *op.open_controls, *op.wires]
    rest = [w for w in range(1, n + 1) if w not in order]
    perm = permutation(n, order + rest)
    return perm @ kron(local, np.eye(2 ** len(rest))) @ perm.T


def unitary(circuit: Circuit) -> np.ndarray:
    result = np.eye(2**circuit.n_wires, dtype=complex)
    for op in circuit.ops:
        assert not op.is_measurement, "the naive builder has no measurements"
        result = embed(circuit.n_wires, op) @ result
    return result


def basis(label: str) -> np.ndarray:
    state = np.zeros(2 ** len(label), dtype=complex)
    state[int(label, 2)] = 1
    return state


def as_state(n: int, input_state: str | Sequence[complex] | np.ndarray | None) -> np.ndarray:
    if input_state is None:
        return basis("0" * n)
    if isinstance(input_state, str):
        return basis(input_state)
    return np.asarray(input_state, dtype=complex)


def statevector(
    circuit: Circuit, input_state: str | Sequence[complex] | np.ndarray | None = None
) -> np.ndarray:
    return unitary(circuit) @ as_state(circuit.n_wires, input_state)


def equal_up_to_phase(a: np.ndarray, b: np.ndarray, atol: float = 1e-9) -> bool:
    a, b = np.asarray(a), np.asarray(b)
    if a.shape != b.shape:
        return False
    k = np.unravel_index(np.argmax(np.abs(b)), b.shape)
    if abs(b[k]) < atol:
        return bool(np.max(np.abs(a)) < atol)
    if abs(a[k]) < atol:
        return False
    phase = a[k] / b[k]
    phase /= abs(phase)
    return bool(np.max(np.abs(a - phase * b)) < atol)
