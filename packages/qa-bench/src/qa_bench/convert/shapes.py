"""The older question-set files as a table, and the whole-file conversion to and from ``qset/1``."""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from qa_bench.convert import circuits, mathcirc, qasper, qubitqa
from qa_bench.convert.errors import ConversionError
from qa_bench.formats import QSET, FormatError, FormatFile, build, md5_hex
from qa_bench.formats.jsonl import dumps_line, parse_lines

ToItem = Callable[[dict[str, Any], str | None], dict[str, Any]]
FromItem = Callable[[dict[str, Any]], dict[str, Any]]


def _read_json_rows(text: str) -> list[dict[str, Any]]:
    values, problems = parse_lines(text)
    if problems:
        raise ConversionError(str(problems[0]))
    for number, value in values:
        if not isinstance(value, dict):
            raise ConversionError(f"line {number}: expected a JSON object per line")
    return [value for _, value in values]


def _write_json_rows(rows: list[dict[str, Any]]) -> str:
    return "".join(dumps_line(row) + "\n" for row in rows)


def _read_tsv_rows(text: str) -> list[dict[str, Any]]:
    return [dict(row) for row in csv.DictReader(io.StringIO(text, newline=""), delimiter="\t")]


def _write_tsv_rows(rows: list[dict[str, Any]]) -> str:
    out = io.StringIO()
    if rows:
        writer = csv.DictWriter(out, fieldnames=list(rows[0]), delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return out.getvalue()


@dataclass(frozen=True)
class Shape:
    """A source file shape: how to read its rows, map each to an item and back."""

    name: str
    description: str
    suffix: str
    needs_corpus: bool
    read_rows: Callable[[str], list[dict[str, Any]]]
    write_rows: Callable[[list[dict[str, Any]]], str]
    to_item: ToItem
    from_item: FromItem


SHAPES: dict[str, Shape] = {
    s.name: s
    for s in (
        Shape(
            "mathcirc-v1",
            "mathcirc v1 questions: one gold paper per question, key facts, categories",
            ".jsonl",
            True,
            _read_json_rows,
            _write_json_rows,
            mathcirc.to_item,
            mathcirc.from_item,
        ),
        Shape(
            "circuit-tasks-v1",
            "circuit tasks: verify a drawn circuit or create one from a target",
            ".jsonl",
            True,
            _read_json_rows,
            _write_json_rows,
            circuits.task_to_item,
            circuits.task_from_item,
        ),
        Shape(
            "circuit-claims-v1",
            "circuit claims read from papers (TSV: paper_id, block_index, claim, target)",
            ".tsv",
            True,
            _read_tsv_rows,
            _write_tsv_rows,
            circuits.claim_to_item,
            circuits.claim_from_item,
        ),
        Shape(
            "qubitqa-pilot",
            "QubitQA pilot: multiple choice, one gold paper, English and Spanish mirrors",
            ".jsonl",
            False,
            _read_json_rows,
            _write_json_rows,
            qubitqa.to_item,
            qubitqa.from_item,
        ),
        Shape(
            "qasper",
            "QASPER questions with several reference answers each",
            ".jsonl",
            True,
            _read_json_rows,
            _write_json_rows,
            qasper.to_item,
            qasper.from_item,
        ),
    )
}


def _shape(name: str) -> Shape:
    try:
        return SHAPES[name]
    except KeyError:
        raise ConversionError(
            f"unknown source shape {name!r}; the shapes are {', '.join(SHAPES)}"
        ) from None


def to_qset(
    shape: str,
    data: bytes,
    *,
    set_name: str,
    created: str,
    corpus: str | None = None,
    source_file: str | None = None,
    notes: str | None = None,
) -> FormatFile:
    """The bytes of a source file as a valid ``qset/1`` file.

    ``source_file`` is the name recorded in the header's ``source`` with the md5 of ``data``;
    ``corpus`` is required for the shapes whose rows do not name one. Every item is checked by the
    ``qset/1`` schema; a row that cannot be mapped or an item that is invalid raises
    ``ConversionError`` that names it.
    """
    spec = _shape(shape)
    if spec.needs_corpus and not corpus:
        raise ConversionError(f"{shape} rows do not name a corpus; give one")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ConversionError(f"the source is not UTF-8 text: {error}") from None
    items: list[dict[str, Any]] = []
    for number, row in enumerate(spec.read_rows(text), 1):
        try:
            items.append(spec.to_item(row, corpus))
        except ConversionError:
            raise
        except (KeyError, TypeError, IndexError, AttributeError) as error:
            label = row.get("id") or row.get("qid") or f"row {number}"
            raise ConversionError(
                f"{label}: not a {shape} row ({type(error).__name__}: {error})"
            ) from None
    header: dict[str, Any] = {"format": QSET, "set": set_name, "created": created}
    if source_file is not None:
        header["source"] = {"file": source_file, "md5": md5_hex(data)}
    if notes:
        header["notes"] = notes
    try:
        return build(QSET, header, items)
    except FormatError as error:
        raise ConversionError(f"the converted set is invalid: {error}") from None


def from_qset(shape: str, qset: FormatFile) -> bytes:
    """The rows of the source shape that the items of ``qset`` were made from, as file bytes."""
    spec = _shape(shape)
    rows: list[dict[str, Any]] = []
    for item in qset.records:
        try:
            rows.append(spec.from_item(item))
        except (KeyError, TypeError, IndexError) as error:
            raise ConversionError(
                f"{item.get('id', '?')}: not an item made from a {shape} row "
                f"({type(error).__name__}: {error})"
            ) from None
    return spec.write_rows(rows).encode("utf-8")
