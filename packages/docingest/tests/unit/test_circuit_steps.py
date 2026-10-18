"""The gate list as data (domain.circuits.GateList.circuits): every circuit here is invented.

The text of a gate list stays what it was; ``circuits`` says the same thing as plain fields. The
first half writes the text again from those fields alone (``render_lines`` below, which shares no
code with the reader except ``wires_text``) and compares it with ``gate_list(...).text`` on every
construct of the text tests, on every prefix of each, and on random mixes and edits of them. A
wire, a label, a flag or a kind that the fields get wrong makes a line differ. The second half
checks the fields of the constructs one by one, ``CircuitSteps.to_dict`` and the size of the data.

Three wordings say one thing in the text: a classical control written as dots in the column
(``controlled-X (classically controlled): control wire 1, target wire 2``), as a wire drawn from a
row with nothing in the column (``X on wire 2, classically controlled by wire 1``), and as the
result of a measurement beside it (``measure wire 1; its result classically controls X on wire
2``). The fields are the same for the first two, so a line is allowed to be any of the wordings the
fields can give; every wire, label and flag in it must still come from the fields.
"""

import json
import random

import pytest
from test_circuits import CASES, FIXTURES, VOCAB, qcircuit, quantikz

from docingest.domain.circuits import (
    CIRCUIT_FORMAT,
    MAX_GATE_WIRES,
    MAX_STEPS,
    CircuitSteps,
    GateList,
    GateOp,
    gate_list,
    wires_text,
)

# ------------------------------------------------------------------ the text, written from the data


def W(numbers) -> str:
    return wires_text(sorted(set(numbers)))


def controls_words(closed, opened) -> str:
    parts = []
    if closed:
        parts.append(("control " if len(closed) == 1 else "controls ") + W(closed))
    if opened:
        parts.append(
            ("open control (on |0>) " if len(opened) == 1 else "open controls (on |0>) ")
            + W(opened)
        )
    return "; ".join(parts)


def x_name(controls: int, opened: bool) -> str:
    if opened and controls > 1:
        return f"{controls}-controlled X"
    return {1: "CNOT", 2: "Toffoli (CCNOT)"}.get(controls, f"{controls}-controlled X")


def wired_words(op: GateOp) -> str:
    """The ending of a gate whose classical control is a wire drawn to another row."""
    return ", classically controlled" + (f" by wire {op.controls[0]}" if op.controls else "")


def can_be_wired(op: GateOp) -> bool:
    return op.classical and len(op.controls) <= 1 and not op.open_controls


def several(note: str, wires: int) -> list[str]:
    """The note of an item drawn over several rows. With two wires or more it is always there;
    with fewer, some of the rows it covers draw no wire, and it may or may not be."""
    return [f" ({note})"] if wires > 1 else ["", f" ({note})"]


def gate_words(op: GateOp, cl: str, ctrl: str, wired: str) -> list[tuple[str, str]]:
    alone = [
        (f"{op.label} on {W(op.targets)}{note}", wired)
        for note in several("one gate on these wires", len(op.targets))
    ]
    if not (op.controls or op.open_controls):
        return alone
    forms = [(f"controlled-{op.label}{cl}: {ctrl}, target {W(op.targets)}", "")]
    return forms + (alone if can_be_wired(op) else [])


def x_words(op: GateOp, cl: str, ctrl: str, wired: str) -> list[tuple[str, str]]:
    alone = [(f"X (NOT, drawn as \\targ) on {W(op.targets)}", wired)]
    if not (op.controls or op.open_controls):
        return alone
    name = x_name(len(op.controls) + len(op.open_controls), bool(op.open_controls))
    tgt = ("target " if len(op.targets) == 1 else "targets ") + W(op.targets)
    return [(f"{name}{cl}: {ctrl}, {tgt}", "")] + (alone if can_be_wired(op) else [])


def phase_words(op: GateOp, cl: str, ctrl: str, wired: str) -> list[tuple[str, str]]:
    alone = [(f"phase {op.label} on {W(op.targets)}", wired)]
    if not (op.controls or op.open_controls) and len(op.targets) == 1:
        return alone
    everything = [*op.controls, *op.open_controls, *op.targets]
    forms = [(f"controlled-phase {op.label}{cl} on {W(everything)} (symmetric dots)", "")]
    return forms + (alone if can_be_wired(op) and len(op.targets) == 1 else [])


def grid_words(op: GateOp) -> list[tuple[str, str]]:
    """The ways the line of a grid item can read, from its fields: the words, and the ending of a
    classical wire drawn to another row, which comes after a label drawn next to the item."""
    cl = " (classically controlled)" if op.classical else ""
    ctrl = controls_words(op.controls, op.open_controls)
    wired = wired_words(op) if op.classical else ""
    with_wire = {"gate": gate_words, "x": x_words, "phase": phase_words}
    if op.kind in with_wire:
        return with_wire[op.kind](op, cl, ctrl, wired)
    return [(PLAIN[op.kind](op, cl, ctrl), "")]


