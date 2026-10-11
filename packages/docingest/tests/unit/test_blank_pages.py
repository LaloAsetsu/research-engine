"""Blank pages: no text layer and no ink -> an empty page, never OCR input."""

import random

import numpy as np
import pytest
from fakes import FakeOcr, FakePage, blank, digital, inked, make_service, scanned
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from docingest.application.ingest import IngestOptions
from docingest.application.ink import ink_ratio
from docingest.config import AppConfig
from docingest.domain.models import PageMethod, PageProbe, PageSignals, SourceKind
from docingest.domain.routing import (
    RoutingPolicy,
    blank_probe,
    could_be_blank,
    is_blank,
    is_page_number,
)

TEXT = "Attention is all you need. " * 10
SIZE = (1275, 1650)  # US Letter at 150 dpi, what the OCR engine is handed
CONTRAST = RoutingPolicy().blank_ink_contrast


def page_with(text: str, *, size: int, at=(600, 80), paper="white") -> Image.Image:
    img = Image.new("RGB", SIZE, paper)
    ImageDraw.Draw(img).text(at, text, font=ImageFont.load_default(size=size), fill="black")
    return img


# ------------------------------------------------------------------- the measurement
def test_a_white_page_has_no_ink():
    assert ink_ratio(Image.new("RGB", SIZE, "white"), CONTRAST) == 0.0


def test_tinted_paper_and_scanner_speckle_are_not_ink():
    paper = Image.new("RGB", SIZE, (238, 228, 196))  # yellowed paper
    assert ink_ratio(paper, CONTRAST) == 0.0
    rng = random.Random(1)
    noisy = Image.new("L", SIZE, 235)
    noisy.putdata([235 + rng.randint(-14, 14) for _ in range(SIZE[0] * SIZE[1])])
    assert ink_ratio(noisy, CONTRAST) == 0.0


def test_a_page_number_is_almost_nothing_and_a_page_of_text_is_not():
    number = ink_ratio(page_with("42", size=22), CONTRAST)
    assert 0 < number < RoutingPolicy().blank_max_ink
    assert ink_ratio(inked(SIZE), CONTRAST) > 20 * RoutingPolicy().blank_max_ink


def test_a_title_page_with_a_few_large_words_is_not_blank():
    title = page_with("Part IV", size=60, at=(500, 700))
    assert ink_ratio(title, CONTRAST) > RoutingPolicy().blank_max_ink


def noisy_paper(level: int, sigma: float, seed: int = 1, blur: float = 0.0) -> Image.Image:
    """Blank paper of one grey level with Gaussian noise (a scanner, JPEG, paper grain)."""
    rng = np.random.default_rng(seed)
    grey = np.clip(rng.normal(level, sigma, (SIZE[1], SIZE[0])), 0, 255).astype("uint8")
    img = Image.fromarray(grey, "L")
    return img.filter(ImageFilter.GaussianBlur(blur)) if blur else img


def text_on(paper: Image.Image, levels_darker: int, lines: int = 12) -> Image.Image:
    img = paper.convert("RGB")
    ink = int(np.median(np.asarray(paper))) - levels_darker
    draw = ImageDraw.Draw(img)
    for n in range(lines):
        draw.text(
            (80, 80 + 40 * n),
            "Faded words on a photocopy of a page",
            font=ImageFont.load_default(size=24),
            fill=(ink,) * 3,
        )
    return img


@pytest.mark.parametrize(
    "paper",
    [
        noisy_paper(255, 5),
        noisy_paper(235, 8),
        noisy_paper(235, 10, seed=2),  # the 3x3 median takes single-pixel noise away
        noisy_paper(213, 4),  # grey paper of an old scan (median 213 in the corpus)
        noisy_paper(235, 6, blur=1.5),  # paper grain
    ],
    ids=["noise 5", "noise 8", "noise 10", "grey paper", "grain"],
)
def test_noise_grain_and_grey_paper_are_not_ink(paper):
    assert ink_ratio(paper, CONTRAST) < RoutingPolicy().blank_max_ink


def test_show_through_of_the_other_side_is_not_ink():
    # Text of the back, 40 levels darker than the paper, blurred as it is through paper.
    ghost = text_on(noisy_paper(235, 3), 40).filter(ImageFilter.GaussianBlur(3))
    assert ink_ratio(ghost, CONTRAST) < RoutingPolicy().blank_max_ink


@pytest.mark.parametrize("levels", [35, 45, 70])
def test_faint_text_is_ink_even_on_noisy_grey_paper(levels):
    # A faded copy: only 35 to 45 levels darker than the paper. At the old contrast of 48
    # (and without the median filter) such a page had no ink or was lost in the noise.
    faint = text_on(noisy_paper(213, 4), levels)
    assert ink_ratio(faint, CONTRAST) > 5 * RoutingPolicy().blank_max_ink


