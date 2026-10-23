"""``RunFormats`` over qa-bench: the lines of a ``qa-run/1`` file, and a lenient reader of one.

A run file grows while the run goes on, so a reader here accepts what a writer may have left: a
last line without its line feed (cut by a crash or caught half written) is not part of the file.
Lines the service writes are checked against the schema first: an invalid line is a bug of this
service, never something to store.
"""

from __future__ import annotations

import gzip
import io
from collections.abc import Mapping
from typing import Any

from qa_bench.formats import QA_RUN, check_line
from qa_bench.formats.jsonl import GZIP_MAGIC, dumps_line, loads

from ...domain.errors import CorruptStoreError
from ...ports.jobs import RunData

MAX_RUN_BYTES = 512 * 1024 * 1024
"""Most a run file may unzip to. A run is a few megabytes; this stops a bomb in a dropped file."""


def _unzip(data: bytes) -> bytes:
    if data[:2] != GZIP_MAGIC:
        return data
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
        out = stream.read(MAX_RUN_BYTES + 1)
    if len(out) > MAX_RUN_BYTES:
        raise CorruptStoreError("a run file is far larger than any run")
    return out


class QaBenchRunFormats:
    def header_line(self, header: Mapping[str, Any]) -> bytes:
        return self._line(header, header=True)

    def item_line(self, item: Mapping[str, Any]) -> bytes:
        return self._line(item, header=False)

    def item_fits(self, item: Mapping[str, Any]) -> bool:
        return not check_line(QA_RUN, dict(item), header=False)

    @staticmethod
    def _line(record: Mapping[str, Any], *, header: bool) -> bytes:
        problems = check_line(QA_RUN, dict(record), header=header)
        if problems:
            shown = "; ".join(f"{p.path or '/'}: {p.message}" for p in problems[:3])
            raise ValueError(f"a line the service made is not qa-run/1: {shown}")
        return (dumps_line(record) + "\n").encode("utf-8")

    def read(self, data: bytes) -> RunData:
        try:
            raw = _unzip(data)
        except (OSError, EOFError):
            raise CorruptStoreError("a run file cannot be unzipped") from None
        end = raw.rfind(b"\n") + 1
        complete = raw[:end]
        lines = [line for line in complete.decode("utf-8", errors="replace").split("\n") if line]
        if not lines:
            raise CorruptStoreError("a run file has no header")
        try:
            header = loads(lines[0])
            items = tuple(loads(line) for line in lines[1:])
        except ValueError:
            raise CorruptStoreError("a run file has a line that is not JSON") from None
        if not isinstance(header, dict) or header.get("format") != QA_RUN:
            raise CorruptStoreError("a run file does not start with a qa-run/1 header")
        if check_line(QA_RUN, header, header=True):
            raise CorruptStoreError("a run file has a qa-run/1 header that does not fit the format")
        if not all(isinstance(i, dict) for i in items):
            raise CorruptStoreError("a run file has a line that is not an object")
        return RunData(header, items, end)
