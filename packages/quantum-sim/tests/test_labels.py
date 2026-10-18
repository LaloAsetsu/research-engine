"""The label resolver: every spelling, the daggers, the refusals, and that no label is guessed."""

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from quantum_sim import GATES, LabelError, ResolvedLabel, label_of, resolve_label
from quantum_sim.gates import matrix
from quantum_sim.labels import MAX_LABEL_CHARS, SPELLINGS, _unbrace

PI = math.pi
FIXED_NAMES = {name for name, spec in GATES.items() if spec.n_params == 0}


def params_of(resolved: ResolvedLabel) -> tuple[float, ...]:
    return tuple(angle.value for angle in resolved.params)


def spellings():
    for gate, texts in SPELLINGS.items():
        for text in texts:
            yield gate, text.replace("ARG", r"\pi/2")


@pytest.mark.parametrize(("gate", "label"), list(spellings()), ids=str)
def test_every_spelling_resolves_to_its_gate(gate, label):
    resolved = resolve_label(label)
    if gate == "cz":
        assert (resolved.gate, resolved.implied_controls) == ("z", 1)
        assert resolved.drawn_wires == 2
        return
    assert resolved.gate == gate
    assert resolved.implied_controls == 0
    if gate == "rk":
        assert params_of(resolved)[0] == int(re_digits(label))
    elif GATES[gate].n_params:
        assert params_of(resolved) == pytest.approx((PI / 2,))
    else:
        assert resolved.params == ()


def re_digits(label: str) -> str:
    return "".join(c for c in label if c.isdigit())


def test_the_spelling_table_covers_the_gate_table():
    assert set(SPELLINGS) - {"cz"} == set(GATES)


@pytest.mark.parametrize(
    ("label", "gate"),
    [
        ("H", "h"),
        (" H ", "h"),
        ("$H$", "h"),
        ("{H}", "h"),
        ("{{H}}", "h"),
        (r"\mathrm{H}", "h"),
        (r"\text{ H }", "h"),
        (r"\textrm{H}", "h"),
        (r"\operatorname{SWAP}", "swap"),
        (r"\mathsf{X}", "x"),
        (r"\mathit{Z}", "z"),
        (r"{T}^{\dagger}", "tdg"),
        (r"{T^\dagger}", "tdg"),  # enclosing braces are removed first
        (r"{T^{-1}}", "tdg"),
        (r"{{S^{\dag}}}", "sdg"),
        (r"{X^{1/2}}", "sx"),
        (r"{X^{1/2}}^\dagger", "sxdg"),  # braces make it a superscript on a group
        (r"{\sqrt{X}^\dagger}", "sxdg"),
        (r"R_\mathrm{z}(1)", "rz"),
        (r"R_\mathrm{zz}(1)", "rzz"),  # the font group is the whole subscript
        (r"R_\text{zz}(1)", "rzz"),
        (r"R_ \mathrm{XX}(1)", "rxx"),
        (r"R_\mathrm{2}", "rk"),
        (r"{T}^\dagger", "tdg"),
        (r"T ^ \dagger", "tdg"),
        (r"T^{ \dagger }", "tdg"),
        (r"\mathrm{T}^{\dagger}", "tdg"),
        (r"S^{\dag}", "sdg"),
        ("S†", "sdg"),
        ("S^{†}", "sdg"),
        ("T^{-1}", "tdg"),
        (r"\sqrt{X}", "sx"),
        (r"\sqrt X", "sx"),
        (r"\sqrt  X", "sx"),
        (r"\sqrt{ X }", "sx"),
        (r"\sqrt{\mathrm{NOT}}", "sx"),
        (r"\sqrt{\text{NOT}}", "sx"),
        (r"X^{\frac12}", None),  # \frac12 is refused in angles too
        (r"X^{\frac{1}{2}}", "sx"),
        (r"X^{-\frac12}", None),
        (r"X^{-\frac{1}{2}}", "sxdg"),
        (r"\sqrt{X}^{\dagger}", "sxdg"),
        (r"\sqrt{X}^\dag", "sxdg"),
        (r"\sqrt X^\dagger", "sxdg"),
        (r"SX^\dagger", "sxdg"),
        ("X^{-1/2}", "sxdg"),
        (r"X^{1/2\dagger}", None),
        ("Sdg", "sdg"),
        ("Tdg", "tdg"),
        (r"Sdg^\dagger", "s"),
        (r"S^\dagger{}^\dagger", None),
        (r"\mathbb{I}", "id"),
        (r"\mathbb{1}", "id"),
        ("I^\\dagger", "id"),
        ("H^\\dagger", "h"),
        ("SWAP^\\dagger", "swap"),
        ("CZ^\\dagger", "z"),
    ],
)
def test_wrappers_spacings_and_daggers(label, gate):
    if gate is None:
        with pytest.raises(LabelError, match="unknown gate label"):
            resolve_label(label)
        return
    assert resolve_label(label).gate == gate


