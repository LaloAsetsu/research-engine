# quantum-agent

`quantum-agent` is a small, local, explainable statevector engine for agent
workflows that need explicit quantum linear algebra. It is intentionally
separate from `docingest` and does not require quantum hardware or hosted
services.

## Scope

The first version supports normalized pure state vectors, unitary matrices,
Hadamard, Pauli-X/Y/Z, S/phase, CNOT, Toffoli, tensor-compatible local gate
application, computational-basis probabilities, seeded sampling, constrained
Dirac notation, typed circuit specifications, and JSON/Markdown state summaries.

The engine rejects non-unitary gates, malformed dimensions, invalid qubit
operands, non-normalized vectors, and requests over its configured statevector
limits. It uses big-endian display labels (`|q0...qN>`).

```python
from quantum_agent import Circuit, Operation, StatevectorSimulator, gates
from quantum_agent.visualizer import state_markdown

circuit = Circuit(
    2,
    (
        Operation(gates["H"], (0,)),
        Operation(gates["CNOT"], (0, 1)),
    ),
)
state = StatevectorSimulator().run(circuit)
print(state_markdown(state))
```

Install the workspace with `uv sync`; optional framework extras are available
for future oracle/export adapters:

```bash
uv sync --package quantum-agent
uv run --package quantum-agent pytest packages/quantum-agent/tests
```

Qiskit, Cirq, PennyLane, SciPy, and SymPy are deliberately optional. NumPy is
the only runtime numerical dependency because the native engine is the default
for deterministic explanations and avoids forcing a full quantum SDK into the
agent environment.
