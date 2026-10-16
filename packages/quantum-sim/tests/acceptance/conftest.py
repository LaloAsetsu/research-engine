"""``sim``: the same tests run on the naive builder (which checks the tests) and
on the engine."""

from dataclasses import dataclass
from typing import Any

import naive
import pytest

from quantum_sim import engine, gates


@dataclass(frozen=True)
class Simulator:
    name: str
    matrix: Any
    unitary: Any
    statevector: Any


NAIVE = Simulator("naive", naive.literal, naive.unitary, naive.statevector)
ENGINE = Simulator("engine", gates.matrix, engine.unitary, engine.statevector)


@pytest.fixture(params=[NAIVE, ENGINE], ids=lambda s: s.name)
def sim(request) -> Simulator:
    return request.param