@pytest.mark.parametrize(
    ("label", "gate", "expected"),
    [
        (r"R_y(\pi/2)", "ry", PI / 2),
        (r"R_{y}(\pi/2)", "ry", PI / 2),
        (r"R_Z(-2*1)", "rz", -2.0),
        (r"R_x\left(\frac{\pi}{2}\right)", "rx", PI / 2),
        (r"R_x \left( \pi / 2 \right)", "rx", PI / 2),
        (r"\mathrm{R}_x(\pi)", "rx", PI),
        (r"\mathrm{R}_{\mathrm{x}}(\pi)", "rx", PI),
        (r"R_{\text{y}}(\pi)", "ry", PI),
        (r"RY(0.5)", "ry", 0.5),
        (r"Ry(0.5)", "ry", 0.5),
        (r"R_{xx}(\pi/4)", "rxx", PI / 4),
        (r"R_{XX}(\pi/4)", "rxx", PI / 4),
        (r"RXX(\pi/4)", "rxx", PI / 4),
        (r"R_{\mathrm{ZZ}}(1)", "rzz", 1.0),
        (r"P(\pi/3)", "p", PI / 3),
        (r"P\left(\pi/3\right)", "p", PI / 3),
        (r"\mathrm{P}(1)", "p", 1.0),
        (r"R_y(\pi/2)^\dagger", "ry", -PI / 2),
        (r"R_y^\dagger(\pi/2)", "ry", -PI / 2),
        (r"R_y^{\dagger}(\pi/2)", "ry", -PI / 2),
        (r"R_y(\pi/2)^{-1}", "ry", -PI / 2),
        (r"P(\pi)^\dagger", "p", -PI),
        (r"R_{zz}(2)^\dagger", "rzz", -2.0),
        (r"R_x(1.5\pi)", "rx", 1.5 * PI),
        (r"R_x(-\pi)", "rx", -PI),
        (r"R_x((1+1)\pi)", "rx", 2 * PI),
    ],
)
def test_rotations_and_phases(label, gate, expected):
    resolved = resolve_label(label)
    assert resolved.gate == gate
    assert params_of(resolved) == pytest.approx((expected,))


def test_a_symbolic_angle_stays_a_parameter():
    resolved = resolve_label(r"R_z(-2\gamma)")
    (angle,) = resolved.params
    assert angle.symbols == ("gamma",)
    assert angle.evaluate({"gamma": 0.25}) == -0.5
    daggered = resolve_label(r"R_y(\theta_1)^\dagger")
    assert daggered.params[0].evaluate({"theta_1": 0.5}) == -0.5


@pytest.mark.parametrize(
    ("label", "k"),
    [
        ("R_1", 1),
        ("R_2", 2),
        ("R_9", 9),
        ("R_{9}", 9),
        ("R_{ 3 }", 3),
        ("R_{k=10}", 10),
        ("R_{k = 12}", 12),
        ("R_{ k=40 }", 40),
        (r"\mathrm{R}_{k=100}", 100),
    ],
)
def test_the_qft_rk(label, k):
    resolved = resolve_label(label)
    assert (resolved.gate, params_of(resolved)) == ("rk", (float(k),))
    assert np.allclose(matrix("rk", (k,)), np.diag([1, np.exp(2j * PI / 2**k)]))


