"""Use cases of the circuit check: read LaTeX, simulate, compare."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..domain.circuits import (
    MAX_STATE_WIRES,
    MAX_UNITARY_WIRES,
    CheckTarget,
    CircuitCaps,
    ensure_within_caps,
    read_json_strict,
)
from ..domain.errors import InvalidInputError
from ..ports.circuits import CircuitEngine, GateListReading

CIRCUIT = "circuit_json"
TARGET_CIRCUIT = "target.circuit_json"


def _pointer(name: str) -> str:
    return "/" + name.replace(".", "/")


class ParseCircuit:
    def __init__(self, *, engine: CircuitEngine) -> None:
        self._engine = engine

    async def __call__(self, latex: str) -> GateListReading:
        return await self._engine.parse(latex)


class RunCircuit:
    """Simulate one ``circuit/1`` document given as raw JSON text."""

    def __init__(self, *, engine: CircuitEngine, caps: CircuitCaps) -> None:
        self._engine = engine
        self._caps = caps

    async def __call__(
        self,
        *,
        circuit_json: str,
        input_state: object = None,
        values: Mapping[str, Any] | None = None,
        unitary: bool = False,
    ) -> Mapping[str, Any]:
        document = read_json_strict(circuit_json, what=CIRCUIT)
        ensure_within_caps(
            [(_pointer(CIRCUIT), document)],
            self._caps,
            max_wires=MAX_UNITARY_WIRES if unitary else MAX_STATE_WIRES,
        )
        return await self._engine.run(
            document, input_state=input_state, values=values, unitary=unitary
        )


class CheckCircuit:
    """Compare a circuit with a state, a unitary, a second circuit or, with no target, the two
    sides of an equality drawn in the document."""

    def __init__(self, *, engine: CircuitEngine, caps: CircuitCaps) -> None:
        self._engine = engine
        self._caps = caps

    async def __call__(
        self,
        *,
        circuit_json: str,
        target: CheckTarget | None = None,
        input_state: object = None,
        values: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        document = read_json_strict(circuit_json, what=CIRCUIT)
        documents = [(_pointer(CIRCUIT), document)]
        request: dict[str, Any] = {}
        max_wires = MAX_UNITARY_WIRES
        if target is None:
            request["circuit"] = document
        elif target.state is not None:
            request.update(circuit=document, target={"state": target.state})
            max_wires = MAX_STATE_WIRES
        elif target.unitary is not None:
            request.update(circuit=document, target={"unitary": target.unitary})
        elif target.circuit_json is not None:
            other = read_json_strict(target.circuit_json, what=TARGET_CIRCUIT)
            documents.append((_pointer(TARGET_CIRCUIT), other))
            request.update(a=document, b=other)
        else:
            raise InvalidInputError("A target needs one of state, unitary or circuit_json.")
        ensure_within_caps(documents, self._caps, max_wires=max_wires)
        if input_state is not None:
            request["input"] = input_state
        return await self._engine.check(request, values=values)


class ShowCircuitConventions:
    def __init__(self, *, engine: CircuitEngine) -> None:
        self._engine = engine

    def __call__(self) -> Mapping[str, Any]:
        return self._engine.conventions()
