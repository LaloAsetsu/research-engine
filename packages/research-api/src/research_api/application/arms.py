"""The arms of a run, on the ports of the live service: what answers a benchmark question.

``bare`` asks the model with no papers (the closed-book prompt of R-281), through the model
server. ``harness`` asks through the same answer engine as a person's question, so the run sees
what an ask sees. ``oracle`` hands the same engine the gold passages of the question, and
nothing else: no retrieval, and none of the additions the harness gets (whole blocks, gate lists,
equation headers), so its contexts are exactly the ``md_text`` of the set.
"""

from __future__ import annotations

import time

from ..domain.ask import AskOptions, AskQuestion, AskTimings
from ..domain.runs import (
    BARE_PROMPT_MD5,
    MODEL_MAX_TOKENS,
    MODEL_TEMPERATURE,
    RunAnswer,
    Usage,
    bare_messages,
    gold_passages,
    question_of,
)
from ..ports.ask import AskEngine
from ..ports.jobs import ArmCall
from ..ports.models import ModelServer


class LiveArms:
    def __init__(self, *, engine: AskEngine, models: ModelServer) -> None:
        self._engine = engine
        self._models = models

    def refusal(self, arm_name: str) -> str | None:
        return None

    def prompt_md5(self, arm_name: str) -> str | None:
        return BARE_PROMPT_MD5 if arm_name == "bare" else None

    async def waiting_for(self, arm_name: str, model: str) -> str | None:
        if arm_name == "bare":
            info = await self._models.get(model)
            if info is None or not info.available:
                label = info.label if info else model
                return f"The model server of {label} does not answer."
            return None
        health = self._engine.health()
        if health.state == "warming_up":
            return "The answer engine is starting."
        if health.state == "unavailable":
            return health.reason or "The answer engine is unavailable."
        return None

    async def answer(self, call: ArmCall) -> RunAnswer:
        if call.arm_name == "bare":
            return await self._bare(call)
        if call.arm_name == "harness":
            return await self._harness(call)
        return await self._oracle(call)

    async def _bare(self, call: ArmCall) -> RunAnswer:
        system, user = bare_messages(question_of(call.item))
        started = time.perf_counter()
        reply = await self._models.chat(
            call.model,
            system,
            user,
            max_tokens=MODEL_MAX_TOKENS,
            temperature=MODEL_TEMPERATURE,
        )
        wall = round(time.perf_counter() - started, 4)
        return RunAnswer(
            answer=reply.text.strip(),
            timings=AskTimings(0.0, wall, wall),
            usage=Usage(1, reply.prompt_tokens, reply.completion_tokens),
            prompt_md5=BARE_PROMPT_MD5,
        )

    async def _harness(self, call: ArmCall) -> RunAnswer:
        overrides = call.overrides
        question = AskQuestion(
            question_of(call.item),
            AskOptions(
                use_index=bool(overrides.get("use_index", True)),
                bm25=overrides.get("bm25"),
                whole_blocks=overrides.get("whole_blocks"),
            ),
            return_contexts=True,
        )
        answer = await self._engine.ask(question, _ignore_phase)
        return RunAnswer.from_ask(answer)

    async def _oracle(self, call: ArmCall) -> RunAnswer:
        answer = await self._engine.answer_from(question_of(call.item), gold_passages(call.item))
        return RunAnswer.from_oracle(answer)


def _ignore_phase(_phase: str) -> None:
    return None