def test_a_faint_scan_is_not_blank():
    img = text_on(Image.new("RGB", SIZE, (235, 235, 235)), 45)
    assert ink_ratio(img, 48) == 0.0  # the contrast of the first version
    assert ink_ratio(img, CONTRAST) > 5 * RoutingPolicy().blank_max_ink


def test_a_dark_page_is_all_ink():
    assert ink_ratio(Image.new("RGB", SIZE, "black"), CONTRAST) == 1.0
    assert ink_ratio(Image.new("RGB", SIZE, (40, 40, 60)), CONTRAST) == 1.0
    grey = Image.new("RGB", SIZE, (110, 110, 110))  # darker than the default paper level
    assert ink_ratio(grey, CONTRAST) == 1.0 and ink_ratio(grey, CONTRAST, dark_page=100) == 0.0


def test_a_hairline_below_the_threshold_does_not_make_a_page_inked():
    img = Image.new("RGB", SIZE, "white")
    ImageDraw.Draw(img).line([(100, 800), (400, 800)], fill="black")  # 300 px of 2.1 million
    assert ink_ratio(img, CONTRAST) < RoutingPolicy().blank_max_ink


# ------------------------------------------------------------------- the rule
POLICY = RoutingPolicy()


@pytest.mark.parametrize(
    ("n_chars", "ink", "blank_"),
    [
        (0, 0.0, True),
        (3, 0.0001, True),  # a page number
        (10, 0.0004, True),
        (11, 0.0, False),  # more text than a page number: the text layer is not "almost none"
        (0, 0.0005, False),  # the threshold itself is ink
        (0, 0.02, False),  # a scan
    ],
)
def test_is_blank(n_chars, ink, blank_):
    assert is_blank(PageSignals(n_chars=n_chars), ink, POLICY) is blank_


def test_a_zero_ink_threshold_turns_the_rule_off():
    off = RoutingPolicy(blank_max_ink=0)
    assert not could_be_blank(PageSignals(n_chars=0), off)
    assert not is_blank(PageSignals(n_chars=0), 0.0, off)


def test_the_probe_of_a_blank_page_keeps_the_measurements_and_is_not_ocr_input():
    probe = PageProbe(n_chars=2, needs_ocr=True, reasons=["only 2 embedded chars (<50)"])
    blank_one = blank_probe(probe, 0.00009, POLICY)
    assert not blank_one.needs_ocr and blank_one.n_chars == 2
    (reason,) = blank_one.reasons
    assert "blank page" in reason and "2 embedded chars" in reason and "0.00009" in reason


def test_blank_keys_are_validated_and_unknown_ones_rejected():
    from pydantic import ValidationError

    assert (
        AppConfig.model_validate({"routing": {"blank_max_chars": 0}}).routing.blank_max_chars == 0
    )
    for bad in (
        {"blank_max_chars": -1},
        {"blank_max_ink": 1.5},
        {"blank_ink_contrast": 0},
        {"blank_dark_page": 0},
    ):
        with pytest.raises(ValidationError):
            AppConfig.model_validate({"routing": bad})
    with pytest.raises(ValidationError, match="blank_max_ink_ratio"):
        AppConfig.model_validate({"routing": {"blank_max_ink_ratio": 0.1}})


@pytest.mark.parametrize(
    ("key", "other"),
    [
        ("blank_max_chars", 20),
        ("blank_max_ink", 0.001),
        ("blank_ink_contrast", 48),
        ("blank_dark_page", 100),
    ],
)
def test_every_blank_threshold_is_part_of_the_cache_key(key, other):
    svc = make_service()
    before = svc.config_hash(SourceKind.PDF)
    svc.policy = RoutingPolicy(**{key: other})
    assert svc.config_hash(SourceKind.PDF) != before


@pytest.mark.parametrize(
    ("text", "number"),
    [("42", True), (" 7 \n", True), ("xiv", True), ("- 123 -", True), ("IV", True), ("12-13", True),
     ("Index", False), ("Part I", False), ("Notes", False), ("mild", False), ("civil", False),
     ("Appendix A", False), ("A", False)],
)  # fmt: skip
def test_is_page_number(text, number):
    assert is_page_number(text) is number


# ------------------------------------------------------------------- the service
def ingest(pages, src, *, ocr=None, policy=None, options=None):
    svc = make_service({"a.pdf": pages}, ocr=ocr or FakeOcr("# OCR page"))
    if policy is not None:
        svc.policy = policy
    return svc, svc.ingest(src("a.pdf"), options)


