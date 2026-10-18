r"""The consumption check of the circuit readers (domain.circuits): every circuit here is invented.

A reader of ``quantikz``, ``\Qcircuit`` or ``yquant`` must account for every token of the source:
a command with its options and arguments, a run of text, a cell, a row, also on a row that draws no
wire. A token is consumed by a rule that understood it (the reading used it, or an allowlist says
it has no effect on the circuit: spacing, a comment, an environment size). Anything else is
unparsed by default, so a command the reader does not know, or a text it has no rule for, can
never vanish from a circuit whose status stays ``parsed``.

The tests below inject an unknown command or a random text token at random places into every
fixture circuit and require a status other than ``parsed``, inject tokens that are allowlisted
and require the list to be unchanged, and check the individual rules that closed the cases of
the reader-wide verification: text on a row that draws no wire, the type argument of a quantikz
``\wire``, a label on a vertical ``\wire``, and the backstop that reports a token no rule consumed.
"""

import json
import random
import re
from pathlib import Path

import pytest
from test_circuits import FIXTURES, VOCAB, beside_h, lines, qcircuit, quantikz, yquant

from docingest.domain import circuits
from docingest.domain.circuits import find_circuits, gate_list, split_top, tokenize

# commands no reader knows, and text that is neither empty nor a ket |0> or |1> nor dots
UNKNOWN_COMMANDS = [
    "\\zzfoo",
    "\\mygate",
    "\\macro{a}",
    "\\qux[x]{y}",
    "\\hbox",
    "\\ar[r]",
    "\\textit",
]
TEXT_TOKENS = ["n", "k", "xyz", "42", "q_0", "(omitted)", "repeat n times", "\\times n", "\\alpha"]
# tokens with no effect on a circuit
HARMLESS = ["\\hspace{1em}", "\\,", "\\quad", "\\vphantom{X}", "\\rule{1em}{0pt}"]


def rebuilt(kind: str, body: str, options: str) -> str:
    """The source of a circuit found by ``find_circuits``, written again."""
    if kind == "qcircuit":
        return f"\\Qcircuit {options} {{{body}}}"
    option = f"[{options}]" if options else ""
    return f"\\begin{{{kind}}}{option}\n{body}\n\\end{{{kind}}}"


def with_token(
    kind: str, body: str, token: str, rng: random.Random, *, occupied: bool = False
) -> str:
    """``body`` with ``token`` as one more token of a random cell (or statement), at a random
    place between its tokens. Never inside a brace or a bracket of another token. With
    ``occupied`` only a row that holds something is chosen (a token in an empty quantikz row makes
    it a wire)."""
    if kind == "yquant":
        statements = [s for s in body.split(";") if s.strip()]
        statements.insert(rng.randrange(len(statements) + 1), token)
        return ";\n".join(statements) + ";"
    qc = kind == "qcircuit"
    rows = [split_top(row, "&") for row in split_top(body, "\\\\")]
    chosen = [i for i, cells in enumerate(rows) if any(c.strip() for c in cells) or not occupied]
    row = rng.choice(chosen)
    col = rng.randrange(len(rows[row]))
    pieces = [t.src for t in tokenize(rows[row][col], qc)]
    # a group right after \\meter or \\targ would be their optional argument: keep it apart
    pieces.insert(rng.randrange(len(pieces) + 1), "\\relax " + token if token[0] == "{" else token)
    rows[row][col] = " ".join(pieces)
    return " \\\\\n".join(" & ".join(r) for r in rows)


def circuits_of(source: str):
    return [(k, b, o) for k, b, _, o in find_circuits(source) if b is not None]


ALL_SOURCES = [
    *(pytest.param(s, id=f"case{i}") for i, s in enumerate(FIXTURES)),
    pytest.param(yquant("h a;", "cnot b | a;", "measure a;"), id="yquant"),
    pytest.param(yquant("box {U} a, b;", "swap a, b;", "barrier a, b;"), id="yquant2"),
]


def parsed_circuits():
    """The fixture circuits that are read as ``parsed``, each as (id, kind, body, options)."""
    found = []
    for param in ALL_SOURCES:
        source = param.values[0]
        for n, (kind, body, options) in enumerate(circuits_of(source)):
            if gate_list(rebuilt(kind, body, options)).status == "parsed":
                found.append(pytest.param(kind, body, options, id=f"{param.id}-{n}"))
    return found


PARSED = parsed_circuits()


def test_there_are_many_fixture_circuits_to_inject_into():
    kinds = {p.values[0] for p in PARSED}
    assert len(PARSED) >= 70
    assert kinds == {"quantikz", "qcircuit", "yquant", "tikzcd"}


@pytest.mark.parametrize(("kind", "body", "options"), PARSED)
def test_an_unknown_command_at_a_random_place_is_never_parsed(kind, body, options):
    rng = random.Random(f"{kind}{body}{options}command")
    for _ in range(4):
        token = rng.choice(UNKNOWN_COMMANDS)
        source = rebuilt(kind, with_token(kind, body, token, rng), options)
        found = gate_list(source)
        assert found.status != "parsed", (token, source)
        assert found.text.count("unparsed") >= 1 or found.status == "unparsed", (token, source)


@pytest.mark.parametrize(("kind", "body", "options"), PARSED)
def test_a_random_text_token_at_a_random_place_is_never_parsed(kind, body, options):
    rng = random.Random(f"{kind}{body}{options}text")
    for _ in range(4):
        token = rng.choice(TEXT_TOKENS)
        source = rebuilt(kind, with_token(kind, body, token, rng), options)
        found = gate_list(source)
        assert found.status != "parsed", (token, source)


# the places a token can stand besides a cell of its own: in a group, in a wrapper, after a spacing
TEMPLATES = [
    "{TOKEN}",
    "{{TOKEN}}",
    "\\mbox{TOKEN}",
    "\\raisebox{1ex}{TOKEN}",
    "\\scalebox{.5}{TOKEN}",
    "\\textcolor{red}{TOKEN}",
    "\\hspace{1em}TOKEN",
    "\\quad TOKEN",
    "\\protect TOKEN",
    "\\relax TOKEN",
    "TOKEN \\hfill",
    "\\label{x} TOKEN",
    "\\phantom{x} TOKEN",
]


@pytest.mark.parametrize(
    ("kind", "body", "options"), [p for p in PARSED if p.values[0] != "yquant"]
)
def test_a_token_in_a_group_a_wrapper_or_beside_a_spacing_is_never_parsed(kind, body, options):
    rng = random.Random(f"{kind}{body}{options}template")
    for _ in range(6):
        token = rng.choice([*UNKNOWN_COMMANDS, *TEXT_TOKENS])
        placed = rng.choice(TEMPLATES).replace("TOKEN", token)
        source = rebuilt(kind, with_token(kind, body, placed, rng), options)
        assert gate_list(source).status != "parsed", (placed, source)


