"""What the package promises about itself: numpy only, no docingest, documented gates."""

import re
import subprocess
import sys
from importlib.metadata import requires
from pathlib import Path

from quantum_sim import GATES

PACKAGE = Path(__file__).parents[1]


def test_the_only_runtime_dependency_is_numpy():
    assert requires("quantum-sim") == ["numpy>=2"]


def test_importing_the_package_loads_neither_docingest_nor_a_quantum_sdk():
    code = (
        "import sys, quantum_sim; "
        "print(sorted(m for m in sys.modules if m.split('.')[0] in "
        "{'docingest', 'docingest_index', 'qiskit', 'cirq', 'scipy'}))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_every_gate_is_in_the_conventions_document():
    text = (PACKAGE / "docs" / "conventions.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^\| `([a-z]+)` \|", text, flags=re.MULTILINE))
    assert documented == set(GATES)
