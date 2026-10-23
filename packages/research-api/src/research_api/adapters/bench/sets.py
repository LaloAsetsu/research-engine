"""``SetFormats`` over qa-bench: ``qset/1`` and ``reviews/1`` files, checked as qa-bench checks."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from qa_bench.convert import SHAPES, ConversionError, to_qset
from qa_bench.formats import (
    QSET,
    REVIEWS,
    FormatError,
    Problem,
    build,
    check_line,
    load_bytes,
    parse,
)
from qa_bench.formats.jsonl import decode, dumps_line, md5_hex, parse_lines
from qa_bench.reviews import ReviewError, apply_reviews

from ...domain.errors import CorruptStoreError, FieldError, InvalidInputError
from ...domain.sets import (
    MAX_REPORTED_PROBLEMS,
    REVIEWS_FORMAT,
    AppliedReviews,
    BuiltSet,
    ImportShape,
    SetFile,
)

_FILE_REFUSED = "The file is not a valid qset/1 file."


_REQUIRED = re.compile(r"^'(?P<name>.+)' is a required property$")
_UNEXPECTED = re.compile(
    r"^(?:Additional|Unevaluated) properties are not allowed "
    r"\((?P<names>.+) (?:was|were) unexpected\)$"
)


def _escape(name: str) -> str:
    return name.replace("~", "~0").replace("/", "~1")


def _points(problem: Problem) -> list[FieldError]:
    """The problem as errors with the place of the field at fault.

    The schema reports a missing or unexpected property at its parent; the person fixing it needs
    the property, so the name is added to the pointer. Other problems keep the pointer as given.
    """
    message = problem.message
    if found := _REQUIRED.match(message):
        return [FieldError(f"{problem.path}/{_escape(found['name'])}", message)]
    if found := _UNEXPECTED.match(message):
        names = re.findall(r"'([^']*)'", found["names"])
        return [FieldError(f"{problem.path}/{_escape(n)}", message) for n in names]
    return [FieldError(problem.path or "/", message)]


def _under(prefix: str, pointer: str) -> str:
    """``pointer`` inside the place ``prefix`` names (the whole value is ``/``)."""
    return prefix if pointer == "/" else prefix + pointer


def _capped(errors: list[FieldError]) -> tuple[FieldError, ...]:
    if len(errors) > MAX_REPORTED_PROBLEMS:
        extra = len(errors) - MAX_REPORTED_PROBLEMS
        errors = [*errors[:MAX_REPORTED_PROBLEMS], FieldError("/", f"and {extra} more problems")]
    return tuple(errors)


def _errors(problems: Sequence[Problem]) -> tuple[FieldError, ...]:
    """Problems of an uploaded file: ``/lines/<line>`` then the pointer inside the line."""
    shown: list[FieldError] = []
    for p in problems:
        for e in _points(p):
            shown.append(
                FieldError(_under(f"/lines/{p.line}", e.path) if p.line else e.path, e.message)
            )
    return _capped(shown)


def _built_errors(problems: Sequence[Problem]) -> tuple[FieldError, ...]:
    """Problems of a set assembled in memory: line 1 is the header, line N+2 is ``items[N]``."""
    shown: list[FieldError] = []
    for p in problems:
        for e in _points(p):
            if p.line == 1:
                shown.append(FieldError(_under("/header", e.path), e.message))
            elif p.line:
                shown.append(FieldError(_under(f"/items/{p.line - 2}", e.path), e.message))
            else:
                shown.append(e)
    return _capped(shown)


class QaBenchSetFormats:
    def peek(self, data: bytes) -> SetFile:
        try:
            values, problems = parse_lines(data.decode("utf-8"))
        except UnicodeDecodeError as e:
            raise CorruptStoreError(f"a stored set is not UTF-8 text: {e}") from None
        if problems or not values or not isinstance(values[0][1], dict):
            raise CorruptStoreError("a stored set file cannot be read")
        records = tuple(v for _, v in values[1:])
        if not all(isinstance(r, dict) for r in records):
            raise CorruptStoreError("a stored set file has a line that is not an object")
        return SetFile(values[0][1], records, data, md5_hex(data))

    def read(self, data: bytes) -> SetFile:
        loaded = load_bytes(data, QSET)
        if loaded.file is None:
            raise InvalidInputError(_FILE_REFUSED, _errors(loaded.problems))
        file = loaded.file
        return SetFile(file.header, file.records, decode(data), file.md5)

    def check_text(self, text: str) -> tuple[FieldError, ...]:
        return _errors(parse(text, QSET).problems)

    def check_item(self, item: Mapping[str, Any]) -> tuple[FieldError, ...]:
        return _capped([e for p in check_line(QSET, dict(item), header=False) for e in _points(p)])

    def build(self, header: Mapping[str, Any], items: Sequence[Mapping[str, Any]]) -> BuiltSet:
        try:
            file = build(QSET, header, items)
        except FormatError as e:
            raise InvalidInputError(
                "The set would not be valid.", _built_errors(e.problems)
            ) from None
        return BuiltSet(file.header, file.records, file.to_bytes(), file.md5)

    def shapes(self) -> tuple[ImportShape, ...]:
        return tuple(
            ImportShape(s.name, s.description, s.suffix, s.needs_corpus) for s in SHAPES.values()
        )

    def convert(
        self,
        shape: str,
        data: bytes,
        *,
        set_name: str,
        created: str,
        corpus: str | None,
        source_file: str | None,
    ) -> BuiltSet:
        try:
            file = to_qset(
                shape,
                data,
                set_name=set_name,
                created=created,
                corpus=corpus,
                source_file=source_file,
            )
        except ConversionError as e:
            raise InvalidInputError(f"The {shape} file was refused: {e}") from None
        return BuiltSet(file.header, file.records, file.to_bytes(), file.md5)

    def peek_reviews(self, data: bytes) -> list[dict[str, Any]]:
        try:
            values, problems = parse_lines(data.decode("utf-8"))
        except UnicodeDecodeError as e:
            raise CorruptStoreError(f"a stored reviews file is not UTF-8 text: {e}") from None
        if problems:
            raise CorruptStoreError("a stored reviews file cannot be read")
        return [v for _, v in values[1:] if isinstance(v, dict)]

    def review_header(self, set_name: str, created: str) -> bytes:
        header = {"format": REVIEWS_FORMAT, "set": set_name, "created": created}
        return (dumps_line(header) + "\n").encode("utf-8")

    def review_line(self, line: Mapping[str, Any]) -> bytes:
        return (dumps_line(line) + "\n").encode("utf-8")

    def check_review(self, line: Mapping[str, Any]) -> tuple[FieldError, ...]:
        return _capped(
            [e for p in check_line(REVIEWS, dict(line), header=False) for e in _points(p)]
        )

    def apply_reviews(
        self, items: Sequence[Mapping[str, Any]], lines: Sequence[Mapping[str, Any]], set_md5: str
    ) -> AppliedReviews:
        try:
            applied = apply_reviews(items, lines, set_md5=set_md5)
        except ReviewError as e:
            raise InvalidInputError(f"The reviews cannot be applied: {e}") from None
        return AppliedReviews(
            items=applied.items,
            accepted=applied.accepted,
            edited=applied.edited,
            rejected=applied.rejected,
            unreviewed=applied.unreviewed,
            stale=applied.stale,
            unknown=applied.unknown,
        )
