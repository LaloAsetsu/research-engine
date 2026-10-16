"""Write tests/fixtures/reference-circuits.json: random circuits and their unitaries from Qiskit.

The fixture lets the tests compare the engine with an independent library without making that
library a dependency. Run it once, in a throwaway environment outside the repository, so that
Qiskit never enters uv.lock:

    uv venv /tmp/qsim-ref && VIRTUAL_ENV=/tmp/qsim-ref uv pip install qiskit
    /tmp/qsim-ref/bin/python tools/make_reference.py      # from packages/quantum-sim

Qiskit is little-endian (qubit 0 is the least significant bit). Wire w of our circuit is put
on Qiskit qubit w - 1, and the operator's qubit order is then reversed, so the stored matrices
use our order: wire 1 is the most significant bit. Open controls are written as X before and
after a closed control, which does not depend on Qiskit's ctrl_state bit order. The script
checks the conversion on a CNOT before it writes anything.
"""

from __future__ import annotations

import json
import math
import platform
import sys
from pathlib import Path

import numpy as np
import qiskit
from qiskit import QuantumCircuit
from qiskit.circuit import library as lib
from qiskit.quantum_info import Operator

SEED = 20261015
N_CIRCUITS = 240
MAX_WIRES = 5
MAX_OPS = 30
DIGITS = 13  # rounding of the stored entries; the test's tolerance is 1e-10
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "reference-circuits.json"

FIXED = {
    "id": lib.IGate,
    "h": lib.HGate,
    "x": lib.XGate,
    "y": lib.YGate,
    "z": lib.ZGate,
    "s": lib.SGate,
    "sdg": lib.SdgGate,
    "t": lib.TGate,
    "tdg": lib.TdgGate,
    "sx": lib.SXGate,
    "sxdg": lib.SXdgGate,
    "swap": lib.SwapGate,
    "iswap": lib.iSwapGate,
}
ANGLED = {
    "rx": lib.RXGate,
    "ry": lib.RYGate,
    "rz": lib.RZGate,
    "p": lib.PhaseGate,
    "rxx": lib.RXXGate,
    "ryy": lib.RYYGate,
    "rzz": lib.RZZGate,
}
TWO_WIRE = {"swap", "iswap", "rxx", "ryy", "rzz"}
NAMES = sorted([*FIXED, *ANGLED, "rk"])


def qiskit_gate(name: str, params: list[float]):
    if name in FIXED:
        return FIXED[name]()
    if name == "rk":
        return lib.PhaseGate(2 * math.pi / 2 ** params[0])
    return ANGLED[name](params[0])


def to_qiskit(n_wires: int, ops: list[dict]) -> QuantumCircuit:
    qc = QuantumCircuit(n_wires)
    q = [w - 1 for w in range(n_wires + 1)]  # q[w]: the qiskit qubit of wire w
    for o in ops:
        gate = qiskit_gate(o["gate"], o["params"])
        controls = o["controls"] + o["open_controls"]
        for w in o["open_controls"]:
            qc.x(q[w])
        if controls:
            gate = gate.control(len(controls))
        qc.append(gate, [q[w] for w in controls + o["wires"]])
        for w in o["open_controls"]:
            qc.x(q[w])
    return qc


def our_order(qc: QuantumCircuit) -> np.ndarray:
    return Operator(qc).reverse_qargs().data


def random_op(rng: np.random.Generator, n: int, name: str) -> dict:
    k = 2 if name in TWO_WIRE else 1
    wires = [int(w) for w in rng.permutation(np.arange(1, n + 1))]
    targets, free = wires[:k], wires[k:]
    n_controls = int(rng.integers(0, min(len(free), 3) + 1))
    n_closed = int(rng.integers(0, n_controls + 1))
    if name == "rk":
        params = [int(rng.integers(1, 7))]
    elif name in ANGLED:
        params = [float(rng.uniform(-2 * math.pi, 2 * math.pi))]
    else:
        params = []
    return {
        "gate": name,
        "wires": targets,
        "controls": free[:n_closed],
        "open_controls": free[n_closed:n_controls],
        "params": params,
    }


def random_circuit(rng: np.random.Generator, index: int) -> dict:
    n = int(rng.integers(1, MAX_WIRES + 1))
    names = [g for g in NAMES if n >= 2 or g not in TWO_WIRE]
    n_ops = int(rng.integers(1, MAX_OPS + 1))
    # The first ops of each circuit walk through the gate names, so every name appears often.
    picks = [names[(index + i) % len(names)] for i in range(min(n_ops, 3))]
    picks += [str(rng.choice(names)) for _ in range(n_ops - len(picks))]
    return {"n_wires": n, "ops": [random_op(rng, n, name) for name in picks]}


def encode(matrix: np.ndarray) -> list:
    return [
        [[round(float(z.real), DIGITS), round(float(z.imag), DIGITS)] for z in row]
        for row in matrix
    ]


def check_conversion() -> None:
    cnot = to_qiskit(
        2, [{"gate": "x", "wires": [2], "controls": [1], "open_controls": [], "params": []}]
    )
    expected = np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 0, 1], [0, 0, 1, 0]])
    assert np.allclose(our_order(cnot), expected), "wire-order conversion is wrong"
    x_top = to_qiskit(
        2, [{"gate": "x", "wires": [1], "controls": [], "open_controls": [], "params": []}]
    )
    assert np.allclose(our_order(x_top)[:, 0], [0, 0, 1, 0]), "X on wire 1 must map |00> to |10>"


def main() -> int:
    check_conversion()
    rng = np.random.default_rng(SEED)
    circuits = []
    for index in range(N_CIRCUITS):
        c = random_circuit(rng, index)
        u = our_order(to_qiskit(c["n_wires"], c["ops"]))
        assert np.allclose(u @ u.conj().T, np.eye(len(u)), atol=1e-12)
        circuits.append({"id": index, **c, "unitary": encode(u)})
    seen = {o["gate"] for c in circuits for o in c["ops"]}
    assert seen == set(NAMES), sorted(set(NAMES) - seen)
    document = {
        "format": "quantum-sim-reference/1",
        "generator": "packages/quantum-sim/tools/make_reference.py",
        "library": "qiskit",
        "version": qiskit.__version__,
        "numpy": np.__version__,
        "python": platform.python_version(),
        "seed": SEED,
        "wire_order": "wire 1 is the most significant bit (Qiskit's qubit order reversed)",
        "entries": f"complex numbers as [re, im], rounded to {DIGITS} decimals",
        "circuits": circuits,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(document, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{len(circuits)} circuits -> {OUT.name} ({OUT.stat().st_size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
