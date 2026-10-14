"""Gate lists (domain.circuits): every circuit and sentence here is invented.

Each case asserts the exact list, so a wrong gate, wire or step order fails the test that names
it. The last sections check that no input makes the reader raise, that every item it writes is
a known operation or says ``unparsed``, and that its time grows in proportion to the source.
"""

import gc
import random
import re
import time

import pytest

from docingest.domain.circuits import (
    MAX_STEPS,
    CodeBlock,
    add_gate_lists,
    circuit_code_blocks,
    gate_list,
)

HEADER = (
    "Gate list (from the circuit code above; wires numbered top to bottom; "
    "one step per drawn column, left to right):"
)


def quantikz(*rows: str, options: str = "") -> str:
    return f"\\begin{{quantikz}}{options}\n" + " \\\\\n".join(rows) + "\n\\end{quantikz}"


def qcircuit(*rows: str) -> str:
    return "\\Qcircuit @C=1em @R=1em {\n" + " \\\\\n".join(rows) + "\n}"


def lines(source: str) -> list[str]:
    """The list of ``source`` without its header line."""
    text = gate_list(source).text
    first, *rest = text.split("\n")
    assert first.startswith("Gate list ")
    return rest


# ------------------------------------------------------------------ the format


def test_the_list_has_a_header_a_wires_line_and_one_line_per_step():
    source = quantikz(
        "\\lstick{$\\ket{a}$} & \\gate{H} & \\ctrl{1} & \\qw",
        "\\lstick{$\\ket{b}$} & \\qw & \\targ{} & \\meter{}",
    )
    result = gate_list(source)
    assert result.status == "parsed" and result.n_circuits == 1
    assert result.text.split("\n") == [
        HEADER,
        "Wires: 1 = \\ket{a}; 2 = \\ket{b}",
        "Step 1: H on wire 1",
        "Step 2: CNOT: control wire 1, target wire 2",
        "Step 3: measure wire 2",
    ]


def test_what_is_not_a_circuit_has_no_list():
    for source in ["", "plain words", "\\begin{foo} \\gate{H} \\end{foo}", "\\Qcircuit"]:
        assert gate_list(source).text == ""
    assert gate_list("words").status == "unparsed"


# ------------------------------------------------------------------ one case per construct

HADAMARDS = quantikz("& \\gate{H} & \\qw", "& \\gate{H} & \\qw")

