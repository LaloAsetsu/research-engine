"""The live arms on fake ports: what each arm sends and what it makes of the reply."""

from __future__ import annotations

import pytest
from ask_helpers import ScriptedEngine
from helpers import arun

from research_api.application.arms import ORACLE_REFUSAL, LiveArms
from research_api.domain.ask import AskTimings
from research_api.domain.errors import UnavailableError, UpstreamError
from research_api.domain.runs import BARE_PROMPT_MD5, MODEL_MAX_TOKENS
from research_api.domain.sets import SetRef
from research_api.ports.jobs import ArmCall
from research_api.ports.models import ChatReply, ModelInfo

ITEM = {"id": "DP-001", "question": "  What is J?  ", "answer": "J"}


class Models:
    """A model list the test controls, and the chats it was asked for."""

    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.chats: list[tuple[str, str, str, int, float]] = []
        self.error: BaseException | None = None

    async def models(self) -> list[ModelInfo]:
        return [ModelInfo("m", "The M", None, self.available)]

    async def get(self, name: str) -> ModelInfo | None:
        return (await self.models())[0] if name == "m" else None

    async def chat(
        self, name: str, system: str, user: str, *, max_tokens: int, temperature: float
    ) -> ChatReply:
        self.chats.append((name, system, user, max_tokens, temperature))
        if self.error is not None:
            raise self.error
        return ChatReply("  an answer \n", "stop", 12, 3)


def call(arm: str = "bare", overrides: dict[str, object] | None = None) -> ArmCall:
    return ArmCall(arm, None, overrides or {}, "m", ITEM, 1, SetRef("demo", "v1"))


def arms(models: Models | None = None, engine: ScriptedEngine | None = None) -> LiveArms:
    return LiveArms(engine=engine or ScriptedEngine(), models=models or Models())


@arun
async def test_the_closed_book_arm_asks_the_model_with_the_r_281_prompt() -> None:
    models = Models()
    answer = await arms(models).answer(call("bare"))
    name, system, user, max_tokens, temperature = models.chats[0]
    assert (name, max_tokens, temperature) == ("m", MODEL_MAX_TOKENS, 0.0)
    assert "Question: What is J?\n" in user and system.startswith("Answer in a direct")
    assert answer.answer == "an answer" and answer.contexts == () and answer.citations == ()
    assert answer.prompt_md5 == BARE_PROMPT_MD5
    assert (
        answer.usage.llm_requests,
        answer.usage.prompt_tokens,
        answer.usage.completion_tokens,
    ) == (  # type: ignore[union-attr]
        1,
        12,
        3,
    )
    assert isinstance(answer.timings, AskTimings) and answer.timings.retrieve_s == 0.0


@arun
async def test_a_model_that_fails_fails_the_question_in_the_words_it_chose() -> None:
    models = Models()
    models.error = UpstreamError("The model server refused the request.")
    with pytest.raises(UpstreamError):
        await arms(models).answer(call("bare"))
    models.error = UnavailableError("gone")
    with pytest.raises(UnavailableError):
        await arms(models).answer(call("bare"))


@arun
async def test_the_harness_asks_the_engine_as_a_member_would_with_the_overrides() -> None:
    engine = ScriptedEngine()
    answer = await arms(engine=engine).answer(
        call("harness", {"use_index": False, "bm25": "never", "whole_blocks": True})
    )
    asked = engine.options[0]
    assert asked.text == ITEM["question"]
    assert (asked.options.use_index, asked.options.bm25, asked.options.whole_blocks) == (
        False,
        "never",
        True,
    )
    assert asked.return_contexts
    assert answer.answer == f"Answer to: {asked.text}"
    assert answer.contexts and answer.citations == ()


@arun
async def test_the_harness_defaults_are_the_servers() -> None:
    engine = ScriptedEngine()
    await arms(engine=engine).answer(call("harness"))
    options = engine.options[0].options
    assert (options.use_index, options.bm25, options.whole_blocks) == (True, None, None)


@arun
async def test_the_oracle_is_refused_until_docingest_can_answer_from_passages() -> None:
    live = arms()
    assert live.refusal("oracle") == ORACLE_REFUSAL
    assert live.refusal("bare") is None and live.refusal("harness") is None
    with pytest.raises(UnavailableError):
        await live.answer(call("oracle"))


def test_only_the_closed_book_arm_has_a_prompt() -> None:
    live = arms()
    assert live.prompt_md5("bare") == BARE_PROMPT_MD5
    assert live.prompt_md5("harness") is None and live.prompt_md5("oracle") is None


@arun
async def test_a_closed_book_run_waits_for_a_model_server_that_does_not_answer() -> None:
    models = Models(available=False)
    reason = await arms(models).waiting_for("bare", "m")
    assert reason == "The model server of The M does not answer."
    assert await arms(models).waiting_for("bare", "nobody") == (
        "The model server of nobody does not answer."
    )
    models.available = True
    assert await arms(models).waiting_for("bare", "m") is None


@arun
async def test_a_harness_run_waits_for_the_engine_to_be_ready() -> None:
    engine = ScriptedEngine("warming_up")
    live = arms(engine=engine)
    assert await live.waiting_for("harness", "m") == "The answer engine is starting."
    engine.state, engine.reason = "unavailable", "The index is missing."
    assert await live.waiting_for("harness", "m") == "The index is missing."
    engine.reason = None
    assert await live.waiting_for("harness", "m") == "The answer engine is unavailable."
    engine.state = "ready"
    assert await live.waiting_for("harness", "m") is None
