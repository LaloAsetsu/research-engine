"""``Cache-Control: no-store`` on the routes that carry corpus text.

Answers and papers must not be kept by a browser cache or a proxy. A middleware says so once for
every route under the given prefixes, whatever header the route or the framework set (the event
stream of a question gets ``no-cache`` from FastAPI).
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class NoStore:
    def __init__(self, app: ASGIApp, *, prefixes: tuple[str, ...]) -> None:
        self.app = app
        self.prefixes = prefixes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not any(
            path == prefix or path.startswith(prefix + "/") for prefix in self.prefixes
        ):
            await self.app(scope, receive, send)
            return

        async def send_no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_no_store)
