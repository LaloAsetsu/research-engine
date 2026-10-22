"""QubitQA pilot rows (multiple choice with one gold paper) and ``qset/1`` items, both ways."""

from __future__ import annotations

from typing import Any

from qa_bench.convert.errors import ConversionError

GOLD_EXT = ("route", "marker", "section", "span")


def to_item(row: dict[str, Any], corpus: str | None) -> dict[str, Any]:
    """A pilot row as a ``single`` item with ``choices``; the row names its corpus itself.

    A row with a parent is a mirror (its Spanish or English twin). An unanswerable row is refused:
    ``unanswerable`` needs a ``search_record`` that proves nothing in the corpus answers, and the
    pilot generator does not make one.
    """
    if not row["answerable"]:
        raise ConversionError(
            f"{row['id']}: an unanswerable row needs a search_record, "
            "which this source does not have"
        )
    gold = row["gold"]
    item: dict[str, Any] = {
        "id": row["id"],
        "lang": row["lang"],
        "lang_origin": "mirror" if row["parent_id"] else "native",
        "question": row["question"],
        "answer": row["ideal"],
        "rationale": row["rationale"],
        "form": "single",
        "corpus": row["corpus_snapshot"],
        "split": row["split"],
        "gold_papers": [
            {
                "doc_id": gold["doc_id"],
                "arxiv": gold["arxiv_id"],
                "passages": [{"source_text": gold["evidence_quote"]}],
            }
        ],
        "choices": {
            "distractors": row["distractors"],
            "unsure": row["unsure"],
            "shuffle_seed": row["shuffle_seed"],
        },
        "tags": {"qtype": row["qtype"]},
        "writer": row["generation"]["model"],
        "written": row["generation"]["created"][:10],
        "ext": {
            "version": row["version"],
            "passage_id": row["passage_id"],
            "checks": row["checks"],
            "generation": row["generation"],
            "gold": {k: gold[k] for k in GOLD_EXT},
        },
    }
    if row["parent_id"]:
        item["mirror_of"] = row["parent_id"]
    return item


def from_item(item: dict[str, Any]) -> dict[str, Any]:
    """The pilot row an item was made from."""
    gold, passage = item["gold_papers"][0], item["gold_papers"][0]["passages"][0]
    ext = item["ext"]
    return {
        "id": item["id"],
        "version": ext["version"],
        "lang": item["lang"],
        "parent_id": item.get("mirror_of"),
        "split": item["split"],
        "qtype": item["tags"]["qtype"],
        "answerable": item["form"] == "single",
        "question": item["question"],
        "ideal": item["answer"],
        "distractors": item["choices"]["distractors"],
        "unsure": item["choices"]["unsure"],
        "shuffle_seed": item["choices"]["shuffle_seed"],
        "gold": {
            "arxiv_id": gold["arxiv"],
            "doc_id": gold["doc_id"],
            **ext["gold"],
            "evidence_quote": passage["source_text"],
        },
        "corpus_snapshot": item["corpus"],
        "generation": ext["generation"],
        "rationale": item["rationale"],
        "passage_id": ext["passage_id"],
        "checks": ext["checks"],
    }
