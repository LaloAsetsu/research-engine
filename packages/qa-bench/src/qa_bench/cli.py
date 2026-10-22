"""The ``qa-bench`` command.

    qa-bench validate FILE... [--set QSET] [--format FORMAT] [--all]
    qa-bench convert SHAPE INPUT -o OUTPUT --set-name NAME [--corpus ID] [--created TIME]
                     [--notes TEXT] [--back] [--force]
    qa-bench convert ask-capture INPUT -o OUTPUT --header FILE [--retrieval FILE]

``validate`` checks files against their formats (the format is the one line 1 names), and with
``--set`` also runs, scores and reviews against the set file they point at. ``convert`` turns an
older question-set file into ``qset/1`` (or, with ``--back``, a ``qset/1`` file made from it back
into the older shape), and the records of the ask capture script into a ``qa-run/1`` file under
the header in ``--header``. A name ending in ``.gz`` is written as gzip. An output file that
exists is not overwritten unless ``--force`` is given. Exit status: 0 when the
work was done, 1 when a file or a row was refused (the reasons are printed), 2 for a command line
that does not parse.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from qa_bench.convert import SHAPES, ConversionError, capture_to_item, from_qset, to_qset
from qa_bench.formats import (
    FORMATS,
    QA_RUN,
    QSET,
    FormatError,
    Problem,
    build,
    check_against_set,
    load,
    read_qset,
    write,
)
from qa_bench.formats.jsonl import loads, parse_lines, write_bytes

ASK_CAPTURE = "ask-capture"
SHOWN = 20


class _Refused(Exception):
    pass


def _problems(path: str | Path, problems: Sequence[Problem], *, show_all: bool) -> None:
    for problem in problems if show_all else problems[:SHOWN]:
        print(f"{path}:{problem.line}: {problem.path or '/'}: {problem.message}")
    if not show_all and len(problems) > SHOWN:
        print(f"{path}: ... and {len(problems) - SHOWN} more (--all shows every one)")


def _validate(args: argparse.Namespace) -> int:
    qset = None
    if args.set:
        loaded_set = load(args.set, QSET)
        if loaded_set.file is None:
            _problems(args.set, loaded_set.problems, show_all=args.all)
            return 1
        qset = loaded_set.file
    status = 0
    for path in args.files:
        loaded = load(path, args.format)
        if loaded.file is None:
            _problems(path, loaded.problems, show_all=args.all)
            status = 1
            continue
        file = loaded.file
        problems = check_against_set(file, qset) if qset is not None and file.format != QSET else []
        if problems:
            _problems(path, problems, show_all=args.all)
            status = 1
            continue
        print(f"ok {path}: {file.format}, {len(file.records)} records, md5 {file.md5}")
    return status


def _clock() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _read_source(path: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as error:
        raise _Refused(f"cannot read {path}: {error.strerror or error}") from None


def _convert_capture(args: argparse.Namespace) -> int:
    if not args.header:
        raise _Refused("convert ask-capture needs --header FILE (the qa-run/1 header as JSON)")
    try:
        header = loads(_read_source(args.header).decode("utf-8"))
    except ValueError as error:
        raise _Refused(f"{args.header} is not valid JSON: {error}") from None
    retrieval: dict[str, dict[str, Any]] = {}
    if args.retrieval:
        values, problems = parse_lines(_read_source(args.retrieval).decode("utf-8"))
        if problems:
            raise _Refused(f"{args.retrieval}:{problems[0]}")
        retrieval = {v["id"]: v for _, v in values if isinstance(v, dict) and "id" in v}
    values, problems = parse_lines(_read_source(args.input).decode("utf-8"))
    if problems:
        raise _Refused(f"{args.input}:{problems[0]}")
    items = [
        capture_to_item(record, retrieval.get(record.get("qid", "")))
        for _, record in values
        if isinstance(record, dict)
    ]
    file = build(QA_RUN, header, items)
    md5 = write(args.output, file)
    print(f"wrote {args.output}: {QA_RUN}, {len(items)} answers, md5 {md5}")
    return 0


def _convert(args: argparse.Namespace) -> int:
    if Path(args.output).exists() and not args.force:
        raise _Refused(f"{args.output} already exists; --force overwrites it")
    if args.shape == ASK_CAPTURE:
        return _convert_capture(args)
    if args.back:
        qset = read_qset(args.input)
        data = from_qset(args.shape, qset)
        write_bytes(Path(args.output), data)
        print(f"wrote {args.output}: {args.shape}, {len(qset.records)} rows")
        return 0
    if not args.set_name:
        raise _Refused("convert needs --set-name NAME (the set name and version, e.g. my-set/v1)")
    data = _read_source(args.input)
    file = to_qset(
        args.shape,
        data,
        set_name=args.set_name,
        created=args.created or _clock(),
        corpus=args.corpus,
        source_file=Path(args.input).name,
        notes=args.notes,
    )
    md5 = write(args.output, file)
    print(f"wrote {args.output}: {QSET}, {len(file.records)} items, md5 {md5}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qa-bench",
        description="Check and convert the benchmark formats (sets, runs, scores, reviews).",
    )
    parser.add_argument("--version", action="version", version=version("qa-bench"))
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("validate", help="check files against their formats")
    check.add_argument("files", nargs="+", metavar="FILE", help="a .jsonl or .jsonl.gz file")
    check.add_argument("--format", choices=FORMATS, help="require this format (default: line 1's)")
    check.add_argument("--set", metavar="QSET", help="check runs, scores, reviews against this set")
    check.add_argument("--all", action="store_true", help=f"show every problem, not {SHOWN}")

    convert = commands.add_parser(
        "convert",
        help="turn an older set into qset/1 (or back), or ask captures into qa-run/1",
        description="SHAPE is one of: " + ", ".join([*SHAPES, ASK_CAPTURE]),
    )
    convert.add_argument("shape", metavar="SHAPE", choices=[*SHAPES, ASK_CAPTURE])
    convert.add_argument("input", metavar="INPUT")
    convert.add_argument("-o", "--output", required=True, metavar="OUTPUT")
    convert.add_argument("--set-name", metavar="NAME", help="the set's name and version")
    convert.add_argument("--corpus", metavar="ID", help="the corpus the rows refer to")
    convert.add_argument("--created", metavar="TIME", help="ISO 8601 with offset (default: now)")
    convert.add_argument("--notes", metavar="TEXT", help="free text for the header")
    convert.add_argument("--back", action="store_true", help="qset/1 back into the SHAPE file")
    convert.add_argument("--force", action="store_true", help="overwrite OUTPUT if it exists")
    convert.add_argument("--header", metavar="FILE", help="ask-capture: the qa-run/1 header (JSON)")
    convert.add_argument(
        "--retrieval", metavar="FILE", help="ask-capture: retrieval records (JSONL)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line; see the module docstring."""
    args = _parser().parse_args(argv)
    try:
        return _validate(args) if args.command == "validate" else _convert(args)
    except (_Refused, ConversionError, FormatError, ValueError) as error:
        print(f"qa-bench: {error}", file=sys.stderr)
        if isinstance(error, FormatError):
            for problem in error.problems[:SHOWN]:
                print(f"  {problem}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
