"""Use cases: find a cited paper and read it at a page."""

from __future__ import annotations

from ..domain.documents import (
    DocumentDetail,
    DocumentList,
    DocumentPage,
    is_doc_id,
)
from ..domain.errors import NotFoundError
from ..ports.documents import DocumentReader

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_QUERY = 200


class ListDocuments:
    def __init__(self, *, reader: DocumentReader) -> None:
        self._reader = reader

    def __call__(self, query: str = "", limit: int = DEFAULT_LIMIT) -> DocumentList:
        limit = min(max(limit, 1), MAX_LIMIT)
        return self._reader.search(query.strip()[:MAX_QUERY], limit)


class GetDocument:
    def __init__(self, *, reader: DocumentReader) -> None:
        self._reader = reader

    def __call__(self, doc_id: str) -> DocumentDetail:
        if not is_doc_id(doc_id):
            raise NotFoundError("No such document.")
        return self._reader.get(doc_id)


class ReadPage:
    def __init__(self, *, reader: DocumentReader) -> None:
        self._reader = reader

    def __call__(self, doc_id: str, page: int) -> DocumentPage:
        if not is_doc_id(doc_id) or page < 1:
            raise NotFoundError("No such page.")
        return self._reader.page(doc_id, page)


class DocumentServices:
    """The paper-reading use cases together, the way the container hands them out."""

    def __init__(self, *, reader: DocumentReader) -> None:
        self.search = ListDocuments(reader=reader)
        self.get = GetDocument(reader=reader)
        self.page = ReadPage(reader=reader)