@pytest.mark.parametrize(
    "cell",
    [
        "\\hfill{n}",
        "\\protect{\\gate{X}}",
        "\\relax{n}",
        "\\strut[n]",
        "\\label[n]{x}",
        "\\label{x}{n}",
        "\\noindent{\\gate{X}}",
    ],
)
def test_a_switch_that_swallowed_a_group_is_not_an_ignored_switch(cell):
    source = beside_h(cell, "& \\qw & \\qw & \\qw")
    assert gate_list(source).status == "partial", cell


def with_option_token(kind: str, body: str, token: str, rng: random.Random) -> str | None:
    """``body`` with ``token`` added inside the first ``[..]`` option of a random command, or None
    when no command of the circuit has an option."""
    qc = kind == "qcircuit"
    rows = [split_top(row, "&") for row in split_top(body, "\\\\")]
    found = [
        (i, j, t)
        for i, cells in enumerate(rows)
        for j, cell in enumerate(cells)
        for t in tokenize(cell, qc)
        if t.opts
    ]
    if not found:
        return None
    i, j, tok = rng.choice(found)
    option = f"[{tok.opts[0]}]"
    rows[i][j] = rows[i][j].replace(option, f"[{tok.opts[0]} {token}]", 1)
    return " \\\\\n".join(" & ".join(r) for r in rows)


@pytest.mark.parametrize(
    ("kind", "body", "options"), [p for p in PARSED if p.values[0] in ("quantikz", "qcircuit")]
)
def test_a_token_inside_an_option_of_a_command_is_never_parsed(kind, body, options):
    rng = random.Random(f"{kind}{body}{options}option")
    for _ in range(4):
        token = rng.choice([*UNKNOWN_COMMANDS, *TEXT_TOKENS])
        injected = with_option_token(kind, body, token, rng)
        if injected is None:
            return
        found = gate_list(rebuilt(kind, injected, options))
        assert found.status != "parsed", (token, injected)


@pytest.mark.parametrize(("kind", "body", "options"), PARSED)
def test_a_token_that_is_allowlisted_as_no_effect_leaves_the_list_as_it_was(kind, body, options):
    if kind == "yquant":
        return  # a yquant statement is never spacing; its comments are cut before reading
    rng = random.Random(f"{kind}{body}{options}harmless")
    before = gate_list(rebuilt(kind, body, options))
    for _ in range(4):
        token = rng.choice(HARMLESS)
        source = rebuilt(kind, with_token(kind, body, token, rng, occupied=True), options)
        after = gate_list(source)
        assert (after.status, after.text) == (before.status, before.text), (token, source)


def test_a_comment_in_a_yquant_circuit_is_cut_before_reading():
    plain = gate_list(yquant("h a;", "cnot b | a;"))
    commented = gate_list(yquant("h a; % one more gate n times", "cnot b | a;"))
    assert plain.status == commented.status == "parsed"
    assert plain.text == commented.text


# ------------------------------------------------------------------ the backstop


def test_a_token_that_no_rule_consumed_is_an_unparsed_item_naming_it_and_its_place(monkeypatch):
    # a reader that makes neither an operation nor a fact from its command drops it; the check
    # that every token is consumed is what turns that into an unparsed item
    monkeypatch.setitem(circuits._READERS, "silent", lambda t, ops, facts: None)
    monkeypatch.setitem(circuits._CARRIES, "silent", circuits._Carries((), (), "u"))
    source = quantikz("& \\gate{H} & \\silent{a} & \\qw", "& \\gate{X} & \\qw & \\qw")
    found = gate_list(source)
    assert found.status == "partial"
    assert lines(source) == [
        "Wires: 1; 2",
        "Step 1: H on wire 1; X on wire 2",
        "Step 2: unparsed (read by no rule) at row 1, column 3: \\silent{a}",
    ]
    ops = found.circuits[0].parts[0][-1]
    assert [(o.kind, o.source) for o in ops] == [("unparsed", "\\silent{a}")]


def test_a_command_that_the_reader_does_not_know_is_unparsed_with_nothing_registered():
    source = quantikz("& \\gate{H} & \\newgate{a}{b} & \\qw")
    assert gate_list(source).status == "partial"
    assert lines(source)[-1] == "Step 2: unparsed on wire 1: \\newgate{a}{b}"


def test_the_backstop_is_never_needed_on_random_circuits_and_every_prefix_of_a_fixture(monkeypatch):
    # every rule that reads or drops a token consumes it: whatever the input, no token reaches the
    # backstop, so an unparsed item is always written by the rule that did not understand it
    left: list[object] = []
    real = circuits._unconsumed

    def spy(g, circ):
        found = real(g, circ)
        left.extend(found)
        return found

    monkeypatch.setattr(circuits, "_unconsumed", spy)
    rng = random.Random(20261018)
    for _ in range(600):
        body = rng.choice(["", " ", "\n"]).join(
            rng.choice(VOCAB) for _ in range(rng.randint(1, 40))
        )
        for source in (
            f"\\begin{{quantikz}}{body}\\end{{quantikz}}",
            f"\\Qcircuit @C=1em {{{body}}}",
            f"\\begin{{tikzcd}}{body}\\end{{tikzcd}}",
        ):
            gate_list(source)
    for source in FIXTURES:
        for cut in range(0, len(source) + 1, 3):
            gate_list(source[:cut])
    assert left == []


def test_every_reader_consumes_what_it_reads():
    # no token of a circuit that is read as parsed is left over: the backstop never fires on the
    # fixture circuits (its wording is not one the reader writes for anything else)
    for param in PARSED:
        kind, body, options = param.values
        text = gate_list(rebuilt(kind, body, options)).text
        assert "read by no rule" not in text


# ------------------------------------------------------------------ rows that draw no wire

CAPTIONS = [
    "\\mbox{repeat $n$ times}",
    "\\times n",
    "(\\times n)",
    "\\text{\\vdots}",
    "\u22ee",
    "\\push{\\times n}",
    "\\rstick{\\times n}",
    "\\dstick{\\text{repeat}}",
    "\\text{(gates omitted)}",
    "\\mbox{if $m=1$}",
    "\\text{Repeat $k$ times}",
    "\\text{...}",
    "n",
    "a",
]
H2 = ("& \\gate{H} & \\qw", "& \\gate{H} & \\qw")


@pytest.mark.parametrize("caption", CAPTIONS)
@pytest.mark.parametrize("place", ["below", "between", "above"])
def test_text_on_a_qcircuit_row_that_draws_no_wire_is_unparsed(caption, place):
    row = f"& {caption} &"
    rows = {"below": (*H2, row), "between": (H2[0], row, H2[1]), "above": (row, *H2)}[place]
    source = qcircuit(*rows)
    found = gate_list(source)
    assert found.status == "partial", lines(source)
    assert "unparsed" in " ".join(lines(source))
    assert found.circuits[0].wires == ("1", "2")  # the row is still not a wire


def test_text_on_a_row_that_draws_no_wire_names_the_token_and_that_it_is_on_no_wire():
    source = qcircuit(*H2, "& \\mbox{repeat $n$ times} &")
    assert lines(source) == [
        "Wires: 1; 2",
        "Step 1: H on wire 1; H on wire 2; unparsed on no wire: repeat $n$ times",
    ]
    (step,) = gate_list(source).circuits[0].parts[0]
    assert [(o.kind, o.targets, o.source) for o in step][-1] == (
        "unparsed",
        (),
        "repeat $n$ times",
    )


