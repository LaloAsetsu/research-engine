"""The matrix of each gate in ``domain.GATES``, at given parameters.

A stub: the engine change writes it, against ``tests/acceptance/test_gates.py``.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray


def matrix(name: str, params: Sequence[float] = ()) -> NDArray[np.complex128]:
    """The uncontrolled matrix of gate ``name`` (2 x 2, or 4 x 4 for two target wires).

    Follows the definitions of ``domain.GATES`` exactly, global phase included, since a
    controlled gate exposes it. For a two-wire gate the first target wire is the most
    significant bit. Returns a new complex128 array.

    Raises ``UnknownGateError`` for a name not in ``GATES`` (``MEASURE`` included: it has no
    matrix) and ``InvalidCircuitError`` for a wrong number of parameters, a parameter that is
    not finite, or an ``rk`` parameter that is not a positive integer.
    """
    raise NotImplementedError("quantum_sim.gates.matrix is not built yet")
