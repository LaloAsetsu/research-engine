"""What the ingest does with OCR output it cannot trust: the text layer, or an empty page."""

import pytest
from fakes import FakeImages, FakePage, ScriptedOcr, digital, make_service, scanned

from docingest.application.ingest import IngestOptions
from docingest.domain.models import DocumentManifest, PageMethod, PageSignals, SourceKind
from docingest.domain.ocr_guards import OcrGuards


def sentences(n: int, tag: str = "") -> str:
    return " ".join(f"Sentence {tag}{i} says something about topic {i * 7}." for i in range(n))


LAYER = "Attention is all you need. " * 10
GOOD = sentences(12, "q")  # a page of text that repeats nothing
LOOP = "and then the model kept going " * 20


def page_with_layer(text: str = LAYER, **signals) -> FakePage:
    """A page that went to OCR for its low alphabetic ratio but does have a text layer."""
    layer = FakePage(
        PageSignals(n_chars=len("".join(text.split())), alpha_ratio=0.3, **signals), text
    )
    layer.image = scanned().image
    return layer


def ingest(pages, src, ocr, **kw):
    svc = make_service({"a.pdf": pages}, ocr=ocr, **kw)
    return svc, svc.ingest(src("a.pdf"))


# ------------------------------------------------------------------------- a loop
def test_a_runaway_page_falls_back_to_its_text_layer(src):
    svc, doc = ingest([page_with_layer()], src, ScriptedOcr(LOOP))
    page = doc.manifest.pages[0]
    assert page.method == PageMethod.TEXT_LAYER
    assert page.warnings == ["OCR output rejected (repeating text); the text layer was used"]
    assert page.n_chars == len(LAYER.strip()) and page.probe is not None and page.probe.needs_ocr
    assert "Attention is all you need." in svc.store.markdown(
        doc
    ) and "kept going" not in svc.store.markdown(doc)
    assert doc.manifest.ocr_pages == 0 and doc.manifest.ocr_model is None


def test_a_runaway_scan_without_a_layer_is_an_empty_page_with_a_warning(src):
    svc, doc = ingest([digital(LAYER), scanned(), digital(LAYER)], src, ScriptedOcr(LOOP))
    page = doc.manifest.pages[1]
    assert page.method == PageMethod.VLM_OCR and page.n_chars == 0
    assert page.warnings == ["OCR output rejected (repeating text); the page was left empty"]
    assert "<!-- page 2 | method=vlm_ocr -->\n\n" in svc.store.markdown(doc)
    assert doc.manifest.pages[0].warnings == []  # the neighbours are untouched


def test_a_garbled_text_layer_is_not_a_fallback(src):
    garbled = page_with_layer(garbage_ratio=0.4)
    _, doc = ingest([garbled], src, ScriptedOcr(LOOP))
    page = doc.manifest.pages[0]
    assert (
        page.method == PageMethod.VLM_OCR and page.n_chars == 0 and "left empty" in page.warnings[0]
    )


def test_text_that_cannot_fit_the_page_is_rejected(src):
    ocr = ScriptedOcr(sentences(300))  # 9 thousand characters: 6.2 thousand fit a 7 x 9 in page
    ocr.dpi = 120  # the fake page is 850 x 1100 px
    page = page_with_layer()
    _, doc = ingest([page], src, ocr)
    assert doc.manifest.pages[0].warnings[0].startswith("OCR output rejected (more text than")


def test_a_page_of_ordinary_length_is_kept(src):
    ocr = ScriptedOcr(sentences(300))
    ocr.dpi = 72  # a large page: everything fits
    _, doc = ingest([scanned()], src, ocr)
    assert doc.manifest.pages[0].warnings == [] and doc.manifest.pages[0].n_chars > 9000


# ------------------------------------------------------------------- truncation
def test_a_truncated_page_is_kept_and_says_so(src):
    _, doc = ingest([scanned()], src, ScriptedOcr(sentences(40), finish="length"))
    page = doc.manifest.pages[0]
    assert page.method == PageMethod.VLM_OCR and page.n_chars > 0
    assert len(page.warnings) == 1 and "token limit" in page.warnings[0]
    assert page.finish_reason == "length"


def test_an_empty_answer_is_kept_without_a_warning(src):
    _, doc = ingest([scanned()], src, ScriptedOcr(""))
    page = doc.manifest.pages[0]
    assert page.method == PageMethod.VLM_OCR and page.n_chars == 0 and page.warnings == []


# ------------------------------------------------------------------ repeated pages
def test_pages_with_the_same_text_keep_only_the_first(src):
    pages = [scanned(), digital(LAYER), scanned(), scanned()]
    _, doc = ingest(pages, src, ScriptedOcr(GOOD, GOOD, sentences(60)))
    p = doc.manifest.pages
    assert [x.method for x in p] == [PageMethod.VLM_OCR, PageMethod.TEXT_LAYER] + [
        PageMethod.VLM_OCR
    ] * 2
    assert p[0].warnings == [] and p[0].n_chars > 0
    assert p[2].warnings == ["OCR output rejected (same text as page 1); the page was left empty"]
    assert p[2].n_chars == 0 and p[3].warnings == [] and p[3].n_chars > 0


