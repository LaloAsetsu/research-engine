"""From LaTeX circuit source to a unitary: quantikz and Qcircuit sources through docingest's
``gate_list`` and ``CircuitSteps.to_dict()``, then ``from_gate_list`` and the engine, against
matrices written by hand.

quantum_sim must never import docingest, and the lane that tests quantum-sim installs only that
package, so this test cannot call docingest. It reads the documents saved in
``tests/fixtures/bridge/`` (the sources are the ``.tex`` files next to them, written by
``tools/make_bridge_fixtures.py``). docingest's own tests check that those saved documents are
still what ``gate_list`` returns for the sources, so a change on either side fails a test.
"""

import json
import math
from pathlib import Path

import numpy as np
import pytest

from quantum_sim import RefusedError, check, from_gate_list, run, statevector, unitary

FIXTURES = Path(__file__).parent / "fixtures" / "bridge"
S2 = 1 / math.sqrt(2)

I2 = np.eye(2)
H = np.array([[1, 1], [1, -1]]) * S2
CNOT = np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 0, 1], [0, 0, 1, 0]])
CZ = np.diag([1, 1, 1, -1])
X = np.array([[0, 1], [1, 0]])
Z = np.diag([1, -1])
RY_PI_2 = np.array([[1, -1], [1, 1]]) * S2  # exp(-i (pi/2) Y / 2)
TOFFOLI = np.eye(8)[[0, 1, 2, 3, 4, 5, 7, 6]]  # |110> <-> |111>, wire 1 the most significant bit

BELL_UNITARY = CNOT @ np.kron(H, I2)
GHZ_UNITARY = np.kron(I2, CNOT) @ np.kron(CNOT, I2) @ np.kron(H, np.kron(I2, I2))


