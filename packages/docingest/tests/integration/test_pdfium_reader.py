import random

import pypdfium2 as pdfium
import pytest
from builders import (
    LONG,
    image_pdf,
    layout_pdf,
    paragraph,
    pdf,
    picture_pdf,
    pictures_pdf,
    text_pdf,
)

from docingest.adapters.pdf.pdfium import (
    SIGNALS_VERSION,
    PdfiumReader,
    _Glyph,
    _union_share,
    split_lines,
)
from docingest.domain.errors import DocumentOpenError
from docingest.domain.routing import RoutingPolicy, decide
from docingest.domain.text import HYPHEN_MARK, clean_text_layer

reader = PdfiumReader()
PAGE = [LONG] * 6  # a page of text: 576 characters, over the 400 of the "little text" rule


def probe(path, policy=None):
    doc = reader.open(path)
    signals, raw = doc.page(0).signals()
    return decide(signals, policy or RoutingPolicy()), raw


def test_born_digital_page_uses_text_layer(tmp_path):
    p, raw = probe(text_pdf(tmp_path / "digital.pdf", LONG))
    assert not p.needs_ocr, p.reasons
    assert "Attention" in raw


def test_scanned_page_routes_to_ocr(tmp_path):
    p, _ = probe(image_pdf(tmp_path / "scan.pdf"))
    assert p.needs_ocr and p.image_coverage > 0.9 and p.n_chars == 0


def test_scan_wrapped_in_form_xobject_with_text_footer_routes_to_ocr(tmp_path):
    """Image nested two Form XObjects deep and scaled by the outer CTM, plus a digital footer."""
    footer = b"BT /F1 9 Tf 72 20 Td (" + b"Digitized by the library, page footer. " * 3 + b") Tj ET"
    content = b"q 612 0 0 792 0 0 cm /Fm1 Do Q " + footer
    extra = [
        b"<< /Type /XObject /Subtype /Form /BBox [0 0 1 1] "
        b"/Resources << /XObject << /Fm2 7 0 R >> >> /Length 8 >>\nstream\n/Fm2 Do\nendstream",
        b"<< /Type /XObject /Subtype /Form /BBox [0 0 1 1] "
        b"/Resources << /XObject << /Im1 8 0 R >> >> /Length 8 >>\nstream\n/Im1 Do\nendstream",
        b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceGray "
        b"/BitsPerComponent 8 /Length 4 >>\nstream\n\xff\xff\xff\xff\nendstream",
    ]
    path = pdf(tmp_path / "form_scan.pdf", content, b"/XObject << /Fm1 6 0 R >>", extra)
    p, _ = probe(path)
    assert 50 <= p.n_chars < 400
    assert p.image_coverage > 0.95, p
    assert p.needs_ocr


@pytest.mark.parametrize(
    ("page_box", "cm"),
    [
        (b"/MediaBox [0 0 400 1000] /Rotate 90", (400, 1000, 0, 0)),
        (b"/MediaBox [0 792 612 1584]", (612, 792, 0, 792)),
        (b"/MediaBox [0 0 1224 792] /CropBox [612 0 1224 792]", (612, 792, 612, 0)),
        (b"/MediaBox [-306 -396 306 396]", (612, 792, -306, -396)),
    ],
)
def test_full_page_scan_detected_on_rotated_or_offset_boxes(tmp_path, page_box, cm):
    w, h, x, y = cm
    stamp = b"Scanned by the archive service. " * 3
    content = (
        b"q %d 0 0 %d %d %d cm /Im1 Do Q " % (w, h, x, y)
        + b"BT /F1 9 Tf %d %d Td (" % (x + 20, y + 20)
        + stamp
        + b") Tj ET"
    )
    img = (
        b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceGray "
        b"/BitsPerComponent 8 /Length 4 >>\nstream\n\xff\xff\xff\xff\nendstream"
    )
    path = pdf(tmp_path / "boxed.pdf", content, b"/XObject << /Im1 6 0 R >>", [img], page_box)
    p, _ = probe(path)
    assert 50 <= p.n_chars < 400, p  # routed by coverage, not by an empty text layer
    assert p.image_coverage > 0.95, p
    assert p.needs_ocr


