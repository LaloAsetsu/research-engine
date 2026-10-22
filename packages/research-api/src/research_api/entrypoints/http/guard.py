"""Enforce the bind rule on every start path, not only on ``research-api serve``.

uvicorn puts the local address of the accepted connection in ``scope["server"]``. Whatever
``--host`` a process manager passes, a connection that arrived on an address the settings would
have refused is answered with 403 and nothing else runs. Live mode answers on loopback and on
``bind_host`` only: the rest of the Tailscale block is carrier-grade NAT space that other hosts
can use. A server without an IP address there (a Unix socket, an unknown test host) is refused
too.
"""

from __future__ import annotations

from collections.abc import Callable

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from ...bootstrap.settings import is_loopback, same_address
from ...domain.errors import ForbiddenError
from .problems import problem

DETAIL = "This service does not answer on this address."


def loopback_only(host: str) -> bool:
    return is_loopback(host)


def loopback_or_address(address: str) -> Callable[[str], bool]:
    """Loopback, or exactly ``address`` (the ``bind_host`` of the settings)."""

    def allowed(host: str) -> bool:
        return is_loopback(host) or same_address(host, address)

    return allowed


class BindGuard:
    """Pure ASGI middleware: refuse connections accepted on an address ``allowed`` rejects."""

    def __init__(self, app: ASGIApp, *, allowed: Callable[[str], bool]) -> None:
        self.app = app
        self.allowed = allowed

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and not self._accepts(scope):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            response = problem(Request(scope), status=403, slug=ForbiddenError.slug, detail=DETAIL)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _accepts(self, scope: Scope) -> bool:
        server = scope.get("server")
        return bool(server) and isinstance(server[0], str) and self.allowed(server[0])
