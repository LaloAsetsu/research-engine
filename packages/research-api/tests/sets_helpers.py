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
