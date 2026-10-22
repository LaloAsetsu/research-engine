"""Problem details (RFC 9457): every error leaves the API as ``application/problem+json``."""

from __future__ import annotations

import logging
import uuid
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ...domain.errors import (
    ConflictError,
    FieldError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
    ResearchApiError,
    TooLargeError,
    UnauthenticatedError,
    UnavailableError,
    UpstreamError,
)
from .schemas import Problem

PROBLEM_MEDIA = "application/problem+json"
PROBLEM_REF = "#/components/schemas/Problem"
TYPE_PREFIX = "urn:research-api:problem:"

log = logging.getLogger("research_api")

_STATUS_OF: dict[type[ResearchApiError], int] = {
    UnauthenticatedError: 401,
    ForbiddenError: 403,
    NotFoundError: 404,
    ConflictError: 409,
    TooLargeError: 413,
    InvalidInputError: 422,
    RateLimitedError: 429,
    UpstreamError: 502,
    UnavailableError: 503,
}


def status_of(error: ResearchApiError) -> int:
    for cls in type(error).__mro__:
        if cls in _STATUS_OF:
            return _STATUS_OF[cls]
    return 500


def _phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"


def _pointer(parts: tuple[Any, ...]) -> str:
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def problem(
    request: Request,
    *,
    status: int,
    slug: str,
    detail: str,
    errors: tuple[FieldError, ...] | list[FieldError] | None = None,
    headers: dict[str, str] | None = None,
    error_id: str | None = None,
) -> JSONResponse:
    body = Problem.model_validate(
        {
            "type": TYPE_PREFIX + slug,
            "title": _phrase(status),
            "status": status,
            "detail": detail or _phrase(status),
            "instance": request.url.path,
            "errors": [{"path": e.path, "message": e.message} for e in errors] if errors else None,
            "error_id": error_id,
        }
    )
    return JSONResponse(
        body.model_dump(exclude_none=True),
        status_code=status,
        media_type=PROBLEM_MEDIA,
        headers=headers,
    )


async def _on_domain_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ResearchApiError)
    headers: dict[str, str] = {}
    retry_after = getattr(exc, "retry_after_s", None)
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return problem(
        request,
        status=status_of(exc),
        slug=exc.slug,
        detail=exc.detail,
        errors=getattr(exc, "errors", None),
        headers=headers or None,
    )


async def _on_validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = []
    for err in exc.errors():
        loc = tuple(err["loc"])
        if loc and loc[0] == "body":
            loc = loc[1:]
        errors.append(FieldError(_pointer(loc), str(err["msg"])))
    return problem(
        request,
        status=422,
        slug=InvalidInputError.slug,
        detail="The request is not valid.",
        errors=errors,
    )


async def _on_http_exception(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    status = exc.status_code
    slug = _phrase(status).lower().replace(" ", "-")
    return problem(
        request,
        status=status,
        slug=slug,
        detail=str(exc.detail) if exc.detail else "",
        headers=dict(exc.headers) if exc.headers else None,
    )


async def _on_unexpected(request: Request, exc: Exception) -> JSONResponse:
    error_id = uuid.uuid4().hex
    log.error("unexpected error %s on %s", error_id, request.url.path, exc_info=exc)
    return problem(
        request,
        status=500,
        slug="internal",
        detail="Something went wrong on the server.",
        error_id=error_id,
    )


def install_problem_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ResearchApiError, _on_domain_error)
    app.add_exception_handler(RequestValidationError, _on_validation_error)
    app.add_exception_handler(StarletteHTTPException, _on_http_exception)
    app.add_exception_handler(Exception, _on_unexpected)


def use_problem_media_in_openapi(schema: dict[str, Any]) -> None:
    """Make the published contract say what the handlers send.

    Error responses declared with the ``Problem`` model move to ``application/problem+json``,
    and FastAPI's own 422 shape is replaced by ``Problem``.
    """
    schemas = schema["components"]["schemas"]
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            for code, response in operation.get("responses", {}).items():
                content = response.get("content", {})
                if code == "422":
                    response["description"] = "The request is not valid."
                    response["content"] = {PROBLEM_MEDIA: {"schema": {"$ref": PROBLEM_REF}}}
                elif (
                    content.get("application/json", {}).get("schema", {}).get("$ref") == PROBLEM_REF
                ):
                    content[PROBLEM_MEDIA] = content.pop("application/json")
    schemas.pop("HTTPValidationError", None)
    schemas.pop("ValidationError", None)
