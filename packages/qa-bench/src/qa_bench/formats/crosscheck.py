"""Rules that need two files: a run, a score or a review against the set it belongs to."""

from __future__ import annotations

from typing import Any

from qa_bench.formats.files import FormatFile
from qa_bench.formats.problems import Problem
from qa_bench.formats.schema import QA_RUN, QA_SCORE, QSET, REVIEWS


def _set_ref(fmt: str, name: object, md5: object, qset: FormatFile, where: str) -> list[Problem]:
    problems: list[Problem] = []
    if name != qset.header["set"]:
        problems.append(
            Problem(
                1,
                f"{where}/name",
                f"the {fmt} file is for set {name!r}, not {qset.header['set']!r}",
            )
        )
    if md5 is not None and md5 != qset.md5:
        problems.append(
            Problem(
                1, f"{where}/md5", f"the set file has md5 {qset.md5}, the {fmt} file says {md5}"
            )
        )
    return problems


def _gold_texts(item: dict[str, Any]) -> list[str]:
    return [
        p["md_text"]
        for paper in item.get("gold_papers", [])
        for p in paper.get("passages", [])
        if "md_text" in p
    ]


def check_against_set(file: FormatFile, qset: FormatFile) -> list[Problem]:
    """What a run, a score or a reviews file gets wrong about the set it points at.

    Run: the set name and md5 match the set file, every answer is for an item of the set, and an
    ``oracle`` answer's contexts are the gold passages (``md_text``, in set order), except those of
    an error line, which has no answer and so no contexts to check. Score: the
    set name and md5 match, every item record is for an item of the set. Reviews: the set name
    matches and every decision names an item of the set.
    """
    if qset.format != QSET:
        raise ValueError(f"the set must be a {QSET} file, not {qset.format}")
    items = {r["id"]: r for r in qset.records}
    problems: list[Problem] = []
    if file.format == QA_RUN:
        ref = file.header["set"]
        problems += _set_ref(QA_RUN, ref.get("name"), ref.get("md5"), qset, "/set")
        oracle = file.header["arm"]["name"] == "oracle"
        for i, record in enumerate(file.records):
            line = file.line_of(i)
            item = items.get(record["id"])
            if item is None:
                problems.append(Problem(line, "/id", f"{record['id']!r} is not an item of the set"))
            elif (
                oracle
                and "error" not in record  # an answer that was never made read no passage
                and [c.get("text") for c in record["contexts"]] != _gold_texts(item)
            ):
                problems.append(
                    Problem(
                        line,
                        "/contexts",
                        "an oracle answer's contexts are the gold passages (md_text)",
                    )
                )
    elif file.format == QA_SCORE:
        ref = file.header["set"]
        problems += _set_ref(QA_SCORE, ref.get("name"), ref.get("md5"), qset, "/set")
        problems += _unknown_ids(file, items, only={"item"})
    elif file.format == REVIEWS:
        problems += _set_ref(REVIEWS, file.header["set"], None, qset, "/set")
        problems += _unknown_ids(file, items, field="item_id")
    else:
        raise ValueError(f"a {file.format} file does not belong to a set")
    return problems


def _unknown_ids(
    file: FormatFile,
    items: dict[str, Any],
    *,
    field: str = "id",
    only: set[str] | None = None,
) -> list[Problem]:
    return [
        Problem(file.line_of(i), f"/{field}", f"{record[field]!r} is not an item of the set")
        for i, record in enumerate(file.records)
        if (only is None or record.get("record") in only) and record[field] not in items
    ]
