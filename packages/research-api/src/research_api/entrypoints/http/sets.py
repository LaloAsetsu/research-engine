"""Question sets: the catalog, drafts, items, validation, import and export, reviews, freezing.

Every route needs a signed-in member. Responses are never cached (the items are corpus text) and
carry the version's ETag where it matters: a change to a draft must send it back in ``If-Match``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, Path, Query, Request, Response
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from ...application.auth import Authenticated
from ...bootstrap import Container
from ...domain import sets as rules
from ...domain.circuits import read_json_strict
from ...domain.errors import TooLargeError
from ...domain.sets import (
    DEFAULT_PAGE,
    MAX_PAGE,
    Actor,
    ItemFilter,
    ReviewState,
    SetRef,
    etag_of,
)
from .schemas import Problem
from .security import SESSION_COOKIE, container_of, member, signed_in
from .set_schemas import (
    NAME_REGEX,
    VERSION_REGEX,
    FreezePlanOut,
    FreezePreview,
    FreezeResult,
    ImportResult,
    ImportShapeOut,
    Item,
    ItemDetail,
    ItemPage,
    NewReview,
    NewSet,
    ProblemAt,
    ReviewLine,
    SetOut,
    SetVersionOut,
    ValidateRequest,
    Validation,
)

NO_STORE = {"Cache-Control": "no-store"}

NameParam = Annotated[str, Path(pattern=NAME_REGEX, max_length=64, description="The set's name.")]
VersionParam = Annotated[str, Path(pattern=VERSION_REGEX, description="The version, like `v1`.")]
ItemIdParam = Annotated[
    str, Path(pattern=f"^{rules.ITEM_ID_PATTERN}$", max_length=200, description="The item's id.")
]
IfMatch = Annotated[
    str | None,
    Header(
        description=(
            "The `etag` of the draft you read (its `ETag` header). `428` without it (and for "
            "`*`, which does not say which version you read), `409` when the draft has changed "
            "since."
        )
    ),
]

READ_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": Problem, "description": "No such set, version or item."},
    422: {"model": Problem, "description": "The request is not valid."},
}
BAD_JSON: dict[int | str, dict[str, Any]] = {
    400: {
        "model": Problem,
        "description": "The body is not valid JSON, or a JSON key is written twice.",
    },
    413: {"model": Problem, "description": "The body is over the cap."},
}
WRITE_ERRORS: dict[int | str, dict[str, Any]] = {
    **READ_ERRORS,
    403: {
        "model": Problem,
        "description": "The version is frozen, or you are neither its owner nor an admin "
        "(or the CSRF header is missing).",
    },
    409: {
        "model": Problem,
        "description": "The draft changed since you read it (`If-Match`), or the item has review "
        "decisions and its text is fixed (type `item-has-decisions`: propose the change with an "
        "`edit` decision).",
    },
    428: {"model": Problem, "description": "`If-Match` is missing."},
}
PLAN_ETAG_HEADER = {
    "ETag": {
        "description": "Names this freeze plan (quoted); send it in `If-Match` to freeze.",
        "schema": {"type": "string"},
    }
}
ETAG_HEADER = {
    "ETag": {"description": "The version's etag (its md5, quoted).", "schema": {"type": "string"}}
}


class _BoundedRequest(Request):
    """A request whose body is read with a size cap, stopping as soon as it is over."""

    limit = 0

    async def body(self) -> bytes:
        if not hasattr(self, "_body"):
            declared = self.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > self.limit:
                raise self._too_large()
            chunks: list[bytes] = []
            total = 0
            async for chunk in self.stream():
                total += len(chunk)
                if total > self.limit:
                    raise self._too_large()
                chunks.append(chunk)
            self._body = b"".join(chunks)
        return self._body

    def _too_large(self) -> TooLargeError:
        return TooLargeError(f"The request body is over {self.limit} bytes.")


def _limit_for(template: str) -> int:
    """The body cap of a route, by its template (an item can be called ``import``)."""
    if template.endswith(("/sets/import", "/sets/validate")):
        return rules.MAX_IMPORT_BYTES
    if template.endswith("/versions/{version}/reviews"):
        return rules.MAX_REVIEW_BYTES
    return rules.MAX_ITEM_BYTES


def _is_json_body(template: str) -> bool:
    """Every body but the import's is JSON (the import is the file itself)."""
    return not template.endswith("/sets/import")