def test_scan_with_an_invisible_text_layer_routes_to_ocr(tmp_path):
    # A full-page image with a long, invisible text layer: the old OCR of an old scan.
    # Old: a layer over 400 characters passed every rule and was trusted.
    path = picture_pdf(tmp_path / "ocr_scan.pdf", *PAGE, invisible=True)
    p, raw = probe(path)
    assert p.n_chars > 400 and "Attention" in raw  # the layer is there and is long
    assert p.image_coverage == 1.0
    assert p.needs_ocr and [r.split(":")[0] for r in p.reasons] == [
        "scanned page with a text layer"
    ]
    assert "images cover 100% of the page" in p.reasons[0]


def test_the_rule_can_be_turned_off(tmp_path):
    path = picture_pdf(tmp_path / "ocr_scan.pdf", *PAGE, invisible=True)
    p, raw = probe(path, RoutingPolicy(scan_layer_to_ocr=False))
    assert not p.needs_ocr and p.reasons == []
    assert "Attention" in raw


def test_a_born_digital_page_keeps_its_text_layer(tmp_path):
    p, _ = probe(text_pdf(tmp_path / "digital.pdf", *PAGE))
    assert p.n_images == 0 and not p.needs_ocr


def test_a_page_with_a_figure_keeps_its_text_layer(tmp_path):
    # A 250 x 250 pt figure on a Letter page covers 13% of it.
    path = picture_pdf(tmp_path / "figure.pdf", *PAGE, box=(300, 300, 250, 250))
    p, _ = probe(path)
    assert p.n_images == 1 and 0.1 < p.image_coverage < 0.2
    assert not p.needs_ocr, p.reasons


def test_a_large_figure_below_the_cover_threshold_keeps_its_text_layer(tmp_path):
    # 612 x 500 pt: 63% of the page, over the 0.6 of the "little text" rule, but with the
    # text of a full page. Not a scan.
    path = picture_pdf(tmp_path / "plate.pdf", *PAGE, box=(0, 0, 612, 500))
    p, _ = probe(path)
    assert 0.6 < p.image_coverage < 0.8
    assert not p.needs_ocr, p.reasons


def test_stacked_full_page_images_are_one_scan_not_two(tmp_path):
    # Two layers of one scan (an image and its mask or a second copy): the page is covered
    # once, however many images do it.
    page = [(0, 0, 612, 792)] * 2
    p, _ = probe(pictures_pdf(tmp_path / "stack.pdf", page, *PAGE, invisible=True))
    assert p.n_images == 2 and p.image_coverage == 1.0
    assert p.needs_ocr


def test_overlapping_tiles_under_a_page_of_text_are_not_a_scan(tmp_path):
    # Old: 60 copies of a 250 x 250 pt picture in one place added up (60 x 13%) to a covered
    # page, and a born-digital page with a figure built of many pieces went to OCR.
    tiles = [(300, 300, 250, 250)] * 60
    p, _ = probe(pictures_pdf(tmp_path / "tiles.pdf", tiles, *PAGE))
    assert p.n_images == 60 and 0.1 < p.image_coverage < 0.2
    assert not p.needs_ocr, p.reasons


def test_many_small_images_that_do_not_overlap_add_up(tmp_path):
    # 10 x 10 grid of 61 x 79 pt pieces: a picture cut into tiles covers its area.
    tiles = [(61 * i, 79 * j, 61, 79) for i in range(10) for j in range(10)]
    p, _ = probe(pictures_pdf(tmp_path / "mosaic.pdf", tiles, *PAGE))
    assert p.n_images == 100 and 0.95 <= p.image_coverage <= 1.0
    assert p.needs_ocr and "scanned page" in p.reasons[-1]


def test_side_by_side_strips_that_tile_the_page_are_a_scan(tmp_path):
    strips = [(0, 0, 306, 792), (306, 0, 306, 792)]
    p, _ = probe(pictures_pdf(tmp_path / "strips.pdf", strips, *PAGE, invisible=True))
    assert p.n_images == 2 and p.image_coverage >= 0.99
    assert p.needs_ocr


