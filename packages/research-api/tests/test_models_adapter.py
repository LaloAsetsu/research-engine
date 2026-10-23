"""The OpenAI-compatible model servers, against a fake transport: no socket, no GPU."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from helpers import arun

from research_api.adapters.models import OpenAiModels
from research_api.domain.errors import UnavailableError, UpstreamError
from research_api.ports.models import ModelEntry

ENTRIES = [
    ModelEntry("qwen-local", "http://127.0.0.1:8001/v1", "Qwen local", "abc123"),
    ModelEntry("other", "http://127.0.0.1:8011/v1/", "Other", None),
]


def served(handler: Callable[[httpx.Request], httpx.Response]) -> OpenAiModels:
    return OpenAiModels(ENTRIES, transport=httpx.MockTransport(handler), retry_wait_s=0)


def listing(*ids: str) -> httpx.Response:
    return httpx.Response(200, json={"data": [{"id": i} for i in ids]})


@arun
async def test_models_are_listed_in_settings_order_with_what_their_servers_say() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.port == 8001:
            return listing("qwen-local")
        raise httpx.ConnectError("refused")

    found = await served(handler).models()
    assert [(m.name, m.label, m.revision, m.available) for m in found] == [
        ("qwen-local", "Qwen local", "abc123", True),
        ("other", "Other", None, False),
    ]


@arun
async def test_a_server_that_does_not_serve_the_name_is_not_available() -> None:
    models = served(lambda request: listing("something-else"))
    assert [m.available for m in await models.models()] == [False, False]


@arun
async def test_a_server_that_lists_nothing_or_something_odd_is_taken_at_its_word_or_refused() -> (
    None
):
    assert (await served(lambda r: httpx.Response(200, json={"data": []})).get("other")).available  # type: ignore[union-attr]
    assert (
        await served(lambda r: httpx.Response(200, json={"object": "list"})).get("other")
    ).available  # type: ignore[union-attr]
    assert not (await served(lambda r: httpx.Response(200, text="not json")).get("other")).available  # type: ignore[union-attr]
    assert not (await served(lambda r: httpx.Response(503)).get("other")).available  # type: ignore[union-attr]
    assert not (await served(lambda r: httpx.Response(200, json=[1])).get("other")).available  # type: ignore[union-attr]


@arun
async def test_a_model_not_in_the_settings_is_not_found() -> None:
    assert await served(lambda r: listing("x")).get("nobody") is None


@arun
async def test_a_probe_is_remembered_for_a_moment() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return listing("qwen-local", "other")

    models = served(handler)
    await models.models()
    await models.models()
    assert calls == ["/v1/models", "/v1/models"]  # one probe for each of the two, not four


def chat_ok(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    assert body["model"] == "qwen-local" and body["temperature"] == 0.0 and body["max_tokens"] == 64
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert "authorization" not in request.headers  # no key is ever sent
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": "J"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 2},
        },
    )


@arun
async def test_a_chat_goes_to_the_endpoint_and_comes_back_with_its_usage() -> None:
    reply = await served(chat_ok).chat("qwen-local", "sys", "user", max_tokens=64, temperature=0.0)
    assert (reply.text, reply.finish, reply.prompt_tokens, reply.completion_tokens) == (
        "J",
        "stop",
        11,
        2,
    )


@arun
async def test_the_trailing_slash_of_an_endpoint_does_not_matter() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    models = served(handler)
    await models.chat("other", "s", "u", max_tokens=1, temperature=0.0)
    assert seen == ["http://127.0.0.1:8011/v1/chat/completions"]


@arun
async def test_a_chat_with_no_text_is_an_empty_answer() -> None:
    models = served(
        lambda r: httpx.Response(200, json={"choices": [{"message": {"content": None}}]})
    )
    reply = await models.chat("other", "s", "u", max_tokens=8, temperature=0.0)
    assert reply.text == "" and reply.finish is None and reply.prompt_tokens is None


@arun
async def test_a_refusal_is_an_upstream_error_that_does_not_repeat_the_body() -> None:
    models = served(lambda r: httpx.Response(400, text="the prompt was: SECRET QUESTION"))
    with pytest.raises(UpstreamError) as caught:
        await models.chat("qwen-local", "s", "u", max_tokens=8, temperature=0.0)
    assert "SECRET" not in str(caught.value) and "Qwen local" in caught.value.detail


@arun
@pytest.mark.parametrize("body", [{}, {"choices": []}, {"choices": [{"message": 3}]}, "text"])
async def test_an_answer_in_a_shape_nobody_knows_is_an_upstream_error(body: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(body, str):
            return httpx.Response(200, text=body)
        return httpx.Response(200, json=body)

    with pytest.raises(UpstreamError, match="unknown shape"):
        await served(handler).chat("qwen-local", "s", "u", max_tokens=8, temperature=0.0)


@arun
async def test_a_busy_server_is_asked_again_and_then_the_run_is_told_it_is_not_ready() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(503)

    with pytest.raises(UnavailableError, match="not ready"):
        await served(handler).chat("qwen-local", "s", "u", max_tokens=8, temperature=0.0)
    assert len(attempts) == 3

    flaky = iter([httpx.Response(502), chat_ok_response()])
    reply = await served(lambda r: next(flaky)).chat(
        "qwen-local", "s", "u", max_tokens=8, temperature=0.0
    )
    assert reply.text == "ok"


def chat_ok_response() -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})


@arun
async def test_a_server_that_cannot_be_reached_makes_the_model_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    models = served(handler)
    with pytest.raises(UnavailableError, match="cannot be reached"):
        await models.chat("qwen-local", "s", "u", max_tokens=8, temperature=0.0)


@arun
async def test_a_slow_server_is_a_timeout_not_an_outage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(TimeoutError):
        await served(handler).chat("qwen-local", "s", "u", max_tokens=8, temperature=0.0)


@arun
async def test_a_model_that_is_not_in_the_settings_cannot_be_called() -> None:
    with pytest.raises(UnavailableError, match="not in the settings"):
        await served(chat_ok).chat("nobody", "s", "u", max_tokens=8, temperature=0.0)


@arun
async def test_a_server_that_never_connects_is_an_outage_not_a_slow_question() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("no route")

    with pytest.raises(UnavailableError, match="cannot be reached"):
        await served(handler).chat("qwen-local", "s", "u", max_tokens=8, temperature=0.0)
