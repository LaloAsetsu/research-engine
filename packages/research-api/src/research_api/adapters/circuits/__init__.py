"""Adapter: docingest gate lists and the quantum-sim engine behind the ``CircuitEngine`` port."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from docingest.domain.circuits import gate_list
from quantum_sim import check, run
from quantum_sim.entrypoints import conventions

from ...ports.circuits import GateListReading
from .lane import ComputeLane

__all__ = ["CircuitsAdapter", "ComputeLane"]


class CircuitsAdapter:
    """Reads LaTeX with docingest's ``gate_list`` and simulates with quantum-sim.

    Both are pure functions of their input, so any number of calls may run in parallel; the lane
    decides how many do.
    """

    def __init__(self, *, timeout_s: float, max_busy: int = 2) -> None:
        self._lane = ComputeLane(timeout_s=timeout_s, max_busy=max_busy)

    async def parse(self, latex: str) -> GateListReading:
        return await self._lane.run(lambda: _parse(latex))

    async def run(
        self,
        document: object,
        *,
        input_state: object = None,
        values: Mapping[str, Any] | None = None,
        unitary: bool = False,
    ) -> Mapping[str, Any]:
        # Wrapped, so a document is always read as a circuit/1 document and never as a request.
        return await self._lane.run(
            lambda: run(
                {"circuit": document}, input_state=input_state, values=values, unitary=unitary
            )
        )

    async def check(
        self, request: Mapping[str, Any], *, values: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any]:
        return await self._lane.run(lambda: check(dict(request), values=values))

    def conventions(self) -> Mapping[str, Any]:
        return conventions()


def _parse(latex: str) -> GateListReading:
    found = gate_list(latex)
    return GateListReading(
        status=found.status,
        text=found.text,
        circuits=tuple(circuit.to_dict() for circuit in found.circuits),
    )