CASES = {
    "single gates on two wires": (
        HADAMARDS,
        ["Wires: 1; 2", "Step 1: H on wire 1; H on wire 2"],
    ),
    "cnot with the control above": (
        quantikz("& \\ctrl{1} & \\qw", "& \\targ{} & \\qw"),
        ["Wires: 1; 2", "Step 1: CNOT: control wire 1, target wire 2"],
    ),
    "cnot with the control below": (
        quantikz("& \\targ{} & \\qw", "& \\ctrl{-1} & \\qw"),
        ["Wires: 1; 2", "Step 1: CNOT: control wire 2, target wire 1"],
    ),
    "symmetric controlled-Z with two ctrl": (
        quantikz("& \\ctrl{1} & \\qw", "& \\ctrl{-1} & \\qw"),
        [
            "Wires: 1; 2",
            "Step 1: controlled-Z on wires 1 and 2 (control dots on both, symmetric: no target)",
        ],
    ),
    "symmetric controlled-Z with ctrl and control": (
        quantikz("& \\ctrl{1} & \\qw", "& \\control{} & \\qw"),
        [
            "Wires: 1; 2",
            "Step 1: controlled-Z on wires 1 and 2 (control dots on both, symmetric: no target)",
        ],
    ),
    "toffoli": (
        quantikz("& \\ctrl{2} & \\qw", "& \\ctrl{1} & \\qw", "& \\targ{} & \\qw"),
        ["Wires: 1; 2; 3", "Step 1: Toffoli (CCNOT): controls wires 1 and 2, target wire 3"],
    ),
    "three controls on one target": (
        quantikz(
            "& \\ctrl{3} & \\qw", "& \\ctrl{2} & \\qw", "& \\ctrl{1} & \\qw", "& \\targ{} & \\qw"
        ),
        ["Wires: 1; 2; 3; 4", "Step 1: 3-controlled X: controls wires 1, 2 and 3, target wire 4"],
    ),
    "open control cnot": (
        quantikz("& \\octrl{1} & \\qw", "& \\targ{} & \\qw"),
        ["Wires: 1; 2", "Step 1: CNOT: open control (on |0>) wire 1, target wire 2"],
    ),
    "open and closed control toffoli": (
        quantikz("& \\octrl{2} & \\qw", "& \\ctrl{1} & \\qw", "& \\targ{} & \\qw"),
        [
            "Wires: 1; 2; 3",
            "Step 1: 2-controlled X: control wire 2; open control (on |0>) wire 1, target wire 3",
        ],
    ),
    "controlled-U": (
        quantikz("& \\ctrl{1} & \\qw", "& \\gate{U} & \\qw"),
        ["Wires: 1; 2", "Step 1: controlled-U: control wire 1, target wire 2"],
    ),
    "controlled-U over a multi-wire gate": (
        quantikz("& \\ctrl{1} & \\qw", "& \\gate[2]{U} & \\qw", "& & \\qw"),
        ["Wires: 1; 2; 3", "Step 1: controlled-U: control wire 1, target wires 2 and 3"],
    ),
    "swap with swap and targX": (
        quantikz("& \\swap{1} & \\qw", "& \\targX{} & \\qw"),
        ["Wires: 1; 2", "Step 1: SWAP wires 1 and 2"],
    ),
    "controlled swap": (
        quantikz("& \\ctrl{1} & \\qw", "& \\swap{1} & \\qw", "& \\targX{} & \\qw"),
        ["Wires: 1; 2; 3", "Step 1: controlled-SWAP of wires 2 and 3 (control wire 1)"],
    ),
    "qcircuit swap with qswap and qwx": (
        qcircuit("& \\qswap & \\qw", "& \\qswap \\qwx[-1] & \\qw"),
        ["Wires: 1; 2", "Step 1: SWAP wires 1 and 2"],
    ),
    "qcircuit multigate and ghost": (
        qcircuit("\\lstick{a} & \\multigate{1}{U} & \\qw", "\\lstick{b} & \\ghost{U} & \\qw"),
        ["Wires: 1 = a; 2 = b", "Step 1: U on wires 1 and 2 (one gate on these wires)"],
    ),
    "qcircuit toffoli": (
        qcircuit("& \\ctrl{1} & \\qw", "& \\ctrl{1} & \\qw", "& \\targ & \\qw"),
        ["Wires: 1; 2; 3", "Step 1: Toffoli (CCNOT): controls wires 1 and 2, target wire 3"],
    ),
    "gate with wires option and a width": (
        quantikz("& \\gate[wires=3][2cm]{V} & \\qw", "& & \\qw", "& & \\qw"),
        ["Wires: 1; 2; 3", "Step 1: V on wires 1, 2 and 3 (one gate on these wires)"],
    ),
    "gate with a count and nwires": (
        quantikz("& \\gate[4, nwires=3]{W} & \\qw", "& & \\qw", "& & \\qw", "& & \\qw"),
        ["Wires: 1; 2; 3; 4", "Step 1: W on wires 1, 2, 3 and 4 (one gate on these wires)"],
    ),
    "measurements of three kinds": (
        quantikz("& \\meter{} & \\qw", "& \\meterD{Z} & \\qw", "& \\measure{X} & \\qw"),
        [
            "Wires: 1; 2; 3",
            "Step 1: measure wire 1; measure wire 2 (Z); measure wire 3 (X)",
        ],
    ),
    "a leading line break does not number a wire": (
        quantikz("", "& \\gate{H} & \\qw"),
        ["Wires: 1", "Step 1: H on wire 1"],
    ),
    "a row of dots is one wire of dots": (
        quantikz("& \\gate{H} & \\qw", "\\vdots & &", "& \\gate{H} & \\qw"),
        ["Wires: 1; 2 = dots (more wires, not drawn); 3", "Step 1: H on wire 1; H on wire 3"],
    ),
    "a label over two wires": (
        quantikz("\\lstick[wires=2]{$\\ket{\\Phi}$} & \\gate{H} & \\qw", "& \\gate{X} & \\qw"),
        [
            "Wires: 1 = \\ket{\\Phi} (one label for wires 1 and 2); 2",
            "Step 1: H on wire 1; X on wire 2",
        ],
    ),
    "a second lstick further right is text there": (
        quantikz("\\lstick{a} & \\gate{H} & \\lstick{b}", "\\lstick{c} & \\gate{X} & \\qw"),
        ["Wires: 1 = a; 2 = c", "Step 1: H on wire 1; X on wire 2", "Step 2: text b on wire 1"],
    ),
    "bundle and output labels": (
        quantikz(
            "\\lstick{$n$} & \\qwbundle{n} & \\gate{F} & \\qw \\rstick{out}", "& & \\qw & \\qw"
        ),
        ["Wires: 1 = n, bundle n, output out; 2", "Step 1: F on wire 1"],
    ),
    "a label under a pair of dots names the gate": (
        quantikz("& \\ctrl{1} & \\qw", "& \\ctrl{-1}\\dstick{\\theta} & \\qw"),
        [
            "Wires: 1; 2",
            "Step 1: two-qubit gate drawn as control dots on wires 1 and 2 "
            "(symmetric, no target), labelled \\theta",
        ],
    ),
    "a label next to dots in qcircuit": (
        qcircuit("& \\ctrl{1} & \\qw", "& \\control \\qw \\dstick{\\theta} & \\qw"),
        [
            "Wires: 1; 2",
            "Step 1: two-qubit gate drawn as control dots on wires 1 and 2 "
            "(symmetric, no target), labelled \\theta",
        ],
    ),
    "several controls on dots only": (
        quantikz("& \\ctrl{1} & \\qw", "& \\ctrl{1} & \\qw", "& \\ctrl{-2} & \\qw"),
        [
            "Wires: 1; 2; 3",
            "Step 1: multi-controlled Z (3 wires) on wires 1, 2 and 3 "
            "(control dots on all, symmetric: no target)",
        ],
    ),
    "a gate crossed by a vertical wire stays its own step item": (
        quantikz("& \\ctrl{2} & \\qw", "& \\gate{H} & \\qw", "& \\targ{} & \\qw"),
        ["Wires: 1; 2; 3", "Step 1: CNOT: control wire 1, target wire 3; H on wire 2"],
    ),
    "a measurement that classically controls a gate": (
        quantikz("& \\meter{} \\vcw{1} & \\qw", "& \\gate{X} & \\qw"),
        [
            "Wires: 1; 2",
            "Step 1: measure wire 1; its result classically controls X on wire 2",
        ],
    ),
    "a gate classically controlled by an earlier measurement": (
        qcircuit(
            "\\lstick{a} & \\meter & \\control \\cw \\cwx[1] & \\cw",
            "\\lstick{b} & \\qw & \\gate{X} & \\qw",
        ),
        [
            "Wires: 1 = a; 2 = b",
            "Step 1: measure wire 1",
            "Step 2: controlled-X (classically controlled): control wire 1, target wire 2",
        ],
    ),
    "comments are not read": (
        "\\begin{quantikz}\n& \\gate{H} & \\qw \\\\ % first \\gate{Z}\n"
        "& \\gate{X} & \\qw % \\gate{Y}\n\\end{quantikz}",
        ["Wires: 1; 2", "Step 1: H on wire 1; X on wire 2"],
    ),
    "a comment after a line break": (
        "\\begin{quantikz}\n& \\gate{H} & \\qw \\\\% next wire\n& \\gate{X} & \\qw\n\\end{quantikz}",
        ["Wires: 1; 2", "Step 1: H on wire 1; X on wire 2"],
    ),
    "an equals column starts the other side": (
        quantikz(
            "& \\ctrl{1} & \\qw & \\equiv & & \\gate{H} & \\qw",
            "& \\targ{} & \\qw & & & \\qw & \\qw",
        ),
        [
            "Wires: 1; 2",
            "Step 1: CNOT: control wire 1, target wire 2",
            "[\\equiv: the steps below draw the right-hand side, an equivalent circuit]",
            "Step 1: H on wire 1",
        ],
    ),
    "tikzcd drawn as a circuit": (
        "\\begin{tikzcd}\n\\lstick{a} & \\gate{H} & \\qw & \\meter{}\n\\end{tikzcd}",
        ["Wires: 1 = a", "Step 1: H on wire 1", "Step 2: measure wire 1"],
    ),
    "a gate under a wire that spans past the last row": (
        "\\begin{quantikz}\n& \\gate[2]{U} & \\qw \\\\\n& &\n\\end{quantikz}",
        ["Wires: 1; 2", "Step 1: U on wires 1 and 2 (one gate on these wires)"],
    ),
    "dots with an open control": (
        quantikz("& \\octrl{1} & \\qw", "& \\ctrl{-1} & \\qw"),
        [
            "Wires: 1; 2",
            "Step 1: controlled-Z with open controls: control wire 2; "
            "open control (on |0>) wire 1 (dots only, no target)",
        ],
    ),
    "a wire passing two plain dots joins them to the target": (
        qcircuit(
            "& \\ctrl{3} & \\qw", "& \\control & \\qw", "& \\control & \\qw", "& \\targ & \\qw"
        ),
        ["Wires: 1; 2; 3; 4", "Step 1: 3-controlled X: controls wires 1, 2 and 3, target wire 4"],
    ),
    "a gate over the gates of two other wires joins all three": (
        quantikz("& \\gate[3]{U} & \\gate{Z}", "& \\gate{H} & \\qw", "& \\gate{X} & \\qw"),
        [
            "Wires: 1; 2; 3",
            "Step 1: unparsed joined operation (wire 1: gate U, wire 2: gate H, wire 3: gate X)",
            "Step 2: Z on wire 1",
        ],
    ),
    "an empty quantikz row of full width is a wire under a multi-wire gate": (
        quantikz("& \\gate[2]{U} & ", "& & ", "& \\gate{H} & "),
        [
            "Wires: 1; 2; 3",
            "Step 1: U on wires 1 and 2 (one gate on these wires); H on wire 3",
        ],
    ),
    "an empty quantikz row of full width is a wire under a control": (
        quantikz("& \\ctrl{2} & \\qw", "& & ", "& \\targ{} & \\qw"),
        ["Wires: 1; 2; 3", "Step 1: CNOT: control wire 1, target wire 3"],
    ),
    "an empty quantikz row of full width is a wire under a swap": (
        quantikz("& \\swap{2} & \\qw", "& & ", "& \\targX{} & \\qw"),
        ["Wires: 1; 2; 3", "Step 1: SWAP wires 1 and 3"],
    ),
    "an empty quantikz row of full width keeps the wires after it numbered": (
        quantikz("& \\gate{H} & \\qw", "& & ", "& \\gate{X} & \\qw"),
        ["Wires: 1; 2; 3", "Step 1: H on wire 1; X on wire 3"],
    ),
    "an idle Qcircuit wire is a wire under a control": (
        qcircuit("& \\ctrl{2} & \\qw", "& \\qw & \\qw", "& \\targ & \\qw"),
        ["Wires: 1; 2; 3", "Step 1: CNOT: control wire 1, target wire 3"],
    ),
    "an idle Qcircuit wire keeps the wires after it numbered": (
        qcircuit("& \\gate{H} & \\qw", "& \\qw & \\qw", "& \\gate{X} & \\qw"),
        ["Wires: 1; 2; 3", "Step 1: H on wire 1; X on wire 3"],
    ),
    "an idle Qcircuit wire with a label": (
        qcircuit(
            "\\lstick{a} & \\qw & \\gate{H} & \\qw",
            "\\lstick{b} & \\qw & \\qw & \\qw",
            "\\lstick{c} & \\qw & \\gate{X} & \\qw",
        ),
        ["Wires: 1 = a; 2 = b; 3 = c", "Step 1: H on wire 1; X on wire 3"],
    ),
    "a Qcircuit row of text only is not a wire": (
        qcircuit("& \\gate{H} & \\qw", "& \\raisebox{0pt}{\\text{a}} &", "& \\gate{X} & \\qw"),
        ["Wires: 1; 2", "Step 1: H on wire 1; X on wire 2"],
    ),
    "a Qcircuit row of barriers and text only is not a wire": (
        qcircuit(
            "& t\\in[0,1] \\barrier[-1em]{1} &",
            "\\lstick{a} & \\gate{H} & \\qw",
        ),
        ["Wires: 1 = a", "Step 1: H on wire 1; barrier"],
    ),
    "midstick equals splits the circuit in two": (
        quantikz(
            "& \\ctrl{1} & \\midstick[2,brackets=none]{=} & \\gate{H} & \\qw",
            "& \\ctrl{-1} & & \\gate{H} & \\qw",
        ),
        [
            "Wires: 1; 2",
            "Step 1: controlled-Z on wires 1 and 2 (control dots on both, symmetric: no target)",
            "[=: the steps below draw the right-hand side, an equivalent circuit]",
            "Step 1: H on wire 1; H on wire 2",
        ],
    ),
    "push equals splits a Qcircuit in two": (
        qcircuit(
            "& \\ctrl{1} & \\qw & \\push{=} & & \\gate{Z} & \\qw",
            "& \\targ & \\qw & & & \\qw & \\qw",
        ),
        [
            "Wires: 1; 2",
            "Step 1: CNOT: control wire 1, target wire 2",
            "[=: the steps below draw the right-hand side, an equivalent circuit]",
            "Step 1: Z on wire 1",
        ],
    ),
    "a push that only keeps space is nothing": (
        qcircuit("& \\push{\\rule{0em}{1em}} \\qw & \\gate{H} & \\qw"),
        ["Wires: 1", "Step 1: H on wire 1"],
    ),
    "a bit-controlled gate by the vertical wire option": (
        quantikz("& \\ctrl[vertical wire=c]{1} & \\qw", "& \\gate{X} & \\qw"),
        [
            "Wires: 1; 2",
            "Step 1: controlled-X (classically controlled): control wire 1, target wire 2",
        ],
    ),
    "a classical wire from a measurement to a gate": (
        quantikz("& \\meter{} \\wire[d][1]{c} & \\qw", "& \\gate{X} & \\qw"),
        ["Wires: 1; 2", "Step 1: measure wire 1; its result classically controls X on wire 2"],
    ),
    "a classical wire going up": (
        quantikz("& \\gate{X} & \\qw", "& \\meter{} \\wire[u][1]{c} & \\qw"),
        ["Wires: 1; 2", "Step 1: measure wire 2; its result classically controls X on wire 1"],
    ),
    "a Qcircuit vertical wire with no length goes up": (
        qcircuit("& \\qswap & \\qw", "& \\qswap \\qwx & \\qw"),
        ["Wires: 1; 2", "Step 1: SWAP wires 1 and 2"],
    ),
    "a Qcircuit gate with a wire to a control": (
        qcircuit("& \\sgate{Z}{1} & \\qw", "& \\control & \\qw"),
        ["Wires: 1; 2", "Step 1: controlled-Z: control wire 2, target wire 1"],
    ),
    "a label in the cell to the right of the dots": (
        qcircuit("& \\ctrl{1} & \\qw & \\qw", "& \\control & \\dstick{\\theta} & \\qw"),
        [
            "Wires: 1; 2",
            "Step 1: two-qubit gate drawn as control dots on wires 1 and 2 "
            "(symmetric, no target), labelled \\theta",
        ],
    ),
    "two operations in one cell are listed in order": (
        quantikz("& \\gate{H} \\meter{} & \\qw", "& \\gate{X} & \\qw"),
        ["Wires: 1; 2", "Step 1: H on wire 1; measure wire 1; X on wire 2"],
    ),
    "a permutation over as many wires as it lists": (
        quantikz("& \\permute{3,1,2} & \\qw", "& & \\qw", "& & \\qw"),
        [
            "Wires: 1; 2; 3",
            "Step 1: permutation 3,1,2 on wires 1, 2 and 3 (one gate on these wires)",
        ],
    ),
    "an output label over two wires": (
        qcircuit("& \\gate{H} & \\qw \\rstick[2]{out}", "& \\gate{X} & \\qw"),
        [
            "Wires: 1 = output out (one label for wires 1 and 2); 2",
            "Step 1: H on wire 1; X on wire 2",
        ],
    ),
    "a cnot of yquant with two controls is named as in a grid": (
        "\\begin{yquant}\nqubit q[4];\ncnot q[2] | q[0], q[1];\n"
        "cnot q[3] | q[0], q[1], q[2];\ncnot q[3] | q[0], ~q[1];\n\\end{yquant}",
        [
            "Wires: 1 = q[0]; 2 = q[1]; 3 = q[2]; 4 = q[3]",
            "Step 1: Toffoli (CCNOT): control wires 1 and 2, target wire 3",
            "Step 2: 3-controlled X: control wires 1, 2 and 3, target wire 4",
            "Step 3: 2-controlled X: control wire 1, open control wire 2, target wire 4",
        ],
    ),
    "a teleportation correction drawn with a classical wire in quantikz": (
        quantikz(
            "\\lstick{a} & \\ctrl{1} & \\meter{} & \\qw & \\qw",
            "\\lstick{b} & \\targ{} & \\qw & \\meter{} & \\qw",
            "\\lstick{c} & \\qw & \\qw & \\gate{X} \\wire[u][2]{c} & \\gate{Z} \\wire[u][1]{c}",
        ),
        [
            "Wires: 1 = a; 2 = b; 3 = c",
            "Step 1: CNOT: control wire 1, target wire 2",
            "Step 2: measure wire 1",
            "Step 3: measure wire 2; X on wire 3, classically controlled by wire 1",
            "Step 4: Z on wire 3, classically controlled by wire 2",
        ],
    ),
    "a teleportation correction drawn with a classical wire in Qcircuit": (
        qcircuit(
            "\\lstick{a} & \\ctrl{1} & \\meter & \\cw & \\cw",
            "\\lstick{b} & \\targ & \\qw & \\meter & \\cw",
            "\\lstick{c} & \\qw & \\qw & \\gate{X} \\cwx[-2] & \\gate{Z} \\cwx[-1]",
        ),
        [
            "Wires: 1 = a; 2 = b; 3 = c",
            "Step 1: CNOT: control wire 1, target wire 2",
            "Step 2: measure wire 1",
            "Step 3: measure wire 2; X on wire 3, classically controlled by wire 1",
            "Step 4: Z on wire 3, classically controlled by wire 2",
        ],
    ),
    "a measurement box over three wires": (
        quantikz("& \\meter[3]{} & \\qw", "& & \\qw", "& & \\qw", "& \\gate{H} & \\qw"),
        [
            "Wires: 1; 2; 3; 4",
            "Step 1: measure wires 1, 2 and 3 (one measurement over these wires); H on wire 4",
        ],
    ),
    "a measurement style option is not a span": (
        quantikz("& \\meter[fill=red]{} & \\qw"),
        ["Wires: 1", "Step 1: measure wire 1"],
    ),
    "a measurement box over wires whose result controls a gate": (
        quantikz("& \\meter[2]{} \\vcw{2} & \\qw", "& & \\qw", "& \\gate{X} & \\qw"),
        [
            "Wires: 1; 2; 3",
            "Step 1: measure wires 1 and 2 (one measurement over these wires); "
            "its result classically controls X on wire 3",
        ],
    ),
    "a teleportation correction with the classical wire written on the measured row in quantikz": (
        quantikz(
            "\\lstick{a} & \\ctrl{1} & \\meter{} & \\qw \\wire[d][2]{c} & \\qw",
            "\\lstick{b} & \\targ{} & \\qw & \\meter{} & \\qw \\wire[d][1]{c}",
            "\\lstick{c} & \\qw & \\qw & \\gate{X} & \\gate{Z}",
        ),
        [
            "Wires: 1 = a; 2 = b; 3 = c",
            "Step 1: CNOT: control wire 1, target wire 2",
            "Step 2: measure wire 1",
            "Step 3: X on wire 3, classically controlled by wire 1; measure wire 2",
            "Step 4: Z on wire 3, classically controlled by wire 2",
        ],
    ),
    "a teleportation correction with the classical wire written on the measured row in Qcircuit": (
        qcircuit(
            "\\lstick{a} & \\ctrl{1} & \\meter & \\cw \\cwx[2] & \\cw",
            "\\lstick{b} & \\targ & \\qw & \\meter & \\cw \\cwx[1]",
            "\\lstick{c} & \\qw & \\qw & \\gate{X} & \\gate{Z}",
        ),
        [
            "Wires: 1 = a; 2 = b; 3 = c",
            "Step 1: CNOT: control wire 1, target wire 2",
            "Step 2: measure wire 1",
            "Step 3: X on wire 3, classically controlled by wire 1; measure wire 2",
            "Step 4: Z on wire 3, classically controlled by wire 2",
        ],
    ),
}