def z_dots_words(op: GateOp, cl: str, ctrl: str) -> str:
    if op.open_controls:
        return f"controlled-Z with open controls{cl}: {ctrl} (dots only, no target)"
    n = len(op.controls)
    name = "controlled-Z" if n == 2 else f"multi-controlled Z ({n} wires)"
    both = "both" if n == 2 else "all"
    return f"{name}{cl} on {W(op.controls)} (control dots on {both}, symmetric: no target)"


def swap_words(op: GateOp, cl: str, ctrl: str) -> str:
    if not (op.controls or op.open_controls):
        return f"SWAP {W(op.targets)}"
    return f"controlled-SWAP of {W(op.targets)} ({ctrl}){cl}"


# the kinds whose words depend on nothing but their fields: (op, classical note, controls) -> line
PLAIN = {
    "control": lambda op, cl, ctrl: (
        f"lone control dot on {W(op.controls)} (no gate joined to it in this column)"
    ),
    "z_dots": z_dots_words,
    "dots_gate": lambda op, cl, ctrl: (
        f"two-qubit gate drawn as control dots on {W(op.targets)} (symmetric, no target), "
        f"labelled {op.label}"
    ),
    "swap": swap_words,
    "text": lambda op, cl, ctrl: f"text {op.label} on {W(op.targets)}",
    "dots": lambda op, cl, ctrl: f"dots on {W(op.targets)}",
    "discard": lambda op, cl, ctrl: f"{W(op.targets)} discarded",
    "barrier": lambda op, cl, ctrl: "barrier" + (f" {op.label}" if op.label else ""),
    "sign": lambda op, cl, ctrl: f"sign {op.label} on {W(op.targets)}",
    "unparsed": lambda op, cl, ctrl: f"unparsed on {W(op.targets)}: {op.source}",
    "unparsed_op": lambda op, cl, ctrl: f"unparsed: {op.source} on {W(op.targets)}",
    "unparsed_group": lambda op, cl, ctrl: f"unparsed joined operation ({op.source})",
    "unparsed_outside": lambda op, cl, ctrl: (
        "unparsed: a vertical wire or gate points outside the circuit"
    ),
}


def measure_words(op: GateOp) -> list[str]:
    return [
        f"measure {W(op.targets)}" + (f" ({op.label})" if op.label else "") + note
        for note in several("one measurement over these wires", len(op.targets))
    ]


def yquant_controls(op: GateOp) -> str:
    both = [
        f"control {W(op.controls)}" if op.controls else "",
        f"open control {W(op.open_controls)}" if op.open_controls else "",
    ]
    return ", ".join(x for x in both if x) + f", target {W(op.targets)}"


def yquant_words(op: GateOp) -> list[str]:
    k = op.kind
    controlled = bool(op.controls or op.open_controls)
    targets = W(op.targets) if op.targets else ""
    if k == "gate":
        return [
            f"controlled-{op.label}: {yquant_controls(op)}"
            if controlled
            else f"{op.label} on {targets}"
        ]
    if k == "x":
        name = x_name(len(op.controls) + len(op.open_controls), bool(op.open_controls))
        return [f"{name}: {yquant_controls(op)}" if controlled else f"X on {targets}"]
    if k == "swap":
        return [f"SWAP {targets}" + (f" ({yquant_controls(op)})" if controlled else "")]
    if k in ("measure", "barrier", "discard"):
        return [f"{k} {targets}"]
    if k == "unparsed":
        return [f"unparsed: {op.source}"]
    raise AssertionError(f"unknown kind {k}")


def tag_words(op: GateOp) -> str:
    """The label drawn next to an item, which the line puts after the words of the item."""
    return f", labelled {op.source}" if op.source and not op.kind.startswith("unparsed") else ""


def item_words(op: GateOp, yquant: bool) -> list[str]:
    if yquant:
        return yquant_words(op)
    if op.kind == "measure":
        return measure_words(op)
    return [core + tag_words(op) + wired for core, wired in grid_words(op)]


def step_words(ops: tuple[GateOp, ...], yquant: bool) -> set[str]:
    """Every line the items of a step can make (items joined with "; ")."""
    memo: dict[int, set[str]] = {len(ops): {""}}

    def after(i: int) -> set[str]:
        if i in memo:
            return memo[i]
        op = ops[i]
        options = [(1, w) for w in item_words(op, yquant)]
        nxt = ops[i + 1] if i + 1 < len(ops) else None
        if (
            not yquant
            and op.kind == "measure"
            and nxt is not None
            and nxt.kind in ("gate", "x")
            and nxt.classical
            and nxt.controls == op.targets
            and not nxt.open_controls
        ):
            name = nxt.label if nxt.kind == "gate" else "X (\\targ)"
            for measured in measure_words(op):
                driven = f"; its result classically controls {name} on {W(nxt.targets)}"
                options.append((2, measured + driven + tag_words(nxt)))
        found: set[str] = set()
        for used, words in options:
            for rest in after(i + used):
                found.add(words + ("; " + rest if i + used < len(ops) else ""))
        memo[i] = found
        return found

    return after(0)


# the signs after which the list says "an equivalent circuit" (the ones quantum-sim compares)
EQUALITY_SIGNS = {"=", "\\equiv", "\\simeq", "\\cong", "\\Leftrightarrow"}


