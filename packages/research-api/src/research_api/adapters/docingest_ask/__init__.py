"""The ask path on docingest: the answer engine, the paper reader and their wiring."""

from __future__ import annotations

from pathlib import Path

from docingest.bootstrap import Container
from docingest.config import load_config

from .documents import DocingestDocumentReader
from .engine import DocingestAskEngine
from .offline import OfflineAskEngine, OfflineDocumentReader


def open_docingest(config: Path) -> tuple[DocingestAskEngine, DocingestDocumentReader]:
    """The engine and the reader of the corpus that docingest's configuration file names.

    Nothing is opened yet: the session builds the index and the models on its first use. The
    configuration is read once; a change needs a restart (as it does for ``docingest serve``).
    """
    container = Container(load_config(config), log=lambda _message: None)
    return (
        DocingestAskEngine(
            lambda: container.ask_session, read_answer_model=lambda: container.answer_model
        ),
        DocingestDocumentReader(lambda: container.adapter("store")),
    )


__all__ = [
    "DocingestAskEngine",
    "DocingestDocumentReader",
    "OfflineAskEngine",
    "OfflineDocumentReader",
    "open_docingest",
]
