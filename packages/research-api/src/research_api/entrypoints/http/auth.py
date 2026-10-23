"""Sign-in endpoints and the admin's view of sessions (``/auth/*``, ``/admin/sessions``)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response

from ...application.auth import Authenticated, NewSession
from ...bootstrap import Container
from .schemas import LoginRequest, Me, PasswordChange, Problem, SessionOut
from .security import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    admin,
    client_address,
    container_of,
    signed_in,
)

NO_STORE = {"Cache-Control": "no-store"}
RETRY_AFTER = {
    "Retry-After": {
        "description": "Seconds to wait before another attempt.",
        "schema": {"type": "integer"},
    }
}

INVALID = {"model": Problem, "description": "The request is not valid."}
DEFAULT: dict[int | str, dict[str, Any]] = {
    "default": {"model": Problem, "description": "A problem."}
}
# What every route that takes a session can answer besides its own results.
PROTECTED: dict[int | str, dict[str, Any]] = {
    401: {"model": Problem, "description": "No session, or it has ended."},
    403: {
        "model": Problem,
        "description": "The CSRF header is missing or wrong, the password must be changed first, "
        "or the role is not enough.",
    },
}

router = APIRouter(prefix="/auth", tags=["auth"], responses=DEFAULT)
admin_router = APIRouter(prefix="/admin", tags=["admin"], responses=DEFAULT)


def _start_cookies(response: Response, container: Container, opened: NewSession) -> None:
    secure = container.settings.origin.startswith("https://")
    lifetime = container.settings.session_max_days * 86400
    # The session cookie cannot be read by scripts. The CSRF cookie can, on purpose: the web app
    # copies it into the X-CSRF-Token header, and a page on another site cannot read it.
    response.set_cookie(
        SESSION_COOKIE,
        opened.token,
        max_age=lifetime,
        path="/",
        secure=secure,
        httponly=True,
        samesite="strict",
    )
    response.set_cookie(
        CSRF_COOKIE,
        opened.session.csrf_token,
        max_age=lifetime,
        path="/",
        secure=secure,
        httponly=False,
        samesite="strict",
    )


def _no_content(container: Container, opened: NewSession | None = None) -> Response:
    response = Response(status_code=204, headers=NO_STORE)
    if opened is not None:
        _start_cookies(response, container, opened)
    return response


@router.post(
    "/login",
    operation_id="login",
    summary="Sign in",
    status_code=204,
    response_class=Response,
    responses={
        401: {
            "model": Problem,
            "description": "Wrong username or password (one message for both).",
        },
        422: INVALID,
        429: {
            "model": Problem,
            "description": "Too many failed attempts.",
            "headers": RETRY_AFTER,
        },
    },
)
def login(body: LoginRequest, request: Request) -> Response:
    """Sets the `re_session` cookie (HttpOnly) and the `re_csrf` cookie. Answers `204`."""
    container = container_of(request)
    opened = container.auth.sign_in(body.username, body.password, client_address(request))
    return _no_content(container, opened)


@router.post(
    "/logout",
    operation_id="logout",
    summary="Sign out",
    status_code=204,
    response_class=Response,
    responses=PROTECTED,
)
def logout(request: Request, who: Annotated[Authenticated, Depends(signed_in)]) -> Response:
    """Ends this session and clears both cookies."""
    container = container_of(request)
    container.auth.sign_out(who)
    response = _no_content(container)
    secure = container.settings.origin.startswith("https://")
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=secure, httponly=True, samesite="strict"
    )
    response.delete_cookie(CSRF_COOKIE, path="/", secure=secure, httponly=False, samesite="strict")
    return response


@router.get("/me", operation_id="getMe", summary="The signed-in user", responses=PROTECTED)
def me(who: Annotated[Authenticated, Depends(signed_in)], response: Response) -> Me:
    """Answers even while `must_change_password` is true, so the app knows to ask for it."""
    response.headers.update(NO_STORE)
    return Me.from_domain(who.user)


@router.post(
    "/password",
    operation_id="changePassword",
    summary="Change the password",
    status_code=204,
    response_class=Response,
    responses={
        **PROTECTED,
        422: INVALID,
        403: {
            "model": Problem,
            "description": "The current password is wrong, or the CSRF header is missing or wrong.",
        },
        429: {
            "model": Problem,
            "description": "Too many wrong passwords.",
            "headers": RETRY_AFTER,
        },
    },
)
def change_password(
    body: PasswordChange, request: Request, who: Annotated[Authenticated, Depends(signed_in)]
) -> Response:
    """Ends every session of the user and opens a new one: the response sets fresh cookies."""
    container = container_of(request)
    opened = container.auth.change_password(who, body.current, body.new, client_address(request))
    return _no_content(container, opened)


@admin_router.get(
    "/sessions", operation_id="listSessions", summary="Signed-in browsers", responses=PROTECTED
)
def list_sessions(
    request: Request, who: Annotated[Authenticated, Depends(admin)], response: Response
) -> list[SessionOut]:
    """Admin only. Cookie values are never shown."""
    response.headers.update(NO_STORE)
    return [SessionOut.from_info(i) for i in container_of(request).auth.list_sessions(who)]


@admin_router.delete(
    "/sessions/{session_id}",
    operation_id="endSession",
    summary="End a session",
    status_code=204,
    response_class=Response,
    responses={
        **PROTECTED,
        404: {"model": Problem, "description": "No such session."},
        422: INVALID,
    },
)
def end_session(
    session_id: str, request: Request, who: Annotated[Authenticated, Depends(admin)]
) -> Response:
    """Admin only. `404` when there is no such session."""
    container = container_of(request)
    container.auth.end_session(who, session_id)
    return _no_content(container)