@pytest.mark.parametrize(
    "row",
    [
        "& \\hspace{1em} &",
        "& \\, &",
        "& ~ &",
        "& \\vphantom{X} &",
        "& &",
        "&",
        "& \\label{x} &",
    ],
)
def test_a_row_of_spacing_or_nothing_says_nothing(row):
    source = qcircuit(H2[0], row, H2[1])
    assert gate_list(source).status == "parsed"
    assert lines(source) == ["Wires: 1; 2", "Step 1: H on wire 1; H on wire 2"]


def test_an_equals_sign_in_a_column_of_its_own_still_splits_the_circuit():
    source = qcircuit("& \\gate{H} & \\qw & & \\gate{X} & \\qw", "& & & = & &")
    assert gate_list(source).status == "parsed"
    assert lines(source) == [
        "Wires: 1",
        "Step 1: H on wire 1",
        "[=: the steps below draw the right-hand side, an equivalent circuit]",
        "Step 1: X on wire 1",
    ]


def test_an_equals_sign_on_a_row_that_draws_no_wire_in_a_column_of_gates_is_unparsed():
    # it used to be dropped: the circuit then read as H, X in a row with no equality
    source = qcircuit("& \\gate{H} & \\qw & \\gate{X} & \\qw", "& & & = & &")
    assert gate_list(source).status == "partial"
    assert "Step 2: X on wire 1; unparsed on no wire: =" in lines(source)


def test_a_ket_drawn_in_the_column_of_the_equals_sign_opens_the_right_hand_side():
    source = qcircuit("& \\gate{H} & \\qw & \\ket{0} & \\gate{X}", "& & & = & &")
    assert gate_list(source).status == "parsed"
    assert lines(source) == [
        "Wires: 1",
        "Step 1: H on wire 1",
        "[=: the steps below draw the right-hand side, an equivalent circuit]",
        "Step 1: text \\ket{0} on wire 1",
        "Step 2: X on wire 1",
    ]


def test_a_second_sign_in_the_column_of_the_first_is_not_dropped():
    source = qcircuit("& \\gate{H} & = & \\gate{X}", "& \\qw & \\equiv & \\qw")
    text = " ".join(lines(source))
    assert "[=:" in text
    assert "sign \\equiv" in text


# ------------------------------------------------------------------ text on a wire


@pytest.mark.parametrize(
    "text",
    [
        "\\ket{0}",
        "\\ket{1}",
        "\\ket0",
        "|0\\rangle",
        "$|1\\rangle$",
        "\\left|0\\right\\rangle",
        "{\\ket{1}}",
    ],
)
def test_a_basis_ket_on_a_wire_is_kept_as_text(text):
    source = qcircuit(f"& {text} & \\gate{{H}} & \\qw")
    assert gate_list(source).status == "parsed"
    assert "text" in lines(source)[1]


@pytest.mark.parametrize(
    "text",
    [
        "\\ket{+}",
        "\\ket{\\psi}",
        "\\bra{0}",
        "\\langle 0|",
        "|0\\rangle\\langle 0|",
        "\\ket{0}\\bra{0}",
        "0",
        "n",
        "q_0",
        "\\ket{00}",
        "\\ket{0} n",
        "\\textbf{x}",
    ],
)
def test_any_other_text_on_a_wire_is_unparsed(text):
    source = qcircuit(f"& {text} & \\gate{{H}} & \\qw")
    assert gate_list(source).status == "partial", lines(source)
    assert "unparsed on wire 1" in lines(source)[1]


def test_a_spacing_command_that_swallowed_content_is_not_spacing():
    # \small{..} and \quad{..} take the next group as an argument; the group would be dropped
    for cell in ["\\small{\\gate{X}}", "\\quad{n}", "\\hspace{n}", "\\rule{1}{2}", "\\vspace{x}"]:
        source = beside_h(cell, "& \\qw & \\qw & \\qw")
        assert gate_list(source).status == "partial", cell
        assert f"unparsed on wire 1: {cell}" in " ".join(lines(source)), cell


@pytest.mark.parametrize(
    "cell",
    [
        "\\hspace{1em}",
        "\\hspace*{-2em}",
        "\\vspace{.5 cm}",
        "\\hphantom{n}",
        "\\rule[1ex]{1em}{2pt}",
    ],
)
def test_a_spacing_command_with_only_what_a_spacing_has_is_consumed(cell):
    source = beside_h(cell, "& \\qw & \\qw & \\qw")
    assert gate_list(source).status == "parsed"
    assert lines(source)[1:] == ["Step 1: H on wire 1"]


def test_a_group_after_a_spacing_is_a_group_of_its_own_and_is_read():
    source = beside_h("\\hspace{1em}{n}", "& \\qw & \\qw & \\qw")
    assert gate_list(source).status == "partial"
    assert lines(source)[-1] == "Step 2: unparsed on wire 1: n"


def test_dots_and_signs_with_options_or_arguments_are_unparsed():
    for cell in ["\\ddots{n}", "\\iddots[x]", "\\equiv{n}", "\\Rightarrow[x]"]:
        source = beside_h(cell, "& \\qw & \\qw & \\qw")
        assert gate_list(source).status == "partial", cell


def test_a_row_spacing_option_is_dropped_only_if_it_is_a_length():
    def spaced(option: str) -> str:
        rows = ["& \\ctrl{1} & \\qw \\\\" + option, "& \\targ{} & \\qw"]
        return "\\begin{quantikz}\n" + "\n".join(rows) + "\n\\end{quantikz}"

    assert gate_list(spaced("[2mm]")).status == "parsed"
    for option in ["[\\text{repeat n times}]", "[n]", "[dotted]"]:
        assert gate_list(spaced(option)).status == "partial", option


# ------------------------------------------------------------------ facts that are not listed


def test_a_label_or_output_on_a_row_of_dots_is_unparsed_unless_it_is_dots():
    for label in ["\\ket{0}", "n"]:
        source = qcircuit(
            "\\lstick{a} & \\gate{H} & \\qw",
            f"\\lstick{{{label}}} & \\vdots &",
            "\\lstick{c} & \\qw & \\qw",
        )
        assert gate_list(source).status == "partial", label
    dots = qcircuit(
        "\\lstick{a} & \\gate{H} & \\qw",
        "\\lstick{\\vdots} & \\vdots &",
        "\\lstick{c} & \\qw & \\qw",
    )
    assert gate_list(dots).status == "parsed"


def test_an_output_label_on_a_row_that_draws_nothing_else_is_unparsed():
    source = qcircuit("& \\gate{H} & \\qw", "& \\rstick{n} &", "& \\gate{X} & \\qw")
    assert gate_list(source).status == "partial"
    assert "unparsed on no wire: \\rstick{n}" in " ".join(lines(source))
    empty = qcircuit("& \\gate{H} & \\qw", "& \\rstick{} &", "& \\gate{X} & \\qw")
    assert gate_list(empty).status == "parsed"