@pytest.mark.parametrize(("source", "expected"), CASES.values(), ids=CASES)
def test_the_exact_list_of_each_construct(source, expected):
    assert lines(source) == expected
    assert gate_list(source).status == ("partial" if "unparsed" in "".join(expected) else "parsed")


def test_yquant_writes_one_step_per_statement():
    source = (
        "\\begin{yquant}\nqubit {$q$} q[3];\nh q[0];\ncnot q[1] | q[0];\n"
        "x q[2] | q[0], ~q[1];\nswap q[0], q[1];\nbox {$U$} q[2] | q[0];\nmeasure q[2];\n"
        "\\end{yquant}"
    )
    result = gate_list(source)
    assert result.status == "parsed"
    assert result.text.split("\n") == [
        "Gate list (from the circuit code above; wires numbered top to bottom; "
        "one step per statement, in order):",
        "Wires: 1 = q[0] (in q); 2 = q[1] (in q); 3 = q[2] (in q)",
        "Step 1: H on wire 1",
        "Step 2: CNOT: control wire 1, target wire 2",
        "Step 3: controlled-X: control wire 1, open control wire 2, target wire 3",
        "Step 4: SWAP wires 1 and 2",
        "Step 5: controlled-U: control wire 1, target wire 3",
        "Step 6: measure wire 3",
    ]


