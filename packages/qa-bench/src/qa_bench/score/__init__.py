"""The scorer: ``qset/1`` and ``qa-run/1`` files in, a ``qa-score/1`` file out.

Standard library only (plus :mod:`qa_bench.formats`): no numpy, no model call. See
:func:`qa_bench.score.score`.
"""

from qa_bench.score.bootstrap import ENGINES, NumpyPcg64, PythonRandom
from qa_bench.score.scorer import (
    NamedRun,
    Options,
    Scored,
    ScoreError,
    code_md5,
    score,
)

__all__ = [
    "ENGINES",
    "NamedRun",
    "NumpyPcg64",
    "Options",
    "PythonRandom",
    "ScoreError",
    "Scored",
    "code_md5",
    "score",
]
