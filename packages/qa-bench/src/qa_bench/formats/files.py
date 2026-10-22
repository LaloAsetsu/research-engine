"""Whole files: read one with every rule checked, build and write one byte for byte."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qa_bench.formats.jsonl import (
    decode,
    dumps_line,
    dumps_lines,
    md5_hex,
    parse_lines,
    write_bytes,
)
from qa_bench.formats.problems import FormatError, Problem, shorten
from qa_bench.formats.schema import (
    FORMATS,
    QA_RUN,
    QA_SCORE,
    QSET,
    REVIEWS,
    check_line,
)


@dataclass(frozen=True)
class FormatFile:
    """A file of one format: the header line, the other lines in order, and the file's md5.

    ``md5`` is the md5 of the uncompressed text the file was read from (for a file made with
    ``build``, of the text ``to_bytes`` gives), which is the identity of a set or a run; a gzip
    file's md5 is never of the compressed bytes. Text that is not what ``to_bytes`` writes (CRLF
    line ends, other spacing, ``\\u`` escapes, blank lines) keeps its own md5 on the file read from
    it; ``write`` returns the md5 of the bytes it wrote.

    ``numbers`` are the line numbers the records came from; empty for a file built in memory.
    """

    format: str
    header: dict[str, Any]
    records: tuple[dict[str, Any], ...]
    md5: str
    numbers: tuple[int, ...] = ()

    def line_of(self, index: int) -> int:
        """The 1-based line of ``records[index]``."""
        return self.numbers[index] if self.numbers else index + 2

    def to_bytes(self) -> bytes:
        """The text of the file: the header, then the records, one JSON object per line."""
        return dumps_lines([self.header, *self.records])


@dataclass(frozen=True)
class Loaded:
    """The result of reading a file: the file when it is valid, and every problem found."""

    file: FormatFile | None
    problems: tuple[Problem, ...]

    @property
    def ok(self) -> bool:
        return self.file is not None and not self.problems


def _unique(
    records: Sequence[dict[str, Any]],
    numbers: Sequence[int],
    key: Any,
    path: str,
    what: str,
) -> list[Problem]:
    seen: dict[Any, int] = {}
    problems: list[Problem] = []
    for record, number in zip(records, numbers, strict=True):
        k = key(record)
        if k in seen:
            problems.append(Problem(number, path, f"{what} {k!r} is already on line {seen[k]}"))
        else:
            seen[k] = number
    return problems


def _structure(
    fmt: str, header: dict[str, Any], records: Sequence[dict[str, Any]], numbers: Sequence[int]
) -> list[Problem]:
    """The rules of a file that no single line can show."""
    problems: list[Problem] = []
    if fmt == QSET:
        problems += _unique(records, numbers, lambda r: r.get("id"), "/id", "the id")
        ids = {r.get("id") for r in records}
        problems += [
            Problem(
                n, "/mirror_of", f"mirror_of names {r['mirror_of']!r}, which is not in the file"
            )
            for r, n in zip(records, numbers, strict=True)
            if r.get("mirror_of") is not None and r["mirror_of"] not in ids
        ]
    elif fmt == QA_RUN:
        problems += _unique(
            records, numbers, lambda r: (r.get("id"), r.get("repeat")), "/repeat", "the answer"
        )
        if header.get("arm", {}).get("name") == "bare":
            problems += [
                Problem(n, "/contexts", "a bare answer has no contexts")
                for r, n in zip(records, numbers, strict=True)
                if r.get("contexts")
            ]
    elif fmt == QA_SCORE:
        problems += _score_structure(header, records, numbers)
    return problems


def _score_key(record: dict[str, Any]) -> tuple[Any, ...]:
    """What makes a score record the same record: the run, the question and repeat or the group."""
    kind = record.get("record")
    if kind == "item":
        return (kind, record.get("run_id"), record.get("id"), record.get("repeat"))
    if kind == "group":
        return (kind, record.get("run_id"), record.get("group"), record.get("metric"))
    return (
        kind,
        record.get("base_run"),
        record.get("var_run"),
        record.get("group"),
        record.get("metric"),
    )


def _score_structure(
    header: dict[str, Any], records: Sequence[dict[str, Any]], numbers: Sequence[int]
) -> list[Problem]:
    listed = {r.get("run_id") for r in header.get("runs", [])}
    problems: list[Problem] = []
    for name, run in (header.get("noise_floor") or {}).items():
        if run not in listed:
            problems.append(Problem(1, f"/noise_floor/{name}", f"run {run!r} is not in runs"))
    seen: dict[tuple[Any, ...], int] = {}
    for record, number in zip(records, numbers, strict=True):
        for field in ("run_id", "base_run", "var_run"):
            if field in record and record[field] not in listed:
                problems.append(
                    Problem(
                        number,
                        f"/{field}",
                        f"run {record[field]!r} is not in the runs of the header",
                    )
                )
        key = _score_key(record)
        if key in seen:
            problems.append(Problem(number, "", f"the same record is already on line {seen[key]}"))
        else:
            seen[key] = number
    return problems


def parse(text: str, expected: str | None = None) -> Loaded:
    """Check the text of a file against its format. The format is the one the first line names."""
    values, problems = parse_lines(text)
    if problems:
        return Loaded(None, tuple(problems))
    if not values:
        return Loaded(None, (Problem(0, "", "the file is empty: line 1 must be the header"),))
    first_number, header = values[0]
    fmt = header.get("format") if isinstance(header, dict) else None
    if fmt not in FORMATS:
        known = ", ".join(FORMATS)
        return Loaded(
            None, (Problem(first_number, "/format", f"line 1 must name a format: {known}"),)
        )
    if expected is not None and fmt != expected:
        return Loaded(
            None, (Problem(first_number, "/format", f"expected {expected}, the file is {fmt}"),)
        )
    problems = check_line(fmt, header, header=True, line=first_number)
    records: list[dict[str, Any]] = []
    numbers: list[int] = []
    for number, record in values[1:]:
        problems += check_line(fmt, record, header=False, line=number)
        if isinstance(record, dict):
            records.append(record)
            numbers.append(number)
    if not problems:
        problems += _structure(fmt, header, records, numbers)
    if problems:
        return Loaded(None, tuple(problems))
    return Loaded(
        FormatFile(fmt, header, tuple(records), md5_hex(text.encode("utf-8")), tuple(numbers)), ()
    )


def load_bytes(data: bytes, expected: str | None = None) -> Loaded:
    """Check the bytes of a file (gzip is unzipped first) against its format."""
    try:
        text = decode(data).decode("utf-8")
    except (OSError, EOFError, UnicodeDecodeError) as error:
        return Loaded(None, (Problem(0, "", f"cannot read the file as UTF-8 text: {error}"),))
    return parse(text, expected)


def load(path: str | Path, expected: str | None = None) -> Loaded:
    """Read and check a file; the problems come back instead of an exception."""
    try:
        data = Path(path).read_bytes()
    except OSError as error:
        return Loaded(None, (Problem(0, "", f"cannot read {path}: {error.strerror or error}"),))
    return load_bytes(data, expected)


def read(path: str | Path, expected: str | None = None) -> FormatFile:
    """Read a file or raise ``FormatError`` with every problem."""
    loaded = load(path, expected)
    if loaded.file is None:
        raise FormatError(loaded.problems)
    return loaded.file


def read_qset(path: str | Path) -> FormatFile:
    """A ``qset/1`` file."""
    return read(path, QSET)


def read_run(path: str | Path) -> FormatFile:
    """A ``qa-run/1`` file, plain or gzip."""
    return read(path, QA_RUN)


def read_score(path: str | Path) -> FormatFile:
    """A ``qa-score/1`` file."""
    return read(path, QA_SCORE)


def read_reviews(path: str | Path) -> FormatFile:
    """A ``reviews/1`` file."""
    return read(path, REVIEWS)


def build(fmt: str, header: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> FormatFile:
    """A file made in memory; every line and every rule of the file is checked first.

    A line that cannot be written as UTF-8 JSON (an unpaired surrogate, an integer of more than
    4300 digits, NaN, a value JSON has no form for) is a problem of its line, like any other.

    The file holds the values read back from the checked text, not the caller's mappings: a later
    change to what was passed in cannot make ``to_bytes`` differ from ``md5``, and a key that was
    not a string (``1``) is the string the text has (``"1"``).
    """
    lines: list[str] = []
    unwritable: list[Problem] = []
    for number, record in enumerate([header, *records], 1):
        try:
            text_line = dumps_line(record)
            text_line.encode("utf-8")
        except (ValueError, TypeError, RecursionError) as error:
            reason = shorten(str(error), 120)
            unwritable.append(Problem(number, "", f"cannot be written as JSON text: {reason}"))
        else:
            lines.append(text_line)
    if unwritable:
        raise FormatError(unwritable)
    text = "".join(f"{line}\n" for line in lines)
    loaded = parse(text, fmt)
    if loaded.file is None:
        raise FormatError(loaded.problems)
    return FormatFile(fmt, loaded.file.header, loaded.file.records, loaded.file.md5)


def write(path: str | Path, file: FormatFile) -> str:
    """Write ``file`` to ``path`` (gzip when the name ends in ``.gz``); return the md5 of the text.

    The md5 is of the uncompressed bytes written. It equals ``file.md5`` only when the file was
    read from text that ``to_bytes`` reproduces.
    """
    text = file.to_bytes()
    write_bytes(Path(path), text)
    return md5_hex(text)
