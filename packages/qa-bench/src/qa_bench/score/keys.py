"""Key facts: which keys of a question an answer contains, and the metrics built from them.

A key fact is an expression (``expr``), a phrase (``text``) or a number (``num``). The matching is
that of the vendored matcher (:mod:`qa_bench.score.mathnorm`, version 2), with these decisions of
this scorer, all made here so that the matcher stays untouched:

* **Number words are off.** An integer key from 2 to 20 matches the digits in the answer only.
  The matcher can also read ``eight`` or ``ocho``, but a number word counts wherever it stands,
  and a hand check of those hits found about half of them wrong. A key that only a number word
  would match is flagged ``word_only`` on the key and counts in the ``num_word_only`` metric, and
  the columns ending in ``_words`` give the same four metrics with the word route on, so a table
  shows both. ``number_words=True`` makes the word hits count in the main columns (flagged).
  The details of the word route (a non-breaking hyphen or an en dash in a compound, "hundred and
  two") are the matcher's and only touch the ``_words`` columns.
* **Citation keys are not numbers.** ``(<16 hex> pages a-b)`` is cut from the text (the answer,
  and the chunks and the prompt for ``chunks`` and ``evidence``) before a number key is matched,
  so the digits of a page range do not make a hit. Text and expression keys see the text as it is.
* **Lenient hits of category f are exact hits.** A key of an item tagged ``category`` ``f`` that
  only the lenient rule (symbol recall of at least 0.8) matches does not count: the letters of a
  short equation occur in any prose, a refusal included. The key is flagged ``unchecked`` and the
  lenient columns leave it out, so for category ``f`` lenient equals exact.
* **A text key may be followed by ``^``.** The phrase must still end at a word boundary, and a
  ``^`` after it counts as one, so ``U_k`` is found in ``U_k^\\dagger``.
* **A lone Greek name does not count as an alternative.** An alternative of a text key that
  normalizes to one word that is the name of a Greek letter (``ψ+`` becomes ``psi``) is ignored:
  it would match that word anywhere in the answer. Other alternatives, short ones included, are
  used, and so is the key's own value.

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
_CITATION_KEY = r"[0-9a-f]{16}\s+pages?\s+\d+(?:\s*[-\u2013]\s*\d+)?"
# "(<16 hex> pages a-b)", and several keys in one pair of parentheses
CITATION = re.compile(rf"\(\s*{_CITATION_KEY}(?:\s*[,;]\s*{_CITATION_KEY})*\s*\)", re.IGNORECASE)
# the matcher's names for Greek letters, and the "var" variants it does not list
GREEK_NAMES = frozenset(name.lower() for name in mn.GREEK.values()) | {
    "varphi",
    "varepsilon",
    "varpi",
    "varrho",
    "varsigma",
}


@dataclass(frozen=True)
class Match:
    """How one key fares against one text."""

    exact: bool
    lenient: bool
    word_only: bool = False  # a number key that the text gives only as a number word


def strip_citations(text: str) -> str:
    """``text`` without its citation keys ``(<16 hex> pages a-b)``."""
    return CITATION.sub(" ", text)


def is_generic_alt(alt: str) -> bool:
    """Whether an alternative normalizes to one word that is the name of a Greek letter."""
    word = mn.norm_text(alt).strip()
    if not word or " " in word:
        return False
    return word in GREEK_NAMES


def match_text(key: dict[str, Any], text: str) -> bool:
    """A text key: the matcher's rule, ``^`` ends a phrase like a space, generic alts skipped."""
    flat = mn.norm_text(text)
    for i, phrase in enumerate([key["value"], *key.get("alts", [])]):
        if i > 0 and is_generic_alt(phrase):
            continue
        norm = mn.norm_text(phrase)
        word = norm.strip()
        if word and (norm in flat or f" {word}^" in flat):
            return True
    return False


def match_key(key: dict[str, Any], text: str, *, number_words: bool) -> Match:
    """Whether ``text`` holds the key; see the module docstring for the rules of this scorer."""
    if key["type"] == "expr":
        r = mn.match_expr(key, text)
        return Match(r["exact"], r["exact"] or r["symbol_recall"] >= LENIENT_RECALL)
    if key["type"] == "num":
        read = strip_citations(text)
        digits = mn.match_num_digits(key, read)
        word = not digits and mn.match_num_word(key, read)
        counted = digits or (number_words and word)
        return Match(counted, counted, word)
    hit = match_text(key, text)
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
    rows: list[dict[str, Any]] = []
    in_evidence: list[bool] = []
    # per key: exact, lenient, exact with number words, lenient with number words
    hits: list[tuple[bool, bool, bool, bool]] = []
    for i, key in enumerate(keys):
        found = match_key(key, answer, number_words=number_words)
        with_words = match_key(key, answer, number_words=True)
        unchecked = is_f and found.lenient and not found.exact
        lenient = found.lenient and not unchecked
        lenient_words = with_words.exact if is_f else with_words.lenient
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
