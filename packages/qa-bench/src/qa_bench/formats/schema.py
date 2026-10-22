"""The JSON Schemas of the four formats and the check of one line against them."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from functools import cache
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from qa_bench.formats.problems import Problem, shorten

QSET = "qset/1"
QA_RUN = "qa-run/1"
QA_SCORE = "qa-score/1"
REVIEWS = "reviews/1"
FORMATS = (QSET, QA_RUN, QA_SCORE, REVIEWS)

_DATE_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

# Which definition of the schema checks the header, and which one a record, by the value of the
# field that tells them apart (``record`` in a score file, ``target`` in a reviews file).
_HEADER = "header"
_RECORDS: dict[str, tuple[str | None, dict[str, str]]] = {
    QSET: (None, {"": "item"}),
    QA_RUN: (None, {"": "item"}),
    QA_SCORE: ("record", {"item": "item", "group": "group", "compare": "compare"}),
    REVIEWS: ("target", {"item": "item_review", "answer": "answer_review"}),
}


def _is_date_time(value: object) -> bool:
    """ISO 8601 with an offset (``Z`` or ``+hh:mm``), as the formats ask for dates."""
    if not isinstance(value, str):
        return True  # the type keyword reports a wrong type
    if not _DATE_TIME.fullmatch(value):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_date(value: object) -> bool:
    if not isinstance(value, str):
        return True
    if not _DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _format_checker() -> FormatChecker:
    """Only the formats the schemas use, and the same answer whatever optional packages exist.

    jsonschema checks ``date-time`` only when an extra package is installed; the dates of the
    formats are checked here so that the result never depends on the environment.
    """
    checker = FormatChecker(formats=())
    checker.checks("date-time")(_is_date_time)
    checker.checks("date")(_is_date)
    return checker


def schema_name(fmt: str) -> str:
    """The file name of the schema of ``fmt``: ``qset/1`` is ``qset-1.schema.json``."""
    return fmt.replace("/", "-") + ".schema.json"


@cache
def load_schema(fmt: str) -> dict[str, Any]:
    """The JSON Schema (draft 2020-12) of ``fmt``, read from the package."""
    if fmt not in FORMATS:
        raise KeyError(f"unknown format {fmt!r}; the formats are {', '.join(FORMATS)}")
    text = (resources.files("qa_bench.formats") / "schemas" / schema_name(fmt)).read_text(
        encoding="utf-8"
    )
    schema = json.loads(text)
    Draft202012Validator.check_schema(schema)
    return schema


@cache
def _validator(fmt: str, definition: str) -> Draft202012Validator:
    schema = load_schema(fmt)
    wrapper = {
        "$schema": schema["$schema"],
        "$ref": f"#/$defs/{definition}",
        "$defs": schema["$defs"],
    }
    return Draft202012Validator(wrapper, format_checker=_format_checker())


def pointer(parts: Any) -> str:
    """A JSON pointer for the path ``parts`` (``/gold_papers/0/doc_id``)."""
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def definition_for(fmt: str, record: object, *, header: bool) -> str | Problem:
    """The schema definition that applies to a line, or the problem that says why none does."""
    if header:
        return _HEADER
    field, table = _RECORDS[fmt]
    if field is None:
        return table[""]
    kind = record.get(field) if isinstance(record, dict) else None
    if isinstance(kind, str) and kind in table:
        return table[kind]
    allowed = ", ".join(sorted(table))
    return Problem(0, f"/{field}", f"'{field}' must be one of: {allowed}")


def check_line(fmt: str, record: Any, *, header: bool, line: int = 0) -> list[Problem]:
    """What is wrong with one line of a ``fmt`` file; an empty list when it follows the format."""
    definition = definition_for(fmt, record, header=header)
    if isinstance(definition, Problem):
        return [Problem(line, definition.path, definition.message)]
    errors = _validator(fmt, definition).iter_errors(record)
    found = {
        Problem(line, pointer(error.absolute_path), shorten(error.message)) for error in errors
    }
    return sorted(found, key=lambda p: (p.path, p.message))