@pytest.mark.parametrize(
    "label", ["R_10", "R_12", "R_{10}", "R_{12}", "R_{01}", "R_01", "R_{001}", "R_{99}", "R_{123}"]
)
def test_an_index_of_several_digits_or_with_a_leading_zero_is_not_read_as_k(label):
    # R_12 is R_1 then 2 in LaTeX; R_{12} and R_{01} usually name a pair of wires or levels
    with pytest.raises(LabelError, match="ambiguous: an index of more than one digit") as caught:
        resolve_label(label)
    assert caught.value.ambiguous and "R_{k=12}" in str(caught.value)


@pytest.mark.parametrize("label", ["R_0", "R_{0}", "R_{00}"])
def test_an_index_of_zero_needs_k_of_at_least_one(label):
    with pytest.raises(LabelError, match="R_k needs k of at least 1"):
        resolve_label(label)


@pytest.mark.parametrize(
    "label", ["R_{k=0}", "R_{k=01}", "R_{k=1.5}", "R_{k=-2}", "R_{k=}", "R_{k=1e3}", "R_{k=x}"]
)
def test_the_written_out_k_is_a_positive_integer_without_leading_zeros(label):
    with pytest.raises(LabelError, match="unknown gate label"):
        resolve_label(label)


def test_a_written_out_k_that_does_not_fit_a_float_is_refused_not_raised():
    assert params_of(resolve_label("R_{k=" + "9" * 308 + "}")) == (float("9" * 308),)
    with pytest.raises(LabelError, match="k is too large"):
        resolve_label("R_{k=" + "9" * 400 + "}")


def test_label_of_writes_k_below_ten_as_a_digit_and_the_rest_written_out():
    assert label_of("rk", (9.0,)) == "R_{9}"
    assert label_of("rk", (10.0,)) == "R_{k=10}"
    assert label_of("rk", (40.0,)) == "R_{k=40}"


def test_cz_is_z_with_its_first_wire_as_the_control():
    resolved = resolve_label("CZ")
    assert (resolved.gate, resolved.implied_controls, resolved.drawn_wires) == ("z", 1, 2)
    assert resolve_label("H").drawn_wires == 1
    assert resolve_label("SWAP").drawn_wires == 2


AMBIGUOUS = [
    r"R(\theta)",
    "R",
    "R()",
    r"R_\phi",
    r"R_{\phi}",
    r"R_\theta(\phi)",
    r"R_{\mathrm{CZ}}",
    "R_k",
    "R_{k}",
    "U",
    "U_1",
    "U_{1}",
    "U_2",
    "U_3",
    "U_t",
    r"U(\theta, \phi, \lambda)",
    r"U_1(\lambda)",
    r"U_3(\theta,\phi,\lambda)",
    "V",
    "V_0",
    "W",
    "Ph",
    r"Ph(\phi)",
    r"\mathrm{Ph}(\phi)",
    "Phase",
    r"Phase(\phi)",
    r"\hat{U}",
    r"\hat{U}_t(\theta_t/2)",
    r"\tilde{U}_2",
    r"\widehat{U}",
    r"\mathcal{U}",
    r"\mathbb{U}",
    r"\bar{V}",
    "CNOT",
    "CX",
    "CCX",
    "CCNOT",
    "Toffoli",
    "Fredkin",
    "CSWAP",
    r"\mathrm{CNOT}",
    r"\text{Toffoli}",
]


@pytest.mark.parametrize("label", AMBIGUOUS)
def test_ambiguous_labels_are_refused_not_mapped(label):
    with pytest.raises(LabelError, match=r"^unknown gate label") as caught:
        resolve_label(label)
    assert caught.value.ambiguous
    assert "ambiguous" in str(caught.value)


def test_the_reason_names_the_label():
    with pytest.raises(LabelError) as caught:
        resolve_label("U_1")
    assert str(caught.value).startswith("unknown gate label 'U_1'")
    with pytest.raises(LabelError) as caught:
        resolve_label("Bogus")
    assert str(caught.value) == "unknown gate label 'Bogus'"
    assert not caught.value.ambiguous


