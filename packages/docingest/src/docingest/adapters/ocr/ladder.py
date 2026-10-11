"""The retry ladder shared by the OCR adapters.

One page is transcribed up to ``guards.max_attempts`` times (a profile with its own
ladder, such as olmOCR, keeps it). After an attempt the output is checked
(:func:`~docingest.domain.ocr_guards.find_problem`, plus the profile's own validity
test) and, if it is not accepted, the next attempt differs in the way the problem asks:

* ``truncated``: the page is dense and ran out of tokens, so the same settings again
  with more room (``retry_max_tokens``), not another sampling of the same cut-off text,
  provided the new limit is at least ``retry_max_tokens_gain`` times the old one (a
  profile at 8000 would gain 2% and lose its stricter retry);
* anything else (a loop, text too long for the page, nothing): the next, stricter step
  of the ladder (a little sampling, a stronger repetition penalty) at the configured
  limit, never at a raised one: a loop then costs one budget, not two.

A page still cut off with the raised limit ends the ladder, and among cut-off answers the
longer one is kept. A server whose context cannot hold the raised limit answers HTTP 400:
the adapter says so with :class:`LimitRefused` and the ladder stops there, keeping the best
answer so far.

The attempt that is returned is the least bad one, not simply the last.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ...domain.ocr_guards import OcrGuards, OutputProblem, find_problem, retry_max_tokens
from .profiles import OcrProfile, attempts


class LimitRefused(Exception):
    """The runtime refused an attempt because of its raised ``max_tokens`` (a context too
    small for prompt plus the larger limit). Raised by ``generate``; ends the ladder."""


@dataclass(frozen=True)
class Attempt:
    temperature: float
    penalty: float | None
    max_tokens: int


@dataclass(frozen=True)
class Generation:
    """What the ladder needs to know about one attempt's reply."""

    text: str  # the model's output before clean-up
    finish_reason: str | None


# Worse is larger. An output the profile merely finds invalid (no front matter) is still
# a transcription; a cut-off one is a real page that did not fit; the rest is not a page.
_SEVERITY: dict[OutputProblem | None, int] = {
    None: 1,
    OutputProblem.TRUNCATED: 2,
    OutputProblem.EMPTY: 3,
    OutputProblem.LOOP: 4,
    OutputProblem.TOO_LONG: 4,
}


def assess(
    profile: OcrProfile, gen: Generation, area_sq_in: float | None, guards: OcrGuards
) -> tuple[OutputProblem | None, bool]:
    """(problem of the cleaned output, whether the attempt is accepted as it is)."""
    problem = find_problem(profile.postprocess(gen.text), gen.finish_reason, area_sq_in, guards)
    return problem, problem is None and (profile.valid is None or profile.valid(gen.text))


def run_ladder[T](
    profile: OcrProfile,
    guards: OcrGuards,
    *,
    temperature: float,
    penalty: float | None,
    max_tokens: int,
    area_sq_in: float | None,
    generate: Callable[[Attempt, int], T],
    read: Callable[[T], Generation],
) -> tuple[list[T], int]:
    """Every attempt's reply, in order, and the index of the one to use.

    ``generate(attempt, index)`` asks the runtime once; ``read(reply)`` extracts text and
    finish reason from its reply. ``area_sq_in`` is the page size, None when unknown.
    """
    ladder = attempts(profile, temperature, penalty, guards)
    replies: list[T] = []
    scores: list[tuple[int, int, int]] = []
    step = 0
    current = Attempt(*ladder[0], max_tokens)
    while True:
        try:
            reply = generate(current, len(replies))
        except LimitRefused:
            if not replies:
                raise
            break
        replies.append(reply)
        gen = read(reply)
        problem, accepted = assess(profile, gen, area_sq_in, guards)
        # Among cut-off answers the longer one has more of the page.
        longer = -len(profile.postprocess(gen.text)) if problem is OutputProblem.TRUNCATED else 0
        scores.append((0 if accepted else _SEVERITY[problem], longer, -len(replies)))
        step += 1
        if accepted or step >= len(ladder):
            break
        if problem is OutputProblem.TRUNCATED and current.max_tokens > max_tokens:
            # Still cut off with the larger limit: a stricter sampling with half the room
            # cannot finish a page that did not fit in it.
            break
        raised = retry_max_tokens(max_tokens, guards)
        if (
            problem is OutputProblem.TRUNCATED
            and raised > current.max_tokens
            and raised >= max_tokens * guards.retry_max_tokens_gain
        ):
            current = Attempt(current.temperature, current.penalty, raised)
        else:
            current = Attempt(*ladder[step], max_tokens)
    return replies, scores.index(min(scores))
