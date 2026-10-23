"""C7: every LaTeX fixture of quantum-sim goes through the API, parse then check, and gets the
answer that quantum-sim's own bridge tests (``packages/quantum-sim/tests/test_bridge.py``) expect.

The sources are read from quantum-sim's fixtures folder, so the two packages cannot drift apart.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from helpers import sign_in_as_member
from quantum_sim import check as quantum_check
from quantum_sim import run as quantum_run

FIXTURES = Path(__file__).resolve().parents[2] / "quantum-sim" / "tests" / "fixtures" / "bridge"
SOURCES = sorted(FIXTURES.glob("*.tex"))
NAMES = [source.stem for source in SOURCES]

# Copied by hand from REFUSED in quantum-sim's test_bridge.py; keep the two in step. What that file
# expects to be refused: a postselection, a bundle or a row of dots, a circuit the reader left
# partial, an angle that may be a function, a ket drawn after a gate, a brace sum.
REFUSED = {
    "bundle_quantikz",
    "dots_quantikz",
    "partial_wire_dots_quantikz",
    "postselect_quantikz",
    "postselect_qcircuit",
    "postselect_macro_quantikz",
    "postselect_macro_qcircuit",
    "postselect_word_quantikz",
    "postselect_slice_quantikz",
    "postselect_slice_qcircuit",
    "postselect_ket_quantikz",
    "postselect_ket_qcircuit",
    "function_angle_quantikz",
    "function_angle_qcircuit",
    "late_lstick_quantikz",
    "late_lstick_qcircuit",
    "late_lstick_box_quantikz",
    "late_lstick_cell_quantikz",
    "brace_sum_quantikz",
    "brace_sum_qcircuit",
}
# A measured circuit runs, but it has no unitary and no single state to check.
MEASURED = {"measure_quantikz"}
S2 = 1 / math.sqrt(2)
H = np.array([[1, 1], [1, -1]]) * S2
I2 = np.eye(2)
CNOT = np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 0, 1], [0, 0, 1, 0]])
HAND_UNITARIES = {
    "bell": CNOT @ np.kron(H, I2),
    "cz": np.diag([1, 1, 1, -1]),
    "toffoli": np.eye(8)[[0, 1, 2, 3, 4, 5, 7, 6]],
    "ry": np.array([[1, -1], [1, 1]]) * S2,
    "cnot_reversed": np.eye(4)[[0, 3, 2, 1]],
}


def test_the_fixtures_are_there() -> None:
    assert len(SOURCES) == 37 and (FIXTURES / "bell_quantikz.json").is_file()


@pytest.fixture
def client(client: TestClient) -> TestClient:
    return sign_in_as_member(client)


def parse(client: TestClient, name: str) -> dict[str, Any]:
    latex = (FIXTURES / f"{name}.tex").read_text(encoding="utf-8")
    response = client.post("/api/v1/circuits/parse", json={"latex": latex})
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def parse_one(client: TestClient, name: str) -> dict[str, Any]:
    circuits = parse(client, name)["circuits"]
    assert len(circuits) == 1, f"{name} gives {len(circuits)} circuits, these checks take one"
    document: dict[str, Any] = circuits[0]
    return document


@pytest.mark.parametrize("name", NAMES)
def test_parse_gives_the_saved_document(client: TestClient, name: str) -> None:
    body = parse(client, name)
    assert body["status"] == ("partial" if name.startswith("partial_") else "parsed")
    saved = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    assert body["circuits"] == [saved]


@pytest.mark.parametrize("name", NAMES)
def test_run_and_check_answer_as_the_simulator_expects(client: TestClient, name: str) -> None:
    document = parse_one(client, name)
    as_text = json.dumps(document)
    ran = client.post("/api/v1/circuits/run", json={"circuit_json": as_text})
    assert ran.status_code == 200
    assert ran.json()["status"] == ("refused" if name in REFUSED else "ok")

    # a drawn equality is checked left against right; any other circuit against itself
    body: dict[str, Any] = {"circuit_json": as_text}
    if len(document["parts"]) == 1:
        body["target"] = {"circuit_json": as_text}
    checked = client.post("/api/v1/circuits/check", json=body)
    assert checked.status_code == 200
    if name in REFUSED or name in MEASURED:
        assert checked.json()["status"] == "refused"
    else:
        assert checked.json()["status"] == "ok" and checked.json()["equal_to_target"] is True


@pytest.mark.parametrize("name", NAMES)
def test_the_api_result_is_the_simulators_result(client: TestClient, name: str) -> None:
    document = parse_one(client, name)
    ran = client.post("/api/v1/circuits/run", json={"circuit_json": json.dumps(document)})
    assert ran.json() == json.loads(json.dumps(quantum_run({"circuit": document})))


@pytest.mark.parametrize("name", NAMES)
def test_the_api_check_is_the_simulators_check(client: TestClient, name: str) -> None:
    document = parse_one(client, name)
    as_text = json.dumps(document)
    body: dict[str, Any] = {"circuit_json": as_text}
    direct: dict[str, Any] = {"circuit": document}
    if len(document["parts"]) == 1:
        body["target"] = {"circuit_json": as_text}
        direct = {"a": document, "b": document}
    checked = client.post("/api/v1/circuits/check", json=body)
    assert checked.json() == json.loads(json.dumps(quantum_check(direct)))


@pytest.mark.parametrize("kind", ["quantikz", "qcircuit"])
@pytest.mark.parametrize("base", sorted(HAND_UNITARIES))
def test_the_unitary_is_the_one_written_by_hand(client: TestClient, base: str, kind: str) -> None:
    document = parse_one(client, f"{base}_{kind}")
    expected = HAND_UNITARIES[base]
    request = {"circuit_json": json.dumps(document), "target": {"unitary": expected.tolist()}}
    right = client.post("/api/v1/circuits/check", json=request).json()
    assert right["status"] == "ok" and right["equal_to_target"] is True
    wrong = expected[::-1] if base != "ry" else expected.T
    request["target"] = {"unitary": wrong.tolist()}
    assert client.post("/api/v1/circuits/check", json=request).json()["equal_to_target"] is False


def test_the_drawn_identities_are_equal(client: TestClient) -> None:
    for name in ("hzh_equals_x_quantikz", "cnot_from_cz_quantikz"):
        document = parse_one(client, name)
        body = client.post(
            "/api/v1/circuits/check", json={"circuit_json": json.dumps(document)}
        ).json()
        assert body["equal_to_target"] is True
        assert body["comparisons"][0]["between"] == ["part 1", "part 2"]


def test_the_qft_is_the_dft(client: TestClient) -> None:
    document = parse_one(client, "qft3_quantikz")
    omega = np.exp(2j * np.pi / 8)
    dft = np.array([[omega ** (j * k) for k in range(8)] for j in range(8)]) / math.sqrt(8)
    matrix = [[[float(z.real), float(z.imag)] for z in row] for row in dft]
    body = client.post(
        "/api/v1/circuits/check",
        json={"circuit_json": json.dumps(document), "target": {"unitary": matrix}},
    ).json()
    assert body["equal_to_target"] is True


def test_a_bundle_is_refused_with_the_reason_and_a_postselection_too(client: TestClient) -> None:
    for name, reason in (("bundle_quantikz", "bundle m"), ("postselect_quantikz", "output legend")):
        document = parse_one(client, name)
        body = client.post(
            "/api/v1/circuits/run", json={"circuit_json": json.dumps(document)}
        ).json()
        assert body["status"] == "refused" and any(reason in r for r in body["refused"])