def test_a_second_bundle_or_stick_in_a_cell_is_unparsed_not_a_replacement():
    twice = qcircuit("& \\qwbundle{a} \\qwbundle{b} & \\gate{H}")
    assert gate_list(twice).status == "partial"
    assert "unparsed on wire 1: \\qwbundle{b}" in " ".join(lines(twice))
    sticks = qcircuit("& \\ctrl{1} \\dstick{a} \\dstick{b} & \\qw", "& \\ctrl{-1} & \\qw")
    assert gate_list(sticks).status == "partial"
    assert "unparsed on wire 1: \\dstick{b}" in " ".join(lines(sticks))


def test_a_second_bundle_on_a_row_in_a_later_cell_is_unparsed_too():
    source = qcircuit("& \\qwbundle{a} & \\qwbundle{b} & \\gate{H}")
    assert gate_list(source).status == "partial"
    assert lines(source)[0] == "Wires: 1 = bundle a"
    assert "unparsed on wire 1: \\qwbundle{b}" in " ".join(lines(source))


def test_a_stick_label_of_spacing_only_says_nothing():
    source = qcircuit("& \\ctrl{1} \\dstick{\\hspace{1em}} & \\qw", "& \\targ & \\qw")
    assert gate_list(source).status == "parsed"
    assert lines(source) == ["Wires: 1; 2", "Step 1: CNOT: control wire 1, target wire 2"]


def test_an_equals_sign_beside_a_gate_in_a_cell_is_a_sign_item_not_a_split():
    source = quantikz("& \\gate{H} = & \\gate{X}")
    assert gate_list(source).status == "parsed"
    assert lines(source)[1] == "Step 1: H on wire 1; sign = on wire 1"


def test_a_stick_label_that_names_nothing_is_unparsed_not_text():
    source = qcircuit("& \\gate{H} & \\dstick{n} \\qw")
    assert gate_list(source).status == "partial"
    assert "unparsed on wire 1: \\dstick{n}" in " ".join(lines(source))


# ------------------------------------------------------------------ the \wire type argument


def wire_circuit(mark: str, *, vertical: bool = False) -> str:
    if vertical:
        return quantikz(f"& \\control{{}} {mark} & \\qw", "& \\targ{} & \\qw")
    return quantikz(f"& \\gate{{H}} & {mark} \\ctrl{{1}} & \\qw", "& \\qw & \\targ{} & \\qw")


@pytest.mark.parametrize(
    "mark", ["\\wire[l][1]{q}", "\\wire[r][2]{q}", "\\wire[l][1]{a}", '\\wire[l][1]["x"]{a}']
)
def test_a_horizontal_wire_of_type_q_or_a_goes_on_with_the_wire_of_its_row(mark):
    source = wire_circuit(mark)
    assert gate_list(source).status == "parsed"
    assert lines(source)[-1] == "Step 2: CNOT: control wire 1, target wire 2"


@pytest.mark.parametrize(
    "mark",
    [
        "\\wire[l][1]{c}",
        "\\wire[r][1]{c}",
        "\\wire[l][1]{b}",
        "\\wire[r][2]{b}",
        "\\wire[l][1]{n}",
        "\\wire[l][1]{t}",
        "\\wire[l][1]{x}",
        "\\wire[l][1]{}",
        "\\wire[l][1]",
        "\\wire[l][1]{q}{c}",
        '\\wire[l][1]["n"]{b}',
    ],
)
def test_a_horizontal_wire_of_another_type_changes_the_wire_and_is_unparsed(mark):
    source = wire_circuit(mark)
    assert gate_list(source).status == "partial", lines(source)
    assert "unparsed" in lines(source)[-1] or "unparsed" in " ".join(lines(source))


def test_a_quantum_vertical_wire_joins_and_a_classical_one_is_classical_control():
    quantum = wire_circuit("\\wire[d][1]{q}", vertical=True)
    assert gate_list(quantum).status == "parsed"
    assert lines(quantum)[-1] == "Step 1: CNOT: control wire 1, target wire 2"
    up = quantikz("& \\targ{} & \\qw", "& \\control{} \\wire[u][1]{q} & \\qw")
    assert gate_list(up).status == "parsed"
    assert lines(up)[-1] == "Step 1: CNOT: control wire 2, target wire 1"
    classical = gate_list(wire_circuit("\\wire[d][1]{c}", vertical=True))
    assert classical.status == "parsed"
    (op,) = classical.circuits[0].parts[0][-1]
    assert (op.kind, op.classical, op.controls, op.targets) == ("x", True, (1,), (2,))


@pytest.mark.parametrize(
    "mark",
    [
        "\\wire[d][1]{b}",
        "\\wire[d][1]{a}",
        "\\wire[d][1]{n}",
        "\\wire[d][1]{t}",
        "\\wire[d][1]{}",
        "\\wire[d][1]",
        "\\wire[u][1]{b}",
        '\\wire[d][1]["n"]{q}',
        '\\wire[d][1]["k"{right}]{q}',
        '\\wire[d][1]["x3"]{q}',
        '\\wire[d][1]["a"]{c}',
        "\\wire[d][1][n]{q}",
        "\\wire[d][1][dotted]{q}",
    ],
)
def test_a_vertical_wire_of_another_type_or_with_a_label_is_unparsed(mark):
    source = wire_circuit(mark, vertical=True)
    assert gate_list(source).status == "partial", lines(source)
    assert f"unparsed on wire 1: {mark}" in " ".join(lines(source))


def test_a_vertical_wire_with_an_empty_label_option_is_read():
    source = wire_circuit("\\wire[d][1][]{q}", vertical=True)
    assert gate_list(source).status == "parsed"


# ------------------------------------------------------------------ the appearance of a box

BOX_STYLES = [
    "fill=red",
    "fill=cyan!15",
    "fill=red!10",
    "fill=gray!10",
    "fill=yellow!35",
    "fill=lightgray",
    "fill=darkgray!100",
    "fill = violet!99 ",
]


@pytest.mark.parametrize("style", BOX_STYLES)
def test_a_box_style_of_one_visible_fill_is_read(style):
    for box in [f"\\gate[style={{{style}}}]{{U}}", f"\\meter[style={{{style}}}]{{}}"]:
        source = beside_h(box, "& \\qw & \\qw & \\qw")
        assert gate_list(source).status == "parsed", box


# A line or a text colour can hide a box as well as a fill: only `fill` is in the list, and its
# colour is a base colour of xcolor mixed with white to at least 10 percent. White hides the box,
# black hides its label (drawn in black), a colour the author defined may be white and its
# definition is not in the block, and a mix under 10 percent is near white.
HIDING_OR_UNREAD_STYLES = [
    "draw=white",
    "text=white",
    "draw=white,text=white",
    "fill=white,draw=white,text=white",
    "draw=red!0,text=red!0",
    "draw=gray!20,text=gray!20",
    "draw=blue",
    "text=black",
    "text = gray!50",
    "draw=qred, text=qred, fill=red!10",
    "fill=red, draw=blue",
    "fill=red, fill=blue",
    "fill=white",
    "fill=black",
    "fill=black!50",
    "fill=white!50",
    "fill=red!0",
    "fill=red!5",
    "fill=red!9",
    "fill=red!010",
    "fill=red!101",
    "fill=red!1000",
    "fill=red!10.5",
    "fill=red!-10",
    "fill=bg",
    "fill=background",
    "fill=mygreen!40",
    "fill=N2_plasma4!75",
    "fill=Red",
    "fill=green!50!black",
    "fill=red!10!white",
    "fill=red!10!",
]


