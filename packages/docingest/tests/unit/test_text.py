import pytest

from docingest.domain.text import (
    alpha_ratio,
    clean_text_layer,
    equals_count,
    garbage_ratio,
    is_math_char,
    math_density,
    split_pages,
)


def test_garbled_and_symbolic_text_detection():
    assert garbage_ratio("(cid:12)(cid:7)(cid:99) ok") > 0.5
    assert garbage_ratio("clean text") == 0
    assert garbage_ratio("transduc\x02tion") == 0  # pdfium hyphen marker is not garbage
    assert alpha_ratio("∑∫≈ 1234 ## %%") < 0.5
    assert alpha_ratio("plain english words") == 1.0


def test_math_symbols_and_equals_signs_are_counted():
    assert equals_count("a = b, c == d") == 3 and equals_count("no sign") == 0
    assert all(is_math_char(c) for c in "αΩ→∀∑≤⟨⨁ℏ𝐀±×√∞†")
    assert not any(is_math_char(c) for c in "aZ09=+-<>(). é")  # ASCII operators are not counted
    assert math_density("") == 0.0 and math_density("plain words") == 0.0
    assert math_density("α β\n∑ x") == pytest.approx(3 / 4)  # spaces do not count
    assert math_density("∑\x02x") == pytest.approx(1 / 2)  # nor does the hyphen mark


@pytest.mark.parametrize(
    "char",
    [
        "\u0370", "\u03ff",  # both ends of the Greek block
        "ϕ", "ϑ", "ϵ",  # the variant forms TeX papers use (U+03D5, 03D1, 03F5)
        "\u2300", "⌈", "\u23ff",  # technical block: brackets and floors
        "\u2100", "\u214f", "\u2190", "\u21ff", "\u22ff", "\u27ef", "\u2980", "ℏ", "\u27c0", "\u2aff", "\U0001d400", "\U0001d7ff",
        "ˆ", "™", "№",
    ],
)  # fmt: skip
def test_the_edges_of_the_math_symbol_blocks(char):
    assert is_math_char(char)


@pytest.mark.parametrize(
    "char",
    ["\u036f", "\u0400", "\u20ff", "\u2150", "\u218f", "\u2400", "\u27bf", "\u2b00", "ˇ", "é"],
)
def test_just_outside_the_math_symbol_blocks(char):
    assert not is_math_char(char)


def test_clean_text_layer_handles_pdfium_hyphen_marker():
    raw = (
        "The transduc\x02tion model. Transduction works.\r\n"
        "A sequence\x02aligned RNN between 2019-\r\n2020 and Qwen-2.5-\r\nVL-7B."
    )
    out = clean_text_layer(raw)
    assert "\x02" not in out
    assert "transduction model" in out  # joined: the word occurs elsewhere
    assert "sequence-aligned" in out  # compound kept: no "sequencealigned" anywhere
    assert "2019-2020" in out and "Qwen-2.5-VL-7B" in out  # identifiers keep their hyphen


def test_clean_text_layer_uses_document_vocabulary():
    assert clean_text_layer("transduc\x02tion", vocab={"transduction"}) == "transduction"
    assert clean_text_layer("transduc\x02tion", vocab=set()) == "transduc-tion"


def test_split_pages_roundtrip_ignores_marker_like_text():
    md = (
        "# T\n\n<!-- page 1 | method=text_layer -->\nhello <!-- page 9 --> world\n\n"
        "<!-- page 2 | method=vlm_ocr -->\nsecond\n"
    )
    assert split_pages(md) == {1: "hello <!-- page 9 --> world", 2: "second"}