# ------------------------------------------------------------------ never guesses


def test_an_author_macro_is_written_as_unparsed_with_its_source_and_wire():
    source = quantikz("& \\myhadamard & \\qw", "& \\gate{X} & \\qw")
    result = gate_list(source)
    assert result.status == "partial"
    assert lines(source) == [
        "Wires: 1; 2",
        "Step 1: X on wire 2; unparsed on wire 1: \\myhadamard",
    ]


def test_an_arrow_in_a_tikzcd_circuit_is_unparsed_and_the_rest_is_read():
    source = "\\begin{tikzcd}\n\\lstick{a} & \\gate{H} \\ar[r] & \\qw\n\\end{tikzcd}"
    assert lines(source) == ["Wires: 1 = a", "Step 1: H on wire 1; unparsed on wire 1: \\ar[r]"]
    assert gate_list(source).status == "partial"


def test_a_circuit_made_only_of_macros_has_no_list():
    result = gate_list(quantikz("& \\foo & \\qux", "& \\quux & \\qw"))
    assert result.status == "unparsed" and result.text == ""


@pytest.mark.parametrize(
    "source",
    [
        "\\begin{quantikz}\n& \\gate{H} & \\qw",  # never closes
        "\\begin{quantikz}\n\\end{quantikz}",  # empty
        "\\Qcircuit @C=1em { & \\gate{H}",  # the group never closes
        "\\Qcircuit @C=1em \\\\ { & \\gate{H} }",  # a line break before the group
        "\\begin{yquant}\n\\end{yquant}",
    ],
)
def test_a_circuit_that_cannot_be_read_gets_no_list(source):
    result = gate_list(source)
    assert result.text == "" and result.status == "unparsed"