UNKNOWN = [
    "",
    "   ",
    "$$",
    "h",
    "x",
    "t",
    "hadamard",
    "Hadamard",
    "HH",
    "H H",
    "G",
    "O",
    "Q",
    "M",
    "A",
    "B",
    "N",
    "Y_1",
    "X_1",
    "T_1",
    "H_2",
    "H^2",
    "H^{\\otimes n}",
    "H^{\\otimes 2}",
    "T^2",
    "T^{\\dagger}^{\\dagger}",
    r"T^\dagger\dagger",
    "X^{1/4}",
    # a superscript of more than one character needs matching braces; LaTeX sets these another way
    "X^1/2",
    "X^-1/2",
    "X^{1/2",
    "X^1/2}",
    "X^{-1/2",
    "X^-1/2}",
    "T^-1",
    "S^-1",
    "T^{-1",
    "T^-1}",
    "T^{\\dagger",
    "T^\\dagger}",
    "S^{\\dagger",
    "S^\\dagger}",
    "S^{\\dag",
    r"T^\daggerx",
    # X^{1/2} already has a superscript: a second one is a double superscript in LaTeX
    r"X^{1/2}^\dagger",
    r"X^{-1/2}^\dagger",
    r"X^{1/2}^{-1}",
    r"X^{1/2}^\dag",
    r"{X^{1/2}^\dagger}",
    r"\sqrt{X}^-1",
    r"\sqrt{X}^{-1",
    r"R_x^-1(\theta)",
    r"R_x(\theta)^-1",
    r"R_x^{-1(\theta)",
    r"R_x(\theta)^{\dagger",
    "X^{\\frac12}",
    "X^\\frac{1}{2}",
    "R_xx(1)",
    "R_XX(1)",
    "R_zz(1)",
    "R_xy(1)",
    r"\sqrt{Z}",
    r"\sqrt{\mathrm{SWAP}}",
    r"\sqrt[3]{X}",
    r"\sqrt{X}^2",
    "NOT",
    "Z(\\theta)",
    "X(\\theta)",
    "Y(\\theta)",
    "ZZ(\\theta)",
    "XX(\\theta)",
    "MS",
    "iSWAP^\\dagger",
    "\\sqrt{iSWAP}",
    "CR_z(\\theta)",
    "CP(\\theta)",
    "C-Z",
    "CH",
    "CS",
    "\\ket{0}",
    "\\bra{0}",
    "|0\\rangle",
    "\\ket{\\psi}",
    "\\Lambda",
    r"\mathrm{Meas}",
    "?",
    "H;X",
    "H, X",
    "H\nX",
]


@pytest.mark.parametrize("label", UNKNOWN)
def test_unknown_labels_are_refused(label):
    with pytest.raises(LabelError, match="unknown gate label"):
        resolve_label(label)


@pytest.mark.parametrize(
    ("label", "reason"),
    [
        (r"R_x", "needs an angle"),
        (r"R_x()", "needs an angle"),
        (r"P", "unknown gate label 'P'"),
        (r"H(1)", "takes no arguments"),
        (r"X(\pi)", None),
        (r"\sqrt{X}(1)", "takes no arguments"),
        (r"R_2(1)", "takes no angle"),
        (r"R_0", "at least 1"),
        (r"R_{xy}(1)", "not a rotation about one axis"),
        (r"R_{xX}(1)", "not a rotation about one axis"),
        (r"R_y(\theta", "unknown gate label"),
        (r"R_y\theta)", "unknown gate label"),
        (r"R_y(1/2\pi)", "bad angle"),
        (r"R_y(1,2)", "bad angle"),
        (r"R_y(\theta=1)", "bad angle"),
        (r"R_y(1/0)", "bad angle: division by zero"),
        (r"R_y(2^10)", "bad angle"),
        (r"R_y(e)", "bad angle"),
        (r"R_y(\tau\unknown)", "bad angle"),
        (r"R_2^\dagger", "not in the gate table"),
        (r"iSWAP^\dagger", "not in the gate table"),
        (r"R_{3}^{-1}", "not in the gate table"),
        ("T" + r"^\dagger" * 2, "unknown gate label"),
        (r"T^\dagger(1)", "takes no arguments"),
    ],
)
def test_malformed_labels_are_refused_with_a_reason(label, reason):
    with pytest.raises(LabelError, match=reason or "unknown gate label"):
        resolve_label(label)


def test_two_daggers_are_refused():
    with pytest.raises(LabelError, match="two daggers"):
        resolve_label(r"R_y^\dagger(\theta)^\dagger")


