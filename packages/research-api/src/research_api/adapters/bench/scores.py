"""``Scorer`` over qa-bench: runs and a set in, a ``qa-score/1`` file out."""

from __future__ import annotations

from collections.abc import Sequence

from qa_bench.formats import Loaded, load_bytes
from qa_bench.formats.files import FormatFile
from qa_bench.formats.jsonl import loads
from qa_bench.score import ENGINES, NamedRun, Options, ScoreError, score

from ...domain.errors import CorruptStoreError, FieldError, InvalidInputError
from ...domain.jobs import ScoreOptions
from ...ports.jobs import ScoreData, ScoredFile

SHOWN_PROBLEMS = 5


def _file(loaded: Loaded, what: str) -> FormatFile:
    if loaded.file is None:
        shown = tuple(
            FieldError(p.path or "/", f"line {p.line}: {p.message}")
            for p in loaded.problems[:SHOWN_PROBLEMS]
        )
        raise InvalidInputError(f"{what} is not a valid file of its format.", shown)
    return loaded.file


class QaBenchScorer:
    def score(
        self,
        set_data: bytes,
        runs: Sequence[tuple[str, bytes]],
        options: ScoreOptions,
        created: str,
    ) -> ScoredFile:
        if options.engine not in ENGINES:
            known = ", ".join(sorted(ENGINES))
            raise InvalidInputError(
                "The request is not valid.",
                (FieldError("/engine", f"The bootstrap engine is one of {known}."),),
            )
        qset = _file(load_bytes(set_data), "The question set")
        named = [NamedRun(name, _file(load_bytes(data), f"The run {name}")) for name, data in runs]
        try:
            scored = score(
                qset,
                named,
                created=created,
                options=Options(
                    pairs=options.pairs,
                    noise=options.noise,
                    group_by=options.group_by,
                    groups=options.groups,
                    engine=options.engine,
                    number_words=options.number_words,
                ),
            )
        except ScoreError as error:
            raise InvalidInputError(str(error)) from None
        return ScoredFile(
            scored.file.to_bytes(),
            dict(scored.file.header),
            len(scored.file.records),
            scored.warnings,
        )

    def read(self, data: bytes) -> ScoreData:
        """A score file this service wrote, read without checking every line again."""
        try:
            lines = [loads(line) for line in data.decode("utf-8").split("\n") if line]
        except (ValueError, UnicodeDecodeError):
            raise CorruptStoreError("a score file this service wrote no longer reads") from None
        if not lines or not isinstance(lines[0], dict) or lines[0].get("format") != "qa-score/1":
            raise CorruptStoreError("a score file does not start with a qa-score/1 header")
        return ScoreData(lines[0], tuple(r for r in lines[1:] if isinstance(r, dict)))