def render_lines(result: GateList) -> list[set[str]]:
    """The lines of the text, from ``result.circuits`` only; each is the set of lines the fields
    can give."""
    circs = result.circuits
    if not circs or all(c.status == "unparsed" for c in circs):
        return []
    unit = (
        "one step per statement, in order"
        if all(c.kind == "yquant" for c in circs)
        else "one step per drawn column, left to right"
    )
    header = f"Gate list (from the circuit code above; wires numbered top to bottom; {unit}):"
    if len(circs) == 1 and circs[0].label:
        header = header.replace("Gate list (", f"Gate list for \\label{{{circs[0].label}}} (", 1)
    lines = [{header}]
    for i, c in enumerate(circs):
        if len(circs) > 1:
            tag = f" (\\label{{{c.label}}})" if c.label else ""
            note = " not read (unparsed)" if c.status == "unparsed" else ""
            lines.append({f"Circuit {i + 1} of {len(circs)}{tag}:{note}"})
        if c.status == "unparsed":
            continue
        lines.append({"Wires: " + "; ".join(c.wires)})
        steps: list[set[str]] = []
        for part in c.parts:
            number = 0
            for step in part:
                if len(step) == 1 and step[0].kind == "sep":
                    sign = step[0].label
                    claim = (
                        "an equivalent circuit"
                        if sign in EQUALITY_SIGNS
                        else "after an arrow or approximation sign, not compared"
                    )
                    steps.append({f"[{sign}: the steps below draw the right-hand side, {claim}]"})
                    number = 0
                    continue
                number += 1
                steps.append({f"Step {number}: {w}" for w in step_words(step, c.kind == "yquant")})
        if len(steps) > MAX_STEPS:
            more = len(steps) - MAX_STEPS
            steps = [
                *steps[:MAX_STEPS],
                {f"... {more} more {'line' if more == 1 else 'lines'} not listed"},
            ]
        lines += steps
    return lines


def assert_text_from_data(source: str) -> None:
    result = gate_list(source)
    assert len(result.circuits) == result.n_circuits
    lines = render_lines(result)
    actual = result.text.split("\n") if result.text else []
    assert len(lines) == len(actual), (source, actual)
    for allowed, line in zip(lines, actual, strict=True):
        assert line in allowed, (source, line, allowed)


# ------------------------------------------------------------------ the text from the data


@pytest.mark.parametrize("source", FIXTURES, ids=list(CASES))
def test_the_text_of_each_construct_is_written_again_from_the_data(source):
    assert_text_from_data(source)


def test_the_text_is_written_again_from_the_data_for_every_prefix_of_every_construct():
    for source in FIXTURES:
        for cut in range(len(source) + 1):
            assert_text_from_data(source[:cut])


def test_the_text_is_written_again_from_the_data_for_random_mixes_and_edits():
    rng = random.Random(20261013)
    for _ in range(1500):
        body = rng.choice(["", " ", "\n"]).join(
            rng.choice(VOCAB) for _ in range(rng.randint(1, 40))
        )
        for source in (
            f"\\begin{{quantikz}}{body}\\end{{quantikz}}",
            f"\\Qcircuit @C=1em {{{body}}}",
            f"\\begin{{tikzcd}}{body}\\end{{tikzcd}}",
            f"\\begin{{yquant}}{body}\\end{{yquant}}",
            body,
        ):
            assert_text_from_data(source)
    rng = random.Random(7)
    for source in FIXTURES:
        for _ in range(40):
            chars = list(source)
            for _ in range(rng.randint(1, 4)):
                i = rng.randrange(len(chars))
                chars.insert(i, rng.choice("{}[]&\\$%;|~ \n"))
            assert_text_from_data("".join(chars))


# ------------------------------------------------------------------ the fields of each construct


def ops(source: str) -> list[tuple]:
    """The items of the first circuit, step by step, as plain tuples."""
    [circuit] = gate_list(source).circuits
    return [
        [
            (o.kind, o.label, o.targets, o.controls, o.open_controls, o.classical, o.source)
            for o in step
        ]
        for part in circuit.parts
        for step in part
    ]


def test_a_single_gate_is_a_gate_on_its_wire_with_its_label_as_written():
    assert ops(quantikz("& \\gate{R_y(\\pi/2)} & \\qw", "& \\gate{T^\\dagger} & \\qw")) == [
        [
            ("gate", "R_y(\\pi/2)", (1,), (), (), False, ""),
            ("gate", "T^\\dagger", (2,), (), (), False, ""),
        ]
    ]


def test_cnot_toffoli_and_open_controls_name_controls_and_targets_by_wire():
    assert ops(quantikz("& \\targ{} & \\qw", "& \\ctrl{-1} & \\qw")) == [
        [("x", "X", (1,), (2,), (), False, "")]
    ]
    assert ops(quantikz("& \\ctrl{2}", "& \\octrl{1}", "& \\targ{}")) == [
        [("x", "X", (3,), (1,), (2,), False, "")]
    ]


def test_a_controlled_gate_over_two_wires_has_both_wires_as_targets():
    source = quantikz("& \\ctrl{1} & \\qw", "& \\gate[2]{U} & \\qw", "& & \\qw")
    assert ops(source) == [[("gate", "U", (2, 3), (1,), (), False, "")]]


