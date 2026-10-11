import pytest

from docingest.domain.models import PageSignals
from docingest.domain.routing import RoutingPolicy, decide

POLICY = RoutingPolicy()


@pytest.mark.parametrize(
    ("signals", "needs_ocr", "reason"),
    [
        (PageSignals(n_chars=3000, alpha_ratio=0.8), False, None),
        (PageSignals(n_chars=0, image_coverage=1.0, n_images=1), True, "embedded chars"),
        (PageSignals(n_chars=120, image_coverage=0.9, alpha_ratio=0.8), True, "image covers"),
        (PageSignals(n_chars=2000, image_coverage=0.5, alpha_ratio=0.8), False, None),
        (PageSignals(n_chars=2000, image_coverage=0.9, alpha_ratio=0.8), True, "scanned page"),
        (PageSignals(n_chars=900, garbage_ratio=0.4, alpha_ratio=0.8), True, "garbled"),
        (PageSignals(n_chars=900, alpha_ratio=0.2), True, "alphabetic"),
    ],
)
def test_decide(signals, needs_ocr, reason):
    probe = decide(signals, POLICY)
    assert probe.needs_ocr is needs_ocr
    if reason:
        assert any(reason in r for r in probe.reasons)


def test_a_scan_with_a_text_layer_is_judged_by_the_cover_not_by_the_amount_of_text():
    page = PageSignals(n_chars=3000, n_images=1, image_coverage=0.99, alpha_ratio=0.95)
    probe = decide(page, POLICY)
    assert probe.needs_ocr
    assert probe.reasons == [
        "scanned page with a text layer: images cover 99% of the page, 3000 embedded chars"
    ]


@pytest.mark.parametrize(
    ("coverage", "scan"), [(0.536, False), (0.79, False), (0.8, True), (0.883, True), (1.0, True)]
)
def test_the_scan_cover_threshold_is_at_least(coverage, scan):
    # The scan pages of the reference corpus start at 0.88; 0.8 leaves room below them.
    page = PageSignals(n_chars=3000, n_images=1, image_coverage=coverage, alpha_ratio=0.95)
    assert decide(page, POLICY).needs_ocr is scan


def test_the_scan_rule_can_be_turned_off_or_moved():
    page = PageSignals(n_chars=3000, n_images=1, image_coverage=0.95, alpha_ratio=0.95)
    assert not decide(page, RoutingPolicy(scan_layer_to_ocr=False)).needs_ocr
    assert not decide(page, RoutingPolicy(scan_image_cover=0.97)).needs_ocr
    assert decide(page, RoutingPolicy(scan_image_cover=0.9)).needs_ocr


def test_a_page_with_little_text_gets_each_reason_once_per_rule():
    probe = decide(
        PageSignals(n_chars=120, image_coverage=1.0, n_images=1, alpha_ratio=0.9), POLICY
    )
    assert [r.split(":")[0].split(" covers")[0] for r in probe.reasons] == [
        "image",
        "scanned page with a text layer",
    ]
    # Under min_chars the first rule already speaks; the scan rule stays out of it.
    probe = decide(PageSignals(n_chars=20, image_coverage=1.0, n_images=1), POLICY)
    assert not any("scanned page" in r for r in probe.reasons)


@pytest.mark.parametrize("bad", [0, -0.1, 1.5])
def test_the_scan_cover_must_be_a_fraction(bad):
    with pytest.raises(ValueError, match="scan_image_cover"):
        RoutingPolicy(scan_image_cover=bad)


def test_force_ocr_records_reason():
    probe = decide(PageSignals(n_chars=3000, alpha_ratio=0.8), POLICY, force_ocr=True)
    assert probe.needs_ocr and probe.reasons == ["forced (--ocr-all)"]


def test_policy_thresholds_are_configurable():
    lenient = RoutingPolicy(min_chars=0, min_alpha_ratio=0)
    assert not decide(PageSignals(n_chars=0), lenient).needs_ocr


def test_an_unknown_routing_key_is_rejected():
    # Old: a misspelt [routing] key was dropped silently and the default threshold applied.
    from pydantic import ValidationError

    from docingest.config import AppConfig

    with pytest.raises(ValidationError, match="min_char"):
        AppConfig.model_validate({"routing": {"min_char": 0}})