def check_caller(request: Request) -> None:
    member(signed_in(request, request.cookies.get(SESSION_COOKIE)))


class CappedRoute(APIRoute):
    """Checks the caller, caps the body before FastAPI reads it and refuses a key written twice.

    The caller is checked first, so a request with no session is a 401 whatever its body holds and
    costs no body read. The router's ``member`` dependency checks again and puts the session
    cookie in the OpenAPI contract.

    FastAPI would parse a JSON body with ``json.loads``, which keeps the last value of a repeated
    key without a word. qa-bench refuses a repeated key in a file, so the body is read as text
    first and held to the same rule (and ``NaN`` and ``Infinity`` are refused with it).
    """

    def get_route_handler(self) -> Callable[[Request], Any]:
        original = super().get_route_handler()
        template = self.path

        async def handler(request: Request) -> Response:
            await run_in_threadpool(check_caller, request)
            bounded = _BoundedRequest(request.scope, request.receive)
            bounded.limit = _limit_for(template)
            body = await bounded.body()
            if body and _is_json_body(template):
                read_json_strict(body, what="The request body")
            response: Response = await original(bounded)
            return response

        return handler


def _no_store(response: Response) -> None:
    response.headers.update(NO_STORE)


router = APIRouter(
    prefix="/sets",
    tags=["sets"],
    route_class=CappedRoute,
    dependencies=[Depends(member), Depends(_no_store)],
    responses={"default": {"model": Problem, "description": "A problem."}},
)


def _actor(who: Authenticated) -> Actor:
    return Actor.of(who.user)


def _etag(response: Response, md5: str) -> None:
    response.headers["ETag"] = etag_of(md5)


@router.get("", operation_id="listSets", summary="The catalog of question sets")
def list_sets(request: Request) -> list[SetOut]:
    """Every set with its versions, each with its status, md5, items, owner and review progress."""
    return [SetOut.from_domain(e) for e in container_of(request).sets.manage.list_sets()]


@router.post(
    "",
    operation_id="createSet",
    summary="Start a draft",
    status_code=201,
    responses={
        409: {"model": Problem, "description": "A set with that name exists (copy it instead)."},
        404: READ_ERRORS[404],
        422: READ_ERRORS[422],
        **BAD_JSON,
    },
)
def create_set(
    body: NewSet,
    request: Request,
    response: Response,
    who: Annotated[Authenticated, Depends(member)],
) -> SetVersionOut:
    """An empty draft under a new name, or, with `copy_from`, the next version of an existing
    set as a copy of one of its versions."""
    row = container_of(request).sets.manage.create(
        _actor(who), name=body.name, corpus=body.corpus, copy_from=body.copy_from
    )
    response.headers["Location"] = f"/api/v1/sets/{row.ref.name}/versions/{row.ref.version}"
    _etag(response, row.md5)
    return SetVersionOut.from_domain(row)


@router.post(
    "/validate",
    operation_id="validateSet",
    summary="Check an item or a file",
    responses={**BAD_JSON},
)
def validate(body: ValidateRequest, request: Request) -> Validation:
    """A dry run against `qset/1`: every problem with its place, and nothing is stored."""
    errors = container_of(request).sets.manage.validate(text=body.text, item=body.item)
    return Validation(ok=not errors, errors=[ProblemAt.from_error(e) for e in errors])