def test_dots_only_are_a_controlled_z_with_no_target():
    assert ops(quantikz("& \\ctrl{1}", "& \\ctrl{1}", "& \\control{}")) == [
        [("z_dots", "Z", (), (1, 2, 3), (), False, "")]
    ]


def test_swap_and_controlled_swap_have_the_crossed_wires_as_targets():
    assert ops(quantikz("& \\ctrl{1}", "& \\swap{1}", "& \\targX{}")) == [
        [("swap", "", (2, 3), (1,), (), False, "")]
    ]


def test_a_measurement_that_drives_a_gate_is_two_items_and_the_gate_is_classical():
    source = quantikz("& \\meter{} \\vcw{1}", "& \\gate{X}")
    assert ops(source) == [
        [
            ("measure", "", (1,), (), (), False, ""),
            ("gate", "X", (2,), (1,), (), True, ""),
        ]
    ]


def test_a_gate_with_a_classical_wire_to_another_row_is_classical_with_that_wire():
    source = quantikz("& \\meter{} & \\qw & \\qw", "& \\qw & \\gate{X} \\wire[u][1]{c} & \\qw")
    assert ops(source)[-1] == [("gate", "X", (2,), (1,), (), True, "")]


def test_a_label_next_to_control_dots_names_the_gate_and_a_label_on_a_gate_is_kept():
    dots = qcircuit("& \\ctrl{1} & \\qw", "& \\ctrl{-1} \\dstick{\\theta} & \\qw")
    assert ops(dots) == [[("dots_gate", "\\theta", (1, 2), (), (), False, "")]]
    cnot = qcircuit("& \\ctrl{1} & \\qw", "& \\targ \\dstick{\\theta} & \\qw")
    assert ops(cnot) == [[("x", "X", (2,), (1,), (), False, "\\theta")]]


def test_phase_dots_and_a_lone_control_and_a_lone_phase():
    assert ops(quantikz("& \\ctrl{1}", "& \\phase{\\phi}")) == [
        [("phase", "\\phi", (2,), (1,), (), False, "")]
    ]
    assert ops(quantikz("& \\phase{\\phi}")) == [[("phase", "\\phi", (1,), (), (), False, "")]]
    assert ops(quantikz("& \\ctrl{0}")) == [[("control", "", (), (1,), (), False, "")]]


def test_what_is_not_a_gate_is_text_dots_a_discard_a_barrier_or_unparsed_with_its_source():
    source = quantikz("& \\gate{H} \\macro{a} & \\vdots & \\slice{s}", "& \\trash{} & \\ket{0} x")
    kinds = [(o[0], o[1], o[2], o[6]) for step in ops(source) for o in step]
    assert ("unparsed", "", (1,), "\\macro{a}") in kinds
    assert ("barrier", "s", (), "") in kinds
    assert ("discard", "", (2,), "") in kinds
    assert ("dots", "", (1,), "") in kinds
    assert ("unparsed", "", (2,), "\\ket{0} x") in kinds  # text that is not just a ket
    ket = quantikz("& \\gate{H} & \\qw", "& \\gate{X} & \\ket{1}")
    assert ("text", "\\ket{1}", (2,), "") in [(o[0], o[1], o[2], o[6]) for s in ops(ket) for o in s]


def test_a_combination_the_list_does_not_know_is_unparsed_and_keeps_what_it_saw():
    group = quantikz(
        "& \\meter{} \\vcw{1} & \\gate{Z}", "& \\gate{X} & \\qw", "& \\ctrl{-1} & \\qw"
    )
    assert ops(group)[0] == [
        (
            "unparsed_group",
            "",
            (1, 2, 3),
            (),
            (),
            False,
            "wire 1: meter, wire 2: gate X, wire 3: ctrl",
        )
    ]
    one = quantikz("& \\gate{H} \\swap{0}")
    assert ops(one) == [
        [("gate", "H", (1,), (), (), False, ""), ("unparsed_op", "", (1,), (), (), False, "swap")]
    ]
    outside = quantikz("& \\ctrl{5}", "& \\gate{H}")
    assert [o[0] for o in ops(outside)[0]] == ["unparsed_group", "gate", "unparsed_outside"]


def test_the_sides_of_a_drawn_equality_are_parts_and_the_sign_starts_the_second():
    source = quantikz("& \\gate{H} & \\push{=} & \\gate{X}", "& \\qw & \\qw & \\qw")
    [circuit] = gate_list(source).circuits
    assert [[[o.kind for o in step] for step in part] for part in circuit.parts] == [
        [["gate"]],
        [["sep"], ["gate"]],
    ]
    assert circuit.parts[1][0][0].label == "="


