"""Tiny, dependency-free document builders for tests."""

from __future__ import annotations

import re
import textwrap
from collections.abc import Sequence
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def one_line(output: str) -> str:
    """CLI output as one line of words: no color codes, no panel borders, no line breaks.

    typer renders a usage error in a rich panel that CI colors (it highlights the options in
    the message, which splits the text with escape codes) and wraps at the terminal width.
    Assert on ``one_line(result.output)`` for such messages."""
    return " ".join(_ANSI.sub("", output).replace("│", " ").split())


LONG = "Attention is all you need and the transformer architecture works well. " * 3


def pdf(
    path: Path,
    content: bytes,
    resources: bytes = b"",
    extra: list[bytes] | tuple[bytes, ...] = (),
    page_box: bytes = b"/MediaBox [0 0 612 792]",
    *,
    font_encoding: bytes = b"",
) -> Path:
    """Minimal PDF writer: one page, Helvetica as /F1, optional extra objects (6+).

    ``font_encoding`` is added to the font dictionary, e.g. ``/Encoding /WinAnsiEncoding``.
    """
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R " + page_box + b" "
        b"/Resources << /Font << /F1 4 0 R >> " + resources + b" >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica " + font_encoding + b" >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        *extra,
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    path.write_bytes(bytes(out))
    return path


def text_pdf(path: Path, *lines: str) -> Path:
    ops = b"BT /F1 11 Tf 72 700 Td 14 TL " + b" ".join(b"(%s) '" % line.encode() for line in lines)
    return pdf(path, ops + b" ET")


def symbols_pdf(path: Path, *lines: str) -> Path:
    """``text_pdf`` in WinAnsi, so a line may hold the math signs of Latin-1 (± × ÷)."""
    ops = b"BT /F1 11 Tf 72 700 Td 14 TL " + b" ".join(b"(%s) '" % _escape(ln) for ln in lines)
    return pdf(path, ops + b" ET", font_encoding=b"/Encoding /WinAnsiEncoding")


def image_pdf(path: Path) -> Path:
    img = Image.new("RGB", (1275, 1650), "white")
    font = ImageFont.load_default(size=28)  # 10 pt on a 150 dpi scan; the bitmap font is 5 pt
    ImageDraw.Draw(img).text(
        (100, 100), "Scanned words live only in pixels", font=font, fill="black"
    )
    img.save(path, "PDF", resolution=150)
    return path


def blank_pdf(path: Path, pages: int) -> Path:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument.new()
    for _ in range(pages):
        doc.new_page(612, 792)
    doc.save(path)
    return path


Run = tuple[float, float, float, bool, str]  # x, y (from the page bottom), font size, bold, text


def paragraph(
    text: str, *, x: float = 72, top: float = 700, size: float = 10, width: int = 62
) -> list[Run]:
    """The lines of one wrapped paragraph, one text run each, 1.2 line heights apart."""
    return [
        (x, top - i * 1.2 * size, size, False, line)
        for i, line in enumerate(textwrap.wrap(text, width))
    ]


def layout_pdf(path: Path, pages: Sequence[Sequence[Run]]) -> Path:
    """A multi-page PDF with every text run placed by hand (Helvetica, or bold)."""
    objs: list[bytes] = [b"", b""]  # catalog and page tree, filled in below
    kids = []
    fonts = b"/Font << /F1 3 0 R /F2 4 0 R >>"
    objs += [
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
    ]
    for runs in pages:
        ops = b"".join(
            b"BT /F%d %g Tf %g %g Td (%s) Tj ET\n" % (2 if bold else 1, size, x, y, _escape(text))
            for x, y, size, bold, text in runs
        )
        stream = len(objs) + 2
        kids.append(b"%d 0 R" % (len(objs) + 1))
        objs.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << "
            + fonts
            + b" >> /Contents %d 0 R >>" % stream
        )
        objs.append(b"<< /Length %d >>\nstream\n" % len(ops) + ops + b"endstream")
    objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = b"<< /Type /Pages /Kids [" + b" ".join(kids) + b"] /Count %d >>" % len(kids)
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    path.write_bytes(bytes(out))
    return path


def _escape(text: str) -> bytes:
    return (
        text.encode("latin-1").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
    )


def pictures_pdf(
    path: Path,
    boxes: list[tuple[int, int, int, int]],
    *lines: str,
    invisible: bool = False,
) -> Path:
    """One page of text lines and one image drawn over each of ``boxes`` (x, y, width, height
    in points, so boxes may overlap or tile the page).

    ``invisible`` draws the text in render mode 3, as an OCR program does over its scan: the
    page looks like the images and still has a text layer.
    """
    mode = b"3 Tr " if invisible else b""
    text = (
        b"BT /F1 11 Tf "
        + mode
        + b"72 700 Td 14 TL "
        + b" ".join(b"(%s) '" % s.encode() for s in lines)
        + b" ET"
    )
    image = (
        b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceGray "
        b"/BitsPerComponent 8 /Length 4 >>\nstream\n\xc0\xc0\xc0\xc0\nendstream"
    )
    draws = b" ".join(b"q %d 0 0 %d %d %d cm /Im1 Do Q" % (w, h, x, y) for x, y, w, h in boxes)
    return pdf(path, draws + b" " + text, b"/XObject << /Im1 6 0 R >>", [image])


def picture_pdf(
    path: Path, *lines: str, box: tuple[int, int, int, int] = (0, 0, 612, 792), invisible=False
) -> Path:
    """``pictures_pdf`` with one image: the full page by default, or a figure in a small ``box``."""
    return pictures_pdf(path, [box], *lines, invisible=invisible)
