"""A small NumPy simulator of quantum circuits with stated conventions.

Conventions (Nielsen and Chuang): wire 1 is the top wire and the most significant bit, wires
are numbered from 1, and results are compared up to global phase. See ``docs/conventions.md``.
"""

from importlib.metadata import version

from quantum_sim.angles import Angle, parse_angle
from quantum_sim.domain import (
    ATOL,
    CONVENTIONS,
    GATES,
    MAX_OPS,
    MAX_STATEVECTOR_WIRES,
    MAX_UNITARY_WIRES,
    MEASURE,
    RANDOM_STATES,
    AngleError,
    Circuit,
    Equality,
    GateSpec,
    InvalidCircuitError,
    InvalidStateError,
    LabelError,
    LimitExceededError,
    Op,
    QuantumSimError,
    UnknownGateError,
    UnsupportedOperationError,
    validate,
)
from quantum_sim.engine import equal, probabilities, statevector, unitary
from quantum_sim.labels import ResolvedLabel, label_of, resolve_label

__version__ = version("quantum-sim")

__all__ = [
    "ATOL",
    "CONVENTIONS",
    "GATES",
    "MAX_OPS",
    "MAX_STATEVECTOR_WIRES",
    "MAX_UNITARY_WIRES",
    "MEASURE",
    "RANDOM_STATES",
    "Angle",
    "AngleError",
    "Circuit",
    "Equality",
    "GateSpec",
    "InvalidCircuitError",
    "InvalidStateError",
    "LabelError",
    "LimitExceededError",
    "Op",
    "QuantumSimError",
    "ResolvedLabel",
    "UnknownGateError",
    "UnsupportedOperationError",
    "__version__",
    "equal",
    "label_of",
    "parse_angle",
    "probabilities",
    "resolve_label",
    "statevector",
    "unitary",
    "validate",
]
