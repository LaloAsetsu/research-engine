"""HTTP request and response schemas. They are the wire contract that OpenAPI publishes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from ...domain.circuits import CheckTarget
from ...domain.status import Mode, ServerStatus
from ...ports.circuits import GateListReading


class Health(BaseModel):
    ok: bool


class Status(BaseModel):
    mode: Mode = Field(description="`live`, or `fake` when the service runs on test data.")
    api_version: str
    started_at: datetime
    uptime_s: float
    ready: bool

    @classmethod
    def from_domain(cls, status: ServerStatus) -> Status:
        return cls(
            mode=status.mode,
            api_version=status.version,
            started_at=status.started_at,
            uptime_s=status.uptime_s,
            ready=status.ready,
        )


class ProblemField(BaseModel):
    path: str = Field(description="JSON pointer to the offending value.")
    message: str


class Problem(BaseModel):
    """RFC 9457 problem details (`application/problem+json`)."""

    type: str
    title: str
    status: int
    detail: str
    instance: str
    errors: list[ProblemField] | None = None
    error_id: str | None = Field(default=None, description="Only on 500: the id in the server log.")


class Request(BaseModel):
    """Base of the request bodies: a key the API does not know is an error, not ignored."""

    model_config = ConfigDict(extra="forbid")


class ParseRequest(Request):
    latex: str = Field(
        description="The body of one code block: a quantikz, Qcircuit, yquant or tikz-cd source."
    )


class GateListResponse(BaseModel):
    status: Literal["parsed", "partial", "unparsed"] = Field(
        description="`parsed` when every circuit was fully read, `unparsed` when none was."
    )
    text: str = Field(
        description="The gate list in words, as docingest gives it to the answer model."
    )
    circuits: list[dict[str, Any]] = Field(
        description="Each circuit as a `circuit/1` document. To simulate one, send it as text in "
        "`circuit_json`."
    )

    @classmethod
    def from_port(cls, reading: GateListReading) -> Self:
        return cls(
            status=reading.status,  # type: ignore[arg-type]
            text=reading.text,
            circuits=[dict(circuit) for circuit in reading.circuits],
        )


CIRCUIT_JSON = (
    "A `circuit/1` document as JSON text. It is text, not an object, so that a key written twice "
    "is seen and refused (answer 400) instead of the last one winning."
)
STATE_INPUT = (
    "The input: a basis label such as `010` (wire 1 first) or a list of amplitudes, each a "
    "number or a `[re, im]` pair. Default |0...0>."
)
VALUES = "A value for each symbol of the circuit, a number or an angle as text such as `\\pi/2`."


class RunRequest(Request):
    circuit_json: str = Field(description=CIRCUIT_JSON)
    input: str | list[Any] | None = Field(default=None, description=STATE_INPUT)
    values: dict[str, StrictStr | StrictInt | StrictFloat] | None = Field(
        default=None, description=VALUES
    )
    unitary: StrictBool = Field(
        default=False, description="Also list the unitary (up to 8 wires). `true` or `false` only."
    )


class CheckTargetBody(Request):
    """What to compare the circuit with. Give exactly one field."""

    state: str | list[Any] | None = Field(
        default=None, description="A state: a basis label or a list of amplitudes of norm 1."
    )
    unitary: list[list[Any]] | None = Field(
        default=None, description="A unitary matrix: rows of numbers or `[re, im]` pairs."
    )
    circuit_json: str | None = Field(
        default=None, description="A second circuit, as JSON text (see the first `circuit_json`)."
    )

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        given = self.model_fields_set
        if len(given) != 1 or any(getattr(self, name) is None for name in given):
            raise ValueError("give exactly one of state, unitary and circuit_json, and not null")
        return self

    def to_domain(self) -> CheckTarget:
        return CheckTarget(state=self.state, unitary=self.unitary, circuit_json=self.circuit_json)


def _not_nullable(schema: dict[str, Any]) -> None:
    """Publish a field that may be left out, but not sent as null, without the null branch."""
    branches = [b for b in schema.pop("anyOf") if b != {"type": "null"}]
    schema.update(branches[0])


class CheckRequest(Request):
    circuit_json: str = Field(description=CIRCUIT_JSON)
    target: CheckTargetBody | None = Field(
        default=None,
        description="Leave it out to compare the two sides of an equality drawn in the circuit. "
        "Sending `null` is refused.",
        json_schema_extra=_not_nullable,
    )
    input: str | list[Any] | None = Field(default=None, description=STATE_INPUT)
    values: dict[str, StrictStr | StrictInt | StrictFloat] | None = Field(
        default=None, description=VALUES
    )

    @field_validator("target", mode="before")
    @classmethod
    def _target_is_a_target_or_absent(cls, value: Any) -> Any:
        if value is None:
            raise ValueError(
                'target must be {"state": ...}, {"unitary": ...} or {"circuit_json": ...}'
            )
        return value
