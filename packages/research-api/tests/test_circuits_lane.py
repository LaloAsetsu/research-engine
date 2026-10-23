from __future__ import annotations

import asyncio
import threading
import time

import pytest

from research_api.adapters.circuits import CircuitsAdapter, ComputeLane
from research_api.application.circuits import CheckCircuit
from research_api.domain.circuits import CheckTarget, CircuitCaps
from research_api.domain.errors import InvalidInputError, UnavailableError


def test_a_result_comes_back() -> None:
    assert asyncio.run(ComputeLane(timeout_s=5).run(lambda: 41 + 1)) == 42


def test_an_exception_of_the_work_reaches_the_caller() -> None:
    def fail() -> int:
        raise ValueError("inside")

    with pytest.raises(ValueError, match="inside"):
        asyncio.run(ComputeLane(timeout_s=5).run(fail))


def test_the_work_does_not_run_on_the_event_loop_thread() -> None:
    caller = threading.get_ident()
    assert asyncio.run(ComputeLane(timeout_s=5).run(threading.get_ident)) != caller


def test_work_that_takes_too_long_is_a_422_and_keeps_its_slot_until_it_ends() -> None:
    release = threading.Event()
    lane = ComputeLane(timeout_s=0.2, max_busy=1)

    async def scenario() -> None:
        with pytest.raises(InvalidInputError, match=r"longer than 0\.2 s"):
            await lane.run(lambda: release.wait(10))
        # the thread is still running, so the only slot is taken
        with pytest.raises(UnavailableError) as busy:
            await lane.run(lambda: 1)
        assert busy.value.retry_after_s == 5
        release.set()
        for _ in range(100):  # the slot comes back once the thread has ended
            try:
                assert await lane.run(lambda: 7) == 7
                return
            except UnavailableError:
                await asyncio.sleep(0.02)
        raise AssertionError("the slot never came back")

    asyncio.run(scenario())


def test_calls_up_to_the_limit_run_together() -> None:
    both_in = threading.Barrier(2, timeout=5)
    lane = ComputeLane(timeout_s=5, max_busy=2)

    async def scenario() -> list[int]:
        return await asyncio.gather(lane.run(both_in.wait), lane.run(both_in.wait))

    assert sorted(asyncio.run(scenario())) == [0, 1]


def test_a_finished_call_after_the_loop_closed_is_harmless() -> None:
    started = threading.Event()
    release = threading.Event()
    lane = ComputeLane(timeout_s=5)

    def work() -> int:
        started.set()
        release.wait(5)
        return 1

    async def scenario() -> None:
        task = asyncio.ensure_future(lane.run(work))
        await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    release.set()
    time.sleep(0.1)  # the thread ends against a closed loop; nothing must blow up


def test_the_adapter_reads_latex_and_simulates() -> None:
    engine = CircuitsAdapter(timeout_s=30)
    latex = r"\begin{quantikz} \lstick{$\ket{0}$} & \gate{X} & \qw \end{quantikz}"
    reading = asyncio.run(engine.parse(latex))
    assert reading.status == "parsed"
    assert reading.circuits[0]["format"] == "circuit/1"
    result = asyncio.run(engine.run(reading.circuits[0]))
    assert result["status"] == "ok"
    assert result["circuits"][0]["probabilities"]["values"] == [0.0, 1.0]


def test_a_request_shaped_document_is_not_taken_for_a_request() -> None:
    engine = CircuitsAdapter(timeout_s=30)
    bell = {
        "format": "circuit/1",
        "wires": 1,
        "parts": [[[{"kind": "gate", "label": "X", "targets": [1]}]]],
    }
    result = asyncio.run(engine.run({"circuit": bell, "input": "1"}))
    assert result["status"] == "refused"


def test_conventions_come_from_quantum_sim() -> None:
    body = CircuitsAdapter(timeout_s=30).conventions()
    assert {"version", "conventions", "gates", "limits"} <= set(body)


def test_a_check_with_an_empty_target_is_refused_before_any_work() -> None:
    check = CheckCircuit(engine=CircuitsAdapter(timeout_s=1), caps=CircuitCaps())
    with pytest.raises(InvalidInputError, match="needs one of"):
        asyncio.run(check(circuit_json="{}", target=CheckTarget()))
