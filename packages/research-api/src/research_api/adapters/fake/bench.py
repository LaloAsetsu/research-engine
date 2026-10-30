"""Fake arms and a fake model list: a benchmark run that needs no GPU and no model server.

The answers are made up from the key facts of each question, so the scorer has something to
count, and they differ by arm the way real ones tend to (closed-book is worst, the harness is in
the middle, the oracle is best). They are deterministic: the same run made twice gives the same
file, and a second repeat gives different answers, so the noise floor has something to show. A
word in a question makes it fail on purpose, like the fake ask engine: ``simulate-upstream``
(this question is recorded as an error), ``simulate-unavailable`` (the run is interrupted) and
``simulate-crash``. Everything is synthetic.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from ...domain.ask import AskContext, AskTimings, Citation
from ...domain.errors import UnavailableError, UpstreamError
from ...domain.runs import (
    BARE_PROMPT_MD5,
    CANNOT_ANSWER_PHRASE,
    ORACLE_TIMINGS_NOTE,
    RunAnswer,
    Usage,
    gold_passages,
    question_of,
)
from ...ports.jobs import ArmCall
from ...ports.models import ChatReply, ModelInfo
from .corpus import PAPERS

# A key fact has a fixed hardness; an arm finds the facts below its level (plus a little noise
# that changes with the repeat), so a better arm finds what a worse one finds, and more.
HIT_LEVEL = {"bare": 0.25, "harness": 0.7, "oracle": 0.95}
NOISE = 0.2
RETRIEVED_RATE = 0.8
ONLINE = "fake-model"
OFFLINE = "fake-offline"


def _unit(*parts: object) -> list[float]:
    """Numbers in [0, 1) that depend only on ``parts``."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return [b / 256 for b in digest]


def _fact_text(fact: Mapping[str, Any]) -> str:
    value = fact["value"]
    if fact.get("type") == "expr":
        return f"${value}$"
    if fact.get("unit"):
        return f"{value} {fact['unit']}"
    return str(value)


def _gold_papers(item: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return list(item.get("gold_papers") or [])


class FakeArms:
    def __init__(self, *, pause_s: float = 0.0) -> None:
        self._pause_s = pause_s
        self.asked = 0

    def refusal(self, arm_name: str) -> str | None:
        return None

    def prompt_md5(self, arm_name: str) -> str | None:
        return BARE_PROMPT_MD5 if arm_name == "bare" else None

    async def waiting_for(self, arm_name: str, model: str) -> str | None:
        return None

    async def answer(self, call: ArmCall) -> RunAnswer:
        self.asked += 1
        item = call.item
        text = question_of(item).lower()
        pause = self._pause_s
        if pause:
            await asyncio.sleep(pause)
        if "simulate-unavailable" in text:
            raise UnavailableError("The model server did not answer (a simulated failure).")
        if "simulate-upstream" in text:
            raise UpstreamError("The model server refused the request (a simulated failure).")
        if "simulate-crash" in text:
            raise RuntimeError("simulated crash with the question inside: " + text)
        rolls = _unit(call.arm_name, call.variant, call.model, item["id"], call.repeat)
        facts = list(item.get("key_facts") or [])
        level = HIT_LEVEL.get(call.arm_name, 0.5) + (rolls[4] - 0.5) * NOISE
        hardness = _unit("hardness", item["id"])
        hits = [fact for index, fact in enumerate(facts) if hardness[index % 32] < level]
        if facts and hits:
            answer = "The key points are: " + "; ".join(_fact_text(f) for f in hits) + "."
        elif facts and rolls[0] < 0.5:
            answer = f"{CANNOT_ANSWER_PHRASE}."
        elif facts:
            answer = "This is a standard result of the field and needs no further detail."
        else:
            answer = f"{CANNOT_ANSWER_PHRASE}."
        wall = round(pause, 4)
        if call.arm_name == "bare":
            return RunAnswer(
                answer=answer,
                timings=AskTimings(0.0, wall, wall),
                usage=Usage(1, 120 + int(rolls[1] * 40), 60 + int(rolls[2] * 80)),
                prompt_md5=BARE_PROMPT_MD5,
            )
        if call.arm_name == "oracle":
            contexts = tuple(
                AskContext(p.doc_id, f"{p.doc_id[:16]} pages 1-1", 0, 1, 1, False, p.text)
                for p in gold_passages(item)
            )
            return RunAnswer(
                answer=answer,
                contexts=contexts,
                timings=AskTimings(0.0, wall, wall),
                usage=Usage(truncated=False),
                ext={"timings_note": ORACLE_TIMINGS_NOTE},
            )
        return self._harness(item, answer, hit=bool(hits), rolls=rolls, wall=wall)

    @staticmethod
    def _harness(
        item: Mapping[str, Any], answer: str, *, hit: bool, rolls: Sequence[float], wall: float
    ) -> RunAnswer:
        retrieved = rolls[3] < RETRIEVED_RATE
        golds = _gold_papers(item)
        decoy = PAPERS[int(rolls[2] * len(PAPERS))]
        contexts: list[AskContext] = []
        citations: list[Citation] = []
        if golds and retrieved:
            for paper in golds:
                for passage in paper.get("passages", []):
                    text = passage.get("md_text") or passage.get("source_text", "")
                    contexts.append(
                        AskContext(
                            paper["doc_id"],
                            f"{paper['doc_id'][:16]} pages 1-1",
                            0,
                            1,
                            1,
                            False,
                            text,
                        )
                    )
            if hit:
                citations = [
                    Citation(f"{p['doc_id'][:16]} pages 1-1", p["doc_id"], 1, 1) for p in golds
                ]
        if not citations:
            citations = [Citation(f"{decoy.short_id} pages 1-1", decoy.doc_id, 1, 1)]
            contexts.append(
                AskContext(
                    decoy.doc_id,
                    f"{decoy.short_id} pages 1-1",
                    0,
                    1,
                    1,
                    False,
                    decoy.pages[0].markdown[:400],
                )
            )
        retrieve = round(wall / 2, 4)
        return RunAnswer(
            answer=answer,
            citations=tuple(citations),
            contexts=tuple(contexts),
            timings=AskTimings(retrieve, round(wall - retrieve, 4), wall),
        )


class FakeModelServer:
    """Two models: one that answers and one that is down (so the unavailable state can be seen)."""

    def __init__(self) -> None:
        self._models = (
            ModelInfo(ONLINE, "fake-model (demo)", "synthetic-0", True),
            ModelInfo(OFFLINE, "fake-offline (demo, down)", "synthetic-0", False),
        )

    async def models(self) -> list[ModelInfo]:
        return list(self._models)

    async def get(self, name: str) -> ModelInfo | None:
        return next((m for m in self._models if m.name == name), None)

    async def chat(
        self, name: str, system: str, user: str, *, max_tokens: int, temperature: float
    ) -> ChatReply:
        if name != ONLINE:
            raise UnavailableError("This model does not answer.")
        return ChatReply(f"{CANNOT_ANSWER_PHRASE}.", "stop", 10, 5)
