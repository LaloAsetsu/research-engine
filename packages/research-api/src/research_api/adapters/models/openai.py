"""``ModelServer`` over OpenAI-compatible endpoints (the vLLM servers of ``serving/``).

An endpoint is the address up to and including ``/v1``. Nothing here sends a key: the servers are
on loopback (or Tailscale), and the client ignores the proxy variables of the environment. A
response body is never put in an error message, because a server may echo the prompt (a question
of the set).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence

import httpx

from ...domain.errors import UnavailableError, UpstreamError
from ...ports.models import ChatReply, ModelEntry, ModelInfo

PROBE_TIMEOUT_S = 2.0
CHAT_TIMEOUT_S = 600.0
PROBE_CACHE_S = 3.0
RETRIES = 2
RETRY_STATUSES = frozenset({502, 503, 504})


class OpenAiModels:
    def __init__(
        self,
        entries: Sequence[ModelEntry],
        *,
        probe_timeout_s: float = PROBE_TIMEOUT_S,
        chat_timeout_s: float = CHAT_TIMEOUT_S,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_wait_s: float = 1.0,
    ) -> None:
        self._entries = {e.name: e for e in entries}
        self._order = [e.name for e in entries]
        self._probe_timeout_s = probe_timeout_s
        self._chat_timeout_s = chat_timeout_s
        self._transport = transport
        self._retry_wait_s = retry_wait_s
        self._probed: dict[str, tuple[float, bool]] = {}

    def _client(self, timeout: float) -> httpx.AsyncClient:
        # trust_env=False: a proxy variable of the server's environment must not carry a question
        # of the set (or its answer) through a proxy; the servers are addressed directly.
        return httpx.AsyncClient(timeout=timeout, transport=self._transport, trust_env=False)

    async def _available(self, entry: ModelEntry) -> bool:
        seen = self._probed.get(entry.name)
        if seen is not None and time.monotonic() - seen[0] < PROBE_CACHE_S:
            return seen[1]
        ok = False
        try:
            async with self._client(self._probe_timeout_s) as client:
                reply = await client.get(f"{entry.endpoint.rstrip('/')}/models")
            if reply.status_code == 200:
                ok = True
                listed = reply.json().get("data")
                if isinstance(listed, list):
                    ids = {m.get("id") for m in listed if isinstance(m, dict)}
                    ok = not ids or entry.name in ids
        except (httpx.HTTPError, ValueError, AttributeError):
            ok = False
        self._probed[entry.name] = (time.monotonic(), ok)
        return ok

    async def _info(self, entry: ModelEntry) -> ModelInfo:
        return ModelInfo(entry.name, entry.label, entry.revision, await self._available(entry))

    async def models(self) -> list[ModelInfo]:
        return list(await asyncio.gather(*(self._info(self._entries[n]) for n in self._order)))

    async def get(self, name: str) -> ModelInfo | None:
        entry = self._entries.get(name)
        return None if entry is None else await self._info(entry)

    async def chat(
        self, name: str, system: str, user: str, *, max_tokens: int, temperature: float
    ) -> ChatReply:
        entry = self._entries.get(name)
        if entry is None:
            raise UnavailableError("That model is not in the settings.")
        body = {
            "model": entry.name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        url = f"{entry.endpoint.rstrip('/')}/chat/completions"
        for attempt in range(RETRIES + 1):
            try:
                async with self._client(self._chat_timeout_s) as client:
                    reply = await client.post(url, json=body)
            except (httpx.ConnectTimeout, httpx.ConnectError, httpx.NetworkError):
                # never connected: the server is not there, which is not one slow question
                self._probed.pop(entry.name, None)
                raise UnavailableError(
                    f"The model server of {entry.label} cannot be reached.", retry_after_s=30
                ) from None
            except httpx.TimeoutException:
                raise TimeoutError("the model server took too long") from None
            except httpx.HTTPError:
                self._probed.pop(entry.name, None)
                raise UnavailableError(
                    f"The model server of {entry.label} cannot be reached.", retry_after_s=30
                ) from None
            if reply.status_code in RETRY_STATUSES and attempt < RETRIES:
                await asyncio.sleep(self._retry_wait_s * (attempt + 1))
                continue
            return _reply(reply, entry)
        raise AssertionError("unreachable")  # pragma: no cover


def _reply(reply: httpx.Response, entry: ModelEntry) -> ChatReply:
    if reply.status_code in RETRY_STATUSES:
        raise UnavailableError(f"The model server of {entry.label} is not ready.", retry_after_s=30)
    if reply.status_code != 200:
        raise UpstreamError(f"The model server of {entry.label} refused the request.")
    try:
        data = reply.json()
        choice = data["choices"][0]
        text = choice["message"]["content"] or ""
        usage = data.get("usage") or {}
    except (ValueError, KeyError, IndexError, TypeError):
        raise UpstreamError(
            f"The model server of {entry.label} answered in an unknown shape."
        ) from None
    return ChatReply(
        text=str(text),
        finish=choice.get("finish_reason"),
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
    )