def test_yquant_statements_are_one_item_each_with_wires_from_the_registers():
    source = (
        "\\begin{yquant}qubit {a} q[2]; cbit c; h q[0]; cnot q[1] | q[0]; swap q[0], q[1]; "
        "measure q[0]; box {U} q[1] | ~q[0]; zzz q[0];\\end{yquant}"
    )
    [circuit] = gate_list(source).circuits
    assert circuit.wires == ("1 = q[0] (in a)", "2 = q[1] (in a)", "3 = c")
    assert [
        tuple(o.to_dict().values()) for part in circuit.parts for step in part for o in step
    ] == [
        ("gate", "H", [1], [], [], False, ""),
        ("x", "X", [2], [1], [], False, ""),
        ("swap", "", [1, 2], [], [], False, ""),
        ("measure", "", [1], [], [], False, ""),
        ("gate", "U", [2], [], [1], True, ""),
        ("unparsed", "", [], [], [], False, "zzz q[0]"),
    ]


def yq(*statements: str) -> str:
    return (
        "\\begin{yquant}qubit a; qubit b; qubit c; cbit k; "
        + "; ".join(statements)
        + ";\\end{yquant}"
    )


@pytest.mark.parametrize(
    "source",
    [
        qcircuit("& \\meter & \\cw & \\qw", "& \\qw & \\gate{Z}\\gate{X} \\cwx[-1] & \\qw"),
        qcircuit("& \\meter & \\qw & \\qw", "& \\qw & \\gate{Z}\\gate{X} \\qwx[-1] & \\qw"),
        quantikz(
            "\\lstick{a} & \\meter{} & \\cw",
            "\\lstick{b} & \\qw & \\gate{Z}\\gate{X}\\wire[u][1]{c}",
        ),
        quantikz(
            "\\lstick{a} & \\meter{} & \\cw",
            "\\lstick{b} & \\qw & \\gate{Z}\\gate{X}\\wire[u][1]{q}",
        ),
        quantikz("& \\gate{H} & \\qw", "& \\qw & \\gate{A}\\gate{B}\\wire[u][1]{q}"),
    ],
)
def test_several_operations_in_a_cell_with_a_vertical_wire_are_not_read_as_unconditional(source):
    [circuit] = gate_list(source).circuits
    items = [o for part in circuit.parts for step in part for o in step]
    assert not [o for o in items if o.kind == "gate" and o.label in ("Z", "X", "A", "B")]
    [group] = [o for o in items if o.kind == "unparsed_group"]
    assert group.targets == (2,)
    assert gate_list(source).status == "partial"


@pytest.mark.parametrize(
    "cell",
    [
        "\\gate{H} \\meter \\cwx[1]",
        "\\control{} \\targ{} \\qwx[1]",
        "\\targX{} \\targX{} \\qwx[1]",
        "\\control{} \\control{} \\qwx[1]",
        "\\gate{H} \\octrl{1}",
        "\\ctrl{1} \\ctrl{1}",
    ],
)
def test_several_operations_of_one_cell_and_a_vertical_wire_are_one_unparsed_group(cell):
    source = qcircuit(f"& {cell} & \\gate{{Y}} & \\qw", "& \\qw & \\qw & \\qw")
    steps = ops(source)
    assert [[o[0] for o in step] for step in steps] == [["unparsed_group"], ["gate"]]
    assert steps[0][0][2] == (1,)
    assert gate_list(source).status == "partial"
    assert "unparsed joined operation" in gate_list(source).text


@pytest.mark.parametrize(
    "mark",
    [
        '\\wire[l][1]["\\ddots"{below,pos=0.1}]{a}',
        '\\wire[r][1]["\\iddots"]{a}',
        "\\wire[d][2]{\\vdots}",
        '\\wire[l][1]["..."]{a}',
        '\\wire[l][1]["\u2026"]{a}',
        '\\wire[l][1]["\\dotsc"]{a}',
        '\\wire[r][1]["\\cdot\\cdot\\cdot"]{a}',
    ],
)
def test_a_wire_mark_with_dots_is_an_unparsed_item_in_the_data(mark):
    # the dots draw gates or wires that are left out: a reader must refuse the circuit, so the
    # item is in the data (with its source) and the status is partial; no spelling is skipped
    source = quantikz(f"& \\ctrl{{1}} & \\qw {mark} & \\qw", "& \\targ{} & \\qw & \\qw")
    result = gate_list(source)
    [circuit] = result.circuits
    assert circuit.status == result.status == "partial"
    steps = ops(source)
    assert [[o[0] for o in step] for step in steps] == [["x"], ["unparsed"]]
    assert steps[1][0] == ("unparsed", "", (1,), (), (), False, mark)
    assert [o["kind"] for part in circuit.to_dict()["parts"] for step in part for o in step] == [
        "x",
        "unparsed",
    ]


@pytest.mark.parametrize(
    "cell",
    [
        '\\ctrl[wire style={"\\cdots"}]{1}',
        '\\ctrl[wire style={"n"}]{1}',
        "\\ctrl[wire style={dotted}]{1}",
        "\\octrl[label={\\cdots}]{1}",
        '\\ctrlo[wire style={"x3"}]{1}',
    ],
)
def test_a_control_with_an_option_it_does_not_read_is_an_unparsed_item_in_the_data(cell):
    source = quantikz(f"& {cell} & \\qw", "& \\targ{} & \\qw")
    result = gate_list(source)
    assert result.circuits[0].status == result.status == "partial"
    steps = ops(source)
    assert [o[0] for o in steps[0]] == ["x", "unparsed"]
    assert ("unparsed", "", (1,), (), (), False, cell) in steps[0]


