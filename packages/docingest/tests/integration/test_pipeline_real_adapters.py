"""Real detector / pdfium / pillow / filesystem adapters wired by the container; fake OCR."""

from builders import LONG, blank_pdf, image_pdf, pdf, text_pdf
from fakes import FakeOcr
from PIL import Image

from docingest.application.ingest import IngestOptions
from docingest.bootstrap import Container
from docingest.domain.models import PageMethod


def container(cfg):
    return Container(cfg, log=lambda _: None, overrides={"ocr": FakeOcr("# OCR page")})


def test_digital_and_scanned_pdfs(cfg, tmp_path):
    svc = container(cfg).ingest
    digital = svc.ingest(text_pdf(tmp_path / "d.pdf", LONG))
    assert digital.manifest.pages[0].method == PageMethod.TEXT_LAYER
    scan = svc.ingest(image_pdf(tmp_path / "s.pdf"))
    assert scan.manifest.pages[0].method == PageMethod.VLM_OCR
    assert "# OCR page" in svc.store.markdown(scan)


def test_image_and_markdown_inputs(cfg, tmp_path):
    svc = container(cfg).ingest
    png = tmp_path / "page.png"
    Image.new("RGB", (40, 40), "white").save(png)
    assert svc.ingest(png).manifest.pages[0].method == PageMethod.VLM_OCR
    md = tmp_path / "notes.md"
    md.write_text("# Notes\n" + LONG)
    doc = svc.ingest(md)
    assert doc.manifest.pages[0].method == PageMethod.PASSTHROUGH


def test_partial_run_never_replaces_complete_result(cfg, tmp_path):
    cfg.routing.min_chars = 0
    cfg.routing.min_alpha_ratio = 0
    svc = container(cfg).ingest
    src = blank_pdf(tmp_path / "three.pdf", 3)
    full = svc.ingest(src)
    part = svc.ingest(src, IngestOptions(max_pages=1, force=True))
    assert full.canonical and not part.canonical and "_variants" in part.location
    assert svc.ingest(src, IngestOptions(max_pages=2)).location == full.location


def test_blank_and_page_number_pdfs_never_reach_ocr(cfg, tmp_path):
    # Real pdfium rendering and measurement: an untouched page, and one with only "42" at
    # the foot. Old: both went to OCR, which wrote a page of text for each.
    ocr = FakeOcr("# invented page")
    svc = Container(cfg, log=lambda _: None, overrides={"ocr": ocr}).ingest
    empty = svc.ingest(blank_pdf(tmp_path / "blank.pdf", 2))
    numbered = svc.ingest(pdf(tmp_path / "number.pdf", b"BT /F1 11 Tf 300 40 Td (42) Tj ET"))
    assert [p.method for p in empty.manifest.pages] == [PageMethod.BLANK] * 2
    assert [p.method for p in numbered.manifest.pages] == [PageMethod.BLANK]
    assert ocr.calls == 0 and "invented" not in svc.store.markdown(numbered)
    assert numbered.manifest.pages[0].probe.n_chars == 2