def test_a_repeated_page_with_a_text_layer_gets_its_layer(src):
    pages = [scanned(), page_with_layer()]
    svc, doc = ingest(pages, src, ScriptedOcr(GOOD))
    p = doc.manifest.pages
    assert p[0].method == PageMethod.VLM_OCR
    assert p[1].method == PageMethod.TEXT_LAYER and "same text as page 1" in p[1].warnings[0]
    assert "Attention is all you need." in svc.store.markdown(doc)


def test_short_repeats_and_different_pages_are_untouched(src):
    _, doc = ingest([scanned(), scanned()], src, ScriptedOcr("# Appendices"))
    assert [p.warnings for p in doc.manifest.pages] == [[], []]


def test_images_are_checked_too(src):
    ocr = ScriptedOcr(GOOD, GOOD, LOOP)
    svc = make_service(ocr=ocr, images=FakeImages(n_frames=3, size=(850, 1100)))
    doc = svc.ingest(src("scan.png", b"png"))
    p = doc.manifest.pages
    assert p[0].warnings == []
    assert "same text as page 1" in p[1].warnings[0] and p[1].n_chars == 0
    assert "repeating text" in p[2].warnings[0] and p[2].method == PageMethod.VLM_OCR


def test_an_image_is_length_checked_only_when_it_says_how_big_it_is(src):
    huge = " ".join(f"word{i}" for i in range(2000))  # 13 thousand characters

    class Sized(FakeImages):
        def frames(self, path):
            frames = super().frames(path)
            for f in frames:
                f.info["dpi"] = (150, 150)  # 5.7 x 7.3 inches: 6.2 thousand characters fit
            return frames

    for images, rejected in (
        (FakeImages(size=(850, 1100)), False),
        (Sized(size=(850, 1100)), True),
    ):
        svc = make_service(ocr=ScriptedOcr(huge), images=images)
        page = svc.ingest(src("scan.png", b"png")).manifest.pages[0]
        assert bool(page.warnings) is rejected, images
    assert "more text than the page can hold" in page.warnings[0]


def test_a_real_image_file_brings_its_resolution(src, tmp_path):
    from PIL import Image

    from docingest.adapters.images.pillow import PillowImageSource

    path = tmp_path / "scan.png"
    Image.new("RGB", (300, 200), "white").save(path, dpi=(100, 100))
    (frame,) = PillowImageSource().frames(path)
    assert frame.info["dpi"] == pytest.approx((100, 100), abs=0.01)


# ---------------------------------------------------------------------- manifest
def test_warnings_are_written_to_the_manifest_and_old_manifests_still_load(src):
    _, doc = ingest([scanned()], src, ScriptedOcr(LOOP))
    dumped = doc.manifest.model_dump_json()
    assert "left empty" in dumped
    assert DocumentManifest.model_validate_json(dumped) == doc.manifest
    old = doc.manifest.model_dump()
    for page in old["pages"]:
        del page["warnings"]  # written before the field existed
    assert DocumentManifest.model_validate(old).pages[0].warnings == []


def test_the_guard_thresholds_are_part_of_the_cache_key():
    default = make_service()
    stricter = make_service(guards=OcrGuards(max_chars_per_sq_in=100))
    for kind in (SourceKind.PDF, SourceKind.IMAGE):
        assert default.config_hash(kind) != stricter.config_hash(kind)


def test_ocr_all_pages_are_checked_like_any_other(src):
    page = digital(LAYER)
    page.image = scanned().image
    svc = make_service({"a.pdf": [page]}, ocr=ScriptedOcr(LOOP))
    doc = svc.ingest(src("a.pdf"), IngestOptions(ocr_all=True))
    assert doc.manifest.pages[0].method == PageMethod.TEXT_LAYER
    assert "text layer was used" in doc.manifest.pages[0].warnings[0]


def test_the_ingest_summary_counts_pages_with_warnings(src, monkeypatch):
    from rich.console import Console

    from docingest.entrypoints import cli

    out = Console(width=200, record=True, file=open("/dev/null", "w"))  # noqa: SIM115
    monkeypatch.setattr(cli, "console", out)
    _, bad = ingest([scanned(), scanned(), digital(LAYER)], src, ScriptedOcr(LOOP))
    _, clean = ingest([digital(LAYER)], src, ScriptedOcr(GOOD))
    cli._summary([bad, clean])
    rows = [line for line in out.export_text().splitlines() if ".pdf" in line]
    assert len(rows) == 2
    assert rows[0].split("│")[5].strip() == "2" and rows[1].split("│")[5].strip() == ""