def test_a_yquant_value_that_is_not_read_is_an_unparsed_item_in_the_data():
    source = "\\begin{yquant}\nqubit a;\nqubit b;\nh a;\ncnot {$\\times n$} b | a;\n\\end{yquant}"
    result = gate_list(source)
    assert result.circuits[0].status == result.status == "partial"
    assert ops(source)[1] == [("unparsed", "", (), (), (), False, "cnot {$\\times n$} b | a")]


def test_the_wire_of_a_one_row_cell_does_not_make_a_gate_out_of_its_operations_in_quantikz():
    source = quantikz("\\gate{H}\\meter{}\\wire[d][1]{c} & \\gate{Y}", "\\qw & \\qw")
    assert [[o[0] for o in step] for step in ops(source)] == [["unparsed_group"], ["gate"]]


@pytest.mark.parametrize(
    "rows",
    [
        ("\\ctrl{-1} \\ctrl{1} \\ctrl{1}", "\\targ"),
        ("\\targ \\ctrl{-1}", "\\qw"),
        ("\\ghost{W} \\control \\qwx[1]", "\\targ"),
    ],
)
def test_an_item_whose_controls_repeat_or_meet_its_targets_is_an_unparsed_group(rows):
    source = qcircuit(
        "& \\multigate{1}{W} & \\gate{Y} & \\qw",
        f"& {rows[0]} & \\qw & \\qw",
        f"& {rows[1]} & \\qw & \\qw",
    )
    items = [o for step in ops(source) for o in step]
    assert [o[0] for o in items] == ["unparsed_group", "gate"]
    assert gate_list(source).status == "partial"


def test_an_item_that_names_a_wire_twice_is_never_in_the_data():
    for item in (
        "x a | a",
        "cnot a | a, a",
        "box {U} a | ~a",
        "swap a, a",
        "swap a, a | b",
        "box {U} a, b | b",
    ):
        step = ops(yq(item, "h b"))[0]
        assert [o[0] for o in step] == ["unparsed"]
        assert gate_list(yq(item, "h b")).status == "partial"


def test_a_yquant_gate_that_names_a_target_twice_is_unparsed_with_or_without_controls():
    for item in (
        "h a, a",
        "x a, a",
        "z a, a, a",
        "box {U} a, a",
        "cnot a, a",
        "h a, a | b",
        "measure a, a",
        "barrier a, a",
        "discard a, a",
    ):
        source = yq(item, "h b")
        step = ops(source)[0]
        assert [o[0] for o in step] == ["unparsed"], item
        assert gate_list(source).status == "partial", item


def test_a_refused_yquant_measure_still_leaves_its_targets_measured():
    for item in ("measure a, a", "measure a | a"):
        source = yq(item, "x b | a")
        assert [o[0] for step in ops(source) for o in step] == ["unparsed", "gate"], item
        assert ops(source)[1] == [("gate", "X", (2,), (1,), (), True, "")], item
    source = yq("barrier a, a", "x b | a")
    assert ops(source)[1] == [("gate", "X", (2,), (1,), (), False, "")]


def yquant(body: str) -> str:
    return f"\\begin{{yquant}}{body}\\end{{yquant}}"


@pytest.mark.parametrize(
    "body",
    [
        "qubit a; qubit a; h a;",
        "qubit b[2]; qubit b[2]; qubit a; h b[0]; h b[1]; h a;",
        "qubit a; qubit b; qubit a; cnot b | a; h a;",
        "qubit b[2]; qubit b[2]; qubit a; h a, b[0];",
        "qubit a; cbit a; h a;",
        "qubit b; qubit b[2]; h b[0];",
        "qubit b[2]; qubit b[1]; h b[0];",
        "qubit a; qubit b; h a; cbit b; measure b;",
        "qubit a; qubit a[999]; h a;",
        "qubit a[999]; qubit a; h a;",
        "qubit a; qubit a[1234567]; h a;",
    ],
)
def test_a_yquant_name_declared_twice_leaves_the_circuit_unparsed_in_text_and_data(body):
    source = yquant(body)
    result = gate_list(source)
    assert result.text == "" and result.status == "unparsed" and result.n_circuits == 1
    [circuit] = result.circuits
    assert (circuit.kind, circuit.status) == ("yquant", "unparsed")
    assert circuit.wires == () and circuit.parts == ()
    assert circuit.to_dict()["wires"] == 0
    assert_text_from_data(source)


def test_yquant_names_that_differ_are_not_a_redeclaration():
    source = yquant("qubit a; qubit b[2]; qubit c[1]; h a; h b[1]; h c; cnot b[0] | a;")
    result = gate_list(source)
    assert result.status == "parsed"
    assert "Wires: 1 = a; 2 = b[0]; 3 = b[1]; 4 = c" in result.text
    assert [o[0][2] for o in ops(source)] == [(1,), (3,), (4,), (2,)]


def test_a_yquant_name_declared_twice_does_not_affect_the_other_circuits_of_the_block():
    source = yquant("qubit a; qubit a; h a;") + "\n" + yquant("qubit a; qubit b; h b;")
    result = gate_list(source)
    first, second = result.circuits
    assert (first.status, second.status) == ("unparsed", "parsed")
    assert "Step 1: H on wire 2" in result.text
    assert_text_from_data(source)


