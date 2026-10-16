# quantum-sim

A small simulator of quantum circuits in NumPy: the state a circuit prepares, its unitary, outcome probabilities, and whether two circuits (or a circuit and a target) are equal up to global phase. It is meant for checking circuits, the ones a model or an agent writes and the ones docingest reads from a paper's LaTeX, and it states its conventions with every result. numpy is its only dependency; it does not import docingest, and docingest does not import it.

**Status: skeleton.** The package, its conventions, its public API and its acceptance test suite are here. The gate matrices (`quantum_sim.gates.matrix`), the engine (`quantum_sim.engine`) and the circuit check (`quantum_sim.domain.validate`) are stubs that raise `NotImplementedError`; the tests that need them are marked `engine_pending` (see [Tests and checks](#tests-and-checks)). The label resolver, the JSON circuit format and the `run` and `check` commands come after the engine.

The decision and its alternatives are in [ADR 0005](../docingest/docs/adr/0005-own-numpy-circuit-simulator.md).

## Contents

- [Use it](#use-it)
- [Conventions](#conventions)
- [Limits](#limits)
- [Code map](#code-map)
- [Tests and checks](#tests-and-checks)
- [The reference fixture](#the-reference-fixture)

## Use it

```python
from quantum_sim import Circuit, Op, equal, probabilities, statevector, unitary

bell = Circuit(2, [Op("h", (1,)), Op("x", (2,), controls=(1,))])
statevector(bell)                 # [0.707, 0, 0, 0.707]: |00> and |11>
probabilities(bell)               # [0.5, 0, 0, 0.5]

# CNOT = (I ⊗ H) CZ (I ⊗ H), compared up to global phase
cz_form = Circuit(2, [Op("h", (2,)), Op("z", (2,), controls=(1,)), Op("h", (2,))])
equal(cz_form, Circuit(2, [Op("x", (2,), controls=(1,))])).equal    # True
```

| Function | Returns |
|---|---|
| `statevector(circuit, input_state=None)` | `2^n` amplitudes after the circuit; the input is `\|0...0>`, a basis label such as `"010"` or a list of amplitudes |
| `unitary(circuit)` | the `2^n x 2^n` matrix (up to 10 wires; no measurements) |
| `probabilities(circuit, input_state=None, wires=None)` | outcome probabilities of the measured wires, or of the wires given, first wire most significant |
| `equal(a, b, atol=1e-9, seed=0)` | an `Equality` (`equal`, `exact`, `phase`, `max_deviation`, `method`) for two circuits, a circuit and a matrix, or two arrays |
| `validate(circuit)` | nothing, or the typed error that says why the circuit cannot be simulated |

An `Op` is a gate name from the table, its target wires (1-based), closed `controls`, `open_controls`, `params`, and `classical_controls` (refused in version 1). The measurement op is `Op("measure", (w,))`, at the end of its wire only. Errors derive from `QuantumSimError`: `InvalidCircuitError` (and `UnknownGateError`), `UnsupportedOperationError`, `LimitExceededError`, `InvalidStateError`.

The command prints the conventions, the gate table and the limits as JSON:

```bash
uv run quantum-sim conventions
```

## Conventions

Nielsen and Chuang throughout, written out in [docs/conventions.md](docs/conventions.md):

- wire 1 is the top wire and the **most significant** bit (`|w1 w2 ... wn>`), the opposite of Qiskit;
- wires are numbered from 1 in every user-facing structure;
- R_a(θ) = exp(−iθσ_a/2), R_aa(θ) = exp(−iθ σ_a⊗σ_a / 2), P(θ) = diag(1, e^{iθ}), S = P(π/2), T = P(π/4), √X = (1/2)[[1+i, 1−i], [1−i, 1+i]], the QFT's R_k = diag(1, e^{2πi/2^k});
- any gate takes any number of closed and open controls, and controlled R_z is not controlled P;
- results are compared up to global phase (1e-9), and exact equality is reported too.

## Limits

Statevectors up to 20 wires (16 MiB), unitaries up to 10 wires (16 MiB), equality of two circuits of 11 to 20 wires through 8 random states, at most 10,000 operations. Limits are checked from the description before any array is created.

## Code map

| Module | Contains | Imports |
|---|---|---|
| `domain.py` | `Circuit`, `Op`, `GateSpec`, `GATES`, `CONVENTIONS`, the limits, `Equality`, the errors, `validate` (stub) | standard library only |
| `gates.py` | `matrix(name, params)` (stub) | numpy, `domain` |
| `engine.py` | `statevector`, `unitary`, `probabilities`, `equal` (stubs) | numpy, `domain`, `gates` |
| `entrypoints.py` | the `quantum-sim` command | `domain` |

Three import-linter contracts in `pyproject.toml` keep this layering (`entrypoints`, `engine`, `gates`, `domain`), keep numpy out of `domain`, and forbid importing docingest or docingest-index. The reverse direction, docingest never importing `quantum_sim`, is a contract in docingest's `pyproject.toml`.

## Tests and checks

```bash
./scripts/check.sh      # ruff, ruff format, import-linter, pyright, pytest with an 85% coverage floor
```

The acceptance suite in `tests/acceptance/` is the contract for the engine. Most of it runs twice through the `sim` fixture: once on a naive builder written inside the tests (`tests/acceptance/naive.py`: gate matrices typed in from the conventions, full Kronecker products and permutation matrices, no `tensordot`), which passes today and checks the tests themselves, and once on the engine.

| Module | Covers |
|---|---|
| `test_gates.py` | every matrix against hand values and the conventions literal, unitarity, S² = Z, T² = S, √X² = X, HXH = Z, HZH = X, HYH = −Y, R_z(θ) = e^{−iθ/2} P(θ), rotations as exponentials, R_k; unknown names and bad parameters refused |
| `test_wire_order.py` | X on wire 1 of 2 maps `\|00>` to `\|10>`, basis labels read wire 1 first, CNOT in every direction on all basis states of 3 and 4 wires, SWAP and iSWAP on non-adjacent wires |
| `test_controls.py` | CZ, CS, CH, C-R_y, C-P, C-R_z, C-√X, C-R_k on all four inputs with the control above and below; C-R_z is not C-P; open, mixed and extra controls; CZ symmetric; Fredkin; controlled R_zz |
| `test_textbook.py` | Bell and GHZ states, SWAP from 3 CNOTs, CNOT = (I ⊗ H) CZ (I ⊗ H), the exact Toffoli decomposition of Nielsen and Chuang Fig. 4.9 (6 CNOTs, 7 T), CNOT (I ⊗ R_z(2γ)) CNOT = exp(−iγ Z⊗Z), the 3-wire QFT against the DFT with and without its final swap |
| `test_reference.py` | 240 random circuits against Qiskit's unitaries from the fixture, tolerance 1e-10 |
| `test_naive_vs_engine.py` | engine against the naive builder on random circuits from hypothesis (1 to 6 wires, every gate, random controls and angles): unitaries, states, probabilities, `equal` |
| `test_limits.py` | 21-wire statevectors, 11-wire unitaries and 10,001 operations refused with peak memory under 4 MiB (`tracemalloc`); 20 wires, 10 wires and 10,000 operations accepted |
| `test_measurement.py` | end-of-wire measurement and its probabilities; operations after a measurement and classically controlled ones refused |
| `test_equality.py` | global phase, exactness, the phase's range, tolerance, circuits against matrices, random-state equality for 11 to 20 wires |
| `test_validation.py` | unknown names, wires out of range or repeated, wrong numbers of targets and parameters, bad angles, bad input states |

`tests/test_domain.py`, `test_cli.py` and `test_packaging.py` cover the parts that exist today, including that numpy is the only dependency and that every gate is documented in `docs/conventions.md`.

### The `engine_pending` marker

Tests that need the stubbed code carry the marker `engine_pending`. A hook in `tests/conftest.py` turns it into a strict xfail that expects `NotImplementedError`, so `check.sh` passes today while each such test still runs: a test that fails in any other way is a failure, and a test that starts to pass fails as XPASS. To see the real failures, run `uv run pytest --runxfail`. When the engine lands, remove the marker from every test (`grep -rn engine_pending tests`), the hook in `tests/conftest.py` and the marker line in `pyproject.toml`.

## The reference fixture

`tests/fixtures/reference-circuits.json` holds 240 seeded random circuits (up to 5 wires, up to 30 operations, every gate, closed and open controls) and their unitaries as computed by Qiskit, converted to this package's wire order, with the library, its version and the seed in the header. Qiskit is not a dependency of anything in the workspace: [`tools/make_reference.py`](tools/make_reference.py) is run once in a throwaway environment outside the repository, and its docstring shows how. Regenerate the fixture only when the gate table changes.