@pytest.mark.parametrize("style", HIDING_OR_UNREAD_STYLES)
def test_a_fill_that_can_hide_the_box_and_a_line_or_text_colour_are_unparsed(style):
    for box in [f"\\gate[style={{{style}}}]{{U}}", f"\\meter[style={{{style}}}]{{}}"]:
        source = beside_h(box, "& \\qw & \\qw & \\qw")
        assert gate_list(source).status == "partial", box
        assert f"unparsed on wire 1: {box}" in " ".join(lines(source))


@pytest.mark.parametrize(
    "style",
    [
        "fill=none",
        "draw=none",
        "thick",
        "dashed",
        "rounded corners",
        "fill=red, thick",
        "fill=red, rounded corners",
        "opacity=0",
        "fill=white, label={n}",
        "minimum height=42mm",
        "inner xsep=1pt",
        "fill=isometry,draw,minimum width=1cm",
        "draw={rgb,255:red,31; green,119; blue,180}",
        "fill",
        "fill=",
        "fill=red!",
        "fill=1red",
        "text=\\cdots",
        "fill=red; draw=blue",
        "",
    ],
)
def test_a_box_style_with_anything_but_a_fill_is_unparsed(style):
    for box in [f"\\gate[style={{{style}}}]{{U}}", f"\\meter[style={{{style}}}]{{}}"]:
        source = beside_h(box, "& \\qw & \\qw & \\qw")
        assert gate_list(source).status == "partial", box


def test_the_appearance_list_is_for_gate_and_meter_only():
    for cell in [
        "\\ctrl[style={fill=red}]{1}",
        "\\targ[style={fill=red}]{}",
        "\\lstick[style={fill=red}]{a}",
        "\\swap[style={fill=red}]{1}",
        "\\phase[style={fill=red}]{\\theta}",
    ]:
        source = beside_h(cell, "& \\qw & \\qw & \\qw")
        assert gate_list(source).status == "partial", cell


@pytest.mark.parametrize(
    "pin", ["\\setwiretype{n}", "\\setwiretype{c}", "\\gateinput{0}", "\\gateoutput{1}"]
)
def test_wire_types_and_pins_stay_strict(pin):
    source = quantikz(f"& \\gate[2]{{U}} {pin} & \\qw", "& & \\qw")
    assert gate_list(source).status == "partial", pin


# ------------------------------------------------------------------ the kets that docingest allows

KET_SAMPLES = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "quantum-sim"
        / "tests"
        / "fixtures"
        / "basis_kets.json"
    ).read_text(encoding="utf-8")
)


def test_the_ket_samples_are_where_quantum_sim_keeps_them():
    assert sum(s["ket"] for s in KET_SAMPLES) >= 12
    assert sum(not s["ket"] for s in KET_SAMPLES) >= 30


@pytest.mark.parametrize("sample", KET_SAMPLES, ids=lambda s: repr(s["text"]))
def test_a_sample_is_a_basis_ket_as_the_list_says(sample):
    assert circuits._harmless_text(sample["text"]) is (sample["ket"] or not sample["text"].strip())


def test_the_text_of_a_gate_label_is_never_checked_as_a_ket():
    # a label is what the author wrote on a box; quantum-sim resolves it or refuses it
    source = quantikz("& \\gate{n} & \\gate{\\text{repeat}} & \\qw")
    assert gate_list(source).status == "parsed"


def test_the_random_text_of_a_cell_is_the_only_thing_the_vocabulary_checks():
    rng = random.Random(7)
    for _ in range(50):
        word = "".join(rng.choice("abcxyz0123") for _ in range(rng.randint(1, 6)))
        source = qcircuit(f"& {word} & \\gate{{H}} & \\qw")
        assert gate_list(source).status == "partial", word
        assert re.search(rf"unparsed on wire 1: {re.escape(word)}", " ".join(lines(source)))


# ------------------------------------------------------------------ classical wires (\cw)
#
# A Qcircuit cell's \qw or \cw (quantikz keeps both as legacy commands) draws the segment of the
# wire that enters the cell, so a wire is classical from a \cw on. A wire that turns classical
# with no measurement before it in its row, a classical wire that turns quantum again, and a gate
# or a control drawn on a classical wire are not something the list can say, and quantum-sim v1
# refuses a classical control: each one is unparsed. A \cw used to be consumed as "this row
# draws a wire", so \ctrl{1} \cw was listed as a quantum CNOT.


def both(*rows: str) -> list[str]:
    """The same rows as a Qcircuit and as a quantikz (the legacy \\cw)."""
    return [qcircuit(*rows), quantikz(*rows)]


@pytest.mark.parametrize(
    "rows",
    [
        # the probes of the verification (R-463): all were numeric
        ("\\lstick{c} & \\cw & \\ctrl{1} \\cw & \\cw", "\\lstick{q} & \\qw & \\targ & \\qw"),
        ("& \\cw & \\control \\cw \\cwx[1] & \\cw", "& \\qw & \\targ & \\qw"),
        ("& \\gate{H} & \\cw",),
        ("& \\cw & \\gate{H} & \\cw",),
        ("& \\cw & \\cw & \\cw", "& \\qw & \\gate{X} \\cwx[-1] & \\qw"),
        ("& \\cw & \\ctrl{1} \\cw & \\cw", "& \\qw & \\targ{} & \\qw"),
        # a row that is classical from its first cell has no measurement before it
        ("\\lstick{c} & \\cw & \\cw", "\\lstick{q} & \\gate{H} & \\qw"),
        ("& \\cw & \\meter & \\cw",),
    ],
)
def test_a_cw_with_no_measurement_before_it_in_its_row_is_unparsed(rows):
    for source in both(*rows):
        assert gate_list(source).status in ("partial", "unparsed"), source
        assert "unparsed" in gate_list(source).text or gate_list(source).status == "unparsed"


def test_a_cw_with_no_measurement_is_named_on_its_wire():
    source = qcircuit("& \\gate{H} & \\cw", "& \\gate{X} & \\qw")
    assert gate_list(source).status == "partial"
    assert lines(source) == [
        "Wires: 1; 2",
        "Step 1: H on wire 1; X on wire 2",
        "Step 2: unparsed on wire 1: \\cw",
    ]


def test_a_measurement_in_another_row_does_not_make_a_cw_legal():
    source = qcircuit("& \\gate{H} & \\meter & \\qw", "& \\gate{X} & \\cw & \\cw")
    assert gate_list(source).status == "partial"
    assert "unparsed on wire 2: \\cw" in " ".join(lines(source))


