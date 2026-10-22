"""The four formats: schemas, strict readers, deterministic writers and the rules between files."""

from qa_bench.formats.crosscheck import check_against_set
from qa_bench.formats.files import (
    FormatFile,
    Loaded,
    build,
    load,
    load_bytes,
    parse,
    read,
    read_qset,
    read_reviews,
    read_run,
    read_score,
    write,
)
from qa_bench.formats.jsonl import md5_hex
from qa_bench.formats.problems import FormatError, Problem
from qa_bench.formats.schema import (
    FORMATS,
    QA_RUN,
    QA_SCORE,
    QSET,
    REVIEWS,
    check_line,
    load_schema,
)

__all__ = [
    "FORMATS",
    "QA_RUN",
    "QA_SCORE",
    "QSET",
    "REVIEWS",
    "FormatError",
    "FormatFile",
    "Loaded",
    "Problem",
    "build",
    "check_against_set",
    "check_line",
    "load",
    "load_bytes",
    "load_schema",
    "md5_hex",
    "parse",
    "read",
    "read_qset",
    "read_reviews",
    "read_run",
    "read_score",
    "write",
]
