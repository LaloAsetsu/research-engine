"""The ``Origin`` check on writes: half of the CSRF defence.

The other half is the ``X-CSRF-Token`` header, checked in ``security.py``. A browser always
sends ``Origin`` on a write, and it must be the address people type to reach the service
(``public_origin``). Fake mode also accepts any loopback origin, because the web app's
development and test servers run on another loopback port and forward ``/api`` here.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from ...bootstrap.settings import Settings, is_loopback, normalize_origin
from ...domain.errors import ForbiddenError
from .problems import problem

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
DETAIL = "A write request must come from the address of this service (Origin header)."


def origin_allowed_by(settings: Settings, *, fake: bool) -> Callable[[str], bool]:
    def allowed(value: str) -> bool:
        try:
            origin = normalize_origin(value)
        except ValueError:
            return False
        if origin == settings.origin:
            return True
        if not fake:
            return False
        host = urlsplit(origin).hostname or ""
        return host == "localhost" or is_loopback(host)

    return allowed


class OriginGuard:
    def __init__(self, app: ASGIApp, *, allowed: Callable[[str], bool]) -> None:
        self.app = app
        self.allowed = allowed

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in WRITE_METHODS:
            origins = [v for k, v in scope["headers"] if k == b"origin"]
            if len(origins) != 1 or not self.allowed(origins[0].decode("latin-1")):
                response = problem(
                    Request(scope), status=403, slug=ForbiddenError.slug, detail=DETAIL
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
