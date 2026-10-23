"""Ports of the question sets: the catalog, the files, and the reader and writer of the formats."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from ..domain.errors import FieldError
from ..domain.sets import AppliedReviews, BuiltSet, ImportShape, SetFile, SetRef, SetVersion


class SetCatalog(Protocol):
    """Who owns each set version, its status and its md5. The files hold the content."""

    def all(self) -> list[SetVersion]:
        """Every version, ordered by name and version number."""
        ...

    def get(self, ref: SetRef) -> SetVersion | None: ...

    def versions_of(self, name: str) -> list[SetVersion]: ...

    def add(self, version: SetVersion) -> None:
        """Store a new version. ``ConflictError`` when the name and version exist."""
        ...

    def delete(self, ref: SetRef) -> None:
        """Forget a version (only to undo an ``add`` whose file could not be written)."""
        ...

    def save(self, version: SetVersion, *, expected_md5: str) -> None:
        """Replace the entry of ``version.ref`` if it still has ``expected_md5``.

        ``ConflictError`` when it changed meanwhile, ``NotFoundError`` when it is gone.
        """
        ...


class SetFiles(Protocol):
    """The files of the sets under the private data folder."""

    def read(self, ref: SetRef) -> bytes | None: ...

    def write(self, ref: SetRef, data: bytes) -> None:
        """Replace the file of a version all at once: a reader never sees half of it."""
        ...

    def keep_copy(self, ref: SetRef, label: str, data: bytes) -> None:
        """Keep ``data`` beside the version under ``label`` (the draft before it was frozen)."""
        ...

    def read_reviews(self, ref: SetRef) -> bytes | None: ...

    def write_reviews(self, ref: SetRef, data: bytes) -> None: ...

    def append_reviews(self, ref: SetRef, data: bytes) -> None: ...


class SetFormats(Protocol):
    """Reads, checks and writes ``qset/1`` and ``reviews/1`` files (the qa-bench formats).

    Methods that refuse content raise ``InvalidInputError`` with the reasons and their places.
    """

    def peek(self, data: bytes) -> SetFile:
        """A set file this service wrote, read without checking its schema again."""
        ...

    def read(self, data: bytes) -> SetFile:
        """An uploaded ``qset/1`` file (plain or gzip), checked line by line and as a whole."""
        ...

    def check_text(self, text: str) -> tuple[FieldError, ...]:
        """Every problem of a file's text, for a dry run. ``line`` leads each path."""
        ...

    def check_item(self, item: Mapping[str, Any]) -> tuple[FieldError, ...]:
        """The problems of one item against the ``qset/1`` schema (JSON pointer paths)."""
        ...

    def build(self, header: Mapping[str, Any], items: Sequence[Mapping[str, Any]]) -> BuiltSet:
        """Check every line and the rules between them, and give the bytes to store."""
        ...

    def shapes(self) -> tuple[ImportShape, ...]:
        """The older question-set files that can be converted."""
        ...

    def convert(
        self,
        shape: str,
        data: bytes,
        *,
        set_name: str,
        created: str,
        corpus: str | None,
        source_file: str | None,
    ) -> BuiltSet:
        """A source file of an older shape as a valid set."""
        ...

    def peek_reviews(self, data: bytes) -> list[dict[str, Any]]:
        """The decision lines of a reviews file this service wrote (not the header)."""
        ...

    def review_header(self, set_name: str, created: str) -> bytes: ...

    def review_line(self, line: Mapping[str, Any]) -> bytes:
        """The line as it is appended to the file, ending in a line feed."""
        ...

    def check_review(self, line: Mapping[str, Any]) -> tuple[FieldError, ...]: ...

    def apply_reviews(
        self, items: Sequence[Mapping[str, Any]], lines: Sequence[Mapping[str, Any]], set_md5: str
    ) -> AppliedReviews:
        """The items after the latest human decision of each (``reviews/1``)."""
        ...
