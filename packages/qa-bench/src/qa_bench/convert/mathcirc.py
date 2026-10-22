"""mathcirc v1 rows (one gold paper per question) and ``qset/1`` items, both ways."""

from __future__ import annotations

from typing import Any

from qa_bench.convert.errors import ConversionError

TAGS = ("category", "circuit_kind", "survives", "subfield", "mirrors")
EXT = ("category_name", "survives_auto", "survival_detail")


def to_item(row: dict[str, Any], corpus: str | None) -> dict[str, Any]:
    """A mathcirc v1 row as a ``qset/1`` item of form ``single``.

    The passages of a row are grouped by paper in the order of the row; ``gold_arxiv`` and
    ``gold_doc_ids`` are that order, so they are not stored twice. The tags are the category,
    circuit kind, survival, subfield and the category a Spanish item mirrors (not ``mirror_of``:
    no mathcirc item is a translation of another). Fields only this set has go to ``ext``.
    """
    papers: list[dict[str, Any]] = []
    for passage in row["gold_passages"]:
        rest = {k: v for k, v in passage.items() if k not in ("arxiv", "doc_id")}
        if papers and papers[-1]["doc_id"] == passage["doc_id"]:
            papers[-1]["passages"].append(rest)
        else:
            papers.append(
                {"doc_id": passage["doc_id"], "arxiv": passage["arxiv"], "passages": [rest]}
            )
    if [p["arxiv"] for p in papers] != row["gold_arxiv"] or [p["doc_id"] for p in papers] != row[
        "gold_doc_ids"
    ]:
        raise ConversionError(
            f"{row['id']}: the passages are not grouped by paper in the order of gold_arxiv "
            "and gold_doc_ids"
        )
    return {
        "id": row["id"],
        "lang": row["lang"],
        "lang_origin": "native",
        "question": row["question"],
        "answer": row["answer"],
        "form": "single",
        "content": "circuit" if row["category"] == "d" else "math",
        "corpus": corpus,
        "gold_papers": papers,
        "key_facts": row["key_facts"],
        "tags": {k: row[k] for k in TAGS if row[k] is not None},
        "notes": row["notes"],
        "ext": {k: row[k] for k in EXT},
    }


def from_item(item: dict[str, Any]) -> dict[str, Any]:
    """The mathcirc v1 row an item was made from."""
    out: dict[str, Any] = {"id": item["id"]}
    out.update({k: item["tags"].get(k) for k in TAGS})
    out.update(
        {
            "lang": item["lang"],
            "question": item["question"],
            "gold_arxiv": [g["arxiv"] for g in item["gold_papers"]],
            "gold_doc_ids": [g["doc_id"] for g in item["gold_papers"]],
            "gold_passages": [
                {"arxiv": g["arxiv"], "doc_id": g["doc_id"], **p}
                for g in item["gold_papers"]
                for p in g["passages"]
            ],
            "answer": item["answer"],
            "key_facts": item["key_facts"],
            "notes": item["notes"],
        }
    )
    out.update(item["ext"])
    return out