def test_a_wire_that_leads_outside_the_circuit_is_unparsed_not_a_gate():
    source = quantikz("& \\ctrl{5} & \\qw", "& \\gate{H} & \\qw")
    assert lines(source) == [
        "Wires: 1; 2",
        "Step 1: unparsed joined operation (wire 1: ctrl); H on wire 2; "
        "unparsed: a vertical wire or gate points outside the circuit",
    ]
    assert gate_list(source).status == "partial"


def test_a_control_with_no_target_is_not_called_a_gate():
    source = quantikz("& \\ctrl{1} & \\qw", "& & \\qw")
    assert gate_list(source).status == "unparsed"
    assert "controlled" not in gate_list(source).text


def test_dots_with_no_label_are_a_controlled_z_and_dots_with_one_are_not():
    plain = quantikz("& \\ctrl{1} & \\qw", "& \\ctrl{-1} & \\qw")
    named = quantikz("& \\ctrl{1} & \\qw", "& \\ctrl{-1}\\dstick{P(\\theta)} & \\qw")
    assert "controlled-Z" in gate_list(plain).text
    assert "controlled-Z" not in gate_list(named).text
    assert "labelled P(\\theta)" in gate_list(named).text


def test_a_count_too_long_to_be_real_is_unparsed():
    source = quantikz("& \\gate[" + "9" * 40 + "]{U} & \\qw", "& \\gate{H} & \\qw")
    assert "unparsed" in gate_list(source).text and gate_list(source).status == "partial"
    big = "\\begin{yquant}\nqubit a;\nqubit q[99999];\nh a;\n\\end{yquant}"
    assert "unparsed: qubit q[99999]" in gate_list(big).text


def test_a_gate_over_a_million_wires_is_written_as_a_range_not_listed():
    source = quantikz("& \\gate[999999]{G} & \\qw", *["& & \\qw"] * 29)
    [_, step] = lines(source)
    assert step == (
        "Step 1: G on wires 1 to 30 (one gate on these wires); "
        "unparsed: a vertical wire or gate points outside the circuit"
    )


def test_a_spacer_row_between_drawn_rows_is_not_a_wire():
    source = quantikz("\\lstick[wires=3]{$\\ket{x}$} & \\gate{H} & \\qw", "&", "& \\gate{X} & \\qw")
    assert lines(source)[0] == "Wires: 1 = \\ket{x} (one label for wires 1 and 2); 2"


# ------------------------------------------------------------------ labels, several circuits


def test_the_label_of_the_circuit_is_in_the_header():
    source = "\\begin{quantikz}\\label{fig:lamp}\n& \\gate{H} & \\qw\n\\end{quantikz}"
    first, *rest = gate_list(source).text.split("\n")
    assert first == (
        "Gate list for \\label{fig:lamp} (from the circuit code above; wires numbered top to "
        "bottom; one step per drawn column, left to right):"
    )
    assert rest == ["Wires: 1", "Step 1: H on wire 1"]


def test_a_label_inside_a_qcircuit_group_is_found_too():
    source = "\\Qcircuit @C=1em { \\label{eq:u} & \\gate{U} & \\qw }"
    assert gate_list(source).text.startswith("Gate list for \\label{eq:u} (")


def test_a_circuit_without_a_label_has_the_plain_header():
    assert gate_list(HADAMARDS).text.startswith(HEADER)


def test_several_circuits_are_listed_one_after_the_other_each_with_its_label():
    source = (
        "\\begin{quantikz}\\label{a}\n& \\gate{H} & \\qw\n\\end{quantikz}\n"
        "and then\n"
        "\\begin{quantikz}\n& \\gate{X} & \\qw\n\\end{quantikz}"
    )
    result = gate_list(source)
    assert result.n_circuits == 2 and result.status == "parsed"
    assert result.text.split("\n") == [
        HEADER,
        "Circuit 1 of 2 (\\label{a}):",
        "Wires: 1",
        "Step 1: H on wire 1",
        "Circuit 2 of 2:",
        "Wires: 1",
        "Step 1: X on wire 1",
    ]


def test_an_unreadable_circuit_beside_a_readable_one_is_named_and_the_status_is_partial():
    source = HADAMARDS + "\n" + quantikz("& \\foo & \\qw")
    result = gate_list(source)
    assert result.status == "partial"
    assert "Circuit 2 of 2: not read (unparsed)" in result.text.split("\n")


def test_a_circuit_inside_another_one_is_not_listed_twice():
    source = "\\begin{quantikz}\\begin{quantikz} & \\gate{H}\\end{quantikz}\\end{quantikz}"
    assert gate_list(source).n_circuits == 1


# ------------------------------------------------------------------ long circuits


def test_a_long_circuit_is_cut_at_the_step_limit_and_the_rest_is_counted():
    n = MAX_STEPS + 15
    source = quantikz("& " + " & ".join("\\gate{H}" for _ in range(n)))
    out = gate_list(source).text.split("\n")
    assert len(out) == 2 + MAX_STEPS + 1
    assert (
        out[2] == "Step 1: H on wire 1" and out[1 + MAX_STEPS] == f"Step {MAX_STEPS}: H on wire 1"
    )
    assert out[-1] == "... 15 more lines not listed"


def test_one_step_over_the_limit_is_cut_and_counted():
    source = quantikz("& " + " & ".join("\\gate{H}" for _ in range(MAX_STEPS + 1)))
    out = gate_list(source).text.split("\n")
    assert out[-1] == "... 1 more line not listed" and len(out) == 2 + MAX_STEPS + 1


def test_a_circuit_at_the_limit_is_listed_whole():
    source = quantikz("& " + " & ".join("\\gate{H}" for _ in range(MAX_STEPS)))
    assert not gate_list(source).text.endswith("not listed")


# ------------------------------------------------------------------ code blocks of a document

CIRCUIT = quantikz("& \\gate{H} & \\qw")
LIST_H = "\n".join([HEADER, "Wires: 1", "Step 1: H on wire 1"])


def test_code_blocks_are_the_fenced_latex_blocks_with_their_offsets():
    text = f"Before.\n\n``` latex\n{CIRCUIT}\n```\n\nAfter."
    [block] = circuit_code_blocks(text)
    assert block.source == CIRCUIT
    assert text[block.start : block.end] == f"``` latex\n{CIRCUIT}\n```"


def test_the_fence_may_have_no_space_and_other_languages_are_skipped():
    text = f"```latex\n{CIRCUIT}\n```\n\n``` python\nx = 1\n```\n\n```\nplain\n```\n"
    assert [b.source for b in circuit_code_blocks(text)] == [CIRCUIT]


