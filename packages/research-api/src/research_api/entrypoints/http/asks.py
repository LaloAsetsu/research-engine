"""Asking: ``POST /asks``, the event stream, the answer, cancel, and the papers a citation names.

All of it is for signed-in members. Nothing here is cached by a browser or a proxy: answers and
papers hold corpus text.
"""

from __future__ import annotations

from collections.abc import AsyncIterable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Header, Path, Query, Request, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from starlette.concurrency import run_in_threadpool

from ...application.auth import Authenticated
from ...bootstrap import Container
from ...domain.ask import AskEvent, EventKind
from ...domain.documents import DOC_ID_PATTERN
from ...domain.errors import UnauthenticatedError
from .auth import DEFAULT, INVALID, PROTECTED
from .schemas import (
    AskAccepted,
    AskErrorOut,
    AskRequestBody,
    AskResponse,
    AskView,
    DocumentListOut,
    DocumentOut,
    DocumentPageOut,
    Problem,
)
from .security import SESSION_COOKIE, container_of, member

router = APIRouter(prefix="/asks", tags=["asks"], responses={**DEFAULT, **PROTECTED})
documents_router = APIRouter(
    prefix="/documents", tags=["documents"], responses={**DEFAULT, **PROTECTED}
)

NOT_YOURS: dict[int | str, dict[str, Any]] = {
    404: {"model": Problem, "description": "No such question (or it is not yours)."}
}
Member = Annotated[Authenticated, Depends(member)]
DocId = Annotated[
    str,
    Path(
        pattern=DOC_ID_PATTERN,
        description="A document id, or the first 16 or more hex characters of one.",
    ),
]


class StreamEnded(Exception):
    """The reader has seen the last event of a finished ask: answer 204 so it stops asking."""


def install_ask_handlers(app: FastAPI) -> None:
    async def on_ended(request: Request, exc: Exception) -> Response:
        return Response(status_code=204)

    app.add_exception_handler(StreamEnded, on_ended)


@router.post(
    "",
    operation_id="createAsk",
    summary="Ask a question",
    status_code=202,
    responses={
        422: {"model": Problem, "description": "The request is not valid."},
        429: {"model": Problem, "description": "You have too many questions waiting."},
        503: {
            "model": Problem,
            "description": "The engine is starting or the queue is full; see `Retry-After`.",
        },
    },
)
async def create_ask(body: AskRequestBody, request: Request, who: Member) -> AskAccepted:
    """Queues the question for the model servers, which answer one at a time, and answers `202`
    at once. Follow it with `GET /asks/{ask_id}/events`."""
    done = container_of(request).asks.submit(who.user.username, body.to_domain())
    return AskAccepted(ask_id=done.ask_id, position=done.position)


def _open_stream(
    ask_id: str,
    request: Request,
    who: Member,
    last_event_id: Annotated[
        int | None, Header(description="Set by EventSource on reconnect.")
    ] = None,
) -> int | None:
    if container_of(request).asks.replay_is_empty(who.user.username, ask_id, last_event_id):
        raise StreamEnded
    return last_event_id


def _event(ask_id: str, event: AskEvent) -> ServerSentEvent:
    data: Any
    match event.kind:
        case EventKind.QUEUED:
            data = {"ask_id": ask_id, "position": event.position}
        case EventKind.ANSWER:
            assert event.answer is not None
            data = AskResponse.from_domain(ask_id, event.answer)
        case EventKind.ERROR:
            assert event.failure is not None
            data = AskErrorOut.from_domain(ask_id, event.failure)
        case _:
            data = {"ask_id": ask_id}
    return ServerSentEvent(data=data, event=event.kind.value, id=str(event.id))


async def _still_signed_in(container: Container, token: str | None) -> bool:
    try:
        await run_in_threadpool(container.auth.authenticate, token)
    except UnauthenticatedError:
        return False
    return True


