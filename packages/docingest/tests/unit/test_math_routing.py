"""Math pages: the page goes to OCR when the rule is on, the layer stays as the fallback."""

import hashlib
import json
import tomllib
from pathlib import Path

import pytest
from fakes import FakeOcr, FakePage, ScriptedOcr, digital, make_service

from docingest.application.ingest import PIPELINE_VERSION
from docingest.config import load_config
from docingest.domain.models import PageMethod, PageSignals, SourceKind
from docingest.domain.routing import MATH_KEYS, RoutingPolicy, decide

LAYER = "The proof follows from the definition of the operator and the lemma. " * 12
LOOP = "and then the model kept going " * 20
ON = RoutingPolicy(math_to_ocr=True)

# The [routing] section as it was hashed before the math keys existed. The cache key of a
# configuration that does not use the rule must stay this string, key for key.
POLICY_JSON_BEFORE_MATH = (
    '{"min_chars":50,"image_coverage":0.6,"image_coverage_max_chars":400,'
    '"max_garbage_ratio":0.1,"min_alpha_ratio":0.5,"blank_max_chars":10,'
    '"blank_max_ink":0.0005,"blank_ink_contrast":32,"blank_dark_page":128,'
    '"scan_layer_to_ocr":true,"scan_image_cover":0.8}'
)


def math_page(eq: int = 0, density: float = 0.0, text: str = LAYER) -> FakePage:
    signals = PageSignals(
        n_chars=len("".join(text.split())), alpha_ratio=0.9, eq_signs=eq, math_density=density
    )
    return FakePage(signals, text)


def signals(**kw) -> PageSignals:
    return PageSignals(n_chars=2000, alpha_ratio=0.9, **kw)


def test_a_math_page_is_read_by_ocr_with_the_rule_on(src):
    ocr = FakeOcr("# page read by the model")
    svc = make_service({"a.pdf": [math_page(eq=12, density=0.05)]}, ocr=ocr, policy=ON)
    doc = svc.ingest(src("a.pdf"))
    page = doc.manifest.pages[0]
    assert page.method == PageMethod.VLM_OCR and ocr.calls == 1
    assert page.probe is not None and page.probe.needs_ocr
    assert page.probe.reasons == [
        "math-heavy page with a text layer: 12 '=' signs, math symbols 5.0% of the characters"
    ]
    assert page.probe.eq_signs == 12 and page.probe.math_density == 0.05
    assert "page read by the model" in svc.store.markdown(doc)


def test_the_reason_reaches_the_log(src):
    lines: list[str] = []
    svc = make_service({"a.pdf": [math_page(eq=8)]}, policy=ON, log=lines.append)
    svc.ingest(src("a.pdf"))
    assert any("OCR (math-heavy page with a text layer" in line for line in lines)


def test_the_rule_is_off_by_default(src):
    ocr = FakeOcr("# never asked")
    assert RoutingPolicy().math_to_ocr is False
    svc = make_service({"a.pdf": [math_page(eq=200, density=0.5)]}, ocr=ocr)
    doc = svc.ingest(src("a.pdf"))
    page = doc.manifest.pages[0]
    assert page.method == PageMethod.TEXT_LAYER and ocr.calls == 0
    assert page.probe is not None and page.probe.reasons == []
    assert page.probe.eq_signs == 200  # the measurement is recorded either way


def test_a_page_under_both_thresholds_keeps_its_text_layer(src):
    ocr = FakeOcr("# never asked")
    pages = [math_page(), math_page(eq=4, density=0.039)]
    svc = make_service({"a.pdf": pages}, ocr=ocr, policy=ON)
    doc = svc.ingest(src("a.pdf"))
    assert [p.method for p in doc.manifest.pages] == [PageMethod.TEXT_LAYER] * 2
    assert ocr.calls == 0 and doc.manifest.ocr_pages == 0


