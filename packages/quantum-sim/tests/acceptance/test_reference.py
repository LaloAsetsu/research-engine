"""Random circuits against unitaries computed once by Qiskit (tests/fixtures, made by
tools/make_reference.py). Catches a misunderstanding shared by the engine and the naive
builder, since both are ours."""

import json
from functools import cache
from pathlib import Path

import numpy as np
import pytest
from circuits import GATE_NAMES

from quantum_sim import Circuit, Op

FIXTURE = Path(__file__).parents[1] / "fixtures" / "reference-circuits.json"
TOLERANCE = 1e-10


@cache
def reference() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def cases():
    for entry in reference()["circuits"]:
        circuit = Circuit(entry["n_wires"], tuple(Op(**o) for o in entry["ops"]))
        matrix = np.array(entry["unitary"], dtype=float)
        yield pytest.param(circuit, matrix[..., 0] + 1j * matrix[..., 1], id=f"c{entry['id']}")


def test_the_fixture_header_says_where_it_comes_from():
    header = reference()
    assert header["format"] == "quantum-sim-reference/1"
    assert header["library"] == "qiskit"
    assert header["version"]
    assert isinstance(header["seed"], int)
    assert "most significant" in header["wire_order"]


def test_the_fixture_covers_every_gate_controls_and_sizes():
    entries = reference()["circuits"]
    assert len(entries) >= 200
    ops = [o for e in entries for o in e["ops"]]
    assert {o["gate"] for o in ops} == set(GATE_NAMES)
    assert any(o["controls"] for o in ops)
    assert any(o["open_controls"] for o in ops)
    assert any(len(o["controls"]) + len(o["open_controls"]) >= 2 for o in ops)
    assert max(e["n_wires"] for e in entries) == 5
    assert all(len(e["ops"]) <= 30 for e in entries)


@pytest.mark.parametrize(("circuit", "expected"), list(cases()))
def test_unitary_equals_the_reference_exactly(sim, circuit, expected):
    np.testing.assert_allclose(sim.unitary(circuit), expected, atol=TOLERANCE, rtol=0)
