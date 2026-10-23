"""The ``Host`` allow-list, against DNS rebinding.

A page on ``evil.example`` whose name is pointed at this machine reaches the service with
``Host: evil.example``, and the browser would let that page read the answers (DNS rebinding).
Only the names the service has are served: loopback, the host of ``public_origin`` and the
``bind_host`` address. Fake mode serves loopback names only, on any port, because the web app's
development and test servers run on another loopback port and forward ``/api`` here.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from ...bootstrap.settings import Settings, is_ip_address, is_loopback, same_address
from ...domain.errors import ForbiddenError
from .problems import problem

DETAIL = "This service does not answer to that host name."


def host_name(header: str) -> str | None:
    """The host part of a ``Host`` header (no port), lowercase; ``None`` when it is malformed."""
    value = header.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        rest = value[end + 1 :]
        if end < 0 or (rest and not (rest.startswith(":") and rest[1:].isdigit())):
            return None
        return value[1:end]
    name, colon, port = value.partition(":")
    if not name or (colon and not port.isdigit()):
        return None
    return name


def hosts_allowed_by(settings: Settings, *, fake: bool) -> Callable[[str], bool]:
    """Whether a host name is one of this service's own."""
    names = {"localhost"}
    addresses: list[str] = []
    if not fake:
        origin_host = urlsplit(settings.origin).hostname
        for candidate in filter(None, (origin_host, settings.bind_host)):
            if is_ip_address(candidate):
                addresses.append(candidate)
            else:
                names.add(candidate.lower())

    def allowed(host: str) -> bool:
        if is_ip_address(host):
            return is_loopback(host) or any(same_address(host, a) for a in addresses)
        return host in names

    return allowed


class HostGuard:
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
        hosts = [v for k, v in scope["headers"] if k == b"host"]
        if len(hosts) != 1:  # none (HTTP/1.0) or several (a smuggling shape): refuse both
            return False
        name = host_name(hosts[0].decode("latin-1"))
        return name is not None and self.allowed(name)