def test_a_latex_fence_inside_another_block_is_not_an_opening():
    text = "``` python\n```latex\nnot a block\n```\n\n``` latex\nreal\n```\n"
    assert [b.source for b in circuit_code_blocks(text)] == ["real"]


def test_a_block_with_a_longer_fence_ends_at_a_fence_as_long():
    text = "```` latex\nsome ``` inside\n````\nafter"
    assert [b.source for b in circuit_code_blocks(text)] == ["some ``` inside"]


def test_a_closing_fence_must_be_as_long_as_the_opening_one():
    text = "```` latex\nA\n```\nB\n````\nafter"
    [block] = circuit_code_blocks(text)
    assert block.source == "A\n```\nB" and text[block.end :] == "\nafter"


def test_a_block_that_never_closes_is_left_out():
    assert circuit_code_blocks(f"``` latex\n{CIRCUIT}\nno end") == []


def test_two_blocks_in_a_row_and_an_empty_one():
    text = "``` latex\na\n```\n``` latex\n```\n``` latex\nb\n```"
    assert [b.source for b in circuit_code_blocks(text)] == ["a", "", "b"]


def document(*parts: str) -> str:
    return "".join(parts)


def test_a_chunk_gets_the_list_right_after_the_closing_fence():
    doc = f"Intro.\n\n``` latex\n{CIRCUIT}\n```\n\nCaption."
    blocks = circuit_code_blocks(doc)
    start = doc.index("``` latex") - 5
    chunk = doc[start:]
    assert add_gate_lists(chunk, start, doc, blocks) == (
        f"ro.\n\n``` latex\n{CIRCUIT}\n```\n\n{LIST_H}\n\n\nCaption."
    )


def test_a_block_that_began_in_the_chunk_before_gets_the_list_of_the_whole_block():
    doc = f"Intro.\n\n``` latex\n{CIRCUIT}\n```\n\nCaption."
    blocks = circuit_code_blocks(doc)
    cut = doc.index("& \\gate{H}")  # the chunk starts inside the block
    chunk = doc[cut:]
    out = add_gate_lists(chunk, cut, doc, blocks)
    assert out.startswith("& \\gate{H}") and LIST_H in out
    assert out.index("```\n\n" + LIST_H) > out.index("\\end{quantikz}")


def test_a_chunk_that_starts_where_a_block_ends_gets_no_list_for_that_block():
    # the chunk after a block that ended exactly at its start holds another circuit: the first
    # list would sit above a different circuit
    first = f"``` latex\n{CIRCUIT}\n```"
    second = "``` latex\n" + quantikz("& \\ctrl{1} & \\qw", "& \\ctrl{-1} & \\qw") + "\n```"
    doc = f"{first}\n\nMIDDLE\n\n{second}\n\nTAIL"
    blocks = circuit_code_blocks(doc)
    start = blocks[0].end
    chunk = doc[start:]
    out = add_gate_lists(chunk, start, doc, blocks)
    assert out.count("Gate list") == 1 and "controlled-Z" in out
    assert out.index("MIDDLE") < out.index("Gate list") and "Wires: 1 = " not in out
    # the chunk before ends with the first block and does get its list
    before = doc[:start]
    assert add_gate_lists(before, 0, doc, blocks) == f"{before}\n\n{LIST_H}\n"


def test_a_block_cut_by_the_end_of_the_chunk_gets_no_list_in_it():
    doc = f"Intro.\n\n``` latex\n{CIRCUIT}\n```\n\nCaption."
    blocks = circuit_code_blocks(doc)
    chunk = doc[: doc.index("\\end{quantikz}")]
    assert add_gate_lists(chunk, 0, doc, blocks) == chunk


def test_a_chunk_ending_exactly_at_the_closing_fence_gets_the_list():
    doc = f"``` latex\n{CIRCUIT}\n```\n\nCaption."
    blocks = circuit_code_blocks(doc)
    chunk = doc[: blocks[0].end]
    assert add_gate_lists(chunk, 0, doc, blocks) == f"{chunk}\n\n{LIST_H}\n"


def test_two_blocks_in_one_chunk_each_get_their_list():
    doc = f"``` latex\n{CIRCUIT}\n```\nMIDDLE\n``` latex\n{CIRCUIT}\n```\nTAIL"
    out = add_gate_lists(doc, 0, doc, circuit_code_blocks(doc))
    assert out.count(LIST_H) == 2
    assert out.index(LIST_H) < out.index("MIDDLE") < out.rindex(LIST_H) < out.index("TAIL")


def test_a_block_with_nothing_readable_changes_nothing():
    doc = "``` latex\nx = 1\n```\n"
    assert add_gate_lists(doc, 0, doc, circuit_code_blocks(doc)) == doc
    macros = f"``` latex\n{quantikz('& \\foo & \\qux')}\n```\n"
    assert add_gate_lists(macros, 0, macros, circuit_code_blocks(macros)) == macros


def test_a_chunk_that_is_not_where_it_says_is_returned_as_it_is():
    doc = f"``` latex\n{CIRCUIT}\n```\nend"
    blocks = circuit_code_blocks(doc)
    assert add_gate_lists(doc, 1, doc, blocks) == doc  # wrong offset
    assert add_gate_lists(doc + "x", 0, doc, blocks) == doc + "x"  # longer than the document


def test_a_chunk_with_no_block_is_returned_unchanged():
    doc = "No circuit here.\n\n" + f"``` latex\n{CIRCUIT}\n```"
    assert add_gate_lists("No circuit", 0, doc, circuit_code_blocks(doc)) == "No circuit"


def test_blocks_given_by_hand_are_used_as_they_are():
    doc = "abc"
    assert add_gate_lists("abc", 0, doc, [CodeBlock(0, 99, "x")]) == "abc"


# ------------------------------------------------------------------ no input raises

VOCAB = [
    "\\begin{quantikz}", "\\end{quantikz}", "\\Qcircuit @C=1em {", "}", "{", "[", "]", "&", "\\\\",
    "\\gate{H}", "\\gate[2]{U}", "\\gate[wires=3][1cm]{V}", "\\ctrl{1}", "\\ctrl{-1}",
    "\\ctrl{9}", "\\octrl{1}", "\\targ{}", "\\targ", "\\swap{1}", "\\targX{}", "\\qswap",
    "\\qwx[-1]", "\\cwx[1]", "\\vcw{1}", "\\vqw{-1}", "\\meter{}", "\\meterD{Z}", "\\measure{X}",
    "\\lstick{$\\ket{0}$}", "\\lstick[wires=2]{a}", "\\rstick{b}", "\\qwbundle{3}", "\\qw", "\\cw",
    "\\multigate{1}{U}", "\\ghost{U}", "\\sgate{U}{1}", "\\dstick{\\theta}", "\\push{=}",
    "\\phase{\\phi}", "\\wire[d][2]", "\\wire[u][1]{c}", "\\slice{s}", "\\quxrier{x}", "\\equiv",
    "\\vdots", "\\cdots", "\\macro", "\\ar[r]", "\\cwbend{1}", "\\mbox{\\gate{Z}}", "\\label{l}",
    "% note\n", "\\%", "\\\\[2mm]", "$", "text", "\\alpha", "\\begin{yquant}", "\\end{yquant}",
    "qubit q[2];", "h q[0];", "cnot q[1] | q[0];", "box {X} q[0] | ~q[1];", "\\begin{tikzcd}",
    "\\end{tikzcd}", "\\gate[", "\\gate{", "\\", "\\gategroup[2,steps=3]{x}",
    "\\gategroup{1}{1}{2}{2}{1em}{--}", "99999999999999999999", "\n", " ",
]  # fmt: skip

