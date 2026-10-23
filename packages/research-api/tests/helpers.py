"""Shared helpers of the tests (fixtures live in conftest.py)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from starlette.types import Message

from research_api.bootstrap import Settings, open_database

LOOPBACK = "http://127.0.0.1:8090"
ORIGIN = {"Origin": LOOPBACK}
SEED_FILE = Path(__file__).parent / "fixtures" / "seed-users.json"


class MovableClock:
    """A clock the test moves by hand, to cross session and rate-limit windows."""

    def __init__(self, at: datetime = datetime(2026, 1, 1, tzinfo=UTC)) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at

    def advance(self, **delta: float) -> None:
        self.at += timedelta(**delta)


def live_settings(folder: Path, **overrides: object) -> Settings:
    """Settings for a live container whose files sit in a private test folder."""
    folder.mkdir(mode=0o700, exist_ok=True)
    folder.chmod(0o700)
    values: dict[str, object] = {
        "data_dir": folder / "data",
        "db_path": folder / "db" / "research-api.sqlite3",
    }
    return Settings.model_validate({**values, **overrides})


def write_config(path: Path, text: str) -> Path:
    """A settings file the way the service wants it: mode 0600."""
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


def prepare_live(settings: Settings) -> Settings:
    """Make the database of live settings (what `research-api db upgrade` does) and return them."""
    database = open_database(settings, create=True)
    database.upgrade()
    database.close()
    return settings


# --- calling an ASGI app by hand, for the middleware tests ---------------------------------


def call(app: Any, scope: dict[str, Any]) -> list[Message]:
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent


async def ok_app(scope: Any, receive: Any, send: Any) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def http_scope(
    method: str = "GET", headers: list[tuple[bytes, bytes]] | None = None
) -> dict[str, Any]:
    return {
        "type": "http",
        "method": method,
        "path": "/api/v1/health",
        "headers": headers or [],
        "server": ("127.0.0.1", 8090),
        "query_string": b"",
        "scheme": "http",
        "root_path": "",
    }


def status_of(messages: list[Message]) -> int:
    return next(m["status"] for m in messages if m["type"] == "http.response.start")