@router.get(
    "/import/shapes", operation_id="listImportShapes", summary="Older formats that can be imported"
)
def list_import_shapes(request: Request) -> list[ImportShapeOut]:
    return [ImportShapeOut.from_domain(s) for s in container_of(request).sets.manage.shapes()]


@router.post(
    "/import",
    operation_id="importSet",
    summary="Import a file as a new draft",
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "description": "The file itself: a `qset/1` JSON-lines file (plain or gzip), or "
            "the file of an older shape named in `shape`.",
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
    responses={
        409: {"model": Problem, "description": "That name and version exist."},
        413: {"model": Problem, "description": "The file is over the cap (20 MiB)."},
        422: {"model": Problem, "description": "The file was refused; `errors` says why."},
    },
)
async def import_set(
    request: Request,
    response: Response,
    who: Annotated[Authenticated, Depends(member)],
    name: Annotated[
        str | None,
        Query(
            pattern=NAME_REGEX,
            max_length=64,
            description="The new set's name. Default: the name in the file's header.",
        ),
    ] = None,
    version: Annotated[
        str | None,
        Query(pattern=VERSION_REGEX, description="Default: the file's, if free, else the next."),
    ] = None,
    shape: Annotated[
        str | None,
        Query(description="An older shape to convert (see `/sets/import/shapes`)."),
    ] = None,
    corpus: Annotated[
        str | None, Query(max_length=128, description="For shapes with no corpus.")
    ] = None,
    source_file: Annotated[
        str | None, Query(max_length=200, description="The original file name, recorded.")
    ] = None,
) -> ImportResult:
    """Nothing is stored unless the whole file is accepted. A `qset/1` file whose header already
    names the target is stored byte for byte, so its md5 is the one it came with."""
    data = await request.body()
    container: Container = container_of(request)
    outcome = await run_in_threadpool(
        container.sets.manage.import_set,
        _actor(who),
        data,
        name=name,
        version=version,
        shape=shape,
        corpus=corpus,
        source_file=source_file,
    )
    ref = outcome.version.ref
    response.headers["Location"] = f"/api/v1/sets/{ref.name}/versions/{ref.version}"
    _etag(response, outcome.version.md5)
    return ImportResult.from_domain(outcome)


@router.get(
    "/{name}/versions/{version}",
    operation_id="getSetVersion",
    summary="A version and a page of its items",
    responses={**READ_ERRORS, 200: {"headers": ETAG_HEADER}},
)
def get_set_version(
    name: NameParam,
    version: VersionParam,
    request: Request,
    response: Response,
    form: Annotated[
        str | None, Query(description="`single`, `multi_chain`, `circuit_verify`...")
    ] = None,
    content: Annotated[str | None, Query(description="`math`, `circuit` or `other`.")] = None,
    lang: Annotated[str | None, Query(description="`en` or `es`.")] = None,
    split: Annotated[str | None, Query(description="`dev` or `test`.")] = None,
    tag: Annotated[str | None, Query(description="`key=value`, or `key` for any value.")] = None,
    review: Annotated[ReviewState | None, Query(description="The review state.")] = None,
    q: Annotated[
        str | None, Query(max_length=200, description="Text in the id, question, answer or notes.")
    ] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = DEFAULT_PAGE,
) -> ItemPage:
    """The header, the review counts and the matching items (id, form, language, tags, the
    question's start and the review state). Read one item for all of it."""
    flt = ItemFilter(
        form=form, content=content, lang=lang, split=split, tag=tag, review=review, q=q
    )
    view = container_of(request).sets.manage.get_version(
        SetRef(name, version), flt, offset=offset, limit=limit
    )
    _etag(response, view.version.md5)
    return ItemPage.from_domain(view)


@router.get(
    "/{name}/versions/{version}/items/{item_id}",
    operation_id="getSetItem",
    summary="One item with its reviews and its validation",
    responses={**READ_ERRORS, 200: {"headers": ETAG_HEADER}},
)
def get_set_item(
    name: NameParam,
    version: VersionParam,
    item_id: ItemIdParam,
    request: Request,
    response: Response,
) -> ItemDetail:
    view = container_of(request).sets.manage.get_item(SetRef(name, version), item_id)
    _etag(response, view.version.md5)
    return ItemDetail.from_domain(view)