ITEM_WORDS = (
    "unparsed",
    " on wire",
    "CNOT",
    "Toffoli",
    "-controlled X",
    "controlled-",
    "SWAP",
    "measure",
    "text ",
    "dots on",
    "discarded",
    "barrier",
    "sign ",
    "two-qubit gate",
    "lone control dot",
    "phase ",
)


def check_shape(text: str) -> None:
    """Every line of a list is the header, a wires line, a circuit title, a part marker, the
    cut notice or a step, and every step names an operation or says ``unparsed``."""
    if not text:
        return
    first, *rest = text.split("\n")
    assert first.startswith("Gate list ")
    for line in rest:
        if line.startswith("Step "):
            body = line.split(": ", 1)[1]
            assert any(word in body for word in ITEM_WORDS), line
        else:
            assert re.match(r"(Wires: |Circuit \d+ of \d+|\[|\.\.\. \d+ more lines)", line), line


FIXTURES = [source for source, _ in CASES.values()]


def test_every_prefix_of_every_case_is_read_without_raising_and_has_a_known_shape():
    for source in FIXTURES:
        for cut in range(len(source) + 1):
            check_shape(gate_list(source[:cut]).text)


def test_random_mixes_of_circuit_pieces_never_raise_and_keep_the_shape():
    rng = random.Random(20261013)
    for _ in range(1500):
        pieces = [rng.choice(VOCAB) for _ in range(rng.randint(1, 40))]
        glue = rng.choice(["", " ", "\n"])
        body = glue.join(pieces)
        for source in (
            f"\\begin{{quantikz}}{body}\\end{{quantikz}}",
            f"\\Qcircuit @C=1em {{{body}}}",
            f"\\begin{{tikzcd}}{body}\\end{{tikzcd}}",
            f"\\begin{{yquant}}{body}\\end{{yquant}}",
            body,
        ):
            check_shape(gate_list(source).text)


def test_random_edits_of_real_shapes_never_raise_and_keep_the_shape():
    rng = random.Random(7)
    for source in FIXTURES:
        for _ in range(40):
            chars = list(source)
            for _ in range(rng.randint(1, 4)):
                i = rng.randrange(len(chars))
                op = rng.choice(["drop", "dup", "swap", "insert"])
                if op == "drop":
                    del chars[i]
                elif op == "dup":
                    chars.insert(i, chars[i])
                elif op == "swap" and i + 1 < len(chars):
                    chars[i], chars[i + 1] = chars[i + 1], chars[i]
                else:
                    chars.insert(i, rng.choice("{}[]&\\$%;|~ \n"))
            check_shape(gate_list("".join(chars)).text)


def test_circuit_code_blocks_and_chunks_never_raise_on_odd_text():
    rng = random.Random(3)
    pieces = ["```", "``` latex", "```latex", "````", "\n", " ", "x", "``` python", quantikz("&")]
    for _ in range(300):
        text = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 30)))
        blocks = circuit_code_blocks(text)
        for block in blocks:
            assert text[block.start : block.end].startswith("`")
        start = rng.randint(0, len(text))
        chunk = text[start : start + rng.randint(0, 60)]
        out = add_gate_lists(chunk, start, text, blocks)
        assert out.startswith(chunk[:1]) or not chunk


# ------------------------------------------------------------------ linear time

N = 6_000


def seconds(fn, text) -> float:
    """Best of 3 runs in CPU time, with the cyclic collector off: with a big heap (the other
    tests of a session import PaperQA2) collections cost more per allocation than with a small
    one, which would make a linear scan look slower on the larger input."""
    best = float("inf")
    collecting = gc.isenabled()
    gc.disable()
    try:
        for _ in range(3):
            t0 = time.process_time()  # CPU time: waiting for a busy CPU is not counted
            fn(text)
            best = min(best, time.process_time() - t0)
    finally:
        if collecting:
            gc.enable()
    return best


def row(cells: int, cell: str = "\\gate{H}") -> str:
    return " & ".join([cell] * cells)