def test_a_measurement_after_the_cw_does_not_make_it_legal():
    source = qcircuit("& \\cw & \\meter & \\qw", "& \\gate{X} & \\qw & \\qw")
    assert gate_list(source).status == "partial"
    assert "unparsed on wire 1: \\cw" in gate_list(source).text


@pytest.mark.parametrize("measure", ["\\meter", "\\meter{}", "\\meterB{0}", "\\measure{M}"])
def test_a_cw_after_a_measurement_is_the_classical_wire_it_leaves_and_changes_nothing(measure):
    for source in both(
        f"& \\gate{{H}} & {measure} & \\cw & \\cw", "& \\gate{X} & \\qw & \\qw & \\qw"
    ):
        found = gate_list(source)
        assert found.status == "parsed", (source, found.text)
        assert "measure wire 1" in lines(source)[-1], source


def test_a_cw_after_a_measurement_is_the_same_list_as_the_wire_without_it():
    for source, plain in zip(
        both("& \\gate{H} & \\meter & \\cw & \\cw"),
        both("& \\gate{H} & \\meter"),
        strict=True,
    ):
        assert (
            lines(source)
            == lines(plain)
            == ["Wires: 1", "Step 1: H on wire 1", "Step 2: measure wire 1"]
        )


@pytest.mark.parametrize(
    ("after", "item"),
    [
        ("\\gate{H}", "gate H"),
        ("\\gate[2]{U}", "gate U"),
        ("\\ctrl{1}", "ctrl"),
        ("\\octrl{1}", "ctrl"),
        ("\\control", "ctrl"),
        ("\\targ", "targ"),
        ("\\swap{1}", "swap"),
        ("\\phase{\\theta}", "phase"),
        ("\\meter", "meter"),
    ],
)
def test_an_operation_on_a_classical_wire_is_an_unparsed_group(after, item):
    for source in both(f"& \\meter & \\cw & {after}", "& \\qw & \\qw & \\qw"):
        found = gate_list(source)
        assert found.status == "partial", (source, found.text)
        step = lines(source)[-1]
        assert step.startswith("Step 2: unparsed joined operation (wire 1: ") and item in step
        assert step.endswith("drawn on a classical wire)"), step
        [item_data] = found.circuits[0].parts[0][-1]
        assert item_data.kind == "unparsed_group" and item_data.source.endswith("classical wire")


def test_a_control_that_starts_on_a_classical_wire_makes_the_whole_group_unparsed():
    # the classical control drawn on the row of the measured wire: quantum-sim v1 refuses it
    source = qcircuit(
        "\\lstick{a} & \\meter & \\control \\cw \\cwx[1] & \\cw",
        "\\lstick{b} & \\qw & \\gate{X} & \\qw",
    )
    assert gate_list(source).status == "partial"
    assert lines(source)[-1] == (
        "Step 2: unparsed joined operation (wire 1: ctrl, wire 1: wire, wire 2: gate X, "
        "drawn on a classical wire)"
    )


def test_a_control_drawn_with_a_cw_in_its_own_cell_is_not_a_quantum_cnot():
    for source in both(
        "\\lstick{c} & \\cw & \\ctrl{1} \\cw & \\cw", "\\lstick{q} & \\qw & \\targ & \\qw"
    ):
        assert "CNOT" not in gate_list(source).text
        assert gate_list(source).status != "parsed"


def test_the_correction_on_a_quantum_row_is_still_read_as_classically_controlled():
    source = qcircuit(
        "\\lstick{a} & \\meter & \\cw & \\cw", "\\lstick{b} & \\qw & \\gate{X} \\cwx[-1] & \\qw"
    )
    assert gate_list(source).status == "parsed"
    assert lines(source)[-1] == "Step 2: X on wire 2, classically controlled by wire 1"


@pytest.mark.parametrize("mark", ["\\qw", "\\qw[-1]", "\\qw \\qwx[1]"])
def test_a_classical_wire_that_turns_quantum_again_is_unparsed(mark):
    source = qcircuit(f"& \\meter & \\cw & {mark}", "& \\qw & \\qw & \\qw")
    assert gate_list(source).status == "partial"
    assert "unparsed on wire 1: \\qw" in " ".join(lines(source))


def test_a_cell_that_says_both_qw_and_cw_is_unparsed_twice():
    source = qcircuit("& \\meter & \\cw \\qw & \\cw")
    assert gate_list(source).status == "partial"
    text = " ".join(lines(source))
    assert "unparsed on wire 1: \\cw" in text and "unparsed on wire 1: \\qw" in text


@pytest.mark.parametrize("mark", ["\\cw[2]", "\\cw[-2]", "\\cw[1]", "\\cw[0]"])
def test_a_cw_that_reaches_a_cell_other_than_the_one_on_its_left_is_unparsed(mark):
    source = qcircuit(f"& \\meter & {mark} & \\cw")
    assert gate_list(source).status == "partial"
    assert f"unparsed on wire 1: {mark}" in " ".join(lines(source))


def test_a_cw_that_reaches_the_cell_on_its_left_after_a_measurement_is_read():
    source = qcircuit("& \\meter & \\cw[-1] & \\cw")
    assert gate_list(source).status == "parsed"


def test_each_row_has_its_own_wire_type():
    source = qcircuit(
        "& \\gate{H} & \\meter & \\cw & \\cw", "& \\gate{X} & \\gate{Z} & \\gate{Y} & \\qw"
    )
    assert gate_list(source).status == "parsed"
    assert lines(source) == [
        "Wires: 1; 2",
        "Step 1: H on wire 1; X on wire 2",
        "Step 2: measure wire 1; Z on wire 2",
        "Step 3: Y on wire 2",
    ]


@pytest.mark.parametrize(
    "cell", ["\\mbox{\\cw}", "{\\cw}", "\\scalebox{.5}{\\cw}", "\\hspace{1em}\\cw", "\\relax \\cw"]
)
def test_a_cw_in_a_wrapper_is_typed_like_any_other(cell):
    source = qcircuit(f"& \\gate{{H}} & {cell}", "& \\gate{X} & \\qw")
    assert gate_list(source).status == "partial"
    assert "unparsed on wire 1: \\cw" in " ".join(lines(source))
    measured = qcircuit(f"& \\meter & {cell}", "& \\gate{X} & \\qw")
    assert gate_list(measured).status == "parsed"


def test_a_cw_of_a_quantikz_row_is_the_same_as_in_qcircuit():
    assert gate_list(quantikz("& \\gate{H} & \\cw")).status == "partial"
    assert gate_list(quantikz("& \\meter{} & \\cw & \\gate{H}")).status == "partial"


# ------------------------------------------------------------------ text outside the circuits
#
# The consumption check covers the whole block, not only the circuit environment: a token outside
# it that no rule takes (a power, a tensor mark, an \underbrace, brackets, a macro definition,
# any text) can say that the circuit is repeated or is a part of another, so every circuit of the
# block is partial. The rules that take a token are a short closed list of wrappers that have no
# effect.

H_CIRCUIT = quantikz("& \\gate{H} & \\qw")
X_CIRCUIT = quantikz("& \\gate{X} & \\qw")