@router.get(
    "/{ask_id}/events",
    operation_id="followAsk",
    summary="Follow a question as it is answered",
    response_class=EventSourceResponse,
    responses={
        **NOT_YOURS,
        204: {"description": "The question is over and the reader has seen its last event."},
        422: INVALID,
    },
)
async def follow_ask(
    ask_id: str,
    request: Request,
    who: Member,
    last_event_id: Annotated[int | None, Depends(_open_stream)],
) -> AsyncIterable[ServerSentEvent]:
    """Server-sent events, in order, each with an `id` (a reconnecting browser resumes after the
    last one it saw): `queued` (`{ask_id, position}`, again whenever the position changes),
    `retrieving`, `answering` (`{ask_id}`), then one of `answer` (an `AskResponse`), `error` (an
    `AskErrorOut`) or `cancelled`. The stream ends after that one. A comment line is sent every
    15 seconds so proxies keep the connection open. A plain `GET`: the browser's `EventSource`
    sends the session cookie, and no CSRF header is needed because nothing changes. The session
    is checked again before the `answer` is sent: a browser that signed out, or whose session
    ended, while the question was being answered gets no answer and the stream just ends."""
    container = container_of(request)
    token = request.cookies.get(SESSION_COOKIE)
    async for event in container.asks.events(who.user.username, ask_id, last_event_id):
        if event.kind is EventKind.ANSWER and not await _still_signed_in(container, token):
            return
        yield _event(ask_id, event)


@router.get(
    "/{ask_id}",
    operation_id="getAsk",
    summary="Where a question is, and its answer once done",
    responses=NOT_YOURS,
)
async def get_ask(ask_id: str, request: Request, who: Member) -> AskView:
    """Kept in memory for 30 minutes after the answer, never written to disk. Another member's
    question is a `404`."""
    return AskView.from_domain(container_of(request).asks.get(who.user.username, ask_id))


@router.delete(
    "/{ask_id}",
    operation_id="cancelAsk",
    summary="Cancel a question, or forget a finished one",
    status_code=204,
    response_class=Response,
    responses=NOT_YOURS,
)
async def cancel_ask(ask_id: str, request: Request, who: Member) -> Response:
    """A waiting question is taken out of the queue. A running one is not stopped (the model
    server is already working), but its answer is dropped when it arrives. For a question that is
    over, this forgets it and its answer at once."""
    container_of(request).asks.cancel(who.user.username, ask_id)
    return Response(status_code=204)


@documents_router.get(
    "",
    operation_id="listDocuments",
    summary="Find papers by title or id",
    responses={422: INVALID},
)
def list_documents(
    request: Request,
    who: Member,
    query: Annotated[
        str, Query(max_length=200, description="Part of a title, or the start of an id.")
    ] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DocumentListOut:
    return DocumentListOut.from_domain(container_of(request).documents.search(query, limit))


@documents_router.get(
    "/{doc_id}",
    operation_id="getDocument",
    summary="A paper's title, source and pages",
    responses={
        404: {"model": Problem, "description": "No such document."},
        422: INVALID,
        409: {"model": Problem, "description": "The id prefix names more than one paper."},
    },
)
def get_document(doc_id: DocId, request: Request, who: Member) -> DocumentOut:
    return DocumentOut.from_domain(container_of(request).documents.get(doc_id))


@documents_router.get(
    "/{doc_id}/pages/{page}",
    operation_id="getDocumentPage",
    summary="One page of a paper as Markdown",
    responses={
        404: {"model": Problem, "description": "No such document or page."},
        422: INVALID,
        409: {"model": Problem, "description": "The id prefix names more than one paper."},
    },
)
def get_document_page(
    doc_id: DocId,
    page: Annotated[int, Path(ge=1, description="1-based, as the page markers of the paper.")],
    request: Request,
    who: Member,
) -> DocumentPageOut:
    """For a LaTeX source the markers number sections, not printed pages; `kind` says which."""
    return DocumentPageOut.from_domain(container_of(request).documents.page(doc_id, page))
