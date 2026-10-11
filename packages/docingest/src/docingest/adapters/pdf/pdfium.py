"""PdfReader adapter backed by pypdfium2 (PDFium)."""

from __future__ import annotations

import ctypes
import math
import statistics
from collections import Counter
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path
from typing import NamedTuple

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL.Image import Image

from ...domain.errors import DocumentOpenError
from ...domain.layout import PageLayout, TextLine, size_bucket
from ...domain.models import PageSignals
from ...domain.text import HYPHEN_MARK, alpha_ratio, garbage_ratio

BOLD_WEIGHT = 600  # pdfium reports 400 for regular and 700 for bold, with some spread
FORCE_BOLD = 0x40000  # ForceBold bit of the font descriptor flags
_BOLD_NAMES = (b"bold", b"black", b"heavy")
BOLD_SHARE = 0.8  # share of a line's characters that must be bold for the line to be bold


def _page_space_bounds(obj) -> tuple[float, float, float, float]:
    """Bounds of a page object in page space, including enclosing Form XObjects.

    For objects inside a form, pdfium applies the form's /Matrix but not the CTM
    in effect where the form is drawn, which lives on the container object.
    """
    left, bottom, right, top = obj.get_bounds()
    pts = [(left, bottom), (left, top), (right, bottom), (right, top)]
    container = obj.container
    while container is not None:
        m = container.get_matrix()
        pts = [m.on_point(x, y) for x, y in pts]
        container = container.container
    xs, ys = [x for x, _ in pts], [y for _, y in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _is_bold(textpage, i: int) -> bool:
    """Bold by weight; the 14 standard fonts report weight 0, so they are judged by name."""
    weight = pdfium_c.FPDFText_GetFontWeight(textpage, i)
    if weight > 0:
        return weight >= BOLD_WEIGHT
    flags = ctypes.c_int()
    name = (ctypes.c_char * 64)()
    needed = pdfium_c.FPDFText_GetFontInfo(textpage, i, name, len(name), ctypes.byref(flags))
    if needed > len(name):  # a longer name is not written at all: ask again with room
        name = (ctypes.c_char * needed)()
        pdfium_c.FPDFText_GetFontInfo(textpage, i, name, len(name), ctypes.byref(flags))
    return any(w in name.value.lower() for w in _BOLD_NAMES) or bool(flags.value & FORCE_BOLD)


class _Glyph(NamedTuple):
    size: float  # font size times the scale of the text matrix, in points
    left: float
    right: float
    bottom: float
    bold: bool
    upright: bool


def _line(text: str, glyphs: list[_Glyph], box_l: float, box_t: float) -> TextLine | None:
    """One TextLine from the characters pdfium put on a line (None when it has no ink)."""
    if not glyphs or not text.strip():
        return None
    common = Counter(size_bucket(g.size) for g in glyphs).most_common(1)[0][0]
    return TextLine(
        text=text.strip(),
        size=statistics.fmean(g.size for g in glyphs if size_bucket(g.size) == common),
        bold=sum(g.bold for g in glyphs) >= BOLD_SHARE * len(glyphs),
        x0=min(g.left for g in glyphs) - box_l,
        x1=max(g.right for g in glyphs) - box_l,
        baseline=box_t - statistics.median(g.bottom for g in glyphs),
        upright=sum(g.upright for g in glyphs) > len(glyphs) / 2,
    )


def split_lines(
    items: Sequence[tuple[str, _Glyph | None]], box_l: float, box_t: float
) -> list[TextLine]:
    """The lines of a character stream: ``(character, glyph or None for a space)`` in order.

    A new line starts at each line break pdfium generated, and after a U+0002 that ends the
    row (the word goes on in the next line). A U+0002 that is followed by a space is a hyphen
    glyph in the middle of a row: it is a ``-``, as the flat text reads it, and starts nothing.
    """
    lines: list[TextLine] = []
    chars: list[str] = []
    glyphs: list[_Glyph] = []

    def flush() -> None:
        if (line := _line("".join(chars), glyphs, box_l, box_t)) is not None:
            lines.append(line)
        chars.clear()
        glyphs.clear()

    for k, (ch, glyph) in enumerate(items):
        if ch in ("\r", "\n"):
            flush()
            continue
        mid_row = ch == HYPHEN_MARK and k + 1 < len(items) and items[k + 1][0] in (" ", "\t")
        chars.append("-" if mid_row else ch)
        if glyph is not None:
            glyphs.append(glyph)
        if ch == HYPHEN_MARK and not mid_row:
            flush()
    flush()
    return lines


class PdfiumPage:
    def __init__(self, page: pdfium.PdfPage):
        self._page = page
        self._text = None  # the text page, loaded once for signals() and layout()

    def _textpage(self):
        if self._text is None:
            self._text = self._page.get_textpage()
        return self._text

    def signals(self) -> tuple[PageSignals, str]:
        text = self._textpage().get_text_bounded()
        # Visible page box in unrotated user space (same space as object bounds); it need
        # not start at the origin, and /Rotate must not swap its axes.
        box_l, box_b, box_r, box_t = self._page.get_bbox()
        page_area = max((box_r - box_l) * (box_t - box_b), 1.0)
        image_area, n_images = 0.0, 0
        for obj in self._page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE]):
            left, bottom, right, top = _page_space_bounds(obj)
            left, bottom = max(left, box_l), max(bottom, box_b)
            right, top = min(right, box_r), min(top, box_t)
            image_area += max(right - left, 0) * max(top - bottom, 0)
            n_images += 1
        signals = PageSignals(
            n_chars=len("".join(text.split())),
            n_images=n_images,
            image_coverage=round(min(image_area / page_area, 1.0), 3),
            garbage_ratio=round(garbage_ratio(text), 3),
            alpha_ratio=round(alpha_ratio(text), 3),
        )
        return signals, text

    def layout(self) -> PageLayout:
        """Lines with font size, weight and position, from the character data of pdfium."""
        if self._page.get_rotation() % 360:  # axes swapped: keep the flat text
            return PageLayout()
        textpage = self._textpage()
        box_l, box_b, box_r, box_t = self._page.get_bbox()
        matrix = pdfium_c.FS_MATRIX()
        items: list[tuple[str, _Glyph | None]] = []
        count = textpage.count_chars()
        for i in range(count):
            code = pdfium_c.FPDFText_GetUnicode(textpage, i)
            ch = chr(code) if 0 < code <= 0x10FFFF and not 0xD800 <= code <= 0xDFFF else ""
            left, bottom, right, top = textpage.get_charbox(i)
            if ch not in ("\r", "\n") and not (
                box_l - 1 <= (left + right) / 2 <= box_r + 1
                and box_b - 1 <= (bottom + top) / 2 <= box_t + 1
            ):
                continue  # pdfium's bounded text leaves these out too
            glyph = None
            if not ch.isspace() and ch != HYPHEN_MARK:
                size = pdfium_c.FPDFText_GetFontSize(textpage, i)
                upright = True
                if pdfium_c.FPDFText_GetMatrix(textpage, i, matrix):
                    # the vertical scale: a horizontal scale (Tz, a condensed face) is not size
                    size *= math.hypot(matrix.c, matrix.d)
                    upright = abs(matrix.b) <= abs(matrix.a) and abs(matrix.c) <= abs(matrix.d)
                glyph = _Glyph(size, left, right, bottom, _is_bold(textpage, i), upright)
            items.append((ch, glyph))
        lines = split_lines(items, box_l, box_t)
        return PageLayout(tuple(lines), box_r - box_l, box_t - box_b)

    def render(self, dpi: int) -> Image:
        bitmap = self._page.render(scale=dpi / 72)  # pyright: ignore[reportArgumentType]  # pypdfium2 accepts float scale
        return bitmap.to_pil()


class PdfiumDocument:
    def __init__(self, pdf: pdfium.PdfDocument):
        self._pdf = pdf
        self.title = (pdf.get_metadata_dict().get("Title") or "").strip() or None

    def __len__(self) -> int:
        return len(self._pdf)

    def page(self, index: int) -> PdfiumPage:
        return PdfiumPage(self._pdf[index])

    def close(self) -> None:
        self._pdf.close()


class PdfiumReader:
    fingerprint = f"pypdfium2 {version('pypdfium2')}"

    def open(self, path: Path) -> PdfiumDocument:
        try:
            return PdfiumDocument(pdfium.PdfDocument(path))
        except pdfium.PdfiumError as e:
            raise DocumentOpenError(f"cannot open PDF {path.name}: {e}") from e
