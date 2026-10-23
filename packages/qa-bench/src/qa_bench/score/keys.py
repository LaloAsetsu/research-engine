"""Key facts: which keys of a question an answer contains, and the metrics built from them.

A key fact is an expression (``expr``), a phrase (``text``) or a number (``num``). The matching is
that of the vendored matcher (:mod:`qa_bench.score.mathnorm`, version 2), with two decisions of
this scorer:

* **Number words are off.** An integer key from 2 to 20 matches the digits in the answer only.
  The matcher can also read ``eight`` or ``ocho``, but a number word counts wherever it stands,
  and a hand check of those hits found about half of them wrong. A key that only a number word
  would match is flagged ``word_only`` on the key and counts in the ``num_word_only`` metric, and
  the columns ending in ``_words`` give the same four metrics with the word route on, so a table
  shows both. ``number_words=True`` makes the word hits count in the main columns (flagged).
* **Lenient hits of category f need the gold paper.** A key of an item tagged ``category`` ``f``
  that only the lenient rule (symbol recall of at least 0.8) matches counts only when the answer
  cites a gold paper (its 16-hex id in the text or in a citation); otherwise the key is flagged
  ``unchecked`` and the lenient columns leave it out.

The metrics also give what the funnel needs: the share of keys found in the chunks the model was
given (``chunks``) and in its prompt (``evidence``), the math spans of the answer that appear in
neither (``unsupported_math``), and the cannot-answer pattern.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from qa_bench.score import mathnorm as mn

# md5 of the matcher's source after its docstring (the code of version 2, unchanged).
MATHNORM_CODE_MD5 = "49ccac59ddd9dd7ee2319256596fd269"
LENIENT_RECALL = 0.8
MIN_SPAN = 6

MATH = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$|\\\((.+?)\\\)|\\\[(.+?)\\\]", re.DOTALL)
CANNOT_RE = re.compile(
    r"cannot answer|not (?:explicitly )?(?:provide|specif|state|give|list)|"
    r"no (?:se|lo) (?:especifica|proporciona|menciona)|does not (?:provide|"
    r"specify|state|give|list|contain|include)",
    re.IGNORECASE,
)
DOC16 = re.compile(r"\b([0-9a-f]{16})\b")


@dataclass(frozen=True)
class Match:
    """How one key fares against one text."""

    exact: bool
    lenient: bool
    word_only: bool = False  # a number key that the text gives only as a number word


def match_key(key: dict[str, Any], text: str, *, number_words: bool) -> Match:
    """Whether ``text`` holds the key; see the module docstring for the number-word rule."""
    if key["type"] == "expr":
        r = mn.match_expr(key, text)
        return Match(r["exact"], r["exact"] or r["symbol_recall"] >= LENIENT_RECALL)
    if key["type"] == "num":
        digits = mn.match_num_digits(key, text)
        word = not digits and mn.match_num_word(key, text)
        counted = digits or (number_words and word)
        return Match(counted, counted, word)
    hit = mn.match_text(key, text)
    return Match(hit, hit)


def math_spans(text: str) -> list[str]:
    """The canonical forms of the math spans (``$..$``, ``$$..$$``, ``\\(..\\)``, ``\\[..\\]``)."""
    out: list[str] = []
    for m in MATH.finditer(text or ""):
        span = next(g for g in m.groups() if g is not None)
        canon = mn.canon(span)
        if len(canon) >= MIN_SPAN:
            out.append(canon)
    return out


def gold_cited(question: dict[str, Any], item: dict[str, Any], answer: str) -> bool:
    """Whether the answer cites a gold paper: its 16-hex id in the text or in a citation."""
    cited = set(DOC16.findall(answer))
    cited |= {c["doc_id"][:16] for c in item.get("citations") or []}
    return any(g["doc_id"][:16] in cited for g in question["gold_papers"])


def text_of(chunks: list[dict[str, Any]] | None) -> str | None:
    """The joined text of a list of ``{text}``, or None when the capture kept no text."""
    if chunks is None or not any(c.get("text") for c in chunks):
        return None
    return "\n".join(c.get("text", "") for c in chunks)


def evidence_of(ext: dict[str, Any]) -> str | None:
    """The text the answer model read: the answer prompt, else the joined summaries."""
    if ext.get("answer_prompt") is not None:
        return ext["answer_prompt"]
    if ext.get("summaries") is not None:
        return "\n".join(s or "" for s in ext["summaries"])
    return None


def key_scores(
    question: dict[str, Any], item: dict[str, Any], *, number_words: bool = False
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The metrics of one answer to a question with key facts, and one record per key."""
    ext = item.get("ext") or {}
    answer = item.get("answer") or ""
    keys = question["key_facts"]
    n = len(keys)
    chunks = text_of(ext.get("given"))
    evidence = evidence_of(ext)
    is_f = (question.get("tags") or {}).get("category") == "f"
    cited = gold_cited(question, item, answer)
    rows: list[dict[str, Any]] = []
    in_evidence: list[bool] = []
    # per key: exact, lenient, exact with number words, lenient with number words
    hits: list[tuple[bool, bool, bool, bool]] = []
    for i, key in enumerate(keys):
        found = match_key(key, answer, number_words=number_words)
        with_words = match_key(key, answer, number_words=True)
        unchecked = is_f and found.lenient and not found.exact and not cited
        lenient = found.lenient and not unchecked
        lenient_words = with_words.lenient and not (is_f and not with_words.exact and not cited)
        hits.append((found.exact, lenient, with_words.exact, lenient_words))
        row: dict[str, Any] = {"i": i, "exact": found.exact, "lenient": lenient}
        if unchecked:
            row["unchecked"] = True
        if key["type"] == "num":
            row["word_only"] = found.word_only
        if key.get("paper"):
            row["paper"] = key["paper"]
        if chunks is not None:
            row["in_contexts"] = match_key(key, chunks, number_words=number_words).exact
        if evidence is not None:
            in_evidence.append(match_key(key, evidence, number_words=number_words).exact)
        rows.append(row)
    metrics: dict[str, Any] = {
        "all_keys": all(h[0] for h in hits),
        "all_keys_lenient": all(h[1] for h in hits),
        "key_share": sum(h[0] for h in hits) / n,
        "key_share_lenient": sum(h[1] for h in hits) / n,
        "all_keys_words": all(h[2] for h in hits),
        "all_keys_lenient_words": all(h[3] for h in hits),
        "key_share_words": sum(h[2] for h in hits) / n,
        "key_share_lenient_words": sum(h[3] for h in hits) / n,
        "chunks": sum(r["in_contexts"] for r in rows) / n if chunks is not None else None,
        "evidence": sum(in_evidence) / n if evidence is not None else None,
        "answer_chars": len(answer),
        "cannot_answer": bool(CANNOT_RE.search(answer)),
        "lenient_unchecked": any(r.get("unchecked") for r in rows),
        "num_word_only": any(r.get("word_only") for r in rows),
    }
    if chunks is not None and evidence is not None:
        canon_evidence, canon_chunks = mn.canon(evidence), mn.canon(chunks)
        spans = math_spans(answer)
        metrics["math_spans"] = len(spans)
        metrics["unsupported_math"] = len(
            [s for s in spans if s not in canon_evidence and s not in canon_chunks]
        )
    else:
        metrics["math_spans"] = metrics["unsupported_math"] = None
    return metrics, rows
