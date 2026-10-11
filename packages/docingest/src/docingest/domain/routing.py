"""OCR routing policy: pure decision on measured page signals."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from .models import PageProbe, PageSignals


class RoutingPolicy(BaseModel):
    """Thresholds from published pipelines (Marker, olmOCR); tuned in config/pipeline.toml."""

    # Like every other config section: a misspelt [routing] key is an error, not a no-op.
    model_config = ConfigDict(extra="forbid")

    min_chars: int = 50  # fewer embedded chars than this -> treat as scanned
    image_coverage: float = 0.6  # big image + little text -> scanned
    image_coverage_max_chars: int = 400
    max_garbage_ratio: float = 0.10  # broken font encodings -> re-OCR
    min_alpha_ratio: float = 0.5  # olmOCR heuristic: mostly non-letters -> bad text layer
    # Blank pages: a page that would go to OCR for lack of text, but has (almost) no text
    # layer AND (almost) no ink in its rendered image, is an empty page, never OCR input
    # (the model otherwise writes a plausible page that is not there). Measured values:
    # config/README.md. blank_max_ink = 0 turns the rule off.
    blank_max_chars: int = Field(default=10, ge=0)  # embedded chars a blank page may carry
    blank_max_ink: float = Field(default=0.0005, ge=0, le=1)  # share of inked pixels, below this
    # Grey levels under the paper that make a pixel ink, measured after a 3x3 median filter.
    # Low enough that the faint text of a faded copy or a pencil page (35 to 45 levels
    # darker) is ink, high enough that grey paper, scanner noise and show-through are not.
    blank_ink_contrast: int = Field(default=32, ge=1, le=255)
    # A page whose median grey is below this is not paper (a black cover, a picture): all ink.
    blank_dark_page: int = Field(default=128, ge=1, le=255)
    # Scans with an OCR layer: a page that is one image over (almost) the whole page AND has a
    # text layer is an old scan whose layer is the output of a past OCR program. That layer is
    # trusted today; the VLM reads such pages with a fraction of its errors (reference sample:
    # character error rate 0.068 -> 0.011 on plain text, 0.43 -> 0.03 on math). Measured
    # values: config/README.md. scan_layer_to_ocr = false keeps the layer.
    scan_layer_to_ocr: bool = True
    # Share of the page the images cover (their union, not a sum) for a page to count as a
    # scan. On the reference corpus 593 scan pages (17 documents) cover 0.88 to 1.0. The rule
    # also fires on a born-digital cover (full-page picture, already OCR input by the
    # image_coverage rule); a figure of thousands of tiny images measured 1.0 as a sum of
    # areas and 0.13 as a union. A born-digital page drawn over one full-page background image
    # (slide, poster, cover) with a long text is read by OCR too: see config/README.md.
    scan_image_cover: float = Field(default=0.8, gt=0, le=1)


def could_be_blank(signals: PageSignals, policy: RoutingPolicy) -> bool:
    """Whether the page's text alone allows the blank rule; the image decides the rest."""
    return policy.blank_max_ink > 0 and signals.n_chars <= policy.blank_max_chars


def is_blank(signals: PageSignals, ink: float, policy: RoutingPolicy) -> bool:
    """No text layer to speak of and no ink: ``ink`` is the share of inked pixels."""
    return could_be_blank(signals, policy) and ink < policy.blank_max_ink


_PAGE_NUMBER = re.compile(
    r"^[\s\d.\u2010-\u2015\-]*(?:m{0,4}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3}))?"
    r"[\s\d.\u2010-\u2015\-]*$",
    re.IGNORECASE,
)


def is_page_number(text: str) -> bool:
    """Whether a short text layer is only a page number: digits, dashes, a roman numeral.

    A page with no ink beyond such a text is blank; a title ("Index", "Part I") is not,
    and its text layer is the page.
    """
    return bool(_PAGE_NUMBER.match(text.strip()))


def blank_probe(probe: PageProbe, ink: float, policy: RoutingPolicy) -> PageProbe:
    """The probe of a blank page: not OCR input, and the measurements say why."""
    reason = (
        f"blank page: {probe.n_chars} embedded chars (<={policy.blank_max_chars}),"
        f" {ink:.5f} of the pixels inked (<{policy.blank_max_ink:g})"
    )
    return probe.model_copy(update={"needs_ocr": False, "reasons": [reason]})


def decide(signals: PageSignals, policy: RoutingPolicy, *, force_ocr: bool = False) -> PageProbe:
    s, p = signals, policy
    reasons: list[str] = []
    if s.n_chars < p.min_chars:
        reasons.append(f"only {s.n_chars} embedded chars (<{p.min_chars})")
    if s.image_coverage >= p.image_coverage and s.n_chars < p.image_coverage_max_chars:
        reasons.append(f"image covers {s.image_coverage:.0%} of page with little text")
    if s.garbage_ratio > p.max_garbage_ratio:
        reasons.append(f"garbled text layer ({s.garbage_ratio:.0%} bad glyphs)")
    if s.n_chars >= p.min_chars and s.alpha_ratio < p.min_alpha_ratio:
        reasons.append(f"low alphabetic ratio ({s.alpha_ratio:.0%})")
    if (
        p.scan_layer_to_ocr
        and s.n_chars >= p.min_chars  # fewer chars is already a reason above
        and s.image_coverage >= p.scan_image_cover
    ):
        reasons.append(
            f"scanned page with a text layer: images cover {s.image_coverage:.0%} of the page,"
            f" {s.n_chars} embedded chars"
        )
    if force_ocr and not reasons:
        reasons.append("forced (--ocr-all)")
    return PageProbe(**s.model_dump(), needs_ocr=bool(reasons), reasons=reasons)