def test_a_mixed_document_routes_each_page(src):
    ocr = FakeOcr("# read by the model")
    pages = [digital(LAYER), math_page(eq=9), math_page(eq=1), math_page(density=0.07)]
    svc = make_service({"a.pdf": pages}, ocr=ocr, policy=ON)
    doc = svc.ingest(src("a.pdf"))
    assert [p.method for p in doc.manifest.pages] == [
        PageMethod.TEXT_LAYER,
        PageMethod.VLM_OCR,
        PageMethod.TEXT_LAYER,
        PageMethod.VLM_OCR,
    ]
    assert ocr.calls == 2


def test_the_text_layer_is_the_fallback_when_the_ocr_is_rejected(src):
    good = "Sentence one says something. " * 30
    svc = make_service({"a.pdf": [math_page(eq=20, text=good)]}, ocr=ScriptedOcr(LOOP), policy=ON)
    doc = svc.ingest(src("a.pdf"))
    page = doc.manifest.pages[0]
    assert page.method == PageMethod.TEXT_LAYER
    assert page.warnings == ["OCR output rejected (repeating text); the text layer was used"]
    assert page.probe is not None and page.probe.reasons[0].startswith("math-heavy page")
    assert "Sentence one says something." in svc.store.markdown(doc)


@pytest.mark.parametrize(
    ("eq", "density", "routed"),
    [
        (0, 0.0, False),
        (4, 0.0, False),  # one under math_min_equals
        (5, 0.0, True),
        (0, 0.0399, False),  # just under math_min_density
        (0, 0.04, True),
        (4, 0.039, False),  # both just under
        (5, 0.04, True),  # both reached: one reason, not two
        (300, 0.9, True),
    ],
)
def test_each_threshold_is_at_least(eq, density, routed):
    probe = decide(signals(eq_signs=eq, math_density=density), ON)
    assert probe.needs_ocr is routed
    assert len(probe.reasons) == int(routed)


def test_the_thresholds_can_be_moved():
    page = signals(eq_signs=7, math_density=0.02)
    assert decide(page, RoutingPolicy(math_to_ocr=True, math_min_equals=7)).needs_ocr
    assert not decide(page, RoutingPolicy(math_to_ocr=True, math_min_equals=8)).needs_ocr
    dense = signals(eq_signs=0, math_density=0.06)
    assert decide(dense, RoutingPolicy(math_to_ocr=True, math_min_density=0.06)).needs_ocr
    assert not decide(dense, RoutingPolicy(math_to_ocr=True, math_min_density=0.07)).needs_ocr
    # moving a threshold does nothing while the rule is off
    assert not decide(page, RoutingPolicy(math_min_equals=1)).needs_ocr


def test_a_page_under_min_chars_gets_the_first_reason_only():
    probe = decide(PageSignals(n_chars=20, alpha_ratio=0.9, eq_signs=9, math_density=0.2), ON)
    assert probe.reasons == ["only 20 embedded chars (<50)"]


def test_the_force_flag_is_not_a_second_reason():
    probe = decide(signals(eq_signs=9), ON, force_ocr=True)
    assert probe.reasons == [probe.reasons[0]] and probe.reasons[0].startswith("math-heavy")
    assert decide(signals(eq_signs=1), ON, force_ocr=True).reasons == ["forced (--ocr-all)"]


def test_a_math_page_that_is_also_a_garbled_or_letterless_page_records_both_reasons():
    probe = decide(PageSignals(n_chars=2000, alpha_ratio=0.2, eq_signs=9), ON)
    assert [r.split(" (")[0].split(":")[0] for r in probe.reasons] == [
        "low alphabetic ratio",
        "math-heavy page with a text layer",
    ]


def test_a_math_scan_records_both_reasons_and_the_math_rule_does_not_need_the_scan_rule():
    scan = PageSignals(n_chars=2000, n_images=1, image_coverage=0.99, alpha_ratio=0.9, eq_signs=9)
    both = decide(scan, ON)
    assert [r.split(":")[0] for r in both.reasons] == [
        "scanned page with a text layer",
        "math-heavy page with a text layer",
    ]
    # The two rules are independent switches: with the scan rule off, the layer of a
    # math-heavy scan is still not trusted.
    only_math = decide(scan, RoutingPolicy(math_to_ocr=True, scan_layer_to_ocr=False))
    assert only_math.needs_ocr
    assert [r.split(":")[0] for r in only_math.reasons] == ["math-heavy page with a text layer"]
    # and with the math rule off, the scan rule alone decides
    no_math = decide(scan, RoutingPolicy(scan_layer_to_ocr=False))
    assert not no_math.needs_ocr