def document(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def circuit_of(name: str):
    reading = from_gate_list(document(name))
    assert len(reading.circuits) == 1
    return reading.circuits[0].bind()


def basis(index: int, size: int) -> np.ndarray:
    vector = np.zeros(size)
    vector[index] = 1
    return vector


@pytest.mark.parametrize("name", ["bell_quantikz", "bell_qcircuit"])
def test_bell(name):
    c = circuit_of(name)
    assert c.n_wires == 2
    np.testing.assert_allclose(unitary(c), BELL_UNITARY, atol=1e-12)
    np.testing.assert_allclose(statevector(c), [S2, 0, 0, S2], atol=1e-12)
    assert from_gate_list(document(name)).notes == ()  # \ket{0} legends are the default input


@pytest.mark.parametrize("name", ["ghz_quantikz", "ghz_qcircuit"])
def test_ghz(name):
    c = circuit_of(name)
    assert c.n_wires == 3
    np.testing.assert_allclose(unitary(c), GHZ_UNITARY, atol=1e-12)
    np.testing.assert_allclose(statevector(c), (basis(0, 8) + basis(7, 8)) * S2, atol=1e-12)


@pytest.mark.parametrize("name", ["cz_quantikz", "cz_qcircuit"])
def test_cz(name):
    np.testing.assert_allclose(unitary(circuit_of(name)), CZ, atol=1e-12)


@pytest.mark.parametrize("name", ["toffoli_quantikz", "toffoli_qcircuit"])
def test_toffoli(name):
    np.testing.assert_allclose(unitary(circuit_of(name)), TOFFOLI, atol=1e-12)


@pytest.mark.parametrize("name", ["ry_quantikz", "ry_qcircuit"])
def test_ry_pi_over_2(name):
    np.testing.assert_allclose(unitary(circuit_of(name)), RY_PI_2, atol=1e-12)
    np.testing.assert_allclose(statevector(circuit_of(name)), [S2, S2], atol=1e-12)


def test_a_drawn_identity_is_checked_left_against_right():
    reading = from_gate_list(document("hzh_equals_x_quantikz"))
    assert reading.signs == ("=",) and reading.claims_equality(0)
    left, right = (part.bind() for part in reading.circuits)
    np.testing.assert_allclose(unitary(left), H @ Z @ H, atol=1e-12)
    np.testing.assert_allclose(unitary(left), X, atol=1e-12)
    np.testing.assert_allclose(unitary(right), X, atol=1e-12)
    result = check(document("hzh_equals_x_quantikz"))
    assert result["status"] == "ok" and result["equal_to_target"] is True
    assert result["comparisons"][0]["between"] == ["part 1", "part 2"]


def test_cnot_from_cz_and_hadamards():
    reading = from_gate_list(document("cnot_from_cz_quantikz"))
    left, right = (part.bind() for part in reading.circuits)
    np.testing.assert_allclose(unitary(left), np.kron(I2, H) @ CZ @ np.kron(I2, H), atol=1e-12)
    np.testing.assert_allclose(unitary(left), CNOT, atol=1e-12)
    np.testing.assert_allclose(unitary(right), CNOT, atol=1e-12)
    assert check(document("cnot_from_cz_quantikz"))["equal_to_target"] is True


@pytest.mark.parametrize("name", ["cnot_reversed_quantikz", "cnot_reversed_qcircuit"])
def test_a_cnot_with_its_control_below_its_target(name):
    # control wire 2, target wire 1: |01> <-> |11>, whatever the order of the drawing
    assert document(name)["parts"][0][0][0]["targets"] == [1]
    assert document(name)["parts"][0][0][0]["controls"] == [2]
    np.testing.assert_allclose(unitary(circuit_of(name)), np.eye(4)[[0, 3, 2, 1]], atol=1e-12)
    assert not np.allclose(unitary(circuit_of(name)), CNOT)


def test_a_fredkin_gate_controlled_from_below():
    # control wire 3 swaps wires 1 and 2: |011> <-> |101>
    expected = np.eye(8)[[0, 1, 2, 5, 4, 3, 6, 7]]
    np.testing.assert_allclose(unitary(circuit_of("fredkin_quantikz")), expected, atol=1e-12)


def test_the_three_wire_qft_with_its_phase_gates_is_the_dft():
    # Nielsen and Chuang figure 5.1: H, R_2 and R_3 controlled from below, a final swap of 1 and 3
    c = circuit_of("qft3_quantikz")
    assert [op.gate for op in c.ops] == ["h", "rk", "rk", "h", "rk", "h", "swap"]
    assert [op.params for op in c.ops if op.gate == "rk"] == [(2.0,), (3.0,), (2.0,)]
    omega = np.exp(2j * np.pi / 8)
    dft = np.array([[omega ** (j * k) for k in range(8)] for j in range(8)]) / math.sqrt(8)
    np.testing.assert_allclose(unitary(c), dft, atol=1e-12)
    # without the swap it is the DFT with its output wires in reverse order
    without = type(c)(c.n_wires, c.ops[:-1])
    assert not np.allclose(unitary(without), dft)


def test_measurements_at_the_end_of_the_wires():
    result = run(document("measure_quantikz"))
    assert result["status"] == "ok"
    entry = result["circuits"][0]
    assert entry["measured_wires"] == [1, 2]
    assert entry["probabilities"]["wires"] == [1, 2]
    assert entry["probabilities"]["values"] == pytest.approx([0.5, 0, 0, 0.5])
    # a measured circuit has no unitary and no single state to check against
    assert (
        check({"circuit": document("measure_quantikz"), "target": {"state": "00"}})["status"]
        == "refused"
    )


def test_the_two_notations_give_the_same_circuit():
    for pair in (
        ("bell", 2),
        ("ghz", 3),
        ("cz", 2),
        ("toffoli", 3),
        ("ry", 1),
        ("cnot_reversed", 2),
    ):
        quantikz, qcircuit = (circuit_of(f"{pair[0]}_{kind}") for kind in ("quantikz", "qcircuit"))
        assert quantikz.ops == qcircuit.ops and quantikz.n_wires == pair[1]


def test_a_wrong_hand_matrix_would_fail():
    # the comparisons above have teeth: CNOT with the control and target swapped is not Bell's
    swapped = np.array([[1, 0, 0, 0], [0, 0, 0, 1], [0, 0, 1, 0], [0, 1, 0, 0]])
    assert not np.allclose(unitary(circuit_of("bell_quantikz")), swapped @ np.kron(H, I2))
    assert not np.allclose(unitary(circuit_of("ry_quantikz")), RY_PI_2.T)


@pytest.mark.parametrize(
    ("name", "wire", "legend"),
    [
        ("bundle_quantikz", 2, "bundle m"),  # a CNOT onto \qwbundle{m} is not a CNOT onto a qubit
        ("dots_quantikz", 2, "dots (more wires, not drawn)"),  # \vdots is not an idle qubit
    ],
)
def test_a_bundle_and_a_row_of_dots_are_refused(name, wire, legend):
    with pytest.raises(RefusedError) as stop:
        from_gate_list(document(name))
    assert any(f"wire {wire}: the legend" in r and legend in r for r in stop.value.reasons)
    result = run(document(name))
    assert result["status"] == "refused" and result["circuits"] == []
    assert check({"circuit": document(name), "target": {"state": "00"}})["status"] == "refused"


def test_a_wire_mark_with_dots_leaves_the_circuit_partial_and_it_is_refused():
    # a quantikz \\wire[l][1]["\\iddots"]{a} draws gates the figure leaves out (the rungs of a
    # parity ladder), so the two CNOTs that are drawn are not the whole circuit: docingest marks
    # the mark unparsed and the circuit partial, and no unitary is made of the drawn part
    data = document("partial_wire_dots_quantikz")
    assert data["status"] == "partial"
    assert [[o["kind"] for o in step] for step in data["parts"][0]] == [["x"], ["x", "unparsed"]]
    with pytest.raises(RefusedError) as stop:
        from_gate_list(data)
    assert any("read as 'partial', not 'parsed'" in r for r in stop.value.reasons)
    for result in (run(data), check({"a": data, "b": data})):
        assert result["status"] == "refused" and result["circuits"] == []


@pytest.mark.parametrize(
    "name",
    [
        "postselect_quantikz",
        "postselect_qcircuit",
        "postselect_macro_quantikz",  # \rstick{$\Proj{1}$}: a macro of the paper's own
        "postselect_macro_qcircuit",  # \rstick{\kb{1}{1}}
        "postselect_word_quantikz",  # \rstick{\text{post-selected on } 1}
    ],
)
def test_a_bra_at_the_end_of_a_wire_is_refused_because_it_is_a_postselection(name):
    # the drawing keeps only the runs that end in the bra, so |11> for the quantikz one (\bra{1})
    # and |00> for the Qcircuit one (\langle 0|), and not the Bell state; the probabilities of
    # the circuit alone [0.5, 0, 0, 0.5] would be a wrong answer. No spelling is looked for: an
    # output that is not a plain name is not read, so a macro or words are refused the same way
    with pytest.raises(RefusedError) as stop:
        from_gate_list(document(name))
    assert any(r.startswith("wire 1: output legend not read: ") for r in stop.value.reasons)
    for result in (run(document(name)), check({"a": document(name), "b": document(name)})):
        assert result["status"] == "refused" and result["circuits"] == []


@pytest.mark.parametrize(
    "name",
    [
        "postselect_slice_quantikz",  # \slice{postselect on 1}
        "postselect_slice_qcircuit",  # \slice{$q_1 = 1$}
    ],
)
def test_a_slice_title_that_is_not_a_plain_name_is_refused(name):
    # docingest keeps the title as the label of a barrier; the Bell circuit postselected on
    # wire 1 = 1 gives [0, 0, 0, 1], not [0.5, 0, 0, 0.5], so a title is read only if it is a name
    with pytest.raises(RefusedError) as stop:
        from_gate_list(document(name))
    assert any("the slice title " in r and "is not read" in r for r in stop.value.reasons)
    for result in (run(document(name)), check({"a": document(name), "b": document(name)})):
        assert result["status"] == "refused" and result["circuits"] == []


@pytest.mark.parametrize("name", ["postselect_ket_quantikz", "postselect_ket_qcircuit"])
def test_a_ket_drawn_as_text_after_a_gate_is_refused(name):
    # `\push{\ket{1}}` after the CNOT is the same drawing as `\rstick{\ket{1}}`: a postselection
    # (or a reset), not an annotation
    with pytest.raises(RefusedError) as stop:
        from_gate_list(document(name))
    assert any("is a ket after a gate on its wire" in r for r in stop.value.reasons)
    assert run(document(name))["status"] == "refused"


@pytest.mark.parametrize("name", ["function_angle_quantikz", "function_angle_qcircuit"])
def test_an_angle_that_is_a_function_of_a_symbol_is_refused(name):
    # R_z(\phi(x)) is the usual notation of a data-encoding gate: phi of x, not phi times x
    assert document(name)["parts"][0][0][0]["label"] == r"R_z(\phi(x))"
    with pytest.raises(RefusedError) as stop:
        from_gate_list(document(name))
    assert any("may be a function" in r for r in stop.value.reasons)
    for result in (
        run(document(name), values={"phi": 1, "x": 1}),
        check({"a": document(name), "b": document(name)}),
    ):
        assert result["status"] == "refused" and result["circuits"] == []


@pytest.mark.parametrize("name", ["late_lstick_quantikz", "late_lstick_qcircuit"])
def test_an_lstick_after_the_gates_is_text_there_and_a_ket_after_a_gate_is_refused(name):
    # the \lstick{\ket{0}} is drawn after the CNOT, at the output of wire 2: it is not the input
    # legend of the row (the row has no legend), and a ket after a gate may be a postselection
    assert document(name)["wire_labels"] == [r"1 = \ket{0}", "2"]
    with pytest.raises(RefusedError) as stop:
        from_gate_list(document(name))
    assert any("is a ket after a gate on its wire" in r for r in stop.value.reasons)
    assert run(document(name))["status"] == "refused"


@pytest.mark.parametrize("name", ["late_lstick_box_quantikz", "late_lstick_cell_quantikz"])
def test_an_lstick_under_a_box_or_in_the_cell_of_a_gate_is_text_there(name):
    # \gate[2]{SWAP} leaves the cells under it empty and \targ{} \lstick{\ket{0}} is one cell:
    # both draw a gate on the row before the ket, so the ket is not the input of wire 2
    assert document(name)["wire_labels"] == [r"1 = \ket{0}", "2"]
    with pytest.raises(RefusedError) as stop:
        from_gate_list(document(name))
    assert any("is a ket after a gate on its wire" in r for r in stop.value.reasons)
    assert run(document(name))["status"] == "refused"


@pytest.mark.parametrize("name", ["brace_sum_quantikz", "brace_sum_qcircuit"])
def test_an_angle_with_a_sum_in_braces_is_refused(name):
    # braces are not drawn: R_y(2{\theta+1}) is shown as R_y(2\theta+1), not as 2(\theta+1)
    assert document(name)["parts"][0][0][0]["label"] == r"R_y(2{\theta+1})"
    with pytest.raises(RefusedError) as stop:
        from_gate_list(document(name))
    assert any("a brace group that holds a sum" in r for r in stop.value.reasons)
    for result in (
        run(document(name), values={"theta": 0.3}),
        check({"a": document(name), "b": document(name)}),
    ):
        assert result["status"] == "refused" and result["circuits"] == []


def test_the_saved_documents_run_through_the_command_api():
    result = run(document("ghz_qcircuit"))
    assert result["status"] == "ok"
    assert result["circuits"][0]["probabilities"]["values"] == [0.5, 0, 0, 0, 0, 0, 0, 0.5]


def test_every_fixture_has_its_source_and_a_circuit_1_document():
    sources = sorted(p.stem for p in FIXTURES.glob("*.tex"))
    saved = sorted(p.stem for p in FIXTURES.glob("*.json"))
    assert sources == saved and len(sources) == 37
    for name in sources:
        assert document(name)["format"] == "circuit/1"
