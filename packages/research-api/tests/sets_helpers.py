"""Builders of question-set files and items for the tests. Every value is made up."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures" / "sets"
EXAMPLE = FIXTURES / "example.qset.jsonl"
SOURCES = FIXTURES / "sources"
CREATED = "2026-01-03T09:00:00+00:00"
DOC_A = "a1" * 32


def example_bytes() -> bytes:
    return EXAMPLE.read_bytes()


def example_lines() -> list[dict[str, Any]]:
    return [json.loads(line) for line in EXAMPLE.read_text(encoding="utf-8").splitlines() if line]


def example_items() -> list[dict[str, Any]]:
    return example_lines()[1:]


def example_item(item_id: str) -> dict[str, Any]:
    return copy.deepcopy(next(i for i in example_items() if i["id"] == item_id))


def item(item_id: str = "T-001", **changes: Any) -> dict[str, Any]:
    """The smallest valid item: a single question with one gold paper."""
    base: dict[str, Any] = {
        "id": item_id,
        "lang": "en",
        "question": f"Synthetic question {item_id}?",
        "answer": "Synthetic answer.",
        "form": "single",
        "content": "other",
        "corpus": "test-corpus",
        "gold_papers": [{"doc_id": DOC_A, "passages": [{"source_text": "A synthetic quote."}]}],
    }
    base.update(changes)
    return {k: v for k, v in base.items() if v is not None}


def header(name: str = "demo/v1", **changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"format": "qset/1", "set": name, "created": CREATED}
    base.update(changes)
    return base


def qset_bytes(
    items: list[dict[str, Any]] | None = None, name: str = "demo/v1", **header_changes: Any
) -> bytes:
    lines = [header(name, **header_changes), *(items if items is not None else [item()])]
    return "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines).encode()


def broken() -> list[tuple[str, dict[str, Any], str]]:
    chain = example_item("EX-002")
    single = example_item("EX-001")
    without = lambda base, key: {k: v for k, v in base.items() if k != key}  # noqa: E731
    return [
        ("an unknown field", {**single, "colour": "red"}, "/colour"),
        ("multi_chain without necessity", without(chain, "necessity"), "/necessity"),
        ("multi_chain without synthesis", without(chain, "synthesis"), "/synthesis"),
        (
            "multi-paper item with one paper",
            {**chain, "gold_papers": chain["gold_papers"][:1]},
            "/gold_papers",
        ),
        ("verify answer maybe", {**example_item("EX-006"), "answer": "maybe"}, "/answer"),
        ("mirror without mirror_of", without(example_item("EX-002-es"), "mirror_of"), "/mirror_of"),
        (
            "unanswerable with a gold paper",
            {**example_item("EX-003"), "gold_papers": chain["gold_papers"]},
            "/gold_papers",
        ),
        (
            "unanswerable without search_record",
            without(example_item("EX-003"), "search_record"),
            "/search_record",
        ),
        (
            "num key with a string",
            {**single, "key_facts": [{"type": "num", "value": "1.2", "rel": 0.02}]},
            "/key_facts/0/value",
        ),
        (
            "text key with a unit",
            {**single, "key_facts": [{"type": "text", "value": "J", "unit": "GHz"}]},
            "/key_facts/0",
        ),
        ("circuit item without circuit", without(example_item("EX-006"), "circuit"), "/circuit"),
        (
            "circuit item not content circuit",
            {**example_item("EX-006"), "content": "math"},
            "/content",
        ),
        (
            "a bad doc id",
            {**single, "gold_papers": [{"doc_id": "xyz", "passages": []}]},
            "/gold_papers/0/doc_id",
        ),
        ("question missing", without(single, "question"), "/question"),
        ("a bad id", {**single, "id": "has space"}, "/id"),
        ("a bad language", {**single, "lang": "fr"}, "/lang"),
        ("a bad date", {**single, "written": "2026-02-30"}, "/written"),
    ]
