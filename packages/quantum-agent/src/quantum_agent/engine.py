"""NumPy statevector execution with explicit resource and validation limits."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .domain import Circuit, DimensionMismatchError, MeasurementResult, Operation, StateVector


@dataclass(frozen=True)
class SimulationLimits:
    max_qubits: int = 20
    max_state_bytes: int = 256 * 1024 * 1024
    display_threshold: float = 1e-12


def _bits(index: int, num_qubits: int) -> tuple[int, ...]:
    return tuple((index >> (num_qubits - 1 - bit)) & 1 for bit in range(num_qubits))


def _index(bits: tuple[int, ...]) -> int:
    value = 0
    for bit in bits:
        value = (value << 1) | bit
    return value


class StatevectorSimulator:
    def __init__(self, limits: SimulationLimits | None = None) -> None:
        self.limits = limits or SimulationLimits()

    def _validate_size(self, num_qubits: int) -> None:
        if num_qubits > self.limits.max_qubits:
            raise DimensionMismatchError(f"simulation is limited to {self.limits.max_qubits} qubits")
        if 2**num_qubits * np.dtype(np.complex128).itemsize > self.limits.max_state_bytes:
            raise DimensionMismatchError("requested state vector exceeds the configured memory limit")

    def apply_operation(self, state: StateVector, operation: Operation) -> StateVector:
        if any(qubit >= state.num_qubits or qubit < 0 for qubit in operation.qubits):
            raise DimensionMismatchError("operation qubits do not fit the state vector")
        if operation.gate.num_qubits == 2 and operation.gate.name == "CNOT":
            controls = operation.controls or (operation.qubits[0],)
            targets = operation.qubits[1:] if operation.controls else operation.qubits[1:]
            if len(controls) != 1 or len(targets) != 1:
                raise DimensionMismatchError("CNOT requires one control and one target")
            return self._controlled_x(state, controls[0], targets[0])
        if operation.gate.num_qubits == 3 and operation.gate.name == "TOFFOLI":
            controls = operation.controls or operation.qubits[:2]
            target = operation.qubits[-1]
            if len(controls) != 2:
                raise DimensionMismatchError("Toffoli requires two controls and one target")
            return self._controlled_x(state, controls[0], target, controls[1])
        return self._apply_local_gate(state, operation)

    def _apply_local_gate(self, state: StateVector, operation: Operation) -> StateVector:
        targets = operation.qubits
        result = np.zeros_like(state.amplitudes)
        target_count = len(targets)
        for source_index, amplitude in enumerate(state.amplitudes):
            source_bits = _bits(source_index, state.num_qubits)
            source_target = _index(tuple(source_bits[q] for q in targets))
            for output_target in range(2**target_count):
                output_bits = list(source_bits)
                output_target_bits = _bits(output_target, target_count)
                for position, qubit in enumerate(targets):
                    output_bits[qubit] = output_target_bits[position]
                if operation.controls and any(source_bits[c] != 1 for c in operation.controls):
                    output_bits = list(source_bits)
                result[_index(tuple(output_bits))] += operation.gate.matrix[output_target, source_target] * amplitude
        return StateVector(result, state.num_qubits)

    def _controlled_x(self, state: StateVector, control: int, target: int, second_control: int | None = None) -> StateVector:
        result = state.amplitudes.copy()
        for index in range(result.size):
            bits = list(_bits(index, state.num_qubits))
            if bits[control] == 1 and (second_control is None or bits[second_control] == 1):
                bits[target] ^= 1
                partner = _index(tuple(bits))
                if index < partner:
                    result[index], result[partner] = result[partner], result[index]
        return StateVector(result, state.num_qubits)

    def run(self, circuit: Circuit) -> StateVector:
        self._validate_size(circuit.num_qubits)
        state = circuit.initial_state or StateVector.zero(circuit.num_qubits)
        for operation in circuit.operations:
            state = self.apply_operation(state, operation)
        return state

    def measure(self, state: StateVector, *, shots: int = 0, seed: int | None = None) -> MeasurementResult:
        if shots < 0:
            raise ValueError("shots cannot be negative")
        labels = [format(index, f"0{state.num_qubits}b") for index in range(2**state.num_qubits)]
        probabilities = dict(zip(labels, state.probabilities().tolist()))
        samples: dict[str, int] = {}
        if shots:
            rng = np.random.default_rng(seed)
            values = rng.choice(len(labels), size=shots, p=list(probabilities.values()))
            for value in values:
                label = labels[int(value)]
                samples[label] = samples.get(label, 0) + 1
        return MeasurementResult(probabilities, samples, seed=seed)