def test_a_blank_page_is_empty_and_never_sent_to_ocr(src):
    svc, doc = ingest([digital(TEXT), blank(), digital(TEXT)], src)
    methods = [p.method for p in doc.manifest.pages]
    assert methods == [PageMethod.TEXT_LAYER, PageMethod.BLANK, PageMethod.TEXT_LAYER]
    assert svc.ocr.calls == 0
    page = doc.manifest.pages[1]
    assert page.n_chars == 0 and page.model is None and page.gen_tokens is None
    assert page.probe is not None and not page.probe.needs_ocr
    assert "blank page" in page.probe.reasons[0]
    assert doc.manifest.ocr_model is None and doc.manifest.ocr_pages == 0
    assert "<!-- page 2 | method=blank -->\n\n" in svc.store.markdown(doc)


def test_a_page_number_alone_is_a_blank_page(src):
    number = FakePage(PageSignals(n_chars=2, alpha_ratio=0.0), "42", page_with("42", size=22))
    svc, doc = ingest([number], src)
    assert doc.manifest.pages[0].method == PageMethod.BLANK and svc.ocr.calls == 0


@pytest.mark.parametrize(
    "page",
    [
        scanned(),  # ink: a scan
        FakePage(PageSignals(n_chars=2), "42", page_with("Part IV", size=60, at=(500, 700))),
        FakePage(
            PageSignals(n_chars=30, alpha_ratio=0.9), "x" * 30, Image.new("RGB", SIZE, "white")
        ),
        FakePage(PageSignals(n_chars=0), "", Image.new("RGB", SIZE, "black")),
    ],
    ids=["scan", "title page", "too much text for a blank page", "black page"],
)
def test_pages_that_are_not_blank_still_go_to_ocr(src, page):
    svc, doc = ingest([page], src)
    assert doc.manifest.pages[0].method == PageMethod.VLM_OCR and svc.ocr.calls == 1


def test_a_short_title_with_no_other_ink_keeps_its_text_layer(src):
    # Born-digital "Index" or "Part I" pages carry 5 characters and almost no ink. Old: the
    # text was thrown away with the blank page.
    title = FakePage(PageSignals(n_chars=5, alpha_ratio=1.0), "Index", page_with("Index", size=30))
    svc, doc = ingest([title], src)
    page = doc.manifest.pages[0]
    assert page.method == PageMethod.TEXT_LAYER and svc.ocr.calls == 0
    assert page.n_chars == 5 and "Index" in svc.store.markdown(doc)
    assert page.probe is not None and not page.probe.needs_ocr
    assert "text layer is the page" in page.probe.reasons[0]


@pytest.mark.parametrize("number", ["42", "xiv", "- 123 -"])
def test_page_numbers_are_dropped_with_the_blank_page(src, number):
    page = FakePage(
        PageSignals(n_chars=len(number.replace(" ", ""))), number, page_with(number, size=22)
    )
    _, doc = ingest([page], src)
    assert doc.manifest.pages[0].method == PageMethod.BLANK


def test_a_faint_scan_goes_to_ocr(src):
    img = Image.new("RGB", (850, 1100), (235, 235, 235))
    draw = ImageDraw.Draw(img)
    for n in range(12):
        draw.text(
            (80, 80 + 40 * n),
            "Faded words on a photocopy",
            font=ImageFont.load_default(size=24),
            fill=(190,) * 3,
        )
    svc, doc = ingest([scanned(img)], src)
    assert doc.manifest.pages[0].method == PageMethod.VLM_OCR and svc.ocr.calls == 1


def test_the_rule_can_be_turned_off(src):
    svc, doc = ingest([blank()], src, policy=RoutingPolicy(blank_max_ink=0))
    assert doc.manifest.pages[0].method == PageMethod.VLM_OCR and svc.ocr.calls == 1


def test_ocr_all_does_not_resurrect_a_blank_page(src):
    svc, doc = ingest([digital(TEXT), blank()], src, options=IngestOptions(ocr_all=True))
    assert [p.method for p in doc.manifest.pages] == [PageMethod.VLM_OCR, PageMethod.BLANK]
    assert svc.ocr.calls == 1


def test_a_blank_page_renders_once_and_the_image_is_reused_for_ocr(src):
    renders = []

    class Counting(FakePage):
        def render(self, dpi):
            renders.append(dpi)
            return super().render(dpi)

    page = Counting(PageSignals(n_chars=0, n_images=1), "", inked())
    svc, _ = ingest([page], src)
    assert renders == [svc.ocr.dpi]


def test_text_pages_are_never_rendered(src):
    class Exploding(FakePage):
        def render(self, dpi):
            raise AssertionError("a text-layer page must not be rendered")

    page = Exploding(digital(TEXT).signals_, TEXT)
    _, doc = ingest([page], src)
    assert doc.manifest.pages[0].method == PageMethod.TEXT_LAYER
