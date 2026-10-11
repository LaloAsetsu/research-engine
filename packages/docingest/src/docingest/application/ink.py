"""Measurement on a rendered page image: how much of it is ink.

Used by ``IngestService`` on the image it already renders for OCR, so a blank page costs
no extra rendering. Pillow only (the ports already pass images as ``PIL.Image``).
"""

from __future__ import annotations

from PIL import Image, ImageFilter


def ink_ratio(image: Image.Image, contrast: int, dark_page: int = 128) -> float:
    """Share of the pixels darker than the page's own background by ``contrast`` levels.

    The image is first smoothed with a 3x3 median filter, which removes single-pixel noise
    (scanner noise, JPEG blocks, paper grain) but keeps strokes and the faint text of a
    faded copy. The background is the median grey level, so yellowed or grey paper and a
    scanner's tint are not ink, and neither is what stays within ``contrast`` levels of it
    (show-through of the other side). A page that is
    dark overall (median grey below ``dark_page``: a black cover, a full-bleed picture) has
    no background to compare to and counts as all ink: it is never reported blank.
    """
    histogram = image.convert("L").filter(ImageFilter.MedianFilter(3)).histogram()
    total = sum(histogram)
    if total == 0:
        return 0.0
    seen, background = 0, 0
    for level, count in enumerate(histogram):
        seen += count
        if seen * 2 >= total:
            background = level
            break
    if background < dark_page:
        return 1.0
    return sum(histogram[: max(background - contrast, 0)]) / total