def test_a_yquant_measure_barrier_or_discard_keeps_its_label():
    source = yq("measure {$z$} a", "barrier {x} b", "discard {d} c", "measure a")
    assert [(o[0], o[1]) for step in ops(source) for o in step] == [
        ("measure", "z"),
        ("barrier", "x"),
        ("discard", "d"),
        ("measure", ""),
    ]
    assert "Step 1: measure wire 1" in gate_list(source).text


CELL_COMMANDS = [
    "\\gate{H}", "\\gate{X}", "\\meter", "\\ctrl{1}", "\\ctrl{-1}", "\\octrl{1}", "\\targ",
    "\\targX", "\\control", "\\qswap", "\\qwx[1]", "\\qwx[-1]", "\\cwx[1]", "\\cwx[-1]",
    "\\swap{1}", "\\phase{a}", "\\dstick{t}", "\\ghost{U}", "\\qw",
]  # fmt: skip


def meets_itself(o: dict) -> bool:
    """An item of the data that a gate cannot be: a wire named twice among the controls, or a
    control on a target, or a swap of one wire."""
    if o["kind"].startswith("unparsed"):
        return False
    controls = o["controls"] + o["open_controls"]
    named = controls or o["kind"] in ("x", "swap", "phase", "dots_gate")
    wires = controls + (o["targets"] if named else [])
    return len(set(wires)) != len(wires) or (o["kind"] == "swap" and len(set(o["targets"])) != 2)


def test_random_grids_with_several_commands_in_a_cell_have_no_item_that_names_a_wire_twice():
    rng = random.Random(20261016)
    for _ in range(1500):
        rows = []
        for _r in range(rng.randint(1, 4)):
            cells = [
                " ".join(rng.choice(CELL_COMMANDS) for _ in range(rng.randint(0, 3)))
                for _ in range(rng.randint(1, 4))
            ]
            rows.append(" & ".join(cells))
        for source in (quantikz(*rows), qcircuit(*rows)):
            assert_text_from_data(source)
            for circuit in gate_list(source).circuits:
                for part in circuit.to_dict()["parts"]:
                    for step in part:
                        assert not [o for o in step if meets_itself(o)], source


def test_several_operations_in_a_cell_without_a_vertical_wire_are_still_read_in_order():
    assert ops(quantikz("& \\gate{H}\\gate{X} & \\qw")) == [
        [("gate", "H", (1,), (), (), False, ""), ("gate", "X", (1,), (), (), False, "")]
    ]


def test_a_yquant_control_on_a_cbit_or_on_a_measured_wire_is_classical():
    assert ops(yq("x a | k", "x a | ~k", "cnot a | k"))[0:3] == [
        [("gate", "X", (1,), (4,), (), True, "")],
        [("gate", "X", (1,), (), (4,), True, "")],
        [("x", "X", (1,), (4,), (), True, "")],
    ]
    source = yq("h a", "x b | a", "measure a", "x b | a", "box {U} c | ~a, b", "swap b, c | a")
    assert [o[0][3:6] for o in ops(source)] == [
        ((), (), False),
        ((1,), (), False),
        ((), (), False),
        ((1,), (), True),
        ((2,), (1,), True),
        ((1,), (), True),
    ]


def test_a_yquant_wire_stays_quantum_until_it_is_measured_and_a_cbit_array_is_classical():
    source = (
        "\\begin{yquant}qubit q[2]; cbit c[2]; x q[1] | q[0]; x q[1] | c[1]; "
        "measure q[0]; x q[1] | q[0]; x q[0] | q[1];\\end{yquant}"
    )
    assert [o[0][5] for o in ops(source)] == [False, True, False, True, False]


def test_a_yquant_measure_barrier_or_discard_with_controls_keeps_them():
    source = yq("measure a | b", "barrier a | ~b", "discard c | k, b")
    assert ops(source) == [
        [("measure", "", (1,), (2,), (), False, "")],
        [("barrier", "", (1,), (), (2,), False, "")],
        [("discard", "", (3,), (2, 4), (), True, "")],
    ]


def test_yquant_targets_and_controls_are_in_wire_order_whatever_the_order_written():
    assert ops(yq("box {U} b, a", "box {U} a, b", "cnot c | b, a")) == [
        [("gate", "U", (1, 2), (), (), False, "")],
        [("gate", "U", (1, 2), (), (), False, "")],
        [("x", "X", (3,), (1, 2), (), False, "")],
    ]


def test_yquant_statements_with_no_target_or_a_swap_of_other_than_two_wires_are_unparsed():
    source = yq("cnot | a", "h | a", "swap a", "swap a, b, c", "swap a, b | c")
    result = gate_list(source)
    kinds = [o[0] for step in ops(source) for o in step]
    assert kinds == ["unparsed"] * 4 + ["swap"]
    assert result.status == "partial"
    assert "Step 1: unparsed: cnot | a" in result.text


