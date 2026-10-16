"""The matrix of each gate in ``domain.GATES``, at given parameters.

Every matrix is exact, global phase included, because a controlled gate exposes the phase of its
target matrix. ``docs/conventions.md`` is the same table in prose.
"""

from __future__ import annotations

import cmath
import math
from collections.abc import Callable, Sequence

import numpy as np
from numpy.typing import NDArray

from quantum_sim.domain import GATES, UnknownGateError, check_params

Matrix = NDArray[np.complex128]

_R = math.sqrt(0.5)
_PAULI_X = np.array([[0, 1], [1, 0]], dtype=np.complex128)
_PAULI_Y = np.array([[0, -1j], [1j, 0]], dtype=np.complex128)
_PAULI_Z = np.array([[1, 0], [0, -1]], dtype=np.complex128)


def _diag(*entries: complex) -> Matrix:
    return np.diag(np.array(entries, dtype=np.complex128))


def _pair(pauli: Matrix) -> Matrix:
    return np.asarray(np.kron(pauli, pauli), dtype=np.complex128)


def _rotation(pauli: Matrix, theta: float) -> Matrix:
    """exp(-i theta P / 2) for a Pauli product P, which squares to the identity."""
    return math.cos(theta / 2) * np.eye(len(pauli)) - 1j * math.sin(theta / 2) * pauli


def _rk(k: float) -> Matrix:
    # 2 pi / 2^k, written with ldexp so a huge k gives 0 (the identity) instead of an overflow.
    angle = math.ldexp(2 * math.pi, -int(k)) if k < 2048 else 0.0
    return _diag(1, cmath.exp(1j * angle))


_FIXED: dict[str, Matrix] = {
    "id": np.eye(2, dtype=np.complex128),
    "h": np.array([[_R, _R], [_R, -_R]], dtype=np.complex128),
    "x": _PAULI_X,
    "y": _PAULI_Y,
    "z": _PAULI_Z,
    "s": _diag(1, 1j),
    "sdg": _diag(1, -1j),
    "t": _diag(1, cmath.exp(1j * math.pi / 4)),
    "tdg": _diag(1, cmath.exp(-1j * math.pi / 4)),
    "sx": np.array([[1 + 1j, 1 - 1j], [1 - 1j, 1 + 1j]], dtype=np.complex128) / 2,
    "sxdg": np.array([[1 - 1j, 1 + 1j], [1 + 1j, 1 - 1j]], dtype=np.complex128) / 2,
    "swap": np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], dtype=np.complex128),
    "iswap": np.array(
        [[1, 0, 0, 0], [0, 0, 1j, 0], [0, 1j, 0, 0], [0, 0, 0, 1]], dtype=np.complex128
    ),
}

_PARAMETRIC: dict[str, Callable[[float], Matrix]] = {
    "rx": lambda theta: _rotation(_PAULI_X, theta),
    "ry": lambda theta: _rotation(_PAULI_Y, theta),
    "rz": lambda theta: _diag(cmath.exp(-0.5j * theta), cmath.exp(0.5j * theta)),
    "p": lambda theta: _diag(1, cmath.exp(1j * theta)),
    "rk": _rk,
    "rxx": lambda theta: _rotation(_pair(_PAULI_X), theta),
    "ryy": lambda theta: _rotation(_pair(_PAULI_Y), theta),
    "rzz": lambda theta: _rotation(_pair(_PAULI_Z), theta),
}


def matrix(name: str, params: Sequence[float] = ()) -> Matrix:
    """The uncontrolled matrix of gate ``name`` (2 x 2, or 4 x 4 for two target wires).

    Follows the definitions of ``domain.GATES`` exactly, global phase included, since a
    controlled gate exposes it. For a two-wire gate the first target wire is the most
    significant bit. Returns a new complex128 array.

    Raises ``UnknownGateError`` for a name not in ``GATES`` (``MEASURE`` included: it has no
    matrix) and ``InvalidCircuitError`` for a wrong number of parameters, a parameter that is
    not finite, or an ``rk`` parameter that is not a positive integer.
    """
    if not isinstance(name, str) or name not in GATES:
        raise UnknownGateError(f"unknown gate {name!r} (names are exact and lowercase)")
    check_params(name, params)
    if name in _FIXED:
        return _FIXED[name].copy()
    return np.asarray(_PARAMETRIC[name](params[0]), dtype=np.complex128)
