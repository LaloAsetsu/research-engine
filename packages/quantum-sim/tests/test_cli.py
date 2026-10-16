"""The quantum-sim command."""

import json
import subprocess
import sys

import pytest

from quantum_sim import GATES, __version__
from quantum_sim.entrypoints import main


def test_conventions_prints_the_table_as_json(capsys):
    assert main(["conventions"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["version"] == __version__
    assert [g["name"] for g in out["gates"]] == list(GATES)
    assert out["limits"] == {
        "statevector_wires": 20,
        "unitary_wires": 10,
        "ops": 10000,
        "random_states": 8,
        "atol": 1e-9,
    }
    assert "most significant" in out["conventions"]["wire_order"]


def test_a_command_is_required(capsys):
    with pytest.raises(SystemExit) as exit_:
        main([])
    assert exit_.value.code == 2


def test_version(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    assert capsys.readouterr().out.strip() == __version__


def test_the_console_script_is_installed():
    script = subprocess.run(
        [
            sys.executable,
            "-c",
            "from importlib.metadata import entry_points; print(entry_points(group='console_scripts')['quantum-sim'].value)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert script.stdout.strip() == "quantum_sim.entrypoints:main"