def test_the_circuits_of_a_block_are_in_order_and_an_unreadable_one_has_no_wires_or_parts():
    source = (
        quantikz("& \\gate{H}")
        + "\n\\label{a}\n"
        + "\\begin{quantikz}\\foo\\end{quantikz}\n\\begin{quantikz}& \\gate{X}"
    )
    result = gate_list(source)
    assert result.n_circuits == 3 == len(result.circuits)
    first, second, third = result.circuits
    assert (first.status, second.status, third.status) == ("parsed", "unparsed", "unparsed")
    assert second.wires == () and second.parts == () == third.parts
    assert first.kind == "quantikz"


def test_a_label_inside_the_circuit_is_the_label_of_its_data():
    source = quantikz("& \\gate{H}\\label{fig:x}")
    assert gate_list(source).circuits[0].label == "fig:x"


def test_a_block_with_no_circuit_has_no_data_and_the_data_is_not_cut_at_the_step_limit():
    assert gate_list("words").circuits == ()
    source = quantikz(" & ".join(["\\gate{H}"] * (MAX_STEPS + 20)))
    result = gate_list(source)
    assert len(result.circuits[0].parts[0]) == MAX_STEPS + 20
    assert result.text.split("\n")[-1] == "... 20 more lines not listed"


def test_a_gate_over_more_wires_than_the_limit_is_unparsed_as_data_and_listed_in_the_text():
    n = MAX_GATE_WIRES + 5
    source = quantikz(*[f"\\gate[{n}]{{U}} & \\qw"] + ["& \\qw"] * (n - 1))
    result = gate_list(source)
    assert f"U on wires 1 to {n} (one gate on these wires)" in result.text
    [[wide]] = result.circuits[0].parts[0]
    assert wide.kind == "unparsed_op" and wide.targets == () and wide.label == "U"
    narrow = quantikz(
        *[f"\\gate[{MAX_GATE_WIRES}]{{U}} & \\qw"] + ["& \\qw"] * (MAX_GATE_WIRES - 1)
    )
    [[fits]] = gate_list(narrow).circuits[0].parts[0]
    assert fits.kind == "gate" and len(fits.targets) == MAX_GATE_WIRES


def test_the_data_of_a_source_grows_in_proportion_to_it_even_when_every_gate_spans_every_wire():
    for n in (200, 800):
        result = gate_list(quantikz(*[f"& \\gate[{n}]{{U}}"] * n))
        items = [o for part in result.circuits[0].parts for step in part for o in step]
        assert len(items) <= n
        assert sum(len(o.targets) for o in items) <= MAX_GATE_WIRES * n


# ------------------------------------------------------------------ the dict format


def plain(value) -> bool:
    if isinstance(value, dict):
        return all(isinstance(k, str) and plain(v) for k, v in value.items())
    if isinstance(value, list):
        return all(plain(v) for v in value)
    return type(value) in (str, int, bool)


def test_to_dict_is_plain_dicts_and_lists_in_the_circuit_1_format():
    source = quantikz(
        "\\lstick{$\\ket{a}$} & \\gate{H} & \\ctrl{1} & \\push{=} & \\qw",
        "\\lstick{$\\ket{b}$} & \\qw & \\targ{} & \\qw & \\qw",
    )
    [circuit] = gate_list(source).circuits
    data = circuit.to_dict()
    assert plain(data) and json.loads(json.dumps(data)) == data
    assert CIRCUIT_FORMAT == "circuit/1" and data["format"] == "circuit/1"
    assert data["kind"] == "quantikz" and data["status"] == "parsed" and data["label"] == ""
    assert data["wires"] == 2 and data["wire_labels"] == ["1 = \\ket{a}", "2 = \\ket{b}"]
    assert data["parts"][0] == [
        [
            {
                "kind": "gate",
                "label": "H",
                "targets": [1],
                "controls": [],
                "open_controls": [],
                "classical": False,
                "source": "",
            }
        ],
        [
            {
                "kind": "x",
                "label": "X",
                "targets": [2],
                "controls": [1],
                "open_controls": [],
                "classical": False,
                "source": "",
            }
        ],
    ]


def test_to_dict_of_every_circuit_of_every_construct_is_plain_and_has_one_dict_per_item():
    for source in FIXTURES:
        for circuit in gate_list(source).circuits:
            data = circuit.to_dict()
            assert plain(data) and data["wires"] == len(circuit.wires)
            items = [o for part in circuit.parts for step in part for o in step]
            flat = [o for part in data["parts"] for step in part for o in step]
            assert [o["kind"] for o in flat] == [o.kind for o in items]


def test_an_unreadable_circuit_is_a_dict_with_no_wires_and_no_parts():
    data = gate_list("\\begin{quantikz}\\foo\\end{quantikz}").circuits[0].to_dict()
    assert data["status"] == "unparsed" and data["wires"] == 0 and data["parts"] == []


def test_the_data_is_immutable_and_has_the_documented_fields():
    op = GateOp("gate", "H", (1,))
    assert (op.controls, op.open_controls, op.classical, op.source) == ((), (), False, "")
    with pytest.raises(AttributeError):
        op.kind = "x"  # type: ignore[misc]
    circuit = CircuitSteps("quantikz", "parsed", "", (), ())
    assert circuit.to_dict()["parts"] == []
    assert GateList("unparsed", "").circuits == ()
