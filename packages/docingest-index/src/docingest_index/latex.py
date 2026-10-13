"""LaTeX in a question, for ``[index] bm25 = "english"``.

The dense embedder places a quoted formula poorly, while BM25 matches its tokens (the commands,
the symbols and the digits) in the chunks that hold the equation, in any language of the
question. So a question with LaTeX gets the keyword ranking even when it does not look English;
its prose words are left out then, because they rarely occur in an English corpus, and only the
words of the LaTeX are searched. The index side needs nothing: the chunks keep their LaTeX as
written, and tantivy's ``en_stem`` tokenizer splits it at every character that is not a letter
or a digit, so ``\\frac{n!}{2}`` is indexed as ``frac``, ``n`` and ``2`` and a query goes through
the same split (``local.py``).

What counts as LaTeX, outside of which ordinary prose is left alone:

- ``$...$`` (the opening ``$`` is followed by a non-space and the closing one is preceded by a
  non-space and not followed by a digit, so "$5 and $10" is not math), ``$$...$$``,
  ``\\(...\\)`` and ``\\[...\\]`` (up to 1000 characters inside), when the inside has one of
  ``\\^_{}=+<>|``, or a letter and neither a word of three letters nor only numbers and words of
  one or two letters with two numbers or more ("$5 and 10$" and "$5 y 10$" are prose, "$n - 1$"
  and "$2 x$" are math);
- outside delimiters, a word that holds a command of two or more letters (``\\frac``, ``\\sum``,
  ``\\ket``) that does not follow a letter, ``:``, ``/``, ``.``, ``%`` or ``\\`` (a Windows path),
  or a braced subscript or superscript (``x_{i}``, ``10^{-3}``).

A bare ``x_i``, ``T_1`` or ``x^2`` is not LaTeX here: it is as likely a name or a variable, and a
BM25 ranking built from such a mark is unrelated to the dense one, so the fusion would replace
half of the dense candidates with noise. For the same reason the LaTeX of a question counts only
when it holds a token of two letters or more (``frac``, ``omega``, ``mc``) or at least three
tokens: ``$x$``, ``$T_1$``, ``$f(x)$`` and ``$k = 2$`` give nothing to search.

A word here is a run of characters without spaces, where a ``{...}`` group may hold spaces.
"""

from __future__ import annotations

import re

_MAYBE = re.compile(r"[\\$^_]")  # nothing else can start LaTeX: skip the other questions at once
_INSIDE = 1000  # longest math between delimiters; an unclosed opener then costs at most this much
_DELIMITED = re.compile(
    rf"\$\$(.{{1,{_INSIDE}}}?)\$\$"
    rf"|\\\((.{{1,{_INSIDE}}}?)\\\)"
    rf"|\\\[(.{{1,{_INSIDE}}}?)\\\]"
    rf"|(?<![\\$])\$(?![\s$])([^$]{{0,{_INSIDE}}}?[^\s$])\$(?![\d$])",
    re.DOTALL,
)
_STRONG = re.compile(r"[\\^_{}=+<>|]")
_LETTER = re.compile(r"[A-Za-z]")
_PROSE_WORD = re.compile(r"[A-Za-z]{3}")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_SHORT_WORD = re.compile(r"[A-Za-z]{1,2}")
_TOKEN = re.compile(r"[A-Za-z0-9]+")  # what the keyword search keeps of a text
_TWO_LETTERS = re.compile(r"[A-Za-z]{2}")
_MIN_TOKENS = 3  # fewer tokens, none of them a word, are too generic to rank anything
_WORD = re.compile(r"(?:\\[A-Za-z]+|\\.|\{(?:[^{}]|\{[^{}]*\})*\}|[^\s\\{}])+")
_SIGNAL = re.compile(
    r"(?<![A-Za-z:/.%\\])\\[A-Za-z]{2,}"  # \frac, \sum, \ket
    r"|[A-Za-z0-9)\]}][_^]\{[^{}]+\}"  # x_{i}, 10^{-3}
)


def _is_money(inside: str) -> bool:
    """Only numbers and words of one or two letters, with two numbers or more: "5 y 10",
    "5, 6 y 7", "1.000 y 2.000", money between two stray dollar signs. "n - 1", "2 x" and
    "x 2y" are not."""
    numbers = 0
    for raw in inside.split():
        token = raw.rstrip(",.;")
        if _NUMBER.fullmatch(token):
            numbers += 1
        elif not _SHORT_WORD.fullmatch(token):
            return False
    return numbers >= 2


def _is_math(inside: str) -> bool:
    if _STRONG.search(inside):
        return True
    if _is_money(inside):
        return False
    return bool(_LETTER.search(inside)) and not _PROSE_WORD.search(inside)


def latex_text(question: str) -> str:
    """The LaTeX of a question, delimited math and words with LaTeX in them, joined by spaces;
    empty when it has none, or too little to search: no token of two letters and fewer than
    three tokens."""
    if not _MAYBE.search(question):
        return ""
    parts: list[str] = []

    def take(match: re.Match[str]) -> str:
        inside = next(group for group in match.groups() if group is not None)
        if not _is_math(inside):
            return match.group(0)
        parts.append(inside)
        return " "

    rest = _DELIMITED.sub(take, question)
    parts.extend(word for word in _WORD.findall(rest) if _SIGNAL.search(word))
    found = " ".join(parts)
    tokens = _TOKEN.findall(found)
    if len(tokens) < _MIN_TOKENS and not _TWO_LETTERS.search(found):
        return ""
    return found


def has_latex(question: str) -> bool:
    return bool(latex_text(question))
