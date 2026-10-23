"""Who is calling: the dependencies every protected route takes.

``signed_in`` reads the session cookie and, on a write, checks the CSRF header against the token
stored with the session. ``member`` and ``admin`` build on it. A new router protects itself with
``APIRouter(dependencies=[Depends(member)])`` or a ``Depends(member)`` parameter.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Request, Response, Security
from fastapi.security import APIKeyCookie

from ...application.auth import Authenticated
from ...bootstrap import Container
from ...domain.auth import Role
from ...domain.errors import ForbiddenError, UnauthenticatedError

SESSION_COOKIE = "re_session"
CSRF_COOKIE = "re_csrf"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

session_cookie = APIKeyCookie(
    name=SESSION_COOKIE,
    scheme_name="sessionCookie",
    auto_error=False,
    description=(
        "The HttpOnly session cookie set by `POST /auth/login`. Every `POST`, `PUT`, `PATCH` and "
        f"`DELETE` must also carry the value of the `{CSRF_COOKIE}` cookie in the `{CSRF_HEADER}` "
        "header, and an `Origin` header equal to the address of the service."
    ),
)


class SessionLost(UnauthenticatedError):
    """A session cookie was sent and no longer works: the answer also clears both cookies."""


def container_of(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


def forget_session_cookies(response: Response, container: Container) -> None:
    """Ask the browser to drop both cookies (same attributes they were set with)."""
    secure = container.settings.origin.startswith("https://")
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=secure, httponly=True, samesite="strict"
    )
    response.delete_cookie(CSRF_COOKIE, path="/", secure=secure, httponly=False, samesite="strict")


def client_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def signed_in(
    request: Request, token: Annotated[str | None, Security(session_cookie)] = None
) -> Authenticated:
    """The caller, even before the first password change (so they can change it or sign out)."""
    try:
        who = container_of(request).auth.authenticate(token)
    except UnauthenticatedError as e:
        if token:  # the browser holds a dead session: tell it to let go, or it keeps sending it
            raise SessionLost(e.detail) from None
        raise
    if request.method not in SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        # compare_digest takes ASCII text only; bytes keeps a stray character from being a 500
        if not sent or not secrets.compare_digest(
            sent.encode("utf-8"), who.session.csrf_token.encode("utf-8")
        ):
            raise ForbiddenError("The CSRF token is missing or wrong.")
    request.state.username = who.user.username  # read by the access log
    return who


def member(who: Annotated[Authenticated, Depends(signed_in)]) -> Authenticated:
    if who.user.must_change_password:
        raise ForbiddenError("Change your password first.")
    return who


def admin(who: Annotated[Authenticated, Depends(member)]) -> Authenticated:
    if who.user.role is not Role.ADMIN:
        raise ForbiddenError("Only an admin can do this.")
    return who
