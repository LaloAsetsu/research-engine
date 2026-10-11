"""Scans with an OCR text layer: the page goes to OCR, the layer stays as the fallback."""

from fakes import FakeOcr, FakePage, ScriptedOcr, digital, inked, make_service

from docingest.domain.layout import PageLayout, TextLine
from docingest.domain.models import PageMethod, PageSignals, SourceKind
from docingest.domain.routing import RoutingPolicy

LAYER = "The old program read this page with a few mistakes in it. " * 16  # over 400 chars
LOOP = "and then the model kept going " * 20


def scan_with_layer(text: str = LAYER, coverage: float = 0.99) -> FakePage:
    signals = PageSignals(
        n_chars=len("".join(text.split())), n_images=1, image_coverage=coverage, alpha_ratio=0.95
    )
    return FakePage(signals, text, inked())


def figure_page(text: str = LAYER) -> FakePage:
    return scan_with_layer(text, coverage=0.15)


def test_a_scan_with_a_text_layer_is_read_by_ocr(src):
    ocr = FakeOcr("# page read by the model")
    svc = make_service({"a.pdf": [scan_with_layer()]}, ocr=ocr)
    doc = svc.ingest(src("a.pdf"))
    page = doc.manifest.pages[0]
    assert page.method == PageMethod.VLM_OCR and ocr.calls == 1
    assert page.model == "fake/ocr" and page.warnings == []
    assert page.probe is not None and page.probe.needs_ocr
    assert page.probe.reasons[0].startswith("scanned page with a text layer: images cover 99%")
    text = svc.store.markdown(doc)
    assert "page read by the model" in text and "old program" not in text


def test_the_reason_reaches_the_log(src):
    lines: list[str] = []
    svc = make_service({"a.pdf": [scan_with_layer()]}, log=lines.append)
    svc.ingest(src("a.pdf"))
    assert any("OCR (scanned page with a text layer" in line for line in lines)


def test_born_digital_and_figure_pages_keep_their_text_layer(src):
    ocr = FakeOcr("# never asked")
    pages = [digital(LAYER), figure_page(), digital(LAYER)]
    svc = make_service({"a.pdf": pages}, ocr=ocr)
    doc = svc.ingest(src("a.pdf"))
    assert [p.method for p in doc.manifest.pages] == [PageMethod.TEXT_LAYER] * 3
    assert ocr.calls == 0 and doc.manifest.ocr_pages == 0


def test_with_the_rule_off_the_text_layer_is_used(src):
    ocr = FakeOcr("# never asked")
    policy = RoutingPolicy(scan_layer_to_ocr=False)
    svc = make_service({"a.pdf": [scan_with_layer()]}, ocr=ocr, policy=policy)
    doc = svc.ingest(src("a.pdf"))
    assert doc.manifest.pages[0].method == PageMethod.TEXT_LAYER and ocr.calls == 0
    assert "old program" in svc.store.markdown(doc)


def test_a_mixed_document_routes_each_page(src):
    ocr = FakeOcr("# read by the model")
    pages = [digital(LAYER), scan_with_layer(), figure_page(), scan_with_layer()]
    svc = make_service({"a.pdf": pages}, ocr=ocr)
    doc = svc.ingest(src("a.pdf"))
    assert [p.method for p in doc.manifest.pages] == [
        PageMethod.TEXT_LAYER,
        PageMethod.VLM_OCR,
        PageMethod.TEXT_LAYER,
        PageMethod.VLM_OCR,
    ]
    assert ocr.calls == 2 and doc.manifest.ocr_pages == 2


def test_the_text_layer_is_the_fallback_when_the_ocr_is_rejected(src):
    good = "Sentence one says something. " * 30
    svc = make_service({"a.pdf": [scan_with_layer(good)]}, ocr=ScriptedOcr(LOOP))
    doc = svc.ingest(src("a.pdf"))
    page = doc.manifest.pages[0]
    assert page.method == PageMethod.TEXT_LAYER
    assert page.warnings == ["OCR output rejected (repeating text); the text layer was used"]
    assert page.probe is not None and page.probe.reasons[0].startswith("scanned page")
    assert "Sentence one says something." in svc.store.markdown(doc)


def test_the_rule_is_part_of_the_pdf_cache_key():
    # Old results of these pages (the text layer) must not be served once the rule is on.
    on = make_service()
    off = make_service(policy=RoutingPolicy(scan_layer_to_ocr=False))
    moved = make_service(policy=RoutingPolicy(scan_image_cover=0.9))
    keys = {s.config_hash(SourceKind.PDF) for s in (on, off, moved)}
    assert len(keys) == 3
    assert on.config_hash(SourceKind.IMAGE) == off.config_hash(SourceKind.IMAGE)


def with_layout(n: int, coverage: float) -> FakePage:
    """A page with the lines of a running header, a heading and a paragraph; the signals say
    how much of the page the images cover."""

    def line(text, y, size=10.0, bold=False, x1=500.0):
        return TextLine(text, size, bold, 72.0, x1, y)

    lines = (
        line("Running title", 40, 9),
        line("Results of the study", 120, 16, True, 250),
        line("The first line of a paragraph that goes on with " * 3, 160),
        line("a second line that finishes it.", 172, x1=250),
        line(str(n), 760, 9, x1=310),
    )
    flat = "\n".join(ln.text for ln in lines)
    signals = PageSignals(
        n_chars=len("".join(flat.split())), n_images=1, image_coverage=coverage, alpha_ratio=0.9
    )
    return FakePage(signals, flat, inked(), layout_=PageLayout(lines, 612.0, 792.0))


def test_a_page_read_by_ocr_is_not_structured_and_the_others_are(src):
    # Structure is for the text layers that routing trusts; a scan with an OCR layer is read
    # by the model and its layer does not come back.
    pages = [with_layout(1, 0.0), with_layout(2, 0.99), with_layout(3, 0.0)]
    svc = make_service({"a.pdf": pages}, ocr=FakeOcr("# read by the model"))
    doc = svc.ingest(src("a.pdf"))
    first, second, third = doc.manifest.pages
    assert second.method == PageMethod.VLM_OCR and second.removed_lines == []
    assert first.removed_lines and third.removed_lines  # the header and the page number
    markdown = svc.store.markdown(doc)
    assert "# read by the model" in markdown and "Running title" not in markdown


def test_a_rejected_ocr_page_falls_back_to_the_flat_text_layer(src):
    # As for any page whose layer stands in for rejected OCR: the layer's text, not its
    # structure, with the warning.
    pages = [with_layout(1, 0.0), with_layout(2, 0.99), with_layout(3, 0.0)]
    svc = make_service({"a.pdf": pages}, ocr=ScriptedOcr(LOOP))
    doc = svc.ingest(src("a.pdf"))
    first, second, third = doc.manifest.pages
    assert second.method == PageMethod.TEXT_LAYER
    assert second.warnings == ["OCR output rejected (repeating text); the text layer was used"]
    assert second.removed_lines == []
    flat = svc.store.markdown(doc).split("<!-- page 2")[1].split("<!-- page 3")[0]
    assert "Running title" in flat and "Results of the study" in flat and "# Results" not in flat
    assert first.removed_lines and third.removed_lines
