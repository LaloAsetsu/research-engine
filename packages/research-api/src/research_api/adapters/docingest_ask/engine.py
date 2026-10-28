"""The answer engine on docingest's ``AskSession``: the index stays open between questions."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable

from docingest.application.ask import AskOptions, AskSession
from docingest.domain.errors import IndexMismatchError, IndexNotReadyError, NotConfiguredError
from docingest.domain.models import AnswerModel

from ...domain.ask import (
    AskAnswer,
    AskContext,
    AskQuestion,
    AskTimings,
    Phase,
    read_formatted_answer,
)
from ...domain.runs import ModelFacts
from ...ports.ask import EngineHealth
from .errors import classify, start_reason

log = logging.getLogger("research_api.ask")

# Warming up opens the index and makes one search. The embedder it calls may accept the
# connection and never answer, which would hold the engine in `warming_up` (every ask a 503)
# for as long as docingest's own timeout; past this it is `unavailable`, with a reason.
WARM_UP_LIMIT_S = 90.0


def served_name(model: AnswerModel) -> str:
    """The name a ``[[models]]`` entry gives this model: the last ``/`` part of docingest's name.

    For a litellm model (``DOCINGEST_LLM`` or ``[qa] llm``) ``name`` is the litellm string
    (``openai/qwen-local``) and the part after the provider is what the server serves it as
    (``qwen-local``). For the pinned model ``name`` is the repository (``org/repo``) and the part
    after the owner is ``repo``.
    """
    return model.name.rsplit("/", 1)[-1]


class DocingestAskEngine:
    def __init__(
        self,
        open_session: Callable[[], AskSession],
        *,
        read_answer_model: Callable[[], AnswerModel],
        warm_up_limit_s: float = WARM_UP_LIMIT_S,
    ) -> None:
        """``open_session`` builds the session (its adapters, which may fail to import or to
        read their settings); it runs in a worker thread on the first use. Warming up that takes
        longer than ``warm_up_limit_s`` leaves the engine ``unavailable`` until a question works.
        ``read_answer_model`` names the model docingest answers with (settings only)."""
        self._open_session = open_session
        self._read_answer_model = read_answer_model
        self._warm_up_limit_s = warm_up_limit_s
        self._session: AskSession | None = None
        self._lock = threading.Lock()
        self._health = EngineHealth("warming_up")

    def _opened(self) -> AskSession:
        with self._lock:
            if self._session is None:
                self._session = self._open_session()
            return self._session

    def health(self) -> EngineHealth:
        return self._health

    async def warm_up(self) -> None:
        try:
            async with asyncio.timeout(self._warm_up_limit_s):
                session = await asyncio.to_thread(self._opened)
                await session.warm_up()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._health = EngineHealth("unavailable", start_reason(error))
            # docingest's own messages say what to run; anything else is logged by type only
            log.error(
                "the answer engine did not start",
                extra={"event": "ask.warmup_failed", "kind": type(error).__name__},
            )
        else:
            self._health = EngineHealth("ready", None, self._health.index_generation)

    async def ask(self, question: AskQuestion, on_phase: Callable[[Phase], None]) -> AskAnswer:
        options = AskOptions(
            use_index=question.options.use_index,
            keywords=question.options.bm25,
            whole_blocks=question.options.whole_blocks,
        )
        try:
            session = await asyncio.to_thread(self._opened)
            result = await session.ask(question.text, options, on_phase=on_phase)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            translated = classify(error)
            if translated is None:
                raise
            if isinstance(error, IndexNotReadyError | IndexMismatchError | NotConfiguredError):
                # A model server that is down is not recorded: it may be back for the next one.
                self._health = EngineHealth("unavailable", start_reason(error))
            raise translated from None
        self._health = EngineHealth("ready", None, result.index_generation)
        contexts = tuple(
            AskContext(
                doc_id=c.doc_id,
                name=c.name,
                start=c.start,
                first_page=c.first_page,
                last_page=c.last_page,
                is_reference=c.is_reference,
                text=c.text,
            )
            for c in result.contexts
        )
        read = read_formatted_answer(
            result.answer,
            question=question.text,
            full_ids=[*result.sources, *(c.doc_id for c in contexts)],
        )
        return AskAnswer(
            answer=read.answer,
            references_text=read.references_text,
            citations=read.citations,
            contexts=contexts,
            sources=tuple(result.sources),
            warnings=tuple(result.warnings),
            index_generation=result.index_generation,
            timings=AskTimings(
                result.timings.retrieve_s, result.timings.answer_s, result.timings.wall_s
            ),
        )

    def answer_model(self) -> ModelFacts:
        try:
            model = self._read_answer_model()
        except Exception as error:
            translated = classify(error)
            if translated is None:
                raise
            raise translated from None
        return ModelFacts(served_name(model), None, model.revision)

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
