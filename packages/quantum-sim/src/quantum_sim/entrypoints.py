"""The ``quantum-sim`` command. JSON in, JSON out; ``run`` and ``check`` come with the circuit
format."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from importlib.metadata import version

from quantum_sim.domain import (
    ATOL,
    CONVENTIONS,
    GATES,
    MAX_OPS,
    MAX_STATEVECTOR_WIRES,
    MAX_UNITARY_WIRES,
    RANDOM_STATES,
)


def conventions() -> dict[str, object]:
    """The conventions, the gate table and the limits, as printed by ``quantum-sim conventions``."""
    return {
        "version": version("quantum-sim"),
        "conventions": dict(CONVENTIONS),
        "gates": [
            {
                "name": spec.name,
                "targets": spec.n_targets,
                "params": spec.n_params,
                "definition": spec.definition,
            }
            for spec in GATES.values()
        ],
        "limits": {
            "statevector_wires": MAX_STATEVECTOR_WIRES,
            "unitary_wires": MAX_UNITARY_WIRES,
            "ops": MAX_OPS,
            "random_states": RANDOM_STATES,
            "atol": ATOL,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="quantum-sim", description="Simulate small quantum circuits (numpy only)."
    )
    parser.add_argument("--version", action="version", version=version("quantum-sim"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "conventions", help="print the conventions, the gate table and the limits as JSON"
    )
    parser.parse_args(argv)
    json.dump(conventions(), sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0
