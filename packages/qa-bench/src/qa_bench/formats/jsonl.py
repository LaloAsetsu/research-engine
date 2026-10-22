"""JSON lines as the formats write them: strict on read, byte-for-byte reproducible on write."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from qa_bench.formats.problems import Problem, shorten

GZIP_MAGIC = b"\x1f\x8b"
GZIP_LEVEL = 6
JSON_SPACE = " \t\r"
MAX_DEPTH = 64
_TOO_DEEP = f"nested more than {MAX_DEPTH} levels deep"
_SURROGATE = "a string holds an unpaired surrogate, which cannot be written as UTF-8"


class _Repeated(ValueError):
    pass


def _no_repeated_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for key, value in pairs:
        if key in found:
            raise _Repeated(f"the key {shorten(repr(key), 60)} is given twice")
        found[key] = value
    return found


def _no_constants(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")


def _finite(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"the number {shorten(text, 40)} is out of range")
    return value


def _check_value(root: Any) -> None:
    """Refuse nesting deeper than ``MAX_DEPTH`` and an unpaired surrogate anywhere in ``root``.

    The walk keeps its own stack, so what is accepted does not depend on the caller's stack.
    """
    pending: list[tuple[Any, int]] = [(root, 1)]
    while pending:
        value, depth = pending.pop()
        if isinstance(value, str):
            if any("\ud800" <= char <= "\udfff" for char in value):
                raise ValueError(_SURROGATE)
        elif isinstance(value, (list, dict)):
            if depth > MAX_DEPTH:
                raise ValueError(_TOO_DEEP)
            if isinstance(value, list):
                pending.extend((item, depth + 1) for item in value)
            else:
                for key, item in value.items():
                    if any("\ud800" <= char <= "\udfff" for char in key):
                        raise ValueError(_SURROGATE)
                    pending.append((item, depth + 1))


def loads(text: str) -> Any:
    """One JSON value, read as strictly as the formats read a line.

    A key given twice, NaN, Infinity, a number too large for a float, an unpaired surrogate
    escape (``"\\ud800"``, which no UTF-8 file can hold) and arrays or objects nested more than
    ``MAX_DEPTH`` levels deep are refused with ``ValueError``.
    """
    try:
        value = json.loads(
            text,
            object_pairs_hook=_no_repeated_keys,
            parse_constant=_no_constants,
            parse_float=_finite,
        )
    except RecursionError:
        raise ValueError(_TOO_DEEP) from None
    _check_value(value)
    return value


def md5_hex(data: bytes) -> str:
    """The md5 of ``data``: the identity of a set or a run file, as the formats define it."""
    return hashlib.md5(data).hexdigest()


def decode(data: bytes) -> bytes:
    """``data`` unzipped when it is gzip (by its first two bytes), otherwise unchanged."""
    return gzip.decompress(data) if data[:2] == GZIP_MAGIC else data


def parse_lines(text: str) -> tuple[list[tuple[int, Any]], list[Problem]]:
    """The JSON values of ``text`` with their 1-based line numbers, and what could not be read.

    Lines end at a line feed only (a carriage return before it is JSON whitespace): U+2028, U+2029
    and U+0085 are text inside a string, which ``dumps_line`` writes as they are. Blank lines are
    skipped.
    A repeated key, NaN, Infinity, a number out of range, an unpaired surrogate and nesting deeper
    than ``MAX_DEPTH`` are refused:
    the formats leave no room for a value that depends on which duplicate a reader keeps.
    """
    values: list[tuple[int, Any]] = []
    problems: list[Problem] = []
    for number, line in enumerate(text.split("\n"), 1):
        if not line.strip(JSON_SPACE):
            continue
        try:
            values.append((number, loads(line)))
        except ValueError as error:
            problems.append(Problem(number, "", f"not valid JSON: {shorten(str(error), 120)}"))
    return values, problems


def dumps_line(record: Mapping[str, Any]) -> str:
    """One record as one line: UTF-8 kept as is, the default separators, no NaN."""
    return json.dumps(record, ensure_ascii=False, allow_nan=False)


def dumps_lines(records: Iterable[Mapping[str, Any]]) -> bytes:
    """The records as the text of a JSON-lines file."""
    return "".join(dumps_line(r) + "\n" for r in records).encode("utf-8")


def write_bytes(path: Path, text: bytes) -> None:
    """``text`` at ``path``; a ``.gz`` name is gzip with no time stamp (equal text, equal bytes)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".gz":
        path.write_bytes(gzip.compress(text, compresslevel=GZIP_LEVEL, mtime=0))
    else:
        path.write_bytes(text)
