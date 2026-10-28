"""Routes of the circuit check: read LaTeX, simulate, compare.

The answers are quantum-sim's own ``quantum-sim-result/1`` documents, passed through as JSON. A
circuit the simulator refuses is a 200 with ``status: refused`` and the reasons, as the command's
exit status 1; it is not an HTTP error.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from ...bootstrap import Container
from ...domain.circuits import USE_THE_CLI, read_json_strict
from ...domain.errors import TooLargeError
from .schemas import CheckRequest, GateListResponse, ParseRequest, Problem, RunRequest
from .security import SESSION_COOKIE, member, signed_in

SIMULATOR_NOTE = (
    "The body is quantum-sim's `quantum-sim-result/1` document as it is: `status` (`ok`, "
    "`symbolic` or `refused`), `conventions`, `input`, `circuits`, `comparisons`, `notes` and "
    "`refused`. A circuit the simulator refuses is a 200 with `status: refused`."
)


class BoundedRequest(Request):
    """A request whose body is read with a size cap, stopping as soon as it is over."""

    limit = 0

    async def body(self) -> bytes:
        if not hasattr(self, "_body"):
            declared = self.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > self.limit:
                raise self._too_large()
            chunks: list[bytes] = []
            total = 0
            async for chunk in self.stream():
                total += len(chunk)
                if total > self.limit:
                    raise self._too_large()
                chunks.append(chunk)
            self._body = b"".join(chunks)
        return self._body

    def _too_large(self) -> TooLargeError:
        return TooLargeError(f"The request body is over {self.limit} bytes. {USE_THE_CLI}")


def check_caller(request: Request) -> None:
    member(signed_in(request, request.cookies.get(SESSION_COOKIE)))


class CircuitRoute(APIRoute):
    """Checks the caller, caps the body before FastAPI reads it and refuses a key written twice.

    The caller is checked first, so a request with no session is a 401 whatever its body holds and
    costs no body read. The router's ``member`` dependency finds the caller already resolved on
    the request, so the session is read once, and puts the session cookie in the OpenAPI
    contract.

    FastAPI would parse the body with ``json.loads``, which keeps the last value of a repeated
    key without a word. Reading the raw bytes first makes the refusal come before the model.
    """

    def get_route_handler(self) -> Callable[[Request], Any]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            container: Container = request.app.state.container
            await run_in_threadpool(check_caller, request)
            bounded = BoundedRequest(request.scope, request.receive)
            bounded.limit = container.settings.circuit_caps.max_body_bytes
            body = await bounded.body()
            if body:
                read_json_strict(body, what="The request body")
            return await original(bounded)

        return handler


router = APIRouter(
    prefix="/circuits",
    tags=["circuits"],
    route_class=CircuitRoute,
    dependencies=[Depends(member)],
    responses={"default": {"model": Problem, "description": "A problem."}},
)

_SIZE_PROBLEMS: dict[int | str, dict[str, Any]] = {
    400: {
        "model": Problem,
        "description": "The body is not valid JSON, or a JSON key is written twice (in the body "
        "or in a `circuit_json`).",
    },
    413: {"model": Problem, "description": "The body is over the cap."},
    422: {
        "model": Problem,
        "description": "The request is not valid, or the circuit is over the synchronous caps "
        "(wires, operations, time); `detail` points to the `quantum-sim` command.",
    },
    503: {"model": Problem, "description": "Circuit jobs are all busy; see `Retry-After`."},
}


def _container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


@router.post(
    "/parse",
    operation_id="parseCircuit",
    summary="Read the circuits drawn in LaTeX",
    description="Reads quantikz, Qcircuit, yquant and tikz-cd source with docingest and returns "
    "the gate list in words and each circuit as a `circuit/1` document.",
    responses=_SIZE_PROBLEMS,
)
async def parse_circuit(request: Request, body: ParseRequest) -> GateListResponse:
    reading = await _container(request).parse_circuit(body.latex)
    return GateListResponse.from_port(reading)


@router.post(
    "/run",
    operation_id="runCircuit",
    summary="Simulate a circuit",
    description="The state and the outcome probabilities of a circuit, and its unitary on "
    "request. Up to 12 wires, or 8 with the unitary, and up to the configured number of "
    "operations; a bigger one is a 422 that points to the `quantum-sim` command. " + SIMULATOR_NOTE,
    responses=_SIZE_PROBLEMS,
)
async def run_circuit(request: Request, body: RunRequest) -> dict[str, Any]:
    result = await _container(request).run_circuit(
        circuit_json=body.circuit_json,
        input_state=body.input,
        values=body.values,
        unitary=body.unitary,
    )
    return dict(result)


@router.post(
    "/check",
    operation_id="checkCircuit",
    summary="Compare a circuit with a state, a unitary or another circuit",
    description="Whether the circuit equals the target up to global phase. With no target, the "
    "two sides of an equality drawn in the circuit are compared. Up to 12 wires against a "
    "state and 8 for every comparison that builds a unitary. " + SIMULATOR_NOTE,
    responses=_SIZE_PROBLEMS,
)
async def check_circuit(request: Request, body: CheckRequest) -> dict[str, Any]:
    result = await _container(request).check_circuit(
        circuit_json=body.circuit_json,
        target=body.target.to_domain() if body.target else None,
        input_state=body.input,
        values=body.values,
    )
    return dict(result)


@router.get(
    "/conventions",
    operation_id="getCircuitConventions",
    summary="The conventions, the gate table and the limits of the simulator",
    description="What `quantum-sim conventions` prints: the conventions every result states, the "
    "gate table and quantum-sim's own limits (the limits of this API are smaller and are named "
    "in its 422 answers).",
)
async def circuit_conventions(request: Request) -> dict[str, Any]:
    return dict(_container(request).circuit_conventions())