def outside_item(source: str) -> list[str]:
    """The lines of the list that name text outside a circuit."""
    return [x for x in gate_list(source).text.split("\n") if "outside the circuit" in x]


@pytest.mark.parametrize(
    ("before", "after"),
    [
        # the verification's probes (R-463)
        ("$\\left(", "\\right)^{\\otimes n}$"),
        ("", "^{\\times 3}"),
        ("$\\underbrace{", "}_{n\\text{ times}}$"),
        ("\\left[", "\\right]^k"),
        ("", "\\quad (\\text{repeat } n \\text{ times})"),
        ("$(", ")^{2}$"),
        ("\\newcommand{\\x}{1}\n", ""),
        # macros defined before the circuit
        ("\\renewcommand{\\qw}{\\gate{X}}\n", ""),
        ("\\def\\qw{\\gate{X}}\n", ""),
        ("\\let\\targ\\ctrl\n", ""),
        ("\\renewcommand{\\gate}[1]{\\gate{#1}\\gate{#1}}\n", ""),
        # brackets, a tensor power, text of any kind
        ("[", "]"),
        ("(", ")^{\\otimes 3}"),
        ("", " \\otimes \\ket{0}"),
        ("Repeat $k$ times:\n", ""),
        ("", "\nrepeated 10 times"),
        ("{", "}"),
        ("\\mbox{", "}"),
        ("\\scalebox{0.5}{", "}"),
        ("\\begin{figure}\\centering\n", "\n\\caption{three rounds}\\end{figure}"),
        ("\\begin{align}\n", "\n\\end{align}"),
        ("\\begin{tikzpicture}\\node{", "};\\end{tikzpicture}"),
        ("\\documentclass{article}\n", ""),
        ("", "\\\\"),
        ("", "$^n$"),
        ("", "..."),
        ("", "\u22ee"),
    ],
)
def test_text_outside_the_circuit_makes_every_circuit_of_the_block_partial(before, after):
    source = before + H_CIRCUIT + after + "\n" + X_CIRCUIT
    found = gate_list(source)
    assert found.n_circuits == 2 and found.status == "partial", source
    assert [c.status for c in found.circuits] == ["partial", "partial"], source
    assert len(outside_item(source)) == 2, found.text
    for circuit in found.circuits:
        [item] = circuit.parts[-1][-1]
        assert item.kind == "unparsed" and item.source.startswith("outside the circuit: ")
        assert item.targets == ()


def test_the_text_outside_is_named_in_the_last_step_of_each_circuit():
    source = "$\\left(" + H_CIRCUIT + "\\right)^{\\otimes n}$"
    assert lines(source) == [
        "Wires: 1",
        "Step 1: H on wire 1",
        "Step 2: unparsed on no wire: outside the circuit: \\left( \\right)^{\\otimes n}",
    ]
    data = gate_list(source).circuits[0]
    assert data.status == "partial"
    assert [op.kind for step in data.parts[0] for op in step] == ["gate", "unparsed"]


def test_the_text_outside_is_in_the_part_after_a_drawn_equality_too():
    source = quantikz("& \\gate{H} & \\qw & = & \\gate{X} & \\qw") + "^{\\otimes 2}"
    found = gate_list(source)
    assert found.status == "partial"
    assert lines(source)[-2:] == [
        "Step 1: X on wire 1",
        "Step 2: unparsed on no wire: outside the circuit: ^{\\otimes 2}",
    ]


@pytest.mark.parametrize(
    ("kind", "circuit"),
    [
        ("qcircuit", qcircuit("& \\gate{H} & \\qw")),
        ("tikzcd", "\\begin{tikzcd}\n\\lstick{a} & \\gate{H} & \\qw\n\\end{tikzcd}"),
        ("yquant", yquant("h a;")),
    ],
)
def test_text_outside_a_circuit_of_each_kind_is_unparsed(kind, circuit):
    found = gate_list(circuit + "^{\\times 3}")
    assert found.status == "partial" and found.circuits[0].kind == kind
    assert found.circuits[0].status == "partial"
    last = found.text.split("\n")[-1]
    assert last.endswith("outside the circuit: ^{\\times 3}")
    assert last.startswith("Step 2: unparsed: " if kind == "yquant" else "Step 2: unparsed on no")


# What a block may hold outside its circuits without changing them: the delimiters of math, the
# wrappers of a figure without a caption, a label and spacing. Each leaves the list as it was.
NO_EFFECT_AROUND = [
    ("$", "$"),
    ("$$", "$$"),
    ("\\[", "\\]"),
    ("\\(", "\\)"),
    ("$$\n", "\n$$"),
    ("\\begin{equation}\n", "\n\\end{equation}"),
    ("\\begin{equation*}", "\\end{equation*}"),
    ("\\begin{center}\n", "\n\\end{center}"),
    ("\\begin{figure}\n\\centering\n", "\n\\label{fig:circuit}\n\\end{figure}"),
    ("\\begin{figure}[htbp]\n", "\n\\end{figure}"),
    ("\\begin{figure*}[!t]\n", "\n\\end{figure*}"),
    ("\\centering\n", ""),
    ("", "\n\\label{eq:h}"),
    ("\\quad", "\\,\\;\\!\\qquad"),
    ("  \n\n", "\t\n   "),
    ("% a comment with ^{\\otimes n} in it\n", "% and another \\times 3\n"),
]


@pytest.mark.parametrize(("before", "after"), NO_EFFECT_AROUND)
def test_a_wrapper_that_has_no_effect_leaves_the_list_as_it_was(before, after):
    for circuit in (H_CIRCUIT, qcircuit("& \\gate{H} & \\qw"), yquant("h a;")):
        plain = gate_list(circuit)
        wrapped = gate_list(before + circuit + after)
        assert wrapped.status == plain.status == "parsed", (before, after)
        assert wrapped.text == plain.text
        assert wrapped.circuits == plain.circuits


def test_two_circuits_with_only_a_wrapper_or_blank_lines_between_are_both_parsed():
    source = "\\begin{center}\n" + H_CIRCUIT + "\n\n\\quad\n\n" + X_CIRCUIT + "\n\\end{center}"
    found = gate_list(source)
    assert found.status == "parsed" and found.n_circuits == 2


@pytest.mark.parametrize("between", ["=", "$=$", "\\to", "$\\Rightarrow$", "and", "\\otimes", "+"])
def test_a_sign_or_a_word_between_two_circuits_makes_both_partial(between):
    source = H_CIRCUIT + "\n" + between + "\n" + X_CIRCUIT
    found = gate_list(source)
    assert [c.status for c in found.circuits] == ["partial", "partial"], between


def test_a_circuit_that_could_not_be_read_stays_unparsed_beside_text():
    source = quantikz("& \\foo") + "^{n}" + "\n" + H_CIRCUIT
    found = gate_list(source)
    assert [c.status for c in found.circuits] == ["unparsed", "partial"]
    assert found.status == "partial"