@router.put(
    "/{name}/versions/{version}/items/{item_id}",
    operation_id="putSetItem",
    summary="Replace one item of a draft",
    responses={**WRITE_ERRORS, **BAD_JSON, 200: {"headers": ETAG_HEADER}},
)
def put_set_item(
    name: NameParam,
    version: VersionParam,
    item_id: ItemIdParam,
    item: Annotated[Item, Body(description="The whole item, a `qset/1` item.")],
    request: Request,
    response: Response,
    who: Annotated[Authenticated, Depends(member)],
    if_match: IfMatch = None,
) -> ItemDetail:
    """Owner or admin, drafts only. `422` lists each problem with a JSON pointer into the item.
    An item that people have already decided on (accept, edit or reject) keeps its text: the
    answer is `409` with the type `item-has-decisions`, because those decisions are about the
    text they read. Put the change in an `edit` decision, which is applied at the freeze."""
    view = container_of(request).sets.manage.put_item(
        _actor(who), SetRef(name, version), item_id, item, if_match
    )
    _etag(response, view.version.md5)
    return ItemDetail.from_domain(view)


@router.post(
    "/{name}/versions/{version}/items",
    operation_id="addSetItem",
    summary="Add an item to a draft",
    status_code=201,
    responses={**WRITE_ERRORS, **BAD_JSON, 201: {"headers": ETAG_HEADER}},
)
def add_set_item(
    name: NameParam,
    version: VersionParam,
    item: Annotated[Item, Body(description="A `qset/1` item. `corpus` defaults to the set's.")],
    request: Request,
    response: Response,
    who: Annotated[Authenticated, Depends(member)],
    if_match: IfMatch = None,
) -> ItemDetail:
    view = container_of(request).sets.manage.add_item(
        _actor(who), SetRef(name, version), item, if_match
    )
    _etag(response, view.version.md5)
    response.headers["Location"] = f"/api/v1/sets/{name}/versions/{version}/items/{view.item['id']}"
    return ItemDetail.from_domain(view)


@router.delete(
    "/{name}/versions/{version}/items/{item_id}",
    operation_id="deleteSetItem",
    summary="Remove an item from a draft",
    responses={**WRITE_ERRORS, 200: {"headers": ETAG_HEADER}},
)
def delete_set_item(
    name: NameParam,
    version: VersionParam,
    item_id: ItemIdParam,
    request: Request,
    response: Response,
    who: Annotated[Authenticated, Depends(member)],
    if_match: IfMatch = None,
) -> SetVersionOut:
    """`409` when another item is a mirror of this one."""
    row = container_of(request).sets.manage.delete_item(
        _actor(who), SetRef(name, version), item_id, if_match
    )
    _etag(response, row.md5)
    return SetVersionOut.from_domain(row)


@router.get(
    "/{name}/versions/{version}/export",
    operation_id="exportSetVersion",
    summary="Download the file",
    responses={
        **READ_ERRORS,
        200: {
            "description": "The `qset/1` file exactly as stored: its md5 is the version's md5.",
            "content": {"application/x-ndjson": {"schema": {"type": "string", "format": "binary"}}},
            "headers": ETAG_HEADER,
        },
    },
)
def export_set_version(name: NameParam, version: VersionParam, request: Request) -> Response:
    row, data = container_of(request).sets.manage.export(SetRef(name, version))
    return Response(
        content=data,
        media_type="application/x-ndjson",
        headers={
            **NO_STORE,
            "ETag": etag_of(row.md5),
            "Content-Disposition": f'attachment; filename="{row.ref.file_stem}.qset.jsonl"',
        },
    )


