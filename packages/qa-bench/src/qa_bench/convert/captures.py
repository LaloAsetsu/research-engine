"""One captured ``docingest ask`` answer as a ``qa-run/1`` item, and the References list parser."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from qa_bench.convert.errors import ConversionError
from qa_bench.formats import md5_hex

REFERENCE = re.compile(r"^\d+\. \(([0-9a-f]{16}) pages (\d+)-(\d+)\)", re.MULTILINE)
HTTP_400 = re.compile(r"\b400\b|context length|maximum context|too long")
RANKS = ("hybrid", "dense", "bm25", "rerank")
LEVELS = ("paper", "strict", "lenient")
CONTEXT_FIELDS = ("doc_id", "name", "start", "first_page", "last_page", "is_reference", "text")
GIVEN_FIELDS = ("doc_id", "name", "start", "text")
ERROR_TAIL = 300


def parse_citations(text: str, full_ids: Iterable[str] = ()) -> list[dict[str, Any]]:
    """The entries of the References list of a formatted answer as ``qa-run/1`` citations.

    An entry reads ``3. (aaaaaaaaaaaaaaaa pages 3-4): ...``. The key is kept as printed, the doc
    id is the full id from ``full_ids`` whose first 16 characters match, else the 16 printed.
    """
    marker = "\nReferences\n"
    references = text.split(marker, 1)[1] if marker in text else ""
    by_prefix = {doc_id[:16]: doc_id for doc_id in full_ids}
    return [
        {
            "key": f"{m[1]} pages {m[2]}-{m[3]}",
            "doc_id": by_prefix.get(m[1], m[1]),
            "first_page": int(m[2]),
            "last_page": int(m[3]),
        }
        for m in REFERENCE.finditer(references)
    ]


def retrieval_ranks(record: dict[str, Any]) -> dict[str, Any]:
    """The twelve rank fields of a retrieval record (``hybrid_paper`` to ``rerank_lenient``)."""
    return {f"{name}_{level}": record.get(f"{name}_{level}") for name in RANKS for level in LEVELS}


def capture_to_item(
    record: dict[str, Any], retrieval: dict[str, Any] | None = None, *, max_tokens: int = 1024
) -> dict[str, Any]:
    """A record of the ask capture script as a ``qa-run/1`` item.

    The item keeps what the scorers read: the answer without its References list, the citations
    with full doc ids, the contexts the answer model got, the reranked candidates, timings,
    usage, the md5 of the answer prompt and an error when the command failed. What the format has
    no field for stays in ``ext``: ``given`` (the retrieved chunks before answer-time additions),
    ``answer_prompt``, ``llm_calls`` and ``retrieval`` (the rank record, when given).
    ``truncated`` is true when the last call used ``max_tokens``.
    """
    try:
        return _capture_to_item(record, retrieval, max_tokens)
    except (KeyError, TypeError, IndexError) as error:
        raise ConversionError(
            f"{record.get('qid', '?')}: not an ask capture record ({type(error).__name__}: {error})"
        ) from None


def _capture_to_item(
    record: dict[str, Any], retrieval: dict[str, Any] | None, max_tokens: int
) -> dict[str, Any]:
    capture = record["capture"]
    deltas = record["server_deltas"]
    calls = [c for c in capture["llm_calls"] if "prompt_tokens" in c]
    last = max(calls, key=lambda c: c.get("end", 0)) if calls else {}
    full = set(capture["sources"])
    for group in ("answerer_contexts", "candidates", "given"):
        full.update(x["doc_id"] for x in capture[group])
    order = sorted(
        range(len(capture["candidates"])), key=lambda i: (-capture["rerank_scores"][i], i)
    )
    stderr = record.get("stderr") or ""
    item: dict[str, Any] = {
        "id": record["qid"],
        "repeat": 1,
        "answer": capture.get("answer") or None,
        "citations": parse_citations(capture.get("formatted_answer") or "", full),
        "contexts": [
            {k: x[k] for k in CONTEXT_FIELDS if k in x} for x in capture["answerer_contexts"]
        ],
        "candidates": [
            {
                "doc_id": capture["candidates"][i]["doc_id"],
                "start": capture["candidates"][i]["start"],
                "score": round(capture["rerank_scores"][i], 6),
            }
            for i in order
        ],
        "timings": {
            "wall_s": record["wall_s"],
            "retrieve_s": capture["t_retrieve"],
            "rerank_s": capture["t_rerank"],
            "answer_s": capture["t_answerer_ask"],
        },
        "usage": {
            "llm_requests": int(deltas["llm"]["requests"]),
            "prompt_tokens": int(deltas["llm"]["prompt_tokens"]),
            "completion_tokens": int(deltas["llm"]["generation_tokens"]),
            "rerank_requests": int(deltas["rerank"]["requests"]),
            "retries": max(len(capture["llm_calls"]) - 1, 0),
            "truncated": bool(last) and last.get("completion_tokens", 0) >= max_tokens,
        },
        "answer_prompt_md5": md5_hex(capture["answer_prompt"].encode("utf-8")),
        "ext": {
            "given": [{k: x[k] for k in GIVEN_FIELDS if k in x} for x in capture["given"]],
            "answer_prompt": capture["answer_prompt"],
            "llm_calls": capture["llm_calls"],
            "retrieval": retrieval_ranks(retrieval) if retrieval else None,
        },
    }
    if record["exit"] != 0:
        item["error"] = {
            "kind": "exit",
            "exit_code": record["exit"],
            "message": stderr[-ERROR_TAIL:],
        }
    elif HTTP_400.search(stderr):
        item["error"] = {"kind": "http_400", "message": stderr[-ERROR_TAIL:]}
    return item
