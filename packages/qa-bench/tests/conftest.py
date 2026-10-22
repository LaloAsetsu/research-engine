"""Shared helpers: the synthetic fixtures and a reader of their lines."""

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
SOURCES = FIXTURES / "sources"


def lines_of(name: str) -> list[dict[str, Any]]:
    """The JSON objects of a fixture file, header first."""
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def jsonl_text(records: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)


@pytest.fixture
def qset_lines() -> list[dict[str, Any]]:
    return lines_of("example.qset.jsonl")


@pytest.fixture
def item(qset_lines: list[dict[str, Any]]):
    """One item of the example set by id (a copy)."""

    def get(item_id: str) -> dict[str, Any]:
        return json.loads(json.dumps(next(r for r in qset_lines[1:] if r["id"] == item_id)))

    return get