def test_the_body_of_a_circuit_that_never_closes_is_not_outside_text():
    # the circuit that never closes owns the rest of the block: it is already unparsed
    source = H_CIRCUIT + "\n\\begin{quantikz}& \\gate{X}"
    found = gate_list(source)
    assert [c.status for c in found.circuits] == ["parsed", "unparsed"]
    assert not outside_item(source)
    source = H_CIRCUIT + "\n\\Qcircuit @C=1em"
    assert [c.status for c in gate_list(source).circuits] == ["parsed", "unparsed"]
    assert not outside_item(source)


def test_a_circuit_found_after_a_group_that_never_starts_is_still_listed():
    source = "\\Qcircuit @C=1em\n" + H_CIRCUIT
    found = gate_list(source)
    assert [c.status for c in found.circuits] == ["unparsed", "parsed"]
    assert not outside_item(source)


@pytest.mark.parametrize("space", ["\u00a0", "\u1680", "\u2003", "\u3000", "\u200b", "\ufeff"])
def test_a_space_character_that_is_not_ascii_outside_the_circuit_is_text(space):
    # white space outside is ASCII only, as in quantum-sim: any other character may hide a mark
    source = space + H_CIRCUIT
    assert gate_list(source).status == "partial"
    assert outside_item(source)


def test_the_text_of_a_comment_outside_the_circuit_is_not_text():
    source = "% repeat 3 times\n" + H_CIRCUIT + "\n% \\times 3"
    assert gate_list(source).status == "parsed"


def test_there_is_no_list_to_add_text_to_when_there_is_no_circuit():
    found = gate_list("^{\\otimes n} and words")
    assert found.status == "unparsed" and found.text == "" and found.circuits == ()


def test_the_text_outside_is_cut_to_the_line_the_other_unparsed_items_have():
    source = "x" * 500 + H_CIRCUIT
    [item] = outside_item(source)
    assert len(item.split(": ", 1)[1]) <= 80 + len("unparsed on no wire: ")


@pytest.mark.parametrize(
    ("kind", "body", "options"), [p for p in PARSED if p.values[0] != "tikzcd"]
)
def test_a_power_after_any_fixture_circuit_is_never_parsed(kind, body, options):
    circuit = rebuilt(kind, body, options)
    for outside in ("^{\\otimes n}", "^{\\times 3}", "\\underbrace{", "(", "\\def\\x{y}"):
        assert gate_list(circuit + outside).status != "parsed", outside
        assert gate_list(outside + circuit).status != "parsed", outside
    assert gate_list("$" + circuit + "$").status == "parsed"
    assert gate_list("\\[" + circuit + "\\]").status == "parsed"


# ------------------------------------------------------------------ a wire passing a box, a lone wire
#
# \linethrough marks a wire that passes through a box; a vertical wire with nothing joined to it in
# its column may do the same. Neither is dropped any more.


@pytest.mark.parametrize(
    "rows",
    [
        # the probes of the verification (R-463)
        ("& \\gate[3]{\\mathrm{SWAP}} & \\qw", "& \\linethrough & \\qw", "& & \\qw"),
        ("& \\gate[2]{\\mathrm{SWAP}} & \\qw", "& \\linethrough & \\qw"),
        ("& \\gate[3]{\\mathrm{iSWAP}} & \\qw", "& \\linethrough & \\qw", "& & \\qw"),
        ("& \\gate{H} & \\linethrough & \\qw",),
        ("& \\gate{H} & \\linethrough \\qw & \\qw",),
    ],
)
def test_linethrough_is_unparsed(rows):
    source = quantikz(*rows)
    assert gate_list(source).status == "partial"
    assert (
        "unparsed on wire" in gate_list(source).text and "\\linethrough" in gate_list(source).text
    )


def test_linethrough_is_not_spacing():
    assert "linethrough" not in circuits.SPACING


LONE_WIRES = [
    ("quantikz", "\\vqw{1}"),
    ("quantikz", "\\vcw{1}"),
    ("quantikz", "\\wire[d][1]{q}"),
    ("quantikz", "\\wire[d][1]{c}"),
    ("quantikz", "\\wire[u][1]{q}"),
    ("qcircuit", "\\qw \\qwx[1]"),
    ("qcircuit", "\\qw \\cwx[1]"),
    ("qcircuit", "\\qwx[1]"),
]


@pytest.mark.parametrize(("kind", "mark"), LONE_WIRES)
def test_a_vertical_wire_with_nothing_joined_to_it_is_unparsed(kind, mark):
    if "[u]" in mark:  # the wire goes up from the second row
        rows = ("& \\gate{H} & \\qw & \\qw", f"& \\qw & {mark} & \\qw")
    else:
        rows = ("& \\gate{H} & " + mark + " & \\qw", "& \\qw & \\qw & \\qw")
    source = (qcircuit if kind == "qcircuit" else quantikz)(*rows)
    found = gate_list(source)
    assert found.status == "partial", found.text
    assert "a vertical wire with nothing joined" in found.text
    flat = [op for step in found.circuits[0].parts[0] for op in step]
    assert [op.kind for op in flat].count("unparsed_group") == 1


def test_a_vertical_wire_that_joins_something_is_still_read():
    for source, step in [
        (qcircuit("& \\qswap & \\qw", "& \\qswap \\qwx[-1] & \\qw"), "SWAP wires 1 and 2"),
        (quantikz("& \\meter{} \\vcw{1} & \\qw", "& \\gate{X} & \\qw"), "classically controls"),
        (quantikz("& \\gate{H} \\vqw{1} & \\qw", "& \\ctrl{-1} & \\qw"), "controlled-H"),
    ]:
        assert gate_list(source).status == "parsed", source
        assert step in gate_list(source).text


# ------------------------------------------------------------------ signs between circuits

EQUALITY_SIGNS = ["=", "\\equiv", "\\simeq", "\\cong", "\\Leftrightarrow"]
ARROW_SIGNS = ["\\approx", "\\to", "\\mapsto", "\\Rightarrow", "\\rightarrow"]
EQUIVALENT = "the steps below draw the right-hand side, an equivalent circuit]"
NOT_COMPARED = (
    "the steps below draw the right-hand side, after an arrow or approximation sign, not compared]"
)


@pytest.mark.parametrize("sign", [*EQUALITY_SIGNS, *ARROW_SIGNS])
def test_an_arrow_between_two_circuits_is_not_called_an_equivalent_circuit(sign):
    expected = EQUIVALENT if sign in EQUALITY_SIGNS else NOT_COMPARED
    for source in (
        quantikz(f"& \\gate{{H}} & {sign} & \\gate{{X}} & \\qw"),
        qcircuit(f"& \\gate{{H}} & \\push{{{sign}}} \\qw & \\gate{{X}} & \\qw"),
        quantikz(f"& \\gate{{H}} & \\midstick[2,brackets=none]{{{sign}}} & \\gate{{X}} & \\qw"),
    ):
        text = gate_list(source).text
        assert f"[{sign}: {expected}" in text, text
        assert "Step 1: X on wire 1" in text
        [sep] = [
            op for step in gate_list(source).circuits[0].parts[1] for op in step if op.kind == "sep"
        ]
        assert sep.label == sign  # the data names the sign as drawn