def test_the_reader_fingerprint_names_the_signal_version():
    # The cache key of a PDF holds the fingerprint: a changed measurement invalidates PDFs.
    assert reader.fingerprint.endswith(f"signals {SIGNALS_VERSION}")


@pytest.mark.parametrize(
    ("rects", "share"),
    [
        ([], 0.0),
        ([(0.0, 0.0, 1.0, 1.0)], 1.0),
        ([(0.25, 0.25, 0.75, 0.75)], 0.25),
        ([(0.25, 0.25, 0.75, 0.75)] * 5, 0.25),  # on grid lines: no extra cell
        ([(0.0, 0.0, 0.5, 1.0), (0.5, 0.0, 1.0, 1.0)], 1.0),
        ([(0.0, 0.0, 1.0, 1.0), (0.2, 0.2, 0.4, 0.4)], 1.0),
        ([(0.0, 0.0, 0.333, 1.0), (0.333, 0.0, 0.666, 1.0), (0.666, 0.0, 1.0, 1.0)], 1.0),
    ],
)
def test_union_share(rects, share):
    assert _union_share(rects) == pytest.approx(share)


def test_thousands_of_specks_do_not_make_a_page():
    # A figure built of thousands of tiny images: 10,000 specks of 0.001 x 0.001 (0.01% of
    # the page together) that straddle grid lines. Rounding each rectangle outwards turned
    # them into a full page.
    specks = [(0.0049 + 0.0097 * (i % 100), 0.0049 + 0.0097 * (i // 100)) for i in range(10_000)]
    rects = [(x, y, x + 0.001, y + 0.001) for x, y in specks]
    assert _union_share(rects) < 0.05


def test_many_small_tiles_are_measured_close_to_their_union():
    # 2,719 tiles of 0.01 x 0.012 placed pseudo-randomly: the exact union is about 0.28.
    rng = random.Random(7)
    rects = []
    for _ in range(2719):
        x, y = rng.uniform(0, 0.99), rng.uniform(0, 0.988)
        rects.append((x, y, x + 0.01, y + 0.012))
    exact = _exact_union(rects)
    assert abs(_union_share(rects) - exact) < 0.02 < 0.8 - exact


def _exact_union(rects):
    """The union of the rectangles on a grid fine enough for the test (1000 x 1000)."""
    n = 1000
    rows = [0] * n
    for left, bottom, right, top in rects:
        x0, x1 = round(left * n), round(right * n)
        for y in range(round(bottom * n), round(top * n)):
            rows[y] |= ((1 << (x1 - x0)) - 1) << x0
    return sum(r.bit_count() for r in rows) / (n * n)


def test_real_pdfium_hyphenation_roundtrip(tmp_path):
    src = text_pdf(tmp_path / "hy.pdf", "The years 2019-", "2020 saw transduc-", "tion models.")
    raw = pdfium.PdfDocument(src)[0].get_textpage().get_text_bounded()
    out = clean_text_layer(raw)
    assert "\x02" not in out
    assert "2019-2020" in out
    assert "transduc-tion" in out  # no "transduction" elsewhere -> hyphen kept


def test_corrupt_pdf_is_a_document_open_error(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4\n1 0 obj\n<<")
    with pytest.raises(DocumentOpenError):
        reader.open(bad)


JOURNAL = "Journal of Synthetic Results"


def test_pdfium_reports_size_weight_and_position_of_each_line(tmp_path):
    doc = PdfiumReader().open(
        layout_pdf(
            tmp_path / "paper.pdf",
            [
                [
                    (72, 750, 9, False, f"{JOURNAL} 1"),
                    (72, 700, 20, True, "Filters and Noise"),
                    *paragraph(
                        "The transduction models described here were trained on long sequences.",
                        top=650,
                    ),
                ]
            ],
        )
    )
    lines = list(doc.page(0).layout().lines)
    title = next(ln for ln in lines if ln.text == "Filters and Noise")
    assert (title.size, title.bold, title.upright) == (20.0, True, True)
    body = next(ln for ln in lines if ln.text.startswith("The transduction"))
    assert (body.size, body.bold) == (10.0, False)
    header = next(ln for ln in lines if ln.text.startswith(JOURNAL))
    assert header.baseline < title.baseline < body.baseline  # counted from the top
    assert doc.page(0).layout().height == 792


def test_a_line_that_pdfium_hyphenates_ends_with_the_marker(tmp_path):
    runs = [(72, 700, 10, False, "the sequence transduc-"), (72, 688, 10, False, "tion works")]
    lines = PdfiumReader().open(layout_pdf(tmp_path / "h.pdf", [runs])).page(0).layout().lines
    assert [ln.text for ln in lines] == ["the sequence transduc\x02", "tion works"]


def test_rotated_pages_have_no_layout(tmp_path):
    path = layout_pdf(tmp_path / "r.pdf", [paragraph(LONG)])
    doc = pdfium.PdfDocument(path)
    doc[0].set_rotation(90)
    doc.save(path)
    assert not PdfiumReader().open(path).page(0).layout().lines


def test_a_font_set_at_size_one_and_scaled_by_the_text_matrix_has_its_real_size(tmp_path):
    content = b"BT /F1 1 Tf 14 0 0 14 72 700 Tm (Scaled heading words) Tj ET"
    line = PdfiumReader().open(pdf(tmp_path / "m.pdf", content)).page(0).layout().lines[0]
    assert round(line.size, 1) == 14.0


def test_rotated_text_is_not_upright(tmp_path):
    content = b"BT /F1 10 Tf 0 1 -1 0 40 300 Tm (arXiv margin stamp text) Tj ET"
    line = PdfiumReader().open(pdf(tmp_path / "v.pdf", content)).page(0).layout().lines[0]
    assert not line.upright


def test_a_horizontal_scale_does_not_inflate_the_font_size(tmp_path):
    content = b"BT /F1 10 Tf 200 Tz 72 700 Td (Wide words here) Tj ET"
    line = PdfiumReader().open(pdf(tmp_path / "z.pdf", content)).page(0).layout().lines[0]
    assert line.size == 10.0


def test_a_font_name_longer_than_the_buffer_still_tells_bold(tmp_path):
    name = b"ABCDEF+AVeryLongFontFamilyNameThatGoesOnForWellOverSixtyFourBytesInTotal-Bold"
    font = b"<< /Type /Font /Subtype /Type1 /BaseFont /" + name + b" >>"
    content = b"BT /F2 12 Tf 72 700 Td (Long name bold) Tj ET"
    path = pdf(tmp_path / "n.pdf", content, b"", [font])
    path.write_bytes(path.read_bytes().replace(b"/F1 4 0 R", b"/F1 4 0 R /F2 6 0 R"))
    assert PdfiumReader().open(path).page(0).layout().lines[0].bold


def glyph(x):
    return _Glyph(10.0, x, x + 5, 700.0, False, True)


def stream(text):
    return [
        (c, None if c.isspace() or c == HYPHEN_MARK else glyph(72 + 5 * k))
        for k, c in enumerate(text)
    ]


def test_a_marker_at_the_end_of_a_row_ends_the_line_and_keeps_the_marker():
    lines = split_lines(stream("the transduc\x02tion model"), 0.0, 792.0)
    assert [ln.text for ln in lines] == ["the transduc\x02", "tion model"]


def test_a_marker_followed_by_a_space_is_a_hyphen_in_the_middle_of_a_row():
    lines = split_lines(stream("set |a\x02 to |b\x02 con\x02tinues"), 0.0, 792.0)
    assert [ln.text for ln in lines] == ["set |a- to |b- con\x02", "tinues"]


def test_line_breaks_split_the_stream_and_empty_lines_are_dropped():
    lines = split_lines(stream("first\r\n \r\nsecond"), 0.0, 792.0)
    assert [ln.text for ln in lines] == ["first", "second"]
