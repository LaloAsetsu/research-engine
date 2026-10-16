"""Statevector and unitary simulation, outcome probabilities and equality up to global phase.

A stub: the engine change writes it, against ``tests/acceptance``. Every function validates
the circuit (``domain.validate``) and checks the limits from the circuit's description before
it creates any array, so a refused request costs no memory. Gates are meant to be applied with
``numpy.tensordot`` over the gate's wires (O(2^n) per gate on a state, O(4^n) on a unitary).
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from numpy.typing import ArrayLike, NDArray

from quantum_sim.domain import ATOL, Circuit, Equality

StateInput = str | ArrayLike | None
"""``None`` for |0...0>, a basis label such as ``"0110"`` (wire 1 first), or ``2**n``
amplitudes whose norm is 1 within ``ATOL``."""


def statevector(circuit: Circuit, input_state: StateInput = None) -> NDArray[np.complex128]:
    """The state after ``circuit`` acts on ``input_state``, as ``2**n_wires`` amplitudes.

    Amplitude ``k`` belongs to the basis label of ``k`` in binary with wire 1 as the most
    significant bit. Measurements (at the end of their wires) are not applied: the result is
    the state just before them.

    Raises ``LimitExceededError`` above ``MAX_STATEVECTOR_WIRES`` wires or ``MAX_OPS``
    operations, ``InvalidStateError`` for a bad input state, and whatever
    ``domain.validate`` raises.
    """
    raise NotImplementedError("quantum_sim.engine.statevector is not built yet")


def unitary(circuit: Circuit) -> NDArray[np.complex128]:
    """The ``2**n x 2**n`` matrix of ``circuit``, rows and columns indexed as in
    ``statevector``; ``unitary(c) @ v`` equals ``statevector(c, v)``.

    Raises ``LimitExceededError`` above ``MAX_UNITARY_WIRES`` wires or ``MAX_OPS``
    operations, ``UnsupportedOperationError`` if the circuit has a measurement (use
    ``Circuit.without_measurements``), and whatever ``domain.validate`` raises.
    """
    raise NotImplementedError("quantum_sim.engine.unitary is not built yet")


def probabilities(
    circuit: Circuit, input_state: StateInput = None, wires: Sequence[int] | None = None
) -> NDArray[np.float64]:
    """Outcome probabilities of measuring ``wires`` after ``circuit``, as ``2**len(wires)``
    numbers that sum to 1.

    Outcome ``k`` is the binary of ``k`` over ``wires`` in the order given (the first wire
    given is the most significant bit). ``wires=None`` means the measured wires of the circuit
    in increasing order, or every wire if it has no measurement.

    Raises ``InvalidCircuitError`` for a wire out of range or given twice, and what
    ``statevector`` raises.
    """
    raise NotImplementedError("quantum_sim.engine.probabilities is not built yet")


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
    ``TypeError``.
    """
    raise NotImplementedError("quantum_sim.engine.equal is not built yet")
