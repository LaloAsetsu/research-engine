"""Port: read access to the converted papers."""

from __future__ import annotations

from typing import Protocol

from ..domain.documents import DocumentDetail, DocumentList, DocumentPage


class DocumentReader(Protocol):
    """Read access to the converted papers. Blocking calls: the HTTP layer runs them in a
    thread."""

    def search(self, query: str, limit: int) -> DocumentList:
        """Papers whose title contains ``query`` or whose id starts with it (all when empty)."""
        ...

    def get(self, doc_id: str) -> DocumentDetail:
        """``doc_id`` is a full id or a prefix of at least 16 hex characters.
        ``NotFoundError`` or ``ConflictError`` (two papers share the prefix)."""
        ...

    def page(self, doc_id: str, page: int) -> DocumentPage:
        """The Markdown of one page (one section of a LaTeX source). ``NotFoundError``."""
        ...
