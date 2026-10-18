"""What the package promises about itself: numpy only, no docingest, documented gates."""

import re
import subprocess
import sys
from importlib.metadata import requires
from pathlib import Path

from quantum_sim import GATES
from quantum_sim.labels import SPELLINGS

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


def test_every_label_spelling_is_in_the_conventions_document():
    text = (PACKAGE / "docs" / "conventions.md").read_text(encoding="utf-8")
    for gate, spellings in SPELLINGS.items():
        for spelling in spellings:
            assert f"`{spelling.replace('ARG', chr(92) + 'theta')}`" in text, (gate, spelling)


def test_the_spelling_rows_of_the_document_list_nothing_the_resolver_lacks():
    text = (PACKAGE / "docs" / "conventions.md").read_text(encoding="utf-8")
    documented = set()
    for row in re.findall(r"^\| [^|]+ \| [^|]+ \| (`[^|]+`) \|$", text, flags=re.MULTILINE):
        documented |= set(re.findall(r"`([^`]+)`", row))
    expected = {s.replace("ARG", chr(92) + "theta") for texts in SPELLINGS.values() for s in texts}
    assert documented == expected