UNITS = {
    "many columns": lambda n: quantikz(row(n)),
    "many rows": lambda n: quantikz(*["\\gate{H} & \\qw"] * n),
    "one long row and many short ones": lambda n: quantikz(row(n), *["\\gate{X}"] * n),
    "a chain of cnots": lambda n: quantikz(*["& \\ctrl{1} & \\qw"] * n),
    "every control reaches halfway down": lambda n: quantikz(
        *[f"& \\ctrl{{{n // 2}}}"] * (n // 2), *["& \\targ{}"] * (n // 2)
    ),
    "every gate spans every wire": lambda n: quantikz(*[f"& \\gate[{n}]{{U}}"] * n),
    "every label spans every wire": lambda n: quantikz(*[f"\\lstick[wires={n}]{{a}} & \\qw"] * n),
    "a wire through every row": lambda n: quantikz(*[f"& \\ctrl{{{n - 1}}} & \\ctrl{{1}}"] * n),
    "grid of cells": lambda n: quantikz(*[row(60)] * (n // 60)),
    "options that never close": lambda n: quantikz("& " + "\\gate[ " * n),
    "line breaks with options that never close": lambda n: (
        quantikz("& \\gate{H}") + "\\\\[ " * (n * 80)
    ),
    "groups that never close": lambda n: quantikz("& " + "\\gate{ " * n),
    "nested wrappers": lambda n: quantikz("& " + "\\mbox{" * n + "\\gate{H}" + "}" * n),
    "bare groups": lambda n: quantikz("& " + "{" * n + "\\gate{H}" + "}" * n),
    "a long text cell": lambda n: quantikz("& " + "word " * n),
    "text and math runs": lambda n: quantikz("& " + "\\alpha a " * n),
    "backslash pairs": lambda n: quantikz("& " + "\\\\" * (n * 80)),
    "comments": lambda n: quantikz("& \\gate{H} % x\n" * n),
    "unknown commands with groups": lambda n: quantikz("& " + "\\foo{a}{b}[c] " * n),
    "environments that never close": lambda n: "\\begin{quantikz}\n" * n,
    "environments inside one": lambda n: "\\begin{quantikz}" * n + "& \\gate{H}\\end{quantikz}",
    "closed circuits one after the other": lambda n: quantikz("& \\gate{H}") * n,
    "Qcircuit with no group": lambda n: "\\Qcircuit " * n,
    "Qcircuit groups that never close": lambda n: "\\Qcircuit { & \\gate{H} " * n,
    "Qcircuit groups inside one": lambda n: "\\Qcircuit {" * n + "& \\gate{H}" + "}" * n,
    "yquant statements": lambda n: "\\begin{yquant}qubit q[2];" + "h q[0];" * n + "\\end{yquant}",
    "yquant unknown statements": lambda n: "\\begin{yquant}" + "foo q[0];" * n + "\\end{yquant}",
    "yquant registers": lambda n: "\\begin{yquant}" + "qubit {a} q[100];" * n + "\\end{yquant}",
    "yquant braces": lambda n: "\\begin{yquant}h {" + "{" * (n * 160) + "} q[0];\\end{yquant}",
}


@pytest.mark.parametrize("make", UNITS.values(), ids=UNITS)
def test_reading_is_linear_in_the_size_of_the_source(make):
    small, big = seconds(gate_list, make(N // 4)), seconds(gate_list, make(N))
    assert big < 10
    assert big < max(8 * small, 0.05), (small, big)


BLOCK_UNITS = {
    "fences that never close": lambda n: "``` latex\n" * (n * 60),
    "closed blocks": lambda n: "``` latex\nx\n```\n" * n,
    "other languages": lambda n: "``` python\nx\n```\n" * n,
    "openings inside a block": lambda n: "``` python\n" + "```latex\n" * (n * 60) + "```\n",
    "long fences": lambda n: "`" * (n * 6000) + "\n",
    "lines": lambda n: "x\n" * (n * 60),
}


@pytest.mark.parametrize("make", BLOCK_UNITS.values(), ids=BLOCK_UNITS)
def test_finding_the_code_blocks_is_linear_in_the_size_of_the_text(make):
    small = seconds(circuit_code_blocks, make(N * 2 // 4))
    big = seconds(circuit_code_blocks, make(N * 2))
    assert big < max(8 * small, 0.05), (small, big)


def test_adding_lists_to_a_chunk_costs_in_proportion_to_the_blocks_it_holds():
    def work(n: int) -> str:
        doc = f"``` latex\n{CIRCUIT}\n```\ntext\n" * n
        blocks = circuit_code_blocks(doc)
        return add_gate_lists(doc, 0, doc, blocks)

    small, big = seconds(work, N // 8), seconds(work, N // 2)
    assert big < max(8 * small, 0.05), (small, big)


def test_the_list_of_a_long_circuit_is_not_longer_than_a_multiple_of_its_source():
    source = quantikz(*["& \\gate{H} & \\ctrl{1} & \\qw", "& \\qw & \\targ{} & \\qw"] * 400)
    assert len(gate_list(source).text) < 3 * len(source)


# ------------------------------------------------------------------ groups the list does not know


def test_a_measurement_group_with_a_quantum_control_is_not_read_as_a_classical_one():
    source = quantikz(
        "& \\meter{} \\vcw{1} & \\gate{Z}", "& \\gate{X} & \\qw", "& \\ctrl{-1} & \\qw"
    )
    assert lines(source) == [
        "Wires: 1; 2; 3",
        "Step 1: unparsed joined operation (wire 1: meter, wire 2: gate X, wire 3: ctrl)",
        "Step 2: Z on wire 1",
    ]
    assert gate_list(source).status == "partial"


def test_a_measurement_joined_to_a_gate_without_a_classical_wire_is_not_read():
    source = qcircuit("& \\sgate{U}{1} & \\gate{Z}", "& \\meter & \\qw")
    assert lines(source) == [
        "Wires: 1; 2",
        "Step 1: unparsed joined operation (wire 1: gate U, wire 2: meter)",
        "Step 2: Z on wire 1",
    ]


def test_two_cnots_drawn_over_one_another_are_not_read_as_one_gate():
    source = qcircuit(
        "& \\ctrl{2} & \\qw",
        "& \\ctrl{2} & \\qw",
        "& \\targ & \\qw",
        "& \\targ & \\qw",
        "& \\gate{H} & \\qw",
    )
    [_, step] = lines(source)
    assert step.startswith("Step 1: unparsed joined operation")
    assert "Toffoli" not in step


def test_a_fan_out_of_one_control_to_two_targets_is_still_read():
    source = qcircuit("& \\ctrl{1} & \\qw", "& \\targ \\qwx[1] & \\qw", "& \\targ & \\qw")
    assert lines(source)[1] == "Step 1: CNOT: control wire 1, targets wires 2 and 3"


def test_a_register_name_that_was_not_declared_is_unparsed():
    source = "\\begin{yquant}\nqubit q[2];\nh q[5];\nh q[0];\n\\end{yquant}"
    assert lines(source) == [
        "Wires: 1 = q[0]; 2 = q[1]",
        "Step 1: unparsed: h q[5]",
        "Step 2: H on wire 1",
    ]


def test_a_percent_escaped_in_a_label_is_not_a_comment():
    source = "\\begin{quantikz}\\label{eq\\%x}\n& \\gate{H} & \\qw\n\\end{quantikz}"
    assert gate_list(source).text.startswith("Gate list for \\label{eq\\%x} (")
    assert lines(source) == ["Wires: 1", "Step 1: H on wire 1"]


def test_a_quantikz_row_shorter_than_the_widest_stays_a_spacer_and_so_does_tikzcd():
    spacer = quantikz("& \\gate{H} & \\qw", "&", "& \\gate{X} & \\qw")
    assert lines(spacer) == ["Wires: 1; 2", "Step 1: H on wire 1; X on wire 2"]
    tikz = "\\begin{tikzcd}\n\\lstick{a} & \\gate{H} & \\qw \\\\\n& & \\\\\n\\lstick{b} & \\gate{X} & \\qw\n\\end{tikzcd}"
    assert lines(tikz) == ["Wires: 1 = a; 2 = b", "Step 1: H on wire 1; X on wire 2"]


def test_a_quantum_wire_to_a_row_with_nothing_leaves_the_gate_unread():
    for source in (
        quantikz("& \\qw & \\qw", "& \\gate{X} \\vqw{-1} & \\qw", "& \\gate{H} & \\qw"),
        qcircuit("& \\qw & \\qw", "& \\gate{X} \\qwx[-1] & \\qw", "& \\gate{H} & \\qw"),
    ):
        text = gate_list(source).text
        assert "X on wire 2" not in text
        assert "unparsed joined operation" in text and "H on wire 3" in text
        assert gate_list(source).status == "partial"


def test_a_classical_wire_to_a_row_that_is_not_drawn_is_still_a_condition():
    source = quantikz("& \\qw & \\qw", "&", "& \\gate{X} \\wire[u][1]{c} & \\qw")
    assert lines(source) == ["Wires: 1; 2", "Step 1: X on wire 2, classically controlled"]


def test_a_measurement_box_that_reaches_past_the_circuit_is_flagged():
    source = quantikz("& \\meter[5]{} & \\gate{Z}", "& \\gate{H} & \\qw")
    assert "points outside the circuit" in gate_list(source).text
