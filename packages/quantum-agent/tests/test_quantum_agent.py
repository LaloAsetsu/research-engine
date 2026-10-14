import numpy as np
import pytest

from quantum_agent import Circuit, Operation, StatevectorSimulator, gates, solve_quantum_problem
from quantum_agent.domain import Gate, InvalidStateError, NonUnitaryGateError, StateVector
from quantum_agent.parsing import parse_circuit, parse_dirac


def test_hadamard_maps_zero_to_equal_superposition():
    state = StatevectorSimulator().run(Circuit(1, (Operation(gates["H"], (0,)),)))
    assert np.allclose(state.amplitudes, [1 / np.sqrt(2), 1 / np.sqrt(2)])


def test_bell_state_has_only_00_and_11():
    circuit = Circuit(2, (Operation(gates["H"], (0,)), Operation(gates["CNOT"], (0, 1))))
    state = StatevectorSimulator().run(circuit)
    assert np.allclose(state.amplitudes, [1 / np.sqrt(2), 0, 0, 1 / np.sqrt(2)])
    result = StatevectorSimulator().measure(state, shots=1000, seed=4)
    assert set(result.samples) <= {"00", "11"}
    assert sum(result.samples.values()) == 1000


def test_toffoli_flips_target_only_when_both_controls_are_one():
    amplitudes = np.zeros(8, dtype=np.complex128)
    amplitudes[6] = 1
    state = StateVector(amplitudes, 3)
    circuit = Circuit(3, (Operation(gates["TOFFOLI"], (0, 1, 2)),), state)
    result = StatevectorSimulator().run(circuit)
    assert np.argmax(abs(result.amplitudes)) == 7


def test_parser_accepts_bell_state_with_normalized_coefficients():
    state = parse_dirac("1/sqrt(2)|00> + 1/sqrt(2)|11>")
    assert np.allclose(state.amplitudes, [1 / np.sqrt(2), 0, 0, 1 / np.sqrt(2)])


def test_circuit_parser_runs_typed_gate_spec():
    circuit = parse_circuit({"num_qubits": 1, "operations": [{"gate": "H", "qubits": [0]}]})
    assert np.allclose(StatevectorSimulator().run(circuit).amplitudes, [1 / np.sqrt(2)] * 2)


def test_invalid_states_and_gates_are_rejected():
    with pytest.raises(InvalidStateError):
        StateVector(np.array([1, 1], dtype=np.complex128), 1)
    with pytest.raises(NonUnitaryGateError):
        Gate("bad", np.ones((2, 2), dtype=np.complex128), 1)


def test_agent_entrypoint_requires_an_explicit_mode():
    solution = solve_quantum_problem("|0>", mode="dirac")
    assert solution.interpretation == "Dirac state with 1 qubits"
    with pytest.raises(ValueError, match="mode"):
        solve_quantum_problem("|0>", mode="automatic")