def test_the_rule_applies_from_min_chars_up():
    at_min = decide(PageSignals(n_chars=50, alpha_ratio=0.9, eq_signs=9), ON)
    assert at_min.needs_ocr and at_min.reasons[0].startswith("math-heavy")
    under = decide(PageSignals(n_chars=49, alpha_ratio=0.9, eq_signs=9), ON)
    assert under.reasons == ["only 49 embedded chars (<50)"]


@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("math_min_equals", 0),
        ("math_min_equals", -3),
        ("math_min_density", 0),
        ("math_min_density", -0.1),
        ("math_min_density", 1.5),
    ],
)
def test_the_thresholds_are_validated(key, bad):
    with pytest.raises(ValueError, match=key):
        RoutingPolicy(**{key: bad})


def test_the_shipped_config_has_the_rule_off_with_the_measured_thresholds():
    shipped = load_config(Path(__file__).parents[2] / "config" / "pipeline.toml").routing
    assert shipped == RoutingPolicy()
    assert (shipped.math_to_ocr, shipped.math_min_equals, shipped.math_min_density) == (
        False,
        5,
        0.04,
    )
    section = tomllib.loads((Path(__file__).parents[2] / "config" / "pipeline.toml").read_text())[
        "routing"
    ]
    assert {"math_to_ocr", "math_min_equals", "math_min_density"} <= section.keys()


# ------------------------------------------------------------------------------- cache key


def test_the_keys_are_part_of_the_pdf_cache_key_when_the_rule_is_on():
    # Old results of these pages (the text layer) must not be served once the rule is on.
    def key(**kw):
        return make_service(policy=RoutingPolicy(**kw)).config_hash(SourceKind.PDF)

    keys = {
        key(),
        key(math_to_ocr=True),
        key(math_to_ocr=True, math_min_equals=6),
        key(math_to_ocr=True, math_min_density=0.05),
    }
    assert len(keys) == 4
    assert make_service(policy=ON).config_hash(SourceKind.IMAGE) == make_service().config_hash(
        SourceKind.IMAGE
    )


def test_with_the_rule_off_the_thresholds_are_not_in_the_cache_key():
    default = make_service().config_hash(SourceKind.PDF)
    moved = make_service(policy=RoutingPolicy(math_min_equals=9, math_min_density=0.2))
    assert moved.config_hash(SourceKind.PDF) == default
    assert moved.config_hash(SourceKind.PDF, ocr_all=True) == make_service().config_hash(
        SourceKind.PDF, ocr_all=True
    )


def test_the_default_cache_key_is_the_one_computed_before_the_math_keys_existed():
    assert RoutingPolicy().cache_json() == POLICY_JSON_BEFORE_MATH
    svc = make_service()
    before = json.dumps(
        [
            PIPELINE_VERSION,
            "pdf",
            False,
            POLICY_JSON_BEFORE_MATH,
            svc.guards.model_dump_json(),
            svc.pdf.fingerprint,
            svc.ocr.fingerprint,
        ]
    )
    assert svc.config_hash(SourceKind.PDF) == hashlib.sha256(before.encode()).hexdigest()[:12]


def test_the_cache_json_with_the_rule_on_carries_all_three_keys():
    assert set(json.loads(ON.cache_json())) - set(json.loads(POLICY_JSON_BEFORE_MATH)) == {
        "math_to_ocr",
        "math_min_equals",
        "math_min_density",
    }


def test_the_math_keys_are_the_fields_left_out_of_the_cache_key_when_the_rule_is_off():
    assert {"math_to_ocr", "math_min_equals", "math_min_density"} == MATH_KEYS
    assert set(RoutingPolicy.model_fields) >= MATH_KEYS
    everything = set(json.loads(ON.cache_json()))
    assert everything == set(RoutingPolicy.model_fields)
    assert set(json.loads(RoutingPolicy().cache_json())) == everything - MATH_KEYS
