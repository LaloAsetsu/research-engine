"""QASPER questions (public data; several reference answers) and ``qset/1`` items, both ways."""

from __future__ import annotations

from typing import Any


def qtype(refs: list[dict[str, Any]]) -> str:
    """The most common reference type of the question; ties go to the first one in order."""
    counts: dict[str, int] = {}
    for ref in refs:
        counts[ref["type"]] = counts.get(ref["type"], 0) + 1
    top = max(counts.values())
    return next(ref["type"] for ref in refs if counts[ref["type"]] == top)


def to_item(row: dict[str, Any], corpus: str | None) -> dict[str, Any]:
    """A question as a ``single`` item without gold papers: the format needs a doc id for a gold
    paper and the source has the paper's arXiv id only. The first reference is the ``answer``; the
    scorer reads all of them from ``ext.refs``, with the paper, its title and the asked text.
    """
    return {
        "id": row["qid"],
        "lang": row["lang"],
        "lang_origin": "native",
        "question": row["question"],
        "answer": row["refs"][0]["answer"].strip(),
        "form": "single",
        "content": "other",
        "corpus": corpus,
        "gold_papers": [],
        "tags": {"qtype": qtype(row["refs"])},
        "ext": {
            "paper": row["paper"],
            "title": row["title"],
            "text": row["text"],
            "refs": row["refs"],
        },
    }


def from_item(item: dict[str, Any]) -> dict[str, Any]:
    """The question row an item was made from."""
    ext = item["ext"]
    return {
        "qid": item["id"],
        "paper": ext["paper"],
        "title": ext["title"],
        "question": item["question"],
        "text": ext["text"],
        "lang": item["lang"],
        "refs": ext["refs"],
    }
