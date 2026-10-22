"""Driving adapter: questions as JSON lines (``docingest ask --stdin``).

One JSON object per line in, one per line out, in order. The format is the wire format of the
question session (see ``AskSession``); nothing is stored. Parsing and formatting only: the
answering is ``AskSession.ask``.

Request fields: ``question`` (required), ``id`` (echoed back), ``use_index`` (default true),
``bm25`` (``"english"``, ``"always"``, ``"never"`` or null), ``whole_blocks`` (true, false or
null for the ``[qa]`` setting) and ``return_contexts`` (default false). Any other field is an
error. A line that is empty is skipped.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

from ..application.ask import AskOptions, AskResult, AskSession

REQUEST_FIELDS = {"question", "id", "use_index", "bm25", "whole_blocks", "return_contexts"}
KEYWORD_MODES = ("english", "always", "never")


class InvalidRequest(ValueError):
    """The line is not a request. ``request_id`` is its id when that could be read."""

    def __init__(self, message: str, request_id: str | None = None):
        super().__init__(message)
        self.request_id = request_id


def parse_request(line: str) -> tuple[str | None, str, AskOptions, bool]:
    """``(id, question, options, return_contexts)`` of one line; ``InvalidRequest`` otherwise."""
    try:
        body = json.loads(line)
    except ValueError as e:
        raise InvalidRequest(f"not JSON: {e}") from None
    if not isinstance(body, dict):
        raise InvalidRequest("a request is a JSON object")
    request_id = body.get("id")
    if request_id is not None and not isinstance(request_id, str):
        raise InvalidRequest("id must be a string")
    unknown = sorted(set(body) - REQUEST_FIELDS)
    if unknown:
        raise InvalidRequest(f"unknown field {', '.join(unknown)}", request_id)
    question = body.get("question")
    if not isinstance(question, str) or not question.strip():
        raise InvalidRequest("question is required and must be a non-empty string", request_id)
    use_index = body.get("use_index", True)
    keywords = body.get("bm25")
    whole_blocks = body.get("whole_blocks")
    contexts = body.get("return_contexts", False)
    if not isinstance(use_index, bool) or not isinstance(contexts, bool):
        raise InvalidRequest("use_index and return_contexts must be true or false", request_id)
    if keywords is not None and keywords not in KEYWORD_MODES:
        raise InvalidRequest(f"bm25 must be one of {', '.join(KEYWORD_MODES)} or null", request_id)
    if whole_blocks is not None and not isinstance(whole_blocks, bool):
        raise InvalidRequest("whole_blocks must be true, false or null", request_id)
    return request_id, question, AskOptions(use_index, keywords, whole_blocks), contexts


def format_result(request_id: str | None, result: AskResult, *, contexts: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": request_id,
        "answer": result.answer,
        "warnings": list(result.warnings),
        "error": None,
        "sources": list(result.sources),
        "index_generation": result.index_generation,
        "timings": {
            "retrieve_s": result.timings.retrieve_s,
            "answer_s": result.timings.answer_s,
            "wall_s": result.timings.wall_s,
        },
    }
    if contexts:
        out["contexts"] = [
            {
                "doc_id": c.doc_id,
                "name": c.name,
                "first_page": c.first_page,
                "last_page": c.last_page,
                "start": c.start,
                "is_reference": c.is_reference,
                "text": c.text,
            }
            for c in result.contexts
        ]
    return out


def format_error(request_id: str | None, error: Exception, warnings: list[str]) -> dict[str, Any]:
    return {
        "id": request_id,
        "answer": None,
        "warnings": warnings,
        "error": {"kind": type(error).__name__, "message": str(error)},
        "sources": [],
        "index_generation": None,
        "timings": None,
    }


async def serve_lines(
    session: AskSession, lines: Iterable[str], write: Callable[[str], None]
) -> None:
    """Answer each request line with one response line, until ``lines`` ends.

    One bad request or one failing question gives an error line and the loop goes on: only the
    end of the input stops it. Requests are answered one at a time, in order."""
    for line in lines:
        if not line.strip():
            continue
        request_id: str | None = None
        warnings: list[str] = []
        try:
            request_id, question, options, contexts = parse_request(line)
            result = await session.ask(question, options, warnings.append)
            response = format_result(request_id, result, contexts=contexts)
        except Exception as e:  # the answerer's own errors too: a model server may be down
            if isinstance(e, InvalidRequest):
                request_id = e.request_id
            response = format_error(request_id, e, warnings)
        write(json.dumps(response, ensure_ascii=False))
