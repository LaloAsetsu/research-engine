"""Port: convert a whole document (office, LaTeX, text) into Markdown segments."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..domain.models import PageMethod, SourceMetadata


@dataclass(frozen=True)
class Segment:
    """One logical unit of a converted document (a section, a slide, the whole file)."""

    text: str
    title: str | None = None
    # Only for a segment that was made differently from the rest of its conversion (a LaTeX
    # section that fell back to plain text): its page record takes this method instead of
    # the conversion's, and these notes as its warnings.
    method: PageMethod | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class Conversion:
    segments: list[Segment]
    method: PageMethod
    engine: str  # e.g. "pandoc 3.8", "docling 2.130"
    title: str | None = None
    metadata: SourceMetadata | None = None
    warnings: list[str] = field(default_factory=list)
    # True when a fallback ran because of the environment (timeout, crash, missing tool),
    # not because the input is unconvertible: the result must not be cached as canonical.
    degraded: bool = False
    artifacts: list[dict[str, object]] = field(default_factory=list)
    raw_output: object | None = None


@runtime_checkable
class DocumentConverter(Protocol):
    fingerprint: str

    def convert(self, path: Path) -> Conversion:
        """Raise ``ConversionError`` when the document cannot be converted."""
        ...


@runtime_checkable
class AsyncDocumentConverter(Protocol):
    fingerprint: str

    async def convert_async(self, path: Path) -> Conversion:
        """Convert without blocking the caller's event loop."""
        ...
