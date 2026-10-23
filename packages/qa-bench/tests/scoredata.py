"""Synthetic question sets and runs for the scorer tests: made-up papers, questions and answers."""

from typing import Any

from qa_bench.formats import QA_RUN, QSET, FormatFile, build

CREATED = "2026-01-05T09:00:00+00:00"
DOC_A = "a1" * 32
DOC_B = "b2" * 32
CORPUS = "syn-corpus"
SET_NAME = "syn-set/v1"


def question(
    qid: str,
    keys: list[dict[str, Any]] | None,
    *,
    category: str = "a",
    lang: str = "en",
    survives: str = "yes",
    gold: str = DOC_A,
    **extra: Any,
) -> dict[str, Any]:
    """One single-paper item; ``keys`` None gives an item without key facts."""
    item: dict[str, Any] = {
        "id": qid,
        "lang": lang,
        "form": "single",
        "content": "math",
        "corpus": CORPUS,
        "question": f"Question {qid}?",
        "gold_papers": [{"doc_id": gold, "passages": [{"md_text": f"Passage of {qid}."}]}],
        "tags": {"category": category, "survives": survives},
    }
    if keys is not None:
        item["key_facts"] = keys
    item.update(extra)
    return item


def text_key(value: str, **more: Any) -> dict[str, Any]:
    return {"type": "text", "value": value, **more}


def num_key(value: float, rel: float = 0.02) -> dict[str, Any]:
    return {"type": "num", "value": value, "rel": rel}


def expr_key(value: str, **more: Any) -> dict[str, Any]:
    return {"type": "expr", "value": value, **more}


def make_qset(items: list[dict[str, Any]], name: str = SET_NAME) -> FormatFile:
    return build(QSET, {"format": QSET, "set": name, "created": CREATED}, items)


def answer(qid: str, text: str | None, repeat: int = 1, **extra: Any) -> dict[str, Any]:
    return {
        "id": qid,
        "repeat": repeat,
        "answer": text,
        "contexts": [],
        "timings": {"wall_s": 2.0},
        **extra,
    }


def make_run(
    run_id: str,
    qset: FormatFile,
    answers: list[dict[str, Any]],
    *,
    arm: str = "harness",
    variant: str | None = None,
    corpus: str = CORPUS,
) -> FormatFile:
    arm_header: dict[str, Any] = {"name": arm}
    if variant:
        arm_header["variant"] = variant
    header = {
        "format": QA_RUN,
        "run_id": run_id,
        "created": CREATED,
        "set": {"name": qset.header["set"], "md5": qset.md5},
        "arm": arm_header,
        "model": {"id": "toy-model"},
        "code": {"runner": "tests 0.1"},
        "corpus": {"id": corpus},
    }
    return build(QA_RUN, header, answers)


# ---- a set shaped like the 101-question set behind a paired comparison of two runs

CATEGORY_SIZES = {"a": 9, "b": 14, "c": 13, "d": 23, "e": 10, "f": 12, "g": 20}
F_RANGE = range(69, 81)
# What happened to the f questions: (status in the base run, status in the variant run). E: every
# key exact, L: only the lenient rule holds, X: a miss.
F_STATUS = {
    69: "LE", 70: "LE", 71: "LE", 72: "LX", 73: "LX", 74: "EL", 75: "XL", 76: "LL", 77: "LL",
    78: "EE", 79: "XX", 80: "EE",
}  # fmt: skip


def category_of(index: int) -> str:
    start = 0
    for category, size in CATEGORY_SIZES.items():
        if index < start + size:
            return category
        start += size
    raise IndexError(index)


def statuses() -> dict[int, str]:
    """Two letters per question: the status in the base run and in the variant run."""
    out = dict(F_STATUS)
    plain = [i for i in range(101) if i not in F_RANGE]
    out[5], out[20] = "XE", "EX"
    stay_x = [i for i in plain if i not in (5, 20)][::5][:17]
    for i in plain:
        out.setdefault(i, "XX" if i in stay_x else "EE")
    return dict(sorted(out.items()))


def qid(index: int) -> str:
    return f"SYN-{index + 1:03d}"


def keys_of(index: int) -> list[dict[str, Any]]:
    category = category_of(index)
    word = text_key(f"concept{index:03d}")
    if category == "f":
        return [expr_key("a+b+c+d+e")]
    if category == "d":
        return [word, num_key(3)]
    if category == "g":
        return [word, text_key(f"second{index:03d}")]
    return [word]


def big_qset() -> FormatFile:
    items = [
        question(
            qid(i),
            keys_of(i),
            category=category_of(i),
            lang="en" if i % 2 == 0 else "es",
            survives=("yes", "yes", "yes", "partial", "no")[i % 5],
        )
        for i in range(101)
    ]
    return make_qset(items)


def text_for(index: int, status: str) -> str:
    """An answer to question ``index`` with the given status (E, L or X)."""
    category = category_of(index)
    if category == "f":
        cited = f" ({DOC_A[:16]} pages 1-2)"
        return {
            "E": f"The relation is $a+b+c+d+e${cited}.",
            "L": f"The relation among a b c d e is shown{cited}.",
            "X": "I do not know this one.",
        }[status]
    if status == "E":
        parts = [f"concept{index:03d}"]
        if category == "d":
            parts.append("3 gates")
        if category == "g":
            parts.append(f"second{index:03d}")
        return "It holds " + " and ".join(parts) + "."
    return "I cannot answer this." if index % 2 == 0 else "No relevant result was found."


def big_run(run_id: str, which: int, qset: FormatFile, flips: dict[int, str] | None = None):
    """The run for the base (0) or the variant (1) column of :func:`statuses`; the answers are
    written in reverse order so that a scorer that follows the run file would differ."""
    flips = flips or {}
    answers = [
        answer(qid(i), text_for(i, flips.get(i, status[which]))) for i, status in statuses().items()
    ]
    return make_run(run_id, qset, list(reversed(answers)))