@router.get(
    "/{name}/versions/{version}/freeze",
    operation_id="previewFreeze",
    summary="What freezing would do",
    responses={
        **READ_ERRORS,
        403: {"model": Problem, "description": "The version is frozen already."},
        200: {"headers": PLAN_ETAG_HEADER},
    },
)
def preview_freeze(
    name: NameParam, version: VersionParam, request: Request, response: Response
) -> FreezePreview:
    """The reviews it would apply, by item, and the `etag` of this plan: the freeze must send
    it back in `If-Match`. Nothing changes. It answers `422` where the freeze would (nothing
    left, or a set that is not valid after the reviews), naming the item by id."""
    row, plan = container_of(request).sets.freeze.plan(SetRef(name, version))
    _etag(response, plan.token)
    return FreezePreview(
        version=SetVersionOut.from_domain(row), plan=FreezePlanOut.from_domain(plan)
    )


@router.post(
    "/{name}/versions/{version}/freeze",
    operation_id="freezeSetVersion",
    summary="Apply the reviews and freeze a draft",
    responses={**WRITE_ERRORS},
)
def freeze_set_version(
    name: NameParam,
    version: VersionParam,
    request: Request,
    who: Annotated[Authenticated, Depends(member)],
    if_match: Annotated[
        str | None,
        Header(
            description="The `etag` of the plan you read (`GET .../freeze`). `428` without it, "
            "`409` when a decision or an item changed since."
        ),
    ] = None,
) -> FreezeResult:
    """Owner or admin. The latest human decision of each item is applied (accepted and edited
    items stay, rejected ones go) and the file gets a new md5 that never changes again. The draft
    as it was is kept beside it on the server. Only the plan that was read is applied."""
    row, plan = container_of(request).sets.freeze.freeze(
        _actor(who), SetRef(name, version), if_match
    )
    return FreezeResult(
        version=SetVersionOut.from_domain(row), plan=FreezePlanOut.from_domain(plan)
    )


@router.get(
    "/{name}/versions/{version}/reviews",
    operation_id="listSetReviews",
    summary="The decisions on a version's items",
    responses={**READ_ERRORS},
)
def list_set_reviews(
    name: NameParam,
    version: VersionParam,
    request: Request,
    item_id: Annotated[str | None, Query(max_length=200, description="Only this item.")] = None,
) -> list[ReviewLine]:
    """Oldest first, as in the `reviews/1` file."""
    lines = container_of(request).sets.review.list(SetRef(name, version), item_id)
    return [ReviewLine.model_validate(line) for line in lines]


@router.post(
    "/{name}/versions/{version}/reviews",
    operation_id="addSetReview",
    summary="Accept, edit or reject an item",
    status_code=201,
    responses={
        **READ_ERRORS,
        **BAD_JSON,
        403: {
            "model": Problem,
            "description": "The version is frozen (or the CSRF header is missing).",
        },
        409: {
            "model": Problem,
            "description": "The draft changed since you read it (`If-Match`): read it again.",
        },
        428: {"model": Problem, "description": "`If-Match` is missing (or is `*`)."},
    },
)
def add_set_review(
    name: NameParam,
    version: VersionParam,
    body: NewReview,
    request: Request,
    who: Annotated[Authenticated, Depends(member)],
    if_match: IfMatch = None,
) -> ReviewLine:
    """Any member may review any item of a draft, with the `etag` of the draft they read in
    `If-Match`: a decision is about the text that was read, so it is refused when the draft has
    changed since. The file is append only: a new decision replaces yours, it never edits it.
    `edit` and `reject` need a `note`; an `edit` lists the item fields to replace and is applied
    when the draft is frozen."""
    line = container_of(request).sets.review.add(
        _actor(who),
        SetRef(name, version),
        if_match=if_match,
        item_id=body.item_id,
        verdict=body.verdict,
        note=body.note,
        edit=body.edit,
    )
    return ReviewLine.model_validate(line)
