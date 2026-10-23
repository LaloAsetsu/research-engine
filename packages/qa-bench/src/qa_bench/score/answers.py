"""Metrics that do not need key facts: retrieval ranks, cost, and the short-answer F1 of QASPER."""

from __future__ import annotations

import re
import string
from collections import Counter
from typing import Any

# metric -> (field of ext.retrieval, rank threshold): a hit when the gold passage is at or above it
RANK_FIELDS = {
    "psg10": ("hybrid_lenient", 10),
    "psg50": ("hybrid_lenient", 50),
    "rr1": ("rerank_lenient", 1),
    "rr1_strict": ("rerank_strict", 1),
}
GOOD_F1 = 0.5
_PUNCTUATION = frozenset(string.punctuation)


def retrieval_scores(item: dict[str, Any]) -> dict[str, bool | None]:
    """Whether the gold passage was ranked high enough; None when the run kept no ranks."""
    ranks = (item.get("ext") or {}).get("retrieval")
    out: dict[str, bool | None] = {}
    for name, (field, k) in RANK_FIELDS.items():
        rank = ranks.get(field) if ranks else None
        out[name] = None if not ranks else (rank is not None and rank <= k)
    return out


def cost_scores(item: dict[str, Any]) -> dict[str, Any]:
    """Time, tokens and failure of one answer."""
    timings = item.get("timings") or {}
    usage = item.get("usage") or {}
    calls = [c for c in (item.get("ext") or {}).get("llm_calls", []) if "prompt_tokens" in c]
    last = max(calls, key=lambda c: c.get("end", 0)) if calls else {}
    return {
        "wall_s": timings.get("wall_s"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "llm_requests": usage.get("llm_requests"),
        "answer_prompt_tokens": last.get("prompt_tokens"),
        "error": "error" in item,
    }


def normalize_answer(text: str) -> str:
    """The official QASPER normalization: lower case, no punctuation, no articles, one space."""
    text = "".join(ch for ch in text.lower() if ch not in _PUNCTUATION)
    text = re.sub(r"\b(a|an|the)\b", " ", text)
    return " ".join(text.split())


def token_f1(prediction: str, gold: str) -> float:
    """SQuAD token F1 of two answers after :func:`normalize_answer`."""
    p, g = normalize_answer(prediction).split(), normalize_answer(gold).split()
    same = sum((Counter(p) & Counter(g)).values())
    if same == 0:
        return 0.0
    precision, recall = same / len(p), same / len(g)
    return 2 * precision * recall / (precision + recall)


def qasper_scores(question: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """Answer-F1 of the extracted short answer (``ext.short``) against the best reference.

    ``refused`` is the share of answers that say they cannot answer; all three are None when the
    run kept no short answer.
    """
    ext = item.get("ext") or {}
    if "short" not in ext:
        return {"answer_f1": None, "answer_good": None, "refused": None}
    f1 = max(token_f1(ext["short"], ref["answer"]) for ref in question["ext"]["refs"])
    return {
        "answer_f1": f1,
        "answer_good": f1 >= GOOD_F1,
        "refused": "cannot answer" in (item.get("answer") or "").lower(),
    }
