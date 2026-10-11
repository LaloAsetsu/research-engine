import pypdfium2 as pdfium
import pytest
from builders import LONG, image_pdf, layout_pdf, paragraph, pdf, text_pdf

from docingest.adapters.pdf.pdfium import PdfiumReader, _Glyph, split_lines
from docingest.domain.errors import DocumentOpenError
from docingest.domain.routing import RoutingPolicy, decide
from docingest.domain.text import HYPHEN_MARK, clean_text_layer

reader = PdfiumReader()


def probe(path):
    doc = reader.open(path)
    signals, raw = doc.page(0).signals()
    return decide(signals, RoutingPolicy()), raw


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