def test_labels_that_are_not_text_or_too_long_are_refused():
    for bad in (None, 1, b"H", ["H"]):
        with pytest.raises(LabelError, match="is text"):
            resolve_label(bad)  # type: ignore[arg-type]
    with pytest.raises(LabelError, match="more than"):
        resolve_label("H" * (MAX_LABEL_CHARS + 1))
    with pytest.raises(LabelError, match=r"unknown gate label 'H{57}\.\.\.'"):
        resolve_label("H" * 100)


def test_font_commands_are_removed_three_levels_deep():
    assert resolve_label(r"\mathrm{\mathrm{\mathrm{H}}}").gate == "h"
    with pytest.raises(LabelError, match="unknown gate label"):
        resolve_label(r"\mathrm{\mathrm{\mathrm{\mathrm{H}}}}")


def test_no_label_is_matched_by_case_or_by_similarity():
    for label in ("h", "hadamard", "Hh", "HX", "rx(1)", "RX (1)x", "R_X(1)x", "toffoli", "swap"):
        with pytest.raises(LabelError):
            resolve_label(label)


def label_matrix(label: str) -> np.ndarray:
    resolved = resolve_label(label)
    return matrix(resolved.gate, params_of(resolved))


@pytest.mark.parametrize(
    ("label", "plain"),
    [
        (r"S^\dagger", "S"),
        (r"T^\dagger", "T"),
        (r"\sqrt{X}^\dagger", r"\sqrt{X}"),
        (r"R_x(0.7)^\dagger", "R_x(0.7)"),
        (r"R_y(0.7)^\dagger", "R_y(0.7)"),
        (r"R_z(0.7)^\dagger", "R_z(0.7)"),
        (r"P(0.7)^\dagger", "P(0.7)"),
        (r"R_{xx}(0.7)^\dagger", "R_{xx}(0.7)"),
        (r"R_{yy}(0.7)^\dagger", "R_{yy}(0.7)"),
        (r"R_{zz}(0.7)^\dagger", "R_{zz}(0.7)"),
        (r"H^\dagger", "H"),
        (r"Y^{-1}", "Y"),
    ],
)
def test_a_dagger_is_the_conjugate_transpose(label, plain):
    assert np.allclose(label_matrix(label), label_matrix(plain).conj().T)


def test_the_matrices_behind_the_names():
    s2 = 1 / math.sqrt(2)
    assert np.allclose(label_matrix(r"R_y(\pi/2)"), [[s2, -s2], [s2, s2]])
    assert np.allclose(
        label_matrix(r"\sqrt{X}"), 0.5 * np.array([[1 + 1j, 1 - 1j], [1 - 1j, 1 + 1j]])
    )
    assert np.allclose(label_matrix("S"), np.diag([1, 1j]))
    assert np.allclose(label_matrix("T"), np.diag([1, np.exp(1j * PI / 4)]))
    assert np.allclose(label_matrix(r"P(\pi/2)"), np.diag([1, 1j]))
    assert np.allclose(
        label_matrix(r"R_z(\pi/2)"), np.diag([np.exp(-1j * PI / 4), np.exp(1j * PI / 4)])
    )
    # R_z and P are different gates: the resolver keeps them apart
    assert not np.allclose(label_matrix(r"R_z(\pi/2)"), label_matrix(r"P(\pi/2)"))


@settings(max_examples=300, deadline=None)
@given(
    st.sampled_from(sorted(GATES)),
    st.floats(min_value=-1e3, max_value=1e3, allow_nan=False),
    st.integers(1, 60),
)
def test_label_of_is_read_back_by_the_resolver(gate, angle, k):
    spec = GATES[gate]
    params = (float(k),) if gate == "rk" else (angle,) * spec.n_params
    resolved = resolve_label(label_of(gate, params))
    assert resolved.gate == gate
    assert params_of(resolved) == params


def test_label_of_refuses_a_gate_that_is_not_in_the_table():
    with pytest.raises(LabelError, match="unknown gate"):
        label_of("cnot")


@settings(max_examples=500, deadline=None)
@given(st.text(max_size=60))
def test_arbitrary_text_gives_a_label_or_a_label_error(text):
    try:
        resolved = resolve_label(text)
    except LabelError:
        return
    assert resolved.gate in GATES


