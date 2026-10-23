"""One log line per request: who, what route, what status, how long. Nothing from the request.

The route is the template (``/sets/{name}/versions/{v}``), never the path as sent, and the query
string, headers, cookies and body are never read, so a question, a password or a token cannot
end up here. uvicorn's own access log (raw paths and query strings) is turned off.
"""

from __future__ import annotations

import logging
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

access_log = logging.getLogger("research_api.access")


def route_template(scope: Scope) -> str:
    """``/api/v1/sets/{name}`` for a matched route, ``-`` when nothing matched.

    FastAPI keeps the route of an included router relative to its prefix, and the full template
    in the route context it puts in the scope; ``tests/test_logs.py`` pins that, so an upgrade
    that moves it fails a test instead of logging half a path.
    """
    context = scope.get("fastapi", {}).get("effective_route_context")
    full = getattr(context, "path_format", None)
    if isinstance(full, str):
        return full
    route = scope.get("route")
    return getattr(route, "path_format", None) or "-"


class AccessLog:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        request_id = uuid.uuid4().hex[:16]
        status = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)["X-Request-Id"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            access_log.info(
                "request",
                extra={
                    "event": "request",
                    "request_id": request_id,
                    "username": scope.get("state", {}).get("username"),
                    "method": scope["method"],
                    "route": route_template(scope),
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
