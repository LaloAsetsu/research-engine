"""Port: read PDF pages (measurements, embedded text, rasterization)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from PIL.Image import Image

from ..domain.layout import PageLayout
from ..domain.models import PageSignals


@runtime_checkable
class PdfPage(Protocol):
    def signals(self) -> tuple[PageSignals, str]:
        """Measurements for the OCR routing policy, plus the raw embedded text."""
        ...

    def layout(self) -> PageLayout:
        """The embedded text as lines with font size, weight and position.

        Called for pages that keep their text layer. An adapter that cannot tell returns
        ``PageLayout()`` (no lines) and the page keeps its flat ``signals()`` text.
        """
        ...

    def render(self, dpi: int) -> Image: ...


@runtime_checkable
class PdfDocument(Protocol):
    title: str | None

    def __len__(self) -> int: ...

    def page(self, index: int) -> PdfPage: ...

    def close(self) -> None: ...


@runtime_checkable
class PdfReader(Protocol):
    fingerprint: str

    def open(self, path: Path) -> PdfDocument:
        """Raise ``DocumentOpenError`` for corrupt / encrypted / truncated files."""
        ...