PIECES = [
    "H", "X", "R", "_", "x", "y", "{", "}", "(", ")", "^", r"\dagger", r"\pi", "1", "2", " ",
    r"\theta", "/", "-", "U", "P", r"\sqrt", r"\mathrm", "T", "S",
]  # fmt: skip


@settings(max_examples=500, deadline=None)
@given(st.lists(st.sampled_from(PIECES), max_size=14))
def test_random_label_soup_gives_a_label_or_a_label_error(pieces):
    try:
        resolved = resolve_label("".join(pieces))
    except LabelError:
        return
    assert resolved.gate in GATES


@pytest.mark.parametrize(
    ("label", "gate"),
    [
        ("X^{1/2}", "sx"),
        ("X^{-1/2}", "sxdg"),
        ("T^{-1}", "tdg"),
        ("S^{-1}", "sdg"),
        (r"T^{\dagger}", "tdg"),
        (r"T^\dagger", "tdg"),
        ("T^†", "tdg"),
        (r"R_x^{-1}(\theta)", "rx"),
        (r"R_x(\theta)^{-1}", "rx"),
        ("Rxx(1)", "rxx"),
        ("RZZ(1)", "rzz"),
        ("R_{xx}(1)", "rxx"),
    ],
)
def test_the_braced_form_of_each_refused_superscript_and_rotation_is_read(label, gate):
    assert resolve_label(label.replace(r"\theta", "1")).gate == gate


def test_an_unbraced_two_axis_rotation_says_how_to_write_it():
    with pytest.raises(LabelError, match=r"R_zz is R_z followed by z in LaTeX; write R_\{zz\}"):
        resolve_label("R_zz(1)")


@pytest.mark.parametrize("label", [r"R_\mathrm{12}", r"R_\text{12}", r"R_\mathrm{01}"])
def test_a_font_group_after_a_subscript_is_the_whole_subscript(label):
    # R_\mathrm{12} is R_{12}: an index of two digits, not R_1 followed by a 2
    with pytest.raises(LabelError, match="an index of more than one digit") as caught:
        resolve_label(label)
    assert caught.value.ambiguous


@pytest.mark.parametrize(
    "label", [r"R_x^-1(\theta)", r"R_x(\theta)^-1", "T^-1", "S^-1", "X^1/2", "P(1)^-1"]
)
def test_a_known_name_with_a_superscript_that_needs_braces_says_so(label):
    with pytest.raises(LabelError, match="the superscript is not a dagger") as caught:
        resolve_label(label)
    assert not caught.value.ambiguous and "needs braces" in str(caught.value)


def test_a_double_superscript_says_how_to_write_it():
    with pytest.raises(LabelError, match=r"double superscript.*write \{X\^\{1/2\}\}\^\\dagger"):
        resolve_label(r"X^{1/2}^\dagger")


def naive_unbrace(text):
    """The first way of removing the braces that wrap the whole text: one scan for each level."""

    def matching(start):
        depth = 0
        for index in range(start, len(text)):
            depth += (text[index] == "{") - (text[index] == "}")
            if depth == 0:
                return index
        return -1

    while text.startswith("{") and matching(0) == len(text) - 1:
        text = text[1:-1]
    return text


@settings(max_examples=500, deadline=None)
@given(st.lists(st.sampled_from(["{", "}", "{{", "}}", "T", " ", "^"]), max_size=14).map("".join))
def test_the_one_pass_removal_of_enclosing_braces_matches_the_scan_for_each_level(text):
    assert _unbrace(text) == naive_unbrace(text)


@pytest.mark.parametrize(
    ("text", "unbraced"),
    [
        ("{T}", "T"),
        ("{{T}}", "T"),
        ("{{T}{S}}", "{T}{S}"),  # the outer pair wraps the text, the inner ones do not
        ("{T}{S}", "{T}{S}"),
        ("{{T}}{S}", "{{T}}{S}"),
        ("{T", "{T"),
        ("T}", "T}"),
        ("{}", ""),
        ("", ""),
        ("T", "T"),
    ],
)
def test_enclosing_braces_are_removed_only_when_they_wrap_the_whole_text(text, unbraced):
    assert _unbrace(text) == unbraced == naive_unbrace(text)
