"""Gate lists: a plain-text reading of a quantum circuit's LaTeX source, as a pure function.

A circuit reaches the answer model as the author's exact code (``quantikz``, ``\\Qcircuit``,
``yquant`` or a ``tikzcd`` drawn as a circuit, kept as a fenced ``latex`` block by the LaTeX
converter). The model still misreads the grid of wires and columns: a controlled-Z drawn as two
dots comes out as a CNOT. :func:`gate_list` reads the grid the way a reader would and writes
one line per drawn column::

    Gate list (from the circuit code above; wires numbered top to bottom; ...):
    Wires: 1 = \\ket{q_0}; 2 = \\ket{q_1}
    Step 1: H on wire 1; H on wire 2
    Step 2: controlled-Z on wires 1 and 2 (control dots on both, symmetric: no target)

The list is added after the block at answer time (``application.ask``); the block itself is never
changed and nothing is stored.

It never guesses. A command it does not know (an author's macro, a ``\\ar`` arrow, a classical
bend) is written as ``unparsed on wire k: <source>`` at its step, a circuit with no readable grid
gets no list, and a block that holds several circuits lists each under "Circuit k of n". Labels
are copied as written, LaTeX included.

What it never guesses is checked structurally, not command by command. Every token of a circuit's
source (a command with its options and arguments, a run of text; so every cell and every row, also
a row that draws no wire) starts unconsumed. A token is consumed only by a rule that understood it:
the reading used it (an operation, a legend, a sign), or an explicit allowlist says it has no
effect on the circuit (spacing, a comment, a skipped frame, a size, a wire mark that only names a
wire). What no rule consumed is written as an unparsed item that names the token and its place, and
the circuit is partial. So a command the reader does not know, text it has no rule for, or a reader
that drops part of what it read, is refused by default; an allowlist is the only way to let
something through. A ``yquant`` statement is its own token: it is read by a rule of the ``qubit``
and ``cbit`` statements or of a gate, and every other one is unparsed.

The check covers the whole block, not only the circuit environment: any token outside it that no
rule takes (a power, brackets, a macro definition, a caption, any text) makes every circuit of
the block partial, and only a short closed list of wrappers with no effect is let through. The
type of a Qcircuit wire is carried along its row: a ``\\cw`` is a classical segment, and a gate
or control drawn on one, a wire that turns classical with no measurement before it in its row, and
a classical wire that turns quantum again are unparsed.

Order is the drawing order: columns of the grid (``&``) are steps, left to right, and columns
with only wires are skipped. Two gates in different columns may commute or run together on
hardware; the list does not say so. Labels drawn outside the grid (``\\gategroup`` boxes,
captions, TikZ nodes) are not in the list, but a caption or a node of the block is an unparsed item.

The same reading is also returned as data, :attr:`GateList.circuits`: for each circuit its wires,
its parts (the sides of a drawn equality) and its steps, each step a tuple of :class:`GateOp`
(kind, label as written, 1-based wires, controls, open controls). The text and the data come from
the same operations, one item at a time, so they cannot say different things; the data is not
shortened at ``MAX_STEPS`` and is never stored. :meth:`CircuitSteps.to_dict` writes it as plain
dicts and lists (the ``circuit/1`` format) for callers that must not import this module.

The work is linear in the size of the source: every bracket is matched once, the columns are
read sparsely and the vertical wires of a column are joined with a sweep, so no input makes it
quadratic. Output size is linear too: a gate over many wires is written as a range.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from heapq import heappop, heappush
from typing import NamedTuple

__all__ = [
    "CIRCUIT_FORMAT",
    "MAX_GATE_WIRES",
    "MAX_STEPS",
    "CircuitSteps",
    "CodeBlock",
    "GateList",
    "GateOp",
    "add_gate_lists",
    "circuit_code_blocks",
    "gate_list",
]

MAX_STEPS = 60  # a longer circuit is listed up to here, then "... N more lines not listed"
MAX_GATE_WIRES = 128  # a gate over more wires is listed in the text, but as data it is "unparsed"
MAX_REGISTER = 128  # wires of one yquant register that are listed; a larger one is "unparsed"
MAX_NUMBER = 6  # digits of a count or an offset that are read; a longer number is "unparsed"
RANGE_FROM = 12  # more consecutive wires than this are written "wires a to b"
GRID_ENVS = ("quantikz", "tikzcd")
CIRCUIT_FORMAT = "circuit/1"  # the "format" of CircuitSteps.to_dict()

HEADER = "Gate list (from the circuit code above; wires numbered top to bottom; {unit}):"
UNIT_GRID = "one step per drawn column, left to right"
UNIT_STATEMENT = "one step per statement, in order"


# ------------------------------------------------------------------ low-level TeX reading


def strip_comments(s: str) -> str:
    """``s`` without ``%`` comments; a ``%`` after an even number of backslashes starts one."""
    return re.sub(r"(?<!\\)((?:\\\\)*)%[^\n]*", r"\1", s)


def _pairs(s: str, open_: str, close: str) -> dict[int, int]:
    """For each ``open_`` of ``s`` that closes, the index just past its ``close``.

    A backslash escapes the next character. One pass with a stack, so a scanner that asks for the
    end of every group never walks to the end of the text again and again."""
    found: dict[int, int] = {}
    stack: list[int] = []
    j, n = 0, len(s)
    while j < n:
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == open_:
            stack.append(j)
        elif c == close and stack:
            found[stack.pop()] = j + 1
        j += 1
    return found


def split_top(s: str, sep: str) -> list[str]:
    """Split ``s`` at ``sep`` (``&`` or the line break ``\\\\``) outside braces."""
    squares = _pairs(s, "[", "]") if sep == "\\\\" else {}
    parts: list[str] = []
    depth, start, j = 0, 0, 0
    while j < len(s):
        c = s[j]
        if c == "\\":
            if sep == "\\\\" and s.startswith("\\\\", j) and depth == 0:
                parts.append(s[start:j])
                j += 2
                k = j  # row spacing: \\[2mm]
                while k < len(s) and s[k] in " \t\n":
                    k += 1
                # only a length is spacing; any other bracket stays in the next row, as text
                if (
                    k < len(s)
                    and s[k] == "["
                    and k in squares
                    and _SPACING_OPTION.fullmatch(s[k : squares[k]])
                ):
                    j = squares[k]
                start = j
                continue
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth = max(0, depth - 1)
        elif c == "&" and sep == "&" and depth == 0:
            parts.append(s[start:j])
            start = j + 1
        j += 1
    parts.append(s[start:])
    return parts


@dataclass
class Tok:
    """One token of a cell: a command with its options and arguments, or a run of text.

    ``used`` is the consumption check. Every token of a circuit's source starts unconsumed and is
    marked only by a rule that understood it: a reader that made an operation or a fact from it
    (the operation or the fact is then marked when the list says it), or an explicit allowlist
    that says it has no effect on the circuit (spacing, a comment, a skipped frame). A token that
    no rule marked is written as an unparsed item (see :func:`_unconsumed`), so a command or text
    that the reader does not know is refused by default."""

    name: str  # command name without backslash, or "" for plain text
    opts: list[str] = field(default_factory=list)
    args: list[str] = field(default_factory=list)
    src: str = ""
    used: bool = field(default=False, compare=False)


def _use(*toks: Tok) -> None:
    """Mark ``toks`` consumed: a rule has understood them."""
    for t in toks:
        t.used = True


# mandatory brace arguments of the commands read; -1 = take one brace group only if it is there
ARITY = {
    "gate": 1, "ctrl": 1, "octrl": 1, "ctrlo": 1, "swap": 1, "phase": 1, "lstick": 1, "rstick": 1,
    "push": 1, "measure": 1, "measureD": 1, "meterD": -1, "meterB": 1, "multigate": 2, "sgate": 2,
    "ghost": 1, "nghost": 1, "slice": 1, "qwbundle": 1, "setwiretype": 1, "wireoverride": 1,
    "dstick": 1, "midstick": 1, "trash": 1, "vqw": 1, "vcw": 1, "wire": 1, "permute": 1,
    "gateinput": 1, "gateoutput": 1, "ustick": 1, "targ": -1, "control": -1, "ocontrol": -1,
    "targX": -1, "meter": -1, "qw": 0, "cw": 0, "qswap": 0, "controlo": 0, "ground": -1, "qwx": 0,
    "cwx": 0, "barrier": 1, "cwbend": -1, "hphantom": 1, "vphantom": 1, "hspace": 1, "vspace": 1,
    "inputgroupv": 5, "inputgroupv*": 5, "inputgroup": 4, "gategroup": -1, "cds": 2,
    "linethrough": 0, "rule": 2, "phantom": 1,
}  # fmt: skip
QCIRCUIT_GATEGROUP = 6  # \gategroup{r1}{c1}{r2}{c2}{sep}{style} in Qcircuit
SPACING = frozenset(
    {
        *("hspace", "vspace", "hphantom", "vphantom", "phantom", "quad", "qquad", "rule", ",", ";"),
        *("!", " ", "centering", "small", "footnotesize", "scriptsize", "large", "Large"),
        *("normalsize", "hspace*", "vspace*"),
    }
)
ELLIPSIS = frozenset({"vdots", "cdots", "ldots", "dots", "ddots", "iddots", "hdots", "dotsb"})
MATHISH = frozenset(
    {
        *("alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta", "eta", "theta"),
        *("vartheta", "iota", "kappa", "lambda", "mu", "nu", "xi", "pi", "rho", "sigma", "tau"),
        *("upsilon", "phi", "varphi", "chi", "psi", "omega", "Gamma", "Delta", "Theta", "Lambda"),
        *("Xi", "Pi", "Sigma", "Upsilon", "Phi", "Psi", "Omega", "in", "left", "right", "ket"),
        *("bra", "braket", "rangle", "langle", "frac", "text", "textrm", "mathrm", "mathcal"),
        *("mathbb", "mathbf", "bm", "boldsymbol", "hat", "tilde", "bar", "dagger", "dag"),
        *("otimes", "oplus", "pm", "mp", "sqrt", "prime", "cdot", "times", "displaystyle"),
        *("textbf", "textit", "emph", "rm", "bf", "it", "sum", "prod", "infty", "ell", "partial"),
        *("nabla", "vec", "overline", "underline", "mathit", "mathsf", "textsf", "scriptstyle"),
        *("textstyle", "uparrow", "downarrow", "leftarrow", "Tilde", "propto", "neq", "leq"),
        *("geq", "approx", "sim", "dots", "ldots", "cdots", "vdots", "emptyset", "log", "exp"),
        *("sin", "cos", "max", "min", "lceil", "rceil", "lfloor", "rfloor"),
    }
)
WRAPPERS = frozenset(
    {
        *("raisebox", "mbox", "makebox", "scalebox", "textcolor", "color", "smash", "resizebox"),
        *("rotatebox", "framebox", "fbox"),
    }
)
IGNORED = frozenset(
    {"label", "protect", "nonumber", "notag", "relax", "hfill", "vfill", "noindent", "strut"}
)
SEPARATOR_COMMANDS = frozenset(
    {
        "equiv",
        "approx",
        "cong",
        "simeq",
        "Rightarrow",
        "rightarrow",
        "to",
        "mapsto",
        "Leftrightarrow",
    }
)
EQUALS_TEXT = re.compile(
    r"^\$?\s*(=|\\equiv|\\approx|\\cong|\\simeq|\\Leftrightarrow|\\Rightarrow|\\rightarrow|"
    r"\\to|\\mapsto)\s*\$?$"
)
_DOTS_TEXT = re.compile(r"\$?\s*\\?(vdots|cdots|ldots|dots|ddots|iddots)?\s*\$?|[.\s]*")
_DOTS_TOKEN = re.compile(r"\$?\s*\\?(vdots|cdots|ldots|dots)?\s*\$?|[.\s]*")
_SPACING_OPTION = re.compile(
    r"\[\s*[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\s*(?:pt|em|ex|cm|mm|in|bp|pc)?\s*\]"
)
_SPACE_ESCAPES = re.compile(r"\\[ ,;:!]")
_SPACE_ONLY = re.compile(
    r"(\\[,;:! ]|\\q?quad|\\[hv]?space\{[^}]*\}|\\rule(\{[^}]*\}){2}|\\[hv]?phantom\{[^}]*\}|\s)*"
)
_COMMAND = re.compile(r"\\([a-zA-Z]+\*?)")
_LABEL = re.compile(r"\\label\s*\{([^{}]*)\}")


def is_text_token(t: Tok) -> bool:
    return t.name == "" or t.name in MATHISH or (len(t.name) == 1 and not t.name.isalpha())


def _read_arguments(
    cell: str,
    j: int,
    name: str,
    qcircuit: bool,
    *,
    braces: dict[int, int],
    squares: dict[int, int],
) -> tuple[list[str], list[str], int]:
    """Options ``[..]`` and brace arguments of the command whose name ends at ``j`` (``braces``
    and ``squares`` match the groups of ``cell``); returns them and the index just past the last
    one."""
    n = len(cell)
    arity = ARITY.get(name)
    if name == "gategroup" and qcircuit:
        arity = QCIRCUIT_GATEGROUP
    want = arity if arity is not None and arity >= 0 else (1 if arity == -1 else None)
    opts: list[str] = []
    args: list[str] = []
    while True:
        k = j
        while k < n and cell[k] in " \t\n":
            k += 1
        # options may sit between the arguments too (quantikz \gate[2][3cm]{U})
        if k < n and cell[k] == "[" and (want is None or len(args) < max(want, 1)):
            if k not in squares:
                break
            opts.append(cell[k + 1 : squares[k] - 1])
            j = squares[k]
            continue
        if k < n and cell[k] == "{":
            # an unknown command takes its groups: its source is reported
            full = arity is not None and (
                arity == 0 or (arity == -1 and len(args) >= 1) or (arity > 0 and len(args) >= arity)
            )
            if full or k not in braces:
                break
            args.append(cell[k + 1 : braces[k] - 1])
            j = braces[k]
            continue
        break
    return opts, args, j


def tokenize(cell: str, qcircuit: bool) -> list[Tok]:
    """The commands and the runs of text of one cell."""
    toks: list[Tok] = []
    braces, squares = _pairs(cell, "{", "}"), _pairs(cell, "[", "]")
    i, n = 0, len(cell)
    while i < n:
        c = cell[i]
        if c in " \t\n":
            i += 1
            continue
        m = _COMMAND.match(cell, i) if c == "\\" else None
        if m:
            end = i + len(m.group(0))
            opts, args, j = _read_arguments(
                cell, end, m.group(1), qcircuit, braces=braces, squares=squares
            )
            toks.append(Tok(m.group(1), opts, args, cell[i:j]))
            i = j
            continue
        if c == "\\":  # \, \; \\ \$ ...
            nxt = cell[i + 1] if i + 1 < n else " "
            toks.append(Tok(" " if nxt.isspace() else nxt, [], [], cell[i : i + 2]))
            i += 2
            continue
        j = i  # plain text up to the next command
        while j < n and cell[j] != "\\":
            if cell[j] == "{":
                j = braces.get(j, n)
                continue
            j += 1
        text = cell[i:j].strip()
        if text:
            toks.append(Tok("", [], [], text))
        i = j
    return toks


# ------------------------------------------------------------------ cells to operations


@dataclass
class Op:
    # gate, ctrl, targ, swap, meter, phase, text, unparsed, sep, ellipsis, cover, discard, note,
    # wire
    kind: str
    label: str = ""
    link: list[int] = field(default_factory=list)  # relative rows joined by a vertical wire
    span: int = 1  # wires covered (multi-wire gate)
    open_: bool = False
    classical: bool = False
    src: str = ""
    tag: str = ""  # a label drawn next to it (\dstick)
    on_classical: bool = False  # drawn on a wire that a \cw made classical
    toks: list[Tok] = field(
        default_factory=list, repr=False, compare=False
    )  # what it was read from


def _consume(o: Op) -> None:
    """The list has said ``o``: the tokens it was read from are consumed."""
    _use(*o.toks)


@dataclass
class Facts:
    """What a cell says about its wire rather than about a gate. A fact keeps the token it came
    from, which is consumed when the wire legend that shows it is written."""

    label: tuple[str, int] | None = None  # \lstick text and the wires it spans
    label_tok: Tok | None = None
    out: str = ""  # \rstick
    out_span: int = 1  # wires the \rstick label spans
    out_src: str = ""  # the source of that \rstick
    out_tok: Tok | None = None
    bundle: str | None = None  # \qwbundle
    bundle_tok: Tok | None = None
    wire: bool = False  # \qw or \cw: the cell draws a wire
    segments: list[tuple[Tok, str]] = field(default_factory=list)  # (\qw or \cw, "q" or "c")
    stick: str = ""  # \dstick: a label drawn under a pair of dots
    stick_tok: Tok | None = None
    pending: list[Tok] = field(default_factory=list)  # the tokens of the cell not yet consumed


def _one_line(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def clean_label(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == "$" and s[-1] == "$":
        s = s[1:-1].strip()
    return re.sub(r"\s+", " ", s)


def wires_option(opts: list[str]) -> int | None:
    """Wires of ``\\gate[wires=n]`` / ``\\gate[n]``: 1 with no option, None if unreadable."""
    for o in opts:
        m = re.search(r"(?<![a-z])wires\s*=\s*(\d+)", o) or re.match(r"\s*(\d+)\s*(,|$)", o)
        if m:
            digits = m.group(1)
            return int(digits) if len(digits) <= MAX_NUMBER else None
    return 1


def int_arg(s: str, default: int | None) -> int | None:
    s = (s or "").strip()
    if not s:
        return default
    if len(s) > MAX_NUMBER + 1:
        return None
    try:
        return int(s)
    except ValueError:
        return None


# What a wrapper (\raisebox, \scalebox, \textcolor ...) may carry besides the text it wraps: a
# length, a number, a colour or an alignment letter. Anything else is dropped content, so the
# wrapper is not opened and stays an unparsed command.
_FORMATTING = re.compile(
    r"\s*(?:[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\s*(?:pt|em|ex|cm|mm|in|bp|pc)?|!|"
    r"[a-z]+(?:![0-9]{1,3})*)?\s*"
)


def _formatting_only(t: Tok) -> bool:
    return all(_FORMATTING.fullmatch(x) for x in (*t.opts, *t.args[:-1]))


def _ignorable(t: Tok) -> bool:
    """Whether ``t`` is a ``\\label{..}`` or a switch that carries nothing (``\\relax``,
    ``\\strut``). One that swallowed a group (``\\hfill{n}``) is not: the group would be dropped."""
    if t.name == "label":
        return not t.opts and len(t.args) == 1
    return t.name in IGNORED and not t.opts and not t.args


def _expand(toks: list[Tok], qcircuit: bool) -> list[Tok]:
    """``toks`` with the formatting wrappers and bare groups replaced by what they hold. A wrapper,
    a group, a ``\\label`` and a column-spacing option are consumed: what they hold, if anything,
    is read as tokens of its own."""
    out: list[Tok] = []
    for t in toks:
        if t.name in WRAPPERS and t.args and _formatting_only(t):
            _use(t)
            out += tokenize(t.args[-1], qcircuit)
        elif t.name == "" and t.src.startswith("{") and t.src.endswith("}"):
            _use(t)
            out += tokenize(t.src[1:-1], qcircuit)
        elif _ignorable(t) or (t.name == "" and _SPACING_OPTION.fullmatch(t.src.strip())):
            _use(t)  # \label, \protect, a column-spacing option [0.5cm] (a length, not text)
        else:
            out.append(t)
    return out


# The only text the list lets stand on a wire: empty, or a ket of a computational-basis state, in
# the spellings that quantum-sim reads (``_BASIS_KET`` of its formats.py, copied; the samples of
# packages/quantum-sim/tests/fixtures/basis_kets.json give both the same verdicts). Any other text
# on a wire (a word, a bra, ``n``, ``\\times n``, ``(gates omitted)``, a name) can say that the
# drawing leaves gates out or that the wire is measured or postselected, and no list of such
# words is complete, so it is an unparsed item and the circuit is partial.
_KET_BAR = r"(?:\||\\lvert|\\vert)"
_BASIS_KET = re.compile(
    r"\\ket\s*\{\s*[01]\s*\}"
    r"|\\ket\s*[01]"
    rf"|\\left\s*{_KET_BAR}\s*[01]\s*\\right\s*\\rangle"
    rf"|{_KET_BAR}\s*[01]\s*\\rangle",
    re.ASCII,
)


def _harmless_text(label: str) -> bool:
    """Whether the text ``label`` on a wire is empty or a basis ket |0> or |1>."""
    text = re.sub(r"\s+", " ", label).strip()
    return not text or _BASIS_KET.fullmatch(re.sub(r"\s+", " ", _peeled(text))) is not None


def _text_op(src: str, toks: Sequence[Tok] = ()) -> Op | None:
    """The item a run of text and math makes: a separator, dots, text, or nothing. ``toks`` are
    the tokens of the run: the item carries them, or they are consumed here when it is nothing."""
    bare = _SPACE_ESCAPES.sub(" ", src).replace("~", " ").strip()
    if not bare:
        _use(*toks)
        return None
    if EQUALS_TEXT.match(src):
        return Op("sep", label=clean_label(src), toks=list(toks))
    if _DOTS_TEXT.fullmatch(bare):
        if bare.strip(" $"):
            return Op("ellipsis", toks=list(toks))
        _use(*toks)
        return None
    return Op("text", clean_label(src), src=src, toks=list(toks))


def cell_ops(cell: str, qcircuit: bool) -> tuple[list[Op], Facts]:
    """Operations of one cell, and the wire facts found in it (label, output label, bundle).
    ``Facts.pending`` holds the tokens that no rule has consumed yet: those of the operations and
    facts, which the list consumes when it says them."""
    facts = Facts()
    leaves = _expand(tokenize(cell, qcircuit), qcircuit)
    toks = []
    for t in leaves:
        if _spacing(t):
            _use(t)  # no effect on the circuit: a space, a width, an invisible box
        else:
            toks.append(t)
    ops = _read_cell(toks, facts)
    facts.pending = [t for t in leaves if not t.used]
    return ops, facts


def _read_cell(toks: list[Tok], facts: Facts) -> list[Op]:
    if toks and all(is_text_token(t) for t in toks):
        # a state, an output name or an interval: kept as one text item
        src = " ".join(t.src for t in toks).strip()
        if EQUALS_TEXT.match(src) or src in ("\\equiv", "="):
            return [Op("sep", label=clean_label(src), toks=list(toks))]
        op = _text_op(src, toks)
        return [op] if op else []
    ops: list[Op] = []
    runs: list[
        list[Tok]
    ] = []  # runs of text and math between circuit commands become one item each
    commands: list[Tok] = []
    pending: list[Tok] = []
    for t in toks:
        if is_text_token(t):
            pending.append(t)
            continue
        if pending:
            runs.append(pending)
            pending = []
        commands.append(t)
    if pending:
        runs.append(pending)
    for run in runs:
        if op := _text_op(" ".join(t.src for t in run).strip(), run):
            ops.append(op)
    for t in commands:
        _read_command(t, ops, facts)
    return ops


def _stick(t: Tok, facts: Facts) -> None:
    """``\\dstick{label}`` / ``\\ustick{label}``: a label drawn below or above a gate (Qcircuit's
    way to name a controlled phase drawn as two dots)."""
    lab = re.sub(r"\\hspace\{[^}]*\}", "", clean_label(t.args[0])).strip()
    lab = lab[2:-1] if lab.startswith("_{") and lab.endswith("}") else lab
    if lab:
        facts.stick, facts.stick_tok = lab, t
    else:
        _use(t)  # only spacing


def _read_command(t: Tok, ops: list[Op], facts: Facts) -> None:
    """Read the command ``t``. What it makes (operations, facts) carries it, and it is consumed
    when the list says them; a command that no rule below understands is an unparsed item."""
    made = len(ops)
    _dispatch(t, ops, facts)
    for o in ops[made:]:
        o.toks.append(t)


def _read_mark(t: Tok, ops: list[Op], facts: Facts) -> bool:
    """Read ``t`` if it is a mark that makes no operation: a label under dots, a plain wire or a
    frame that draws no gate. It is then consumed, or an unparsed item when it carries more."""
    nm = t.name
    if nm in ("dstick", "ustick") and not t.opts and t.args and clean_label(t.args[0]).strip():
        if facts.stick_tok is not None:  # a second label in a cell would replace the first
            ops.append(_unparsed(t))
        else:
            _stick(t, facts)
    elif nm in ("qw", "cw"):
        if all(_INTEGER.fullmatch(o) for o in t.opts) and not t.args:
            facts.wire = True
            facts.segments.append((t, nm[0]))
            _use(t)  # the wire it draws makes its row a wire; _RowWire types the segment
        else:
            ops.append(_unparsed(t))
    elif nm in _SKIPPED:
        if _skippable(t):
            _use(t)
        else:
            ops.append(_unparsed(t))
    else:
        return False
    return True


def _dispatch(t: Tok, ops: list[Op], facts: Facts) -> None:
    nm = t.name
    if _read_mark(t, ops, facts):
        return
    if nm in ELLIPSIS and not t.opts and not t.args:
        ops.append(Op("ellipsis"))
    elif nm in SEPARATOR_COMMANDS and not t.opts and not t.args:
        ops.append(Op("sep", label="\\" + nm))
    elif (reader := _READERS.get(nm)) is not None and _carries_only_read(t):
        reader(t, ops, facts)
    else:
        ops.append(Op("unparsed", src=t.src))


# Commands that draw no gate. A command is skipped only when everything it carries is harmless: a
# closed grammar per argument, never a list of the words or macros that mean "left out" (no list is
# complete: \\cdots, \\times n, "Repeat k times", "..."). Anything else is an unparsed item, so the
# circuit is partial.
# The kinds of the brace arguments of each skipped command, one letter each: i an integer, l a
# length, e nothing, t nothing or a bare font switch (\\sffamily), q the wire type q (the one every
# wire has by default), s a line style of a Qcircuit \\gategroup (-, -- or .). A label is text, so
# it is never read here: any text in it can say that a block repeats (n, x3, "Repeat k times").
_SKIPPED: dict[str, str] = {
    "setwiretype": "q",
    "wireoverride": "q",
    "gateinput": "t",
    "gateoutput": "t",
    "dstick": "e",
    "ustick": "e",
    "cds": "it",
    "inputgroupv": "iillt",
    "inputgroupv*": "iillt",
    "inputgroup": "iilt",
    "gategroup": "",  # quantikz: options and one label, see _skippable
    "nghost": "x",  # a phantom: its argument only sets the width of an invisible box
}
_INTEGER = re.compile(r"\s*[+-]?[0-9]{1,6}\s*")
_LENGTH = re.compile(r"\s*[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\s*(?:pt|em|ex|cm|mm|in|bp|pc)\s*")
_LINE_STYLE = re.compile(r"\s*(?:--|-|\.)\s*")
# the only text in a \\gategroup label that is not a name
_FONT_SWITCH = re.compile(r"\s*\\sffamily\s*")
_COLOR = r"(?:[a-z]+(?:![0-9]{1,3}){0,2}(?:![a-z]+)?|none)"
_LEN = r"-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:pt|em|ex|cm|mm)"
# what a quantikz \\gategroup option may hold: its size, the style of its frame and of its label,
# the items that the corpus uses. A label is text, so `label=` (even inside `style`) is not here.
_BOX_STYLE_ITEM = re.compile(
    rf"\s*(?:dashed|dotted|rounded corners|fill={_COLOR}|draw={_COLOR}|inner [xy]?sep={_LEN})?\s*"
)
_BOX_LABEL_STYLE_ITEM = re.compile(
    rf"\s*(?:label position=(?:above|below|left|right)|anchor=(?:north|south|east|west)"
    rf"|[xy]shift={_LEN}|text={_COLOR})?\s*"
)
_BOX_OPTION = re.compile(r"\s*(?:[0-9]{1,6}|(?:wires|steps)\s*=\s*[0-9]{1,6}|background)?\s*")
_BOX_STYLE = re.compile(r"\s*(?P<key>style|label style)\s*=\s*\{(?P<body>.*)\}\s*", re.S)


def _split_commas(s: str) -> list[str]:
    """``s`` split at the commas outside braces."""
    parts: list[str] = []
    depth, start = 0, 0
    for j, c in enumerate(s):
        if c == "{":
            depth += 1
        elif c == "}":
            depth = max(0, depth - 1)
        elif c == "," and depth == 0:
            parts.append(s[start:j])
            start = j + 1
    parts.append(s[start:])
    return parts


def _gategroup_options_ok(opt: str) -> bool:
    for item in _split_commas(opt):
        if _BOX_OPTION.fullmatch(item):
            continue
        style = _BOX_STYLE.fullmatch(item)
        if style is None:
            return False
        pattern = _BOX_STYLE_ITEM if style["key"] == "style" else _BOX_LABEL_STYLE_ITEM
        if not all(pattern.fullmatch(x) for x in _split_commas(style["body"])):
            return False
    return True


def _peeled(text: str) -> str:
    """``text`` without the braces and dollar signs that wrap it."""
    text = text.strip()
    while len(text) > 1 and (text[0], text[-1]) in (("{", "}"), ("$", "$")):
        text = text[1:-1].strip()
    return text


_ARGUMENT_PATTERNS = {"i": _INTEGER, "l": _LENGTH, "s": _LINE_STYLE}


def _argument_ok(kind: str, arg: str) -> bool:
    """Whether ``arg`` is of ``kind`` (the letters of ``_SKIPPED`` and ``_CARRIES``)."""
    pattern = _ARGUMENT_PATTERNS.get(kind)
    if pattern is not None:
        return pattern.fullmatch(arg) is not None
    if kind == "q":
        return arg.strip() == "q"
    text = _peeled(arg)
    if kind in "et":
        return not text or (kind == "t" and _FONT_SWITCH.fullmatch(text) is not None)
    return kind in "ux"  # u: the reader reads it; x: a cover's or a phantom's argument, never drawn


def _skippable(t: Tok) -> bool:
    """Whether the skipped command ``t`` carries nothing but harmless options and arguments."""
    if t.name == "gategroup":
        if len(t.args) == QCIRCUIT_GATEGROUP:  # \\gategroup{r1}{c1}{r2}{c2}{margin}{style}
            return not t.opts and all(map(_argument_ok, "iiiils", t.args))
        return (  # quantikz \\gategroup[wires,steps=n,style={..}]{label}
            len(t.opts) == 1
            and _gategroup_options_ok(t.opts[0])
            and len(t.args) == 1
            and _argument_ok("t", t.args[0])
        )
    kinds = _SKIPPED[t.name]
    return not t.opts and len(t.args) == len(kinds) and all(map(_argument_ok, kinds, t.args))


# What a spacing command may carry and still have no effect on the circuit: the kinds of its brace
# arguments (a width is a length, an invisible box holds anything because nothing of it is drawn).
# A command in ``SPACING`` with anything else (``\\small{\\gate{X}}``, ``\\quad{n}``) is not
# spacing: it swallowed content, so it is an unparsed item.
_SPACING_ARGS = {
    **dict.fromkeys(("hspace", "hspace*", "vspace", "vspace*"), "l"),
    **dict.fromkeys(("hphantom", "vphantom", "phantom"), "x"),
    "rule": "ll",
}


def _spacing(t: Tok) -> bool:
    """Whether ``t`` is a spacing command with only the options and arguments of a spacing."""
    if t.name not in SPACING:
        return False
    kinds = _SPACING_ARGS.get(t.name, "")
    lengths = all(_LENGTH.fullmatch(o) for o in t.opts) and len(t.opts) <= 1  # rule[raise]{w}{h}
    return (
        (lengths if t.name == "rule" else not t.opts)
        and len(t.args) == len(kinds)
        and all(map(_argument_ok, kinds, t.args))
    )


# What the options of an operation may hold. An option is read only when the reader of its command
# uses it (wires, open, vertical wire=c) or when it is the one fixed word of a kind that cannot
# hide content (how a bracket is drawn, a bundle's slash, a width). Everything else (wire style,
# label, style, nwires, a colour) is dropped by the reader, and a dropped option can carry a label
# or a dotted or hidden wire that says gates are left out: the operation is then unparsed.
# A closed appearance list for the style of a box: one fill, and nothing else. The fill is a base
# colour of xcolor, alone or mixed with white to a strength of 10 to 100 percent (cyan, red!10,
# gray!40). White and black are not in it, nor is a mix under 10 percent: a white fill hides the
# box, and a black one hides its label, which is drawn in black. A colour the author defined (a
# name such as mygreen or bg) is not in it either, for the definition may be white and is not in
# the block. `draw` and `text` are not in it: a pale line or label hides a gate (draw=white,
# text=gray!5). thick, dashed, rounded corners, sizes, a font and rgb values are unparsed too.
_FILL_COLOURS = (
    "blue|brown|cyan|darkgray|gray|green|lightgray|lime|magenta|olive|orange|pink|purple|red"
    "|teal|violet|yellow"
)
_APPEARANCE = re.compile(
    rf"\s*style\s*=\s*\{{\s*fill\s*=\s*(?:{_FILL_COLOURS})(?:![1-9][0-9]|!100)?\s*\}}\s*"
)
_OPTION_KINDS: dict[str, re.Pattern[str]] = {
    "wires": re.compile(r"\s*(?:[0-9]{1,6}|wires\s*=\s*[0-9]{1,6})\s*"),
    "appearance": _APPEARANCE,
    "open": re.compile(r"\s*open\s*"),
    "classical": re.compile(r"\s*vertical wire\s*=\s*[cq]\s*"),
    "brackets": re.compile(r"\s*brackets\s*=\s*(?:none|left|right|both)\s*"),
    "alternate": re.compile(r"\s*alternate\s*"),
    "length": _LENGTH,
    "offset": _INTEGER,
}


class _Carries(NamedTuple):
    first: tuple[str, ...]  # the kinds of option the first [..] may hold
    later: tuple[str, ...]  # the kinds the following [..] may hold
    args: str  # the kinds of the brace arguments; u is one that the reader reads


_WIRES = ("wires", "brackets")
_CARRIES: dict[str, _Carries] = {
    "lstick": _Carries(_WIRES, (), "u"),
    "rstick": _Carries(_WIRES, (), "u"),
    "midstick": _Carries(_WIRES, (), "u"),
    "push": _Carries(_WIRES, (), "u"),
    "qwbundle": _Carries(("alternate",), (), "u"),
    "gate": _Carries(("wires", "appearance"), ("length",), "u"),
    **dict.fromkeys(
        ("meter", "meterD", "meterB", "measure", "measureD"),
        _Carries(("wires", "appearance"), ("length",), "u"),
    ),
    "multigate": _Carries((), (), "uu"),
    "sgate": _Carries((), (), "uu"),
    "ghost": _Carries((), (), "x"),
    **dict.fromkeys(("ctrl", "octrl", "ctrlo"), _Carries(("open", "classical"), (), "u")),
    **dict.fromkeys(
        ("control", "ocontrol", "controlo", "targX", "qswap", "trash", "ground"),
        _Carries((), (), "e"),
    ),
    **dict.fromkeys(("swap", "targ"), _Carries((), (), "u")),
    "phase": _Carries((), (), "u"),
    **dict.fromkeys(("qwx", "cwx"), _Carries(("offset",), (), "")),
    **dict.fromkeys(("vqw", "vcw"), _Carries((), (), "u")),
    "slice": _Carries((), (), "u"),
    "barrier": _Carries(("length",), (), "i"),  # Qcircuit \\barrier[0em]{5}: only its height
    "permute": _Carries((), (), "u"),
}  # \\wire is not here: _r_wire reads its marks with a grammar of their own


def _options_ok(opts: list[str], first: tuple[str, ...], later: tuple[str, ...]) -> bool:
    """Whether every item of the options is of a kind in ``first`` (the first ``[..]``) or in
    ``later`` (the others), and no item gives the wires twice."""
    wires = 0
    for k, opt in enumerate(opts):
        for item in _split_commas(opt):
            if not item.strip():
                continue
            kind = next(
                (n for n in (first if k == 0 else later) if _OPTION_KINDS[n].fullmatch(item)), ""
            )
            if not kind:
                return False
            wires += kind == "wires"
    return wires <= 1


def _carries_only_read(t: Tok) -> bool:
    """Whether the options and arguments of ``t`` that its reader does not read are empty or of
    a kind that cannot hide content; a command that is not in ``_CARRIES`` is read as it is."""
    spec = _CARRIES.get(t.name)
    if spec is None:
        return True
    return (
        _options_ok(t.opts, spec.first, spec.later)
        and len(t.args) <= len(spec.args)
        and all(_argument_ok(k, a) for k, a in zip(spec.args, t.args, strict=False))
    )


def _unparsed(t: Tok) -> Op:
    return Op("unparsed", src=t.src)


def _first(t: Tok) -> str:
    return clean_label(t.args[0]) if t.args else ""


def _r_lstick(t: Tok, ops: list[Op], facts: Facts) -> None:
    if facts.label is not None:
        ops.append(Op("text", _first(t), src=t.src))
        return
    wires = wires_option(t.opts)
    if wires is None or wires < 1:
        ops.append(_unparsed(t))
    else:
        facts.label, facts.label_tok = (_first(t), wires), t


def _r_rstick(t: Tok, ops: list[Op], facts: Facts) -> None:
    wires = wires_option(t.opts)
    if wires is None or wires < 1 or facts.out:  # a second one would replace the first
        ops.append(_unparsed(t))
        return
    facts.out, facts.out_span, facts.out_src, facts.out_tok = _first(t), wires, t.src, t
    if not facts.out:
        _use(t)  # an empty label says nothing


def _r_bundle(t: Tok, ops: list[Op], facts: Facts) -> None:
    if facts.bundle is not None:  # a second one in a cell would replace the first
        ops.append(_unparsed(t))
    else:
        facts.bundle, facts.bundle_tok = _first(t), t


def _r_gate(t: Tok, ops: list[Op], facts: Facts) -> None:
    wires = wires_option(t.opts)
    ok = wires is not None and wires >= 1
    ops.append(Op("gate", _first(t), span=wires or 1) if ok else _unparsed(t))


def _r_multigate(t: Tok, ops: list[Op], facts: Facts) -> None:
    """Qcircuit ``\\multigate{extra rows}{U}``."""
    extra = int_arg(t.args[0], None) if t.args else None
    if extra is None or extra < 0 or len(t.args) < 2:
        ops.append(_unparsed(t))
    else:
        ops.append(Op("gate", clean_label(t.args[1]), span=extra + 1))


def _r_ghost(t: Tok, ops: list[Op], facts: Facts) -> None:
    ops.append(Op("cover"))


def _r_sgate(t: Tok, ops: list[Op], facts: Facts) -> None:
    """Qcircuit ``\\sgate{U}{row offset}``."""
    off = int_arg(t.args[1], None) if len(t.args) > 1 else None
    if off is None:
        ops.append(_unparsed(t))
    else:
        ops.append(Op("gate", clean_label(t.args[0]), link=[off]))


def _r_ctrl(t: Tok, ops: list[Op], facts: Facts) -> None:
    off = int_arg(t.args[0], None) if t.args else None
    if off is None:
        ops.append(_unparsed(t))
        return
    items = [item for o in t.opts for item in _split_commas(o)]
    classical = any(re.fullmatch(r"\s*vertical wire\s*=\s*c\s*", item) for item in items)
    open_ = t.name != "ctrl" or any(_OPTION_KINDS["open"].fullmatch(item) for item in items)
    ops.append(Op("ctrl", link=[off] if off else [], open_=open_, classical=classical))


def _r_control(t: Tok, ops: list[Op], facts: Facts) -> None:
    ops.append(Op("ctrl", open_=t.name != "control"))


def _r_targ(t: Tok, ops: list[Op], facts: Facts) -> None:
    """quantikz ``\\targ{-1}``: the target draws the vertical wire to its control, as ``\\ctrl{1}``
    does from the other end; the offset is read like a control's."""
    off = int_arg(t.args[0], 0) if t.args else 0
    ops.append(Op("targ", link=[off] if off else []) if off is not None else _unparsed(t))


def _r_swap(t: Tok, ops: list[Op], facts: Facts) -> None:
    off = int_arg(t.args[0], None) if t.args else None
    ops.append(Op("swap", link=[off] if off else []) if off is not None else _unparsed(t))


def _r_swap_end(t: Tok, ops: list[Op], facts: Facts) -> None:
    ops.append(Op("swap"))


def _r_meter(t: Tok, ops: list[Op], facts: Facts) -> None:
    """``\\meter[3]{}``: a measurement box over three wires, read as ``\\gate`` reads its span."""
    wires = wires_option(t.opts)
    ok = wires is not None and wires >= 1
    ops.append(Op("meter", _first(t), span=wires or 1) if ok else _unparsed(t))


def _r_phase(t: Tok, ops: list[Op], facts: Facts) -> None:
    ops.append(Op("phase", _first(t)))


def _r_qwx(t: Tok, ops: list[Op], facts: Facts) -> None:
    off = int_arg(t.opts[0], None) if t.opts else -1
    if off is None:
        ops.append(_unparsed(t))
    else:
        ops.append(Op("wire", link=[off], classical=(t.name == "cwx")))


def _r_vqw(t: Tok, ops: list[Op], facts: Facts) -> None:
    off = int_arg(t.args[0], None) if t.args else None
    if off is None:
        ops.append(_unparsed(t))
    else:
        ops.append(Op("wire", link=[off], classical=(t.name == "vcw")))


# What a \wire mark may carry and still be skipped: the closed grammar of a wire name that
# quantum-sim reads, copied from quantum_sim.formats (_SUBSCRIPT, _NAME_HEAD, _SYMBOL): one letter,
# alone or in \mathrm{} or \text{}, up to two digits, a subscript, primes. Never a word and never a
# list of dots macros: no list is complete. packages/quantum-sim/tests/fixtures/wire_names.json
# holds samples that both packages must give the same verdict on, so the copies cannot drift.
_WIRE_SUB = r"_(?:[A-Za-z0-9]|\{[A-Za-z0-9][A-Za-z0-9+,-]{0,7}\})"
_WIRE_HEAD = r"(?:[A-Za-z]|\\(?:mathrm|text)\{[A-Za-z]\})[0-9]{0,2}'*"
_WIRE_NAME = re.compile(rf"{_WIRE_HEAD}(?:{_WIRE_SUB})?'*")
# After the quoted name of a \wire label, only these TikZ options may follow, alone or in braces,
# apart or separated by commas: where the label sits and how far along the wire. Whatever else
# (dotted, dashed, draw=none, opacity, label=, a word) can change what the wire path shows, and a
# dotted or hidden wire can mean gates or wires that the figure leaves out.
_PLACEMENT = r"(?:(?:above|below)(?:\s+(?:left|right))?|left|right)"
_POSITION = r"pos\s*=\s*-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)"
_LABEL_OPTION = rf"(?:{_PLACEMENT}|{_POSITION})"
_LABEL_OPTIONS = rf"{_LABEL_OPTION}(?:\s*,\s*{_LABEL_OPTION})*"
_LABEL_GROUP = r"(?:\{\s*" + _LABEL_OPTIONS + r"\s*\}|" + _LABEL_OPTIONS + ")"
_WIRE_LABEL = re.compile(
    r'"(?P<name>[^"]*)"(?:\s*,?\s*' + _LABEL_GROUP + r"(?:\s*,\s*" + _LABEL_GROUP + r")*)?\s*"
)


def _plain_wire_text(text: str) -> bool:
    """Whether ``text`` is empty or a plain wire name, alone or between dollar signs or braces."""
    text = _peeled(text)
    return not text or _WIRE_NAME.fullmatch(text) is not None


def _plain_wire_label(opt: str) -> bool:
    opt = opt.strip()
    if not opt:
        return True
    label = _WIRE_LABEL.fullmatch(opt)
    return label is not None and _plain_wire_text(label["name"])


# The type argument of a quantikz \\wire[dir][len][label]{type}: q is a quantum wire, c a classical
# one, a the type the wire already has (automatic), b a bundle. A vertical wire (d, u) joins two
# rows, so it is q or c (a classical one makes what it joins classically controlled). A horizontal
# one (l, r) only continues the wire of its row, so it is q or a; c or b there changes the type of
# the wire. Any other argument is unparsed, and so is a label on a vertical wire: the name grammar
# below fits a wire on its row, not one between two rows.
_WIRE_TYPES = {"d": ("q", "c"), "u": ("q", "c"), "l": ("q", "a"), "r": ("q", "a")}


def _r_wire(t: Tok, ops: list[Op], facts: Facts) -> None:
    """quantikz ``\\wire[d][2]{q}``. A mark may be skipped only when its type is one the direction
    allows and its label and options say nothing but a wire name; any other label (dots in any
    spelling, a word, a macro) can stand for gates or wires the drawing leaves out, so the item is
    unparsed."""
    d = t.opts[0].strip() if t.opts else ""
    length = int_arg(t.opts[1], 1) if len(t.opts) > 1 else 1
    kind = t.args[0].strip() if len(t.args) == 1 else None
    labels = t.opts[2:]
    if length is None or kind not in _WIRE_TYPES.get(d, ()):
        ops.append(_unparsed(t))
    elif d in ("d", "u"):
        if any(o.strip() for o in labels):
            ops.append(_unparsed(t))
        else:
            ops.append(Op("wire", link=[length if d == "d" else -length], classical=kind == "c"))
    elif all(_plain_wire_label(o) for o in labels):
        _use(t)  # the wire of its row goes on, with a name drawn on it
    else:
        ops.append(_unparsed(t))


def _r_push(t: Tok, ops: list[Op], facts: Facts) -> None:
    txt = _first(t)
    txt = "" if _SPACE_ONLY.fullmatch(txt) else txt
    if EQUALS_TEXT.match(txt):  # \midstick[2,brackets=none]{=}, \push{=}: two circuits side by side
        ops.append(Op("sep", label=txt))
    elif txt.strip("\\ ") in ELLIPSIS or txt == "":
        ops.append(Op("ellipsis") if txt else Op("text", ""))
    else:
        ops.append(Op("text", txt, src=t.src))


def _r_discard(t: Tok, ops: list[Op], facts: Facts) -> None:
    ops.append(Op("discard"))


def _r_slice(t: Tok, ops: list[Op], facts: Facts) -> None:
    lab = clean_label(t.args[-1]) if t.name == "slice" and t.args else ""
    ops.append(Op("note", lab))


def _r_permute(t: Tok, ops: list[Op], facts: Facts) -> None:
    """quantikz ``\\permute{2,1}``: one gate over as many wires as the list has items."""
    items = [x for x in re.split(r"[,\s]+", _first(t)) if x]
    ops.append(
        Op("gate", "permutation " + _first(t) if t.args else "permutation", span=max(1, len(items)))
    )


_READERS: dict[str, Callable[[Tok, list[Op], Facts], None]] = {
    "lstick": _r_lstick,
    "rstick": _r_rstick,
    "qwbundle": _r_bundle,
    "gate": _r_gate,
    "multigate": _r_multigate,
    "ghost": _r_ghost,
    "sgate": _r_sgate,
    **dict.fromkeys(("ctrl", "octrl", "ctrlo"), _r_ctrl),
    **dict.fromkeys(("control", "ocontrol", "controlo"), _r_control),
    "targ": _r_targ,
    "swap": _r_swap,
    **dict.fromkeys(("targX", "qswap"), _r_swap_end),
    **dict.fromkeys(("meter", "meterD", "meterB", "measure", "measureD"), _r_meter),
    "phase": _r_phase,
    **dict.fromkeys(("qwx", "cwx"), _r_qwx),
    **dict.fromkeys(("vqw", "vcw"), _r_vqw),
    "wire": _r_wire,
    **dict.fromkeys(("push", "midstick"), _r_push),
    **dict.fromkeys(("trash", "ground"), _r_discard),
    **dict.fromkeys(("slice", "barrier"), _r_slice),
    "permute": _r_permute,
}


# ------------------------------------------------------------------ the steps as data


@dataclass(frozen=True)
class GateOp:
    """One item of a step, as drawn: the data behind one ``;``-separated item of a step line.

    ``kind`` says what the item is and which words its line uses:

    * ``gate``: a named gate on ``targets`` (``label`` as the author wrote it, LaTeX included),
      with ``controls`` (on |1>) and ``open_controls`` (on |0>) when it is controlled. A gate
      drawn over several wires has all of them in ``targets``.
    * ``x``: an X drawn as a target (``\\targ``, ``cnot``), label ``X``; fields as in ``gate``.
    * ``z_dots``: control dots only, a controlled-Z with no target: the dots are ``controls`` and
      ``open_controls``, label ``Z``.
    * ``phase``: ``\\phase{label}`` on ``targets``, alone or joined to control dots (``controls``,
      ``open_controls``); several different labels in one item are joined with ``", "``.
    * ``dots_gate``: control dots that carry a label (Qcircuit ``\\dstick``): a symmetric two-qubit
      gate named ``label`` on ``targets``.
    * ``swap``: ``targets`` are the two crossed wires; ``controls`` make it a controlled swap.
    * ``measure``: a measurement of ``targets`` (``label`` is its option, often empty).
    * ``control``: a control dot with nothing joined to it (``controls`` holds its wire).
    * ``barrier`` and ``discard`` (yquant statements, with ``targets`` and the statement's label;
      in a grid a ``\\slice`` or ``\\barrier`` has no wire and a ``\\trash`` is ``discard`` on its
      wire), ``dots`` (a row of dots on ``targets``), ``text`` and ``sign`` (``label`` drawn on a
      wire without being a gate), and ``sep`` (the drawn equality that opens a part, in a step of
      its own).
    * ``unparsed``, ``unparsed_op``, ``unparsed_group`` and ``unparsed_outside``: something the
      reader did not understand; ``source`` keeps what it saw. A reader of this data must refuse
      every kind it does not know, these four included, and a circuit (or a part) that holds any
      of the four as a whole: skipping the item can leave a wrong unitary.

    ``targets`` and the controls are 1-based wire numbers, top to bottom, the numbering of the
    "Wires:" line; the wires of one item are in that order and none is named twice (an item that
    would name one twice is ``unparsed``). ``classical`` is True when
    the item is classically controlled (a classical wire, the result of a measurement, or in
    ``yquant`` a control on a ``cbit`` or on a wire measured earlier); then ``controls`` holds the
    wire that controls it when the drawing names one. ``source`` is the text
    the reading rests on and no other field holds: the code of an ``unparsed`` item, or the label
    drawn next to a gate (``\\dstick``), which a reader of this data must not ignore. A gate over
    more than ``MAX_GATE_WIRES`` wires is ``unparsed_op``."""

    kind: str
    label: str = ""
    targets: tuple[int, ...] = ()
    controls: tuple[int, ...] = ()
    open_controls: tuple[int, ...] = ()
    classical: bool = False
    source: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "label": self.label,
            "targets": list(self.targets),
            "controls": list(self.controls),
            "open_controls": list(self.open_controls),
            "classical": self.classical,
            "source": self.source,
        }


@dataclass(frozen=True)
class CircuitSteps:
    """One circuit of a block as data: what its part of the gate-list text says, item by item.

    ``wires`` are the legends of the "Wires:" line (``"1 = \\ket{q_0}"``; a wire without a legend
    is ``"2"``), one per numbered wire. ``parts`` are the sides of the drawn equalities; each part
    is a tuple of steps, each step a tuple of :class:`GateOp`. A part that a drawn equality opens
    starts with a step that holds one ``sep`` item, the sign as drawn. The steps are all of them;
    the text stops at ``MAX_STEPS`` lines. A circuit whose ``status`` is ``unparsed`` has no wires
    and no parts."""

    kind: str  # quantikz, qcircuit, tikzcd, yquant
    status: str  # parsed, partial, unparsed
    label: str  # the \label of the circuit, if any
    wires: tuple[str, ...]
    parts: tuple[tuple[tuple[GateOp, ...], ...], ...]

    def to_dict(self) -> dict[str, object]:
        """Plain dicts and lists, the ``circuit/1`` format: ``format``, ``kind``, ``status``,
        ``label``, ``wires`` (how many), ``wire_labels`` and ``parts`` (parts, steps, items)."""
        return {
            "format": CIRCUIT_FORMAT,
            "kind": self.kind,
            "status": self.status,
            "label": self.label,
            "wires": len(self.wires),
            "wire_labels": list(self.wires),
            "parts": [[[op.to_dict() for op in step] for step in part] for part in self.parts],
        }


# ------------------------------------------------------------------ wires and their numbers


def wires_text(nums: Sequence[int]) -> str:
    """ "wire 3", "wires 1 and 2", "wires 1, 2 and 3", or "wires 1 to 40" for a long run."""
    if not nums:
        return "no wire"
    if len(nums) == 1:
        return f"wire {nums[0]}"
    if len(nums) > RANGE_FROM and nums[-1] - nums[0] + 1 == len(nums):
        return f"wires {nums[0]} to {nums[-1]}"
    names = [str(x) for x in nums]
    if len(names) == 2:
        return f"wires {names[0]} and {names[1]}"
    return "wires " + ", ".join(names[:-1]) + " and " + names[-1]


Item = tuple[str, tuple[GateOp, ...]]  # the words of an item, and the operations they describe
_UNREAD: Item = ("", ())  # not a combination the list knows


class Names:
    """Wire numbers of a grid: the rows that draw something, numbered from 1 top to bottom."""

    def __init__(self, drawn: Sequence[int] = ()):
        self.drawn = list(drawn)  # sorted grid rows that are numbered
        self.number = {r: i + 1 for i, r in enumerate(self.drawn)}

    def num(self, r: int) -> int:
        return self.number.get(r, r + 1)

    def wire(self, r: int) -> str:
        return f"wire {self.num(r)}"

    def place(self, r: int) -> str:
        """Where something sits: its wire, or "no wire" on a row that draws none."""
        return self.wire(r) if r in self.number else "no wire"

    def numbers(self, rows: Iterable[int]) -> list[int]:
        """The numbers of ``rows``, each wire once, top to bottom."""
        return [self.num(r) for r in sorted(set(rows))]

    def in_order(self, rows: Iterable[int]) -> tuple[int, ...]:
        """The numbers of ``rows`` in the order given, repeats kept: as drawn."""
        return tuple(self.num(r) for r in rows)

    def wires(self, rows: Iterable[int]) -> str:
        return wires_text(self.numbers(rows))

    def _bounds(self, r: int, count: int) -> tuple[int, int]:
        return bisect_left(self.drawn, r), bisect_left(self.drawn, r + count)

    def span(self, r: int, count: int) -> str:
        """The wires of rows ``r`` to ``r + count - 1`` that exist and are numbered."""
        lo, hi = self._bounds(r, count)
        return wires_text(range(lo + 1, hi + 1))

    def span_numbers(self, r: int, count: int) -> tuple[int, ...] | None:
        """The same wires as numbers; None when they are more than ``MAX_GATE_WIRES``."""
        lo, hi = self._bounds(r, count)
        return tuple(range(lo + 1, hi + 1)) if hi - lo <= MAX_GATE_WIRES else None


def _too_wide(o: Op) -> GateOp:
    return GateOp("unparsed_op", o.label, source=f"{o.kind} over more than {MAX_GATE_WIRES} wires")


def describe_group(items: list[tuple[int, Op]], names: Names) -> Item:
    """:func:`describe_ops` plus the labels drawn next to the operation. Dots with a label are a
    two-qubit gate of that name, not a controlled-Z."""
    tags = [o.tag for _, o in items if o.tag]
    if not tags:
        return describe_ops(items, names)
    label = ", ".join(tags)
    ctrls = [r for r, o in items if o.kind == "ctrl"]
    if len(ctrls) == len(items) and len(ctrls) >= 2:
        return (
            f"two-qubit gate drawn as control dots on {names.wires(ctrls)} (symmetric, no "
            f"target), labelled {label}",
            (GateOp("dots_gate", label, tuple(names.numbers(ctrls))),),
        )
    text, ops = describe_ops(items, names)
    if not ops:
        return _UNREAD
    *head, last = ops
    if not last.kind.startswith("unparsed"):
        last = replace(last, source=label)
    return f"{text}, labelled {label}", (*head, last)


def _controls(ctrls: list[tuple[int, Op]], names: Names) -> str:
    closed = [r for r, o in ctrls if not o.open_]
    opened = [r for r, o in ctrls if o.open_]
    parts: list[str] = []
    if closed:
        parts.append(("control " if len(closed) == 1 else "controls ") + names.wires(closed))
    if opened:
        parts.append(
            ("open control (on |0>) " if len(opened) == 1 else "open controls (on |0>) ")
            + names.wires(opened)
        )
    return "; ".join(parts)


def _control_wires(
    ctrls: list[tuple[int, Op]], names: Names
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """The closed and the open controls as wire numbers, as drawn."""
    return (
        names.in_order(r for r, o in ctrls if not o.open_),
        names.in_order(r for r, o in ctrls if o.open_),
    )


def _measure_item(r: int, o: Op, names: Names) -> tuple[str, GateOp]:
    on = names.span(r, o.span)
    over = " (one measurement over these wires)" if o.span > 1 else ""
    text = f"measure {on}" + (f" ({o.label})" if o.label else "") + over
    targets = names.span_numbers(r, o.span)
    return text, (GateOp("measure", o.label, targets) if targets is not None else _too_wide(o))


def _measured(items: list[tuple[int, Op]], meters: list[tuple[int, Op]], names: Names) -> Item:
    """A measurement alone, or one whose result drives a gate through a classical wire."""
    if any(o.classical for _, o in items):
        rest = [(r, o) for r, o in items if o.kind in ("gate", "targ")]
        # anything else in the group (a quantum control, say) would be lost: not a known shape
        if len(meters) != 1 or len(rest) != 1 or len(items) != 2:
            return _UNREAD
        r, o = rest[0]
        gate = o.label if o.kind == "gate" else "X (\\targ)"
        measured, measure = _measure_item(*meters[0], names)
        targets = names.span_numbers(r, o.span)
        driven = (
            _too_wide(o)
            if targets is None
            else GateOp(
                "gate" if o.kind == "gate" else "x",
                o.label if o.kind == "gate" else "X",
                targets,
                controls=measure.targets,
                classical=True,
            )
        )
        text = f"{measured}; its result classically controls {gate} on {names.span(r, o.span)}"
        return text, (measure, driven)
    if len(items) == len(meters):
        pairs = [_measure_item(r, o, names) for r, o in meters]
        return "; ".join(text for text, _ in pairs), tuple(op for _, op in pairs)
    return _UNREAD


def _single(r: int, o: Op, names: Names) -> Item:
    if o.kind == "gate":
        on = names.span(r, o.span)
        text = f"{o.label} on {on}" + (" (one gate on these wires)" if o.span > 1 else "")
        targets = names.span_numbers(r, o.span)
        return text, (GateOp("gate", o.label, targets) if targets is not None else _too_wide(o),)
    wire = (names.num(r),)
    if o.kind == "targ":
        return f"X (NOT, drawn as \\targ) on {names.wire(r)}", (GateOp("x", "X", wire),)
    if o.kind == "phase":
        return f"phase {o.label} on {names.wire(r)}", (GateOp("phase", o.label, wire),)
    if o.kind == "ctrl":
        text = f"lone control dot on {names.wire(r)} (no gate joined to it in this column)"
        return text, (GateOp("control", controls=wire),)
    return _UNREAD


@dataclass
class _Mix:
    """The operations of one joined group, by kind."""

    ctrls: list[tuple[int, Op]]
    targs: list[int]
    gates: list[tuple[int, Op]]
    swaps: list[int]
    phases: list[tuple[int, Op]]
    cl: str  # " (classically controlled)" or ""


def _swap_text(mix: _Mix, names: Names) -> Item:
    if len(mix.swaps) != 2:
        return _UNREAD
    targets = names.in_order(mix.swaps)
    if mix.ctrls:
        controls = _controls(mix.ctrls, names)
        closed, opened = _control_wires(mix.ctrls, names)
        text = f"controlled-SWAP of {names.wires(mix.swaps)} ({controls}){mix.cl}"
        return text, (GateOp("swap", "", targets, closed, opened, bool(mix.cl)),)
    return f"SWAP {names.wires(mix.swaps)}", (GateOp("swap", "", targets),)


def _x_name(controls: int, opened: bool) -> str:
    """CNOT, Toffoli or an n-controlled X (the first two only with closed controls)."""
    if opened and controls > 1:
        return f"{controls}-controlled X"
    return {1: "CNOT", 2: "Toffoli (CCNOT)"}.get(controls, f"{controls}-controlled X")


def _not_text(mix: _Mix, names: Names) -> Item:
    """CNOT, Toffoli or an n-controlled X: controls and \\targ in one column."""
    if not mix.ctrls or (len(mix.ctrls) > 1 and len(mix.targs) > 1):
        return _UNREAD  # no control, or two gates drawn over one another
    name = _x_name(len(mix.ctrls), any(o.open_ for _, o in mix.ctrls))
    tgt = ("target " if len(mix.targs) == 1 else "targets ") + names.wires(mix.targs)
    closed, opened = _control_wires(mix.ctrls, names)
    text = f"{name}{mix.cl}: {_controls(mix.ctrls, names)}, {tgt}"
    return text, (GateOp("x", "X", names.in_order(mix.targs), closed, opened, bool(mix.cl)),)


def _controlled_gate_text(mix: _Mix, names: Names) -> Item:
    if len(mix.gates) != 1 or not mix.ctrls:
        return _UNREAD  # a gate joined to nothing, or several gates joined
    r, o = mix.gates[0]
    controls = _controls(mix.ctrls, names)
    text = f"controlled-{o.label}{mix.cl}: {controls}, target {names.span(r, o.span)}"
    targets = names.span_numbers(r, o.span)
    if targets is None:
        return text, (_too_wide(o),)
    closed, opened = _control_wires(mix.ctrls, names)
    return text, (GateOp("gate", o.label, targets, closed, opened, bool(mix.cl)),)


def _dots_text(mix: _Mix, names: Names) -> Item:
    """Control dots only: a controlled-Z, which has no target and is symmetric."""
    n = len(mix.ctrls)
    if n < 2:
        return _UNREAD  # one control whose wire leads nowhere: not a gate
    closed, opened = _control_wires(mix.ctrls, names)
    op = GateOp("z_dots", "Z", controls=closed, open_controls=opened, classical=bool(mix.cl))
    if any(o.open_ for _, o in mix.ctrls):
        controls = _controls(mix.ctrls, names)
        return f"controlled-Z with open controls{mix.cl}: {controls} (dots only, no target)", (op,)
    name = "controlled-Z" if n == 2 else f"multi-controlled Z ({n} wires)"
    both = "both" if n == 2 else "all"
    rows = names.wires([r for r, _ in mix.ctrls])
    return f"{name}{mix.cl} on {rows} (control dots on {both}, symmetric: no target)", (op,)


def _phase_text(mix: _Mix, names: Names) -> Item:
    labels = sorted({o.label for _, o in mix.phases})
    rows = names.wires([r for r, _ in mix.ctrls] + [r for r, _ in mix.phases])
    closed, opened = _control_wires(mix.ctrls, names)
    op = GateOp(
        "phase",
        ", ".join(labels),
        names.in_order(r for r, _ in mix.phases),
        closed,
        opened,
        bool(mix.cl),
    )
    return f"controlled-phase {', '.join(labels)}{mix.cl} on {rows} (symmetric dots)", (op,)


# in order: the kind a group must hold, the kinds it must not, and how it is written
_CASES: tuple[tuple[str, frozenset[str], Callable[[_Mix, Names], Item]], ...] = (
    ("swap", frozenset({"targ", "gate", "phase"}), _swap_text),
    ("targ", frozenset({"gate", "swap", "phase"}), _not_text),
    ("gate", frozenset({"targ", "swap", "phase"}), _controlled_gate_text),
    ("ctrl", frozenset({"targ", "gate", "swap", "phase"}), _dots_text),
    ("phase", frozenset({"targ", "gate", "swap"}), _phase_text),
)
_KNOWN_KINDS = ("ctrl", "targ", "gate", "swap", "meter", "phase", "wire")


def describe_ops(items: list[tuple[int, Op]], names: Names) -> Item:
    """One line for the operations of one column that a vertical wire (or one gate) joins, and the
    operations it names. No operations means the combination is not one the list knows."""
    kinds: dict[str, list[tuple[int, Op]]] = {}
    for r, o in items:
        kinds.setdefault(o.kind, []).append((r, o))
    if any(k not in _KNOWN_KINDS for k in kinds):
        return _UNREAD
    if "meter" in kinds:
        return _measured(items, kinds["meter"], names)
    if len(items) == 1 and not items[0][1].link:
        return _single(*items[0], names)
    mix = _Mix(
        ctrls=kinds.get("ctrl", []),
        targs=[r for r, _ in kinds.get("targ", [])],
        gates=kinds.get("gate", []),
        swaps=[r for r, _ in kinds.get("swap", [])],
        phases=kinds.get("phase", []),
        cl=" (classically controlled)" if any(o.classical for _, o in items) else "",
    )
    present = {
        name
        for name, found in (
            ("ctrl", mix.ctrls),
            ("targ", mix.targs),
            ("gate", mix.gates),
            ("swap", mix.swaps),
            ("phase", mix.phases),
        )
        if found
    }
    for need, forbidden, describe in _CASES:
        if need in present and not present & forbidden:
            return describe(mix, names)
    return _UNREAD


class _UnionFind:
    """Union-find over the rows of one column; a row exists once it is asked about."""

    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, a: int) -> int:
        self.parent.setdefault(a, a)
        root = a
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[a] != root:
            self.parent[a], a = root, self.parent[a]
        return root

    def union(self, a: int, b: int) -> None:
        self.parent[self.find(a)] = self.find(b)


class _Sweep:
    """Joins an anchor row to every row of ``rows`` strictly between two bounds, for many
    requests, in time linear in the number of rows: each request joins its anchor to the first
    row it covers, and a difference array marks the neighbours it covers, so a row is never
    visited once per request."""

    def __init__(self, rows: list[int], groups: _UnionFind):
        self.rows = rows
        self.groups = groups
        self.cover = [0] * (len(rows) + 1)

    def add(self, anchor: int, lo: int, hi: int) -> None:
        first, stop = bisect_right(self.rows, lo), bisect_left(self.rows, hi)
        if first < stop:
            self.groups.union(anchor, self.rows[first])
            self.cover[first] += 1
            self.cover[stop - 1] -= 1

    def finish(self) -> None:
        running = 0
        for p in range(len(self.rows) - 1):
            running += self.cover[p]
            if running > 0:
                self.groups.union(self.rows[p], self.rows[p + 1])


# ------------------------------------------------------------------ grid to steps


@dataclass
class Circuit:
    kind: str
    status: str = "parsed"  # parsed, partial, unparsed
    reason: str = ""
    label: str = ""  # the \label inside the circuit's source, if it has one
    wires: list[str] = field(default_factory=list)
    parts: list[list[str]] = field(default_factory=list)  # parts separated by '=', lines of steps
    steps: list[list[tuple[GateOp, ...]]] = field(default_factory=list)  # the same, as data
    n_unparsed: int = 0
    n_items: int = 0


_JOINING = ("ctrl", "targ", "swap", "phase")  # what a vertical wire passing a row connects to
_LOOSE = ("text", "ellipsis", "discard", "note", "unparsed", "sep")


@dataclass
class _Grid:
    rows: list[dict[int, list[Op]]]  # per row: column -> its operations (columns with some only)
    labels: dict[int, str] = field(default_factory=dict)
    spans: dict[int, int] = field(default_factory=dict)
    outs: dict[int, str] = field(default_factory=dict)
    out_spans: dict[int, int] = field(default_factory=dict)
    bundles: dict[int, str] = field(default_factory=dict)
    blank: list[bool] = field(default_factory=list)
    dots: list[bool] = field(default_factory=list)
    # the token and the column of each wire fact the legends show: (kind, row) -> (token, column)
    facts: dict[tuple[str, int], tuple[Tok, int]] = field(default_factory=dict)
    # every token of the grid that no rule had consumed when its cell was read: (token, row, column)
    ledger: list[tuple[Tok, int, int]] = field(default_factory=list)


def _attach_stick(ops: list[Op], previous: list[Op], label: str, tok: Tok) -> None:
    """Qcircuit writes the label of a dot gate in its own cell or in the cell to its right: it
    names the gate in this cell, else the dots in the cell before, else it is text."""
    named = [o for o in ops if o.kind in ("ctrl", "targ", "gate", "phase", "swap")]
    left = [o for o in previous if o.kind in ("ctrl", "phase")]
    if named:
        named[0].tag = label
        named[0].toks.append(tok)
    elif left and not ops:
        left[0].tag = label
        left[0].toks.append(tok)
    else:
        ops.append(Op("text", label, src=tok.src, toks=[tok]))


def _spanned_rows(g: _Grid) -> list[bool]:
    """Which rows lie under the second wire or later of a multi-wire gate or ``\\lstick``."""
    n = len(g.rows)
    marks = [0] * (n + 1)
    spans = [(r, o.span) for r, row in enumerate(g.rows) for ops in row.values() for o in ops]
    spans += [(r, span) for r, span in g.spans.items()]
    for r, span in spans:
        if span > 1 and r + 1 < n:
            marks[r + 1] += 1
            marks[min(n, r + span)] -= 1
    covered, running = [], 0
    for r in range(n):
        running += marks[r]
        covered.append(running > 0)
    return covered


# What a Qcircuit cell can hold without drawing a wire on its row.
_NOT_A_WIRE = ("text", "sep", "wire", "note")


def _blank_rows(g: _Grid, grid: list[list[str]], kind: str, drawn: list[bool]) -> list[bool]:
    """Which rows draw no wire. ``drawn[r]``: a Qcircuit cell of row ``r`` draws one (``\\qw``,
    ``\\cw``, a bundle, a gate, ``\\ghost``); a row of text only, ``\\nghost`` or nothing is
    not a wire. A quantikz row of full width is a wire even when it is empty (its manual draws
    two wires with ``&&&&& \\\\ &&&&&``); a shorter one, or a row of tikzcd or Qcircuit with no
    cell text, is a spacer."""
    width = max((len(row) for row in grid), default=0)
    blank: list[bool] = []
    for r, row in enumerate(grid):
        if kind == "qcircuit":
            blank.append(not (drawn[r] or r in g.labels))
        else:
            empty = not any(cell.strip() for cell in row)
            full = kind == "quantikz" and width >= 2 and len(row) == width
            blank.append(empty and not full)
    return blank


def _covered_from(boxes: list[tuple[int, int]], r: int) -> int:
    """The first column where a multi-wire gate of a row above covers row ``r``, or a number
    past every column. ``boxes`` holds ``(column, last covered row + 1)``, a heap by column;
    the boxes that end before ``r`` are dropped, for rows are read in order."""
    while boxes and boxes[0][1] <= r:
        heappop(boxes)
    return boxes[0][0] if boxes else 1 << 30


def _take_label(
    g: _Grid, at: tuple[int, int], facts: Facts, ops: list[Op], *, legend: bool
) -> None:
    """An ``\\lstick`` is the legend of row ``r`` when ``legend``; otherwise (a second one, one
    drawn after content, or one in the cell of an operation) it is a text item at its place. An
    empty one says nothing. ``at`` is the row and the column of the cell."""
    r, c = at
    tok = facts.label_tok
    if facts.label is None or tok is None:
        return
    text, wires = facts.label
    if legend and text:
        g.labels[r], g.spans[r] = text, wires
        g.facts["label", r] = (tok, c)
    elif text:
        ops.append(Op("text", text, src=tok.src, toks=[tok]))
    else:
        _use(tok)


def _shelve_facts(g: _Grid, at: tuple[int, int], facts: Facts, ops: list[Op]) -> None:
    """Put the output label, the bundle and the stick label of a cell where the wire lines and the
    gates read them. A second output label or bundle on a row would replace the first: it is an
    unparsed item instead."""
    r, c = at
    if facts.out and facts.out_tok is not None:
        if r in g.outs:
            ops.append(Op("unparsed", src=facts.out_src, toks=[facts.out_tok]))
        else:
            g.outs[r], g.out_spans[r] = facts.out, facts.out_span
            g.facts["out", r] = (facts.out_tok, c)
    if facts.bundle is not None and facts.bundle_tok is not None:
        if r in g.bundles:
            ops.append(Op("unparsed", src=facts.bundle_tok.src, toks=[facts.bundle_tok]))
        else:
            g.bundles[r] = facts.bundle
            g.facts["bundle", r] = (facts.bundle_tok, c)
    if facts.stick and facts.stick_tok is not None:
        _attach_stick(ops, g.rows[r].get(c - 1, []), facts.stick, facts.stick_tok)


# The type of a Qcircuit wire follows its row from left to right. A cell's \qw or \cw (quantikz
# has the same two, as legacy commands) draws the segment of the wire that enters the cell, so the
# wire is classical from a \cw on, until a \qw says otherwise. A list of gates cannot say what a
# gate drawn on a classical wire, a control that starts on one, a wire that turns classical with
# no measurement before it in its row, or one that turns quantum again would be, and quantum-sim
# v1 refuses a classical control: each of them is unparsed.
_ON_A_WIRE = frozenset({"gate", "ctrl", "targ", "swap", "phase", "meter"})


class _RowWire:
    """The type of the wire of one row as its cells are read, left to right."""

    def __init__(self) -> None:
        self.kind = "q"
        self.metered = False  # a measurement stands left of the cell being read

    def _refused(self, tok: Tok, kind: str, mixed: bool) -> bool:
        if mixed:  # \qw and \cw in one cell: which type the wire has is not said
            return True
        if kind == "q":
            return self.kind == "c"  # nothing says how a classical wire becomes quantum
        reaches = [o.strip() for o in tok.opts if o.strip()]
        return reaches not in ([], ["-1"]) or not (self.kind == "c" or self.metered)

    def cell(self, ops: list[Op], facts: Facts) -> None:
        """Type the segments of the cell: an unparsed item for each that is refused, and the
        operations drawn on a classical wire marked."""
        kinds = {k for _, k in facts.segments}
        for tok, kind in facts.segments:
            if self._refused(tok, kind, len(kinds) > 1):
                ops.append(Op("unparsed", src=tok.src, toks=[tok]))
        if len(kinds) == 1:
            self.kind = kinds.pop()
        if self.kind == "c":
            for o in ops:
                o.on_classical = o.kind in _ON_A_WIRE
        self.metered = self.metered or any(o.kind == "meter" for o in ops)


def _read_grid(grid: list[list[str]], kind: str) -> _Grid:
    """Every cell of ``grid`` read: operations by row and column, and the wire facts."""
    qc = kind == "qcircuit"
    g = _Grid(rows=[{} for _ in grid])
    drawn = [False] * len(grid)
    boxes: list[tuple[int, int]] = []
    for r, row in enumerate(grid):
        seen_label = False
        row_wire = _RowWire()
        # before: something is drawn left of this cell, or a box of a row above covers this row
        # from this column on (quantikz leaves the cells under a \\gate[2] empty)
        under = _covered_from(boxes, r)
        before = False
        for c, cell in enumerate(row):
            ops, facts = cell_ops(cell, qc)
            g.ledger += [(t, r, c) for t in facts.pending]
            drawn[r] = drawn[r] or facts.wire or facts.bundle is not None
            before = before or c >= under
            _take_label(g, (r, c), facts, ops, legend=not (seen_label or before or ops))
            seen_label = seen_label or facts.label is not None
            before = before or bool(ops) or facts.wire or facts.bundle is not None
            _shelve_facts(g, (r, c), facts, ops)
            row_wire.cell(ops, facts)
            if ops:
                g.rows[r][c] = ops
                for end in (r + o.span for o in ops if o.span > 1):
                    heappush(boxes, (c, end))
                drawn[r] = drawn[r] or any(o.kind not in _NOT_A_WIRE for o in ops)
    # rows that draw nothing (a leading "\\", a spacer row) get no wire number; rows that only
    # hold dots are listed once as "more wires"
    g.blank = _blank_rows(g, grid, kind, drawn)
    # A multi-wire gate or label that reaches past the last drawn row has its wires there, empty
    # as they are; a spacer row between two drawn ones is not taken for a wire.
    last = max((r for r in range(len(grid)) if not g.blank[r]), default=-1)
    for r, under in enumerate(_spanned_rows(g)):
        if under and r > last:
            g.blank[r] = False
    g.dots = []
    for r in range(len(grid)):
        kinds = {o.kind for ops in g.rows[r].values() for o in ops}
        g.dots.append(not g.blank[r] and kinds <= {"ellipsis", "cover"} and "ellipsis" in kinds)
    return g


def _label_span(names: Names, r: int, span: int) -> str:
    """ " (one label for wires 2 and 3)" when a label stands for more than one drawn wire."""
    covered = bisect_left(names.drawn, r + span) - bisect_left(names.drawn, r)
    return f" (one label for {names.span(r, span)})" if covered > 1 else ""


def _wire_lines(g: _Grid, names: Names) -> list[str]:
    lines: list[str] = []
    for r in range(len(g.rows)):
        if g.blank[r]:
            continue
        if g.dots[r]:
            lines.append(f"{names.num(r)} = dots (more wires, not drawn)")
            continue
        bits: list[str] = []
        if r in g.labels:
            bits.append(g.labels[r] + _label_span(names, r, g.spans.get(r, 1)))
        if r in g.bundles:
            bits.append(f"bundle {g.bundles[r]}".strip())
        if r in g.outs:
            bits.append(f"output {g.outs[r]}" + _label_span(names, r, g.out_spans.get(r, 1)))
        for kind in ("label", "out", "bundle"):
            if (kind, r) in g.facts:
                _use(g.facts[kind, r][0])  # the legend shows it
        lines.append(f"{names.num(r)}" + (f" = {', '.join(bits)}" if bits else ""))
    return lines


def _unlisted_facts(g: _Grid) -> None:
    """A legend fact on a row that the wire lines do not list (a row that draws no wire, or a row
    of dots) would be dropped. It becomes an unparsed item at its cell, unless it is only dots."""
    for (kind, r), (tok, c) in sorted(g.facts.items(), key=lambda item: (item[0][1], item[1][1])):
        if tok.used:
            continue
        text = {"label": g.labels, "out": g.outs, "bundle": g.bundles}[kind][r]
        if kind != "bundle" and _DOTS_TEXT.fullmatch(text.strip()):
            _use(tok)  # dots on a row of dots
        else:
            g.rows[r].setdefault(c, []).append(Op("unparsed", src=tok.src, toks=[tok]))


def _column_groups(
    col: list[tuple[int, Op]], nrows: int
) -> tuple[list[list[tuple[int, Op]]], bool]:
    """The operations of one column grouped by the vertical wires and gates that join them, in
    order from the top, and whether some wire or gate points outside the circuit."""
    groups_uf = _UnionFind()
    bad = False
    op_rows = sorted({r for r, _ in col})
    join_rows = sorted({r for r, o in col if o.kind in _JOINING})
    through = _Sweep(join_rows, groups_uf)  # a wire passing a control, target or swap joins it
    covered = _Sweep(op_rows, groups_uf)  # a gate over several wires joins what is on them
    for r, o in col:
        for off in o.link:
            t = r + off
            if 0 <= t < nrows:
                groups_uf.union(r, t)
                through.add(r, min(r, t), max(r, t))
            else:
                bad = True
        if o.kind in ("gate", "meter") and o.span > 1:
            covered.add(r, r, r + o.span)
            bad = bad or r + o.span > nrows
    through.finish()
    covered.finish()
    groups: dict[int, list[tuple[int, Op]]] = {}
    for r, o in col:
        if o.kind not in _LOOSE:
            groups.setdefault(groups_uf.find(r), []).append((r, o))
    ordered = sorted(groups.values(), key=lambda grp: min(r for r, _ in grp))
    return ordered, bad


def _reported(o: Op, blank: bool) -> Op:
    """``o`` as the list reports it. Text that is not a ket |0> or |1>, and text or a sign on a row
    that draws no wire, is unparsed: it can say that the drawing leaves something out (``repeat n
    times``, ``\\times n``, ``(gates omitted)``). Empty text says nothing and stays as it is."""
    if o.kind == "text" and not o.label:
        return o
    if (o.kind in ("text", "sep") and blank) or (o.kind == "text" and not _harmless_text(o.label)):
        return Op("unparsed", src=o.src or o.label)
    return o


def _loose_item(r: int, o: Op, names: Names, circ: Circuit, g: _Grid) -> tuple[str, GateOp | None]:
    """The item of an operation that no vertical wire joins to another."""
    circ.n_items += 1
    _consume(o)
    wire = (names.num(r),) if r in names.number else ()
    o = _reported(o, g.blank[r])
    if o.kind == "unparsed":
        circ.n_unparsed += 1
        source = _one_line(o.src)[:80]
        return f"unparsed on {names.place(r)}: {source}", GateOp(
            "unparsed", targets=wire, source=source
        )
    if o.kind == "text":
        return (
            (f"text {o.label} on {names.wire(r)}", GateOp("text", o.label, wire))
            if o.label
            else ("", None)
        )
    if o.kind == "ellipsis":
        return f"dots on {names.wire(r)}", GateOp("dots", targets=wire)
    if o.kind == "discard":
        return f"{names.wire(r)} discarded", GateOp("discard", targets=wire)
    if o.kind == "note":
        return "barrier" + (f" {o.label}" if o.label else ""), GateOp("barrier", o.label)
    return f"sign {o.label} on {names.wire(r)}", GateOp("sign", o.label, wire)  # sep


def _wired_single(item: Item, joined: list[tuple[int, Op]], g: _Grid, names: Names) -> Item:
    """``item``, the reading of a lone operation whose cell also draws a vertical wire to a row
    with nothing in this column. A classical wire is its condition (teleportation corrections are
    drawn like this, in a later column than the measurement); any other wire leaves the operation
    unexplained, so it is not read. Any other operation keeps ``item``."""
    text, ops = item
    [(gate_row, o)] = [(r, o) for r, o in joined if o.kind != "wire"]
    if o.kind not in ("gate", "targ", "phase"):
        return item
    classical = [(wr, w) for wr, w in joined if w.kind == "wire" and w.classical]
    if not classical:
        return _UNREAD
    wr, w = classical[0]
    # the wire may be written in the gate's cell and point away, or in the controlling row's cell
    far = wr + w.link[0] if wr == gate_row else wr
    drawn = 0 <= far < len(g.rows) and not g.blank[far]
    text += ", classically controlled" + (f" by {names.wire(far)}" if drawn else "")
    [op] = ops
    if not op.kind.startswith("unparsed"):
        op = replace(op, classical=True, controls=(names.num(far),) if drawn else ())
    return text, (op,)


def _meets_itself(op: GateOp) -> bool:
    """True when an item names a wire twice where a gate cannot: a control or a target that
    repeats, a control that sits on a target, or a swap of one wire with itself. Such an item is
    a drawing the reader misjoined, not a gate a simulator could apply."""
    if op.kind.startswith("unparsed"):
        return False
    wires = op.controls + op.open_controls + op.targets
    return len(set(wires)) != len(wires) or (op.kind == "swap" and len(set(op.targets)) != 2)


def _unparsed_group(
    grp: list[tuple[int, Op]], names: Names, circ: Circuit, why: str = ""
) -> tuple[list[str], list[GateOp]]:
    """The item for a group that the list does not read; the group is counted as one item that is
    unparsed (``circ.n_items`` is counted by the caller). ``why`` says what is wrong with it when
    the combination is not."""
    circ.n_unparsed += 1
    srcs = ", ".join(
        f"{names.wire(r)}: {o.kind}{(' ' + o.label) if o.label else ''}" for r, o in grp
    )
    rows = {r for r, _ in grp}
    srcs += f", {why}" if why else ""
    group = GateOp("unparsed_group", targets=tuple(names.numbers(rows)), source=srcs)
    return [f"unparsed joined operation ({srcs})"], [group]


def _group_items(
    joined: list[tuple[int, Op]], g: _Grid, names: Names, circ: Circuit
) -> tuple[list[str], list[GateOp]]:
    """The items of one group of a column that a vertical wire or a gate joins: the words of each,
    and the operations they describe."""
    items: list[str] = []
    ops: list[GateOp] = []
    for _, o in joined:
        _consume(o)  # whatever the group becomes, a gate, an unparsed group or a bare wire
    if any(o.on_classical for _, o in joined):
        circ.n_items += 1  # a gate or a control drawn on a classical wire (see _RowWire)
        return _unparsed_group(joined, names, circ, "drawn on a classical wire")
    # a classical wire in the group (\\cwx, \\vcw) makes what it joins classically controlled
    classical = any(o.classical for _, o in joined)
    grp = [
        (r, replace(o, classical=True) if classical else o)
        for r, o in joined
        if o.kind != "wire" or len(joined) == 1
    ]
    if not grp or all(o.kind == "wire" for _, o in grp):
        # a vertical wire with nothing joined to it in this column: it may pass through a box or
        # join a gate the list does not have, and a wire that is dropped says nothing
        circ.n_items += 1
        return _unparsed_group(joined, names, circ, "a vertical wire with nothing joined")
    rows = {r for r, _ in grp}
    if len(rows) == 1 and len(grp) == len(joined) > 1 and not any(o.link for _, o in grp):
        # several independent ops on one wire (\gate{H} \meter in one cell) in order; a vertical
        # wire in the cell joins them to something, which this reading would drop
        for r, o in grp:
            txt, found = describe_group([(r, o)], names)
            if found:
                items.append(txt)
                ops += found
            else:
                items.append(f"unparsed: {o.kind} on {names.wire(r)}")
                ops.append(GateOp("unparsed_op", targets=(names.num(r),), source=o.kind))
            circ.n_items += 1
            circ.n_unparsed += 0 if found else 1
        return items, ops
    circ.n_items += 1
    if len(rows) == 1 and len(grp) > 1 and len(grp) != len(joined):
        # several operations of one cell and a vertical wire: the wire joins them to something
        # the list cannot name, and describing them as one group would invent a gate
        return _unparsed_group(grp, names, circ)
    txt, found = describe_group(grp, names)
    if found and len(grp) == 1 and len(joined) > 1:
        txt, found = _wired_single((txt, found), joined, g, names)
    if found and not any(_meets_itself(op) for op in found):
        return [txt], list(found)
    return _unparsed_group(grp, names, circ)


def _step_items(
    col: list[tuple[int, Op]], g: _Grid, names: Names, circ: Circuit
) -> tuple[list[str], list[GateOp]]:
    """The items of one step: each joined operation, then the loose texts and marks. The words of
    each item, and the operations they describe."""
    items: list[str] = []
    ops: list[GateOp] = []
    groups, bad = _column_groups(col, len(g.rows))
    for joined in groups:
        group_items, group_ops = _group_items(joined, g, names, circ)
        items += group_items
        ops += group_ops
    for r, o in col:
        if o.kind in _LOOSE:
            text, op = _loose_item(r, o, names, circ, g)
            if op is not None:
                items.append(text)
                ops.append(op)
    if bad:
        circ.n_items += 1
        circ.n_unparsed += 1
        items.append("unparsed: a vertical wire or gate points outside the circuit")
        ops.append(GateOp("unparsed_outside"))
    return items, ops


def grid_circuit(body: str, kind: str) -> Circuit:
    rows_src = split_top(body, "\\\\")
    while rows_src and not rows_src[-1].strip():
        rows_src.pop()
    if not rows_src:
        return Circuit(kind, "unparsed", "empty body")
    g = _read_grid([split_top(r, "&") for r in rows_src], kind)
    if (
        not any(o.kind != "cover" for row in g.rows for ops in row.values() for o in ops)
        and not g.labels
    ):
        return Circuit(kind, "unparsed", "no circuit command in the grid")
    names = Names([r for r in range(len(g.rows)) if not g.blank[r]])
    circ = Circuit(kind)
    circ.wires = _wire_lines(g, names)
    _unlisted_facts(g)
    parts = _Parts(circ)
    for col in _columns(g):
        equality = _equality(col)
        if equality is None:
            parts.add(*_step_items(col, g, names, circ))
            continue
        label, rest = equality
        parts.split(label)
        if rest:  # a ket drawn in the column of the sign opens the right-hand side
            parts.add(*_step_items(rest, g, names, circ))
    parts.add_unconsumed(_unconsumed(g, circ))
    parts.close()
    if not circ.parts:
        return Circuit(kind, "unparsed", "no operation found")
    _grade(circ)
    return circ


# The signs quantum-sim compares the two sides of (formats.py, _EQUALITY_SIGNS, without \iff, which
# docingest does not read as a sign). An arrow or \approx is drawn between circuits too, but it
# says "leads to" or "about", and the sides are not compared across it.
_EQUALITY_SIGNS = frozenset({"=", "\\equiv", "\\simeq", "\\Leftrightarrow", "\\cong"})


def _claim_of(sign: str) -> str:
    """What the circuit after the sign ``sign`` is said to be, in the words of the list."""
    if sign in _EQUALITY_SIGNS:
        return "an equivalent circuit"
    return "after an arrow or approximation sign, not compared"


class _Parts:
    """The parts of a circuit being built: the sides of its drawn equalities, each a list of lines
    and, as data, a list of steps."""

    def __init__(self, circ: Circuit) -> None:
        self.circ = circ
        self.lines: list[str] = []
        self.steps: list[tuple[GateOp, ...]] = []
        self.number = 0

    def add(self, items: list[str], ops: list[GateOp]) -> None:
        if items:
            self.number += 1
            self.lines.append(f"Step {self.number}: " + "; ".join(items))
            self.steps.append(tuple(ops))

    def add_unconsumed(self, found: list[tuple[str, GateOp]]) -> None:
        self.add([text for text, _ in found], [op for _, op in found])

    def close(self) -> None:
        if self.lines:
            self.circ.parts.append(self.lines)
            self.circ.steps.append(self.steps)

    def split(self, label: str) -> None:
        """Start the right-hand side of an equality or an arrow drawn as ``label``."""
        self.close()
        self.lines = [f"[{label}: the steps below draw the right-hand side, {_claim_of(label)}]"]
        self.steps = [(GateOp("sep", label),)]
        self.number = 0


def _columns(g: _Grid) -> Iterator[list[tuple[int, Op]]]:
    """The operations of each column of the grid, left to right, that make steps. A ghost covers
    a box and the dots of a row of dots are listed once with the row: both are consumed here."""
    columns: dict[int, list[tuple[int, Op]]] = {}
    for r, row in enumerate(g.rows):
        for c, ops in row.items():
            columns.setdefault(c, []).extend((r, o) for o in ops)
    for c in sorted(columns):
        col: list[tuple[int, Op]] = []
        for r, o in columns[c]:
            if o.kind == "cover" or (g.dots[r] and o.kind == "ellipsis"):
                _consume(o)
            else:
                col.append((r, o))
        if col:
            yield col


def _equality(col: list[tuple[int, Op]]) -> tuple[str, list[tuple[int, Op]]] | None:
    """The sign and the rest of a column that draws an equality: signs, and text beside them.
    The signs that say the same as the first are consumed; any other sign and the text are the
    rest. None for a column that is not one."""
    if not any(o.kind == "sep" for _, o in col) or any(
        o.kind not in ("sep", "text") for _, o in col
    ):
        return None
    label = next(o.label for _, o in col if o.kind == "sep")
    rest = []
    for r, o in col:
        if o.kind == "sep" and o.label == label:
            _consume(o)
        else:
            rest.append((r, o))
    return label, rest


def _unconsumed(g: _Grid, circ: Circuit) -> list[tuple[str, GateOp]]:
    """The consumption check: an unparsed item for every token of the grid that no rule consumed,
    naming the token and its place. Nothing should be left, because each rule that drops a token
    marks it and each item the list writes consumes the tokens it was read from; this is what
    keeps a command or a text run that no rule knows from vanishing."""
    found: list[tuple[str, GateOp]] = []
    for tok, r, c in g.ledger:
        if tok.used:
            continue
        source = _one_line(tok.src)[:80]
        circ.n_items += 1
        circ.n_unparsed += 1
        found.append(
            (
                f"unparsed (read by no rule) at row {r + 1}, column {c + 1}: {source}",
                GateOp("unparsed", source=source),
            )
        )
    return found


def _grade(circ: Circuit) -> None:
    if circ.n_unparsed:
        circ.status = "partial" if circ.n_unparsed < circ.n_items else "unparsed"
        circ.reason = f"{circ.n_unparsed} of {circ.n_items} operations unparsed"


# ------------------------------------------------------------------ yquant

YQ_GATES = {"h": "H", "x": "X", "y": "Y", "z": "Z", "s": "S", "t": "T", "not": "X"}
_YQ_REGISTER = re.compile(
    r"^(qubit|cbit)\s*(\{(?P<lab>.*)\})?\s*(?P<name>[A-Za-z]\w*)(\[(?P<n>\d+)\])?$", re.S
)


class _NameDeclaredTwice(Exception):
    """A ``yquant`` name that a second ``qubit`` or ``cbit`` statement declares again."""


def _yq_register(
    m: re.Match[str], names: dict[str, int], classical: set[int], circ: Circuit, bases: set[str]
) -> bool:
    """Declare the wires of a ``qubit`` or ``cbit`` statement; False when it is too large. The
    wires of a ``cbit`` are added to ``classical``. Raises ``_NameDeclaredTwice`` when the name
    (in ``bases``) is already declared, whatever its size: which wire a later statement means,
    and the number of every wire after it, would be a guess. The name counts as declared even
    when the register is too large."""
    if m.group("name") in bases:
        raise _NameDeclaredTwice
    bases.add(m.group("name"))
    count = m.group("n")
    if count and len(count) > MAX_NUMBER:
        return False
    n = int(count) if count else 1
    if n > MAX_REGISTER:
        return False
    lab = clean_label(m.group("lab") or "")
    for k in range(n):
        nm = m.group("name") + (f"[{k}]" if n > 1 else "")
        names[nm] = len(names)
        if m.group(1) == "cbit":
            classical.add(names[nm])
        circ.wires.append(f"{len(names)} = {nm}" + (f" (in {lab})" if lab else ""))
    return True


def _yq_operation(
    stmt: str, names: dict[str, int], classical: set[int]
) -> tuple[str, GateOp] | None:
    """The step a gate statement makes and its operation, or None when it is not one the list
    knows. ``classical`` holds the wires that are classical at this statement (the ``cbit``
    wires and the wires measured so far); a measurement adds its targets to it."""
    m = re.match(r"^(?P<g>[a-z]+)\s*", stmt)
    rest = stmt[m.end() :] if m else ""
    lab = ""
    if m and rest.startswith("{"):
        end = _pairs(rest, "{", "}").get(0, -1)
        lab, rest = (clean_label(rest[1 : end - 1]), rest[end:]) if end > 0 else ("", "")
    if not m or not rest.strip() or "{" in rest or "(" in rest:
        return None
    if lab and m.group("g") not in ("box", "measure", "barrier", "discard"):
        return (
            None  # only these four keep the value; h {..} a, cnot {..} b | a and the rest drop it
        )
    target, _, control = rest.partition("|")
    tg = [x.strip() for x in target.split(",") if x.strip()]
    cs = [x.strip() for x in control.split(",") if x.strip()]
    open_cs = [x[1:] for x in cs if x.startswith("~")]
    cs = [x for x in cs if not x.startswith("~")]
    if any(x not in names for x in (*tg, *cs, *open_cs)):
        return None
    if not tg or (m.group("g") == "swap" and len(tg) != 2):
        return None  # no target, or a swap of other than two wires
    wires = Names()
    tw = wires.wires([names[x] for x in tg]) if tg else ""
    cw = wires.wires([names[x] for x in cs]) if cs else ""
    ow = wires.wires([names[x] for x in open_cs]) if open_cs else ""
    ctrl = ""
    if cs or open_cs:
        both = [f"control {cw}" if cs else "", f"open control {ow}" if open_cs else ""]
        ctrl = ": " + ", ".join(x for x in both if x) + f", target {tw}"
    cnot = _x_name(len(cs) + len(open_cs), bool(open_cs))
    numbers = (
        tuple(sorted(names[x] + 1 for x in tg)),
        tuple(sorted(names[x] + 1 for x in cs)),
        tuple(sorted(names[x] + 1 for x in open_cs)),
    )
    controlled_by_result = any(names[x] in classical for x in (*cs, *open_cs))
    step = _yq_text(m.group("g"), lab, ctrl, (tw, cnot), numbers, classical=controlled_by_result)
    if step and m.group("g") == "measure":
        classical.update(names[x] for x in tg)  # also when the statement is refused below
    if step and _meets_itself(step[1]):
        return None
    return step


def _yq_text(
    g: str,
    lab: str,
    ctrl: str,
    wires: tuple[str, str],
    numbers: tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]],
    *,
    classical: bool,
) -> tuple[str, GateOp] | None:
    """The words and the operation for gate ``g`` with the target wires and the name of a cnot
    with these controls (``wires``), the controls ``ctrl`` (": control .., target .." or empty) and
    the wire numbers of the targets, controls and open controls, each top to bottom; ``classical``
    when a control is a classical wire. None for a gate the list does not know."""
    tw, cnot = wires
    targets, closed, opened = numbers
    if g in ("box", *YQ_GATES):
        gate = lab if g == "box" else YQ_GATES[g]
        text = f"controlled-{gate}{ctrl}" if ctrl else f"{gate} on {tw}"
        return text, GateOp("gate", gate, targets, closed, opened, classical)
    if g == "cnot":
        text = f"{cnot}{ctrl}" if ctrl else f"X on {tw}"
        return text, GateOp("x", "X", targets, closed, opened, classical)
    if g == "swap":
        text = f"SWAP {tw}" + (f" ({ctrl[2:]})" if ctrl else "")
        return text, GateOp("swap", "", targets, closed, opened, classical)
    if g in ("measure", "barrier", "discard"):
        return f"{g} {tw}", GateOp(g, lab, targets, closed, opened, classical)
    return None


def yquant_circuit(body: str) -> Circuit:
    circ = Circuit("yquant")
    names: dict[str, int] = {}
    bases: set[str] = set()  # the names declared, a register under its name without the index
    classical: set[int] = set()
    steps: list[str] = []
    data: list[tuple[GateOp, ...]] = []
    unparsed = items = 0
    # each statement is a token, consumed only by the rule of a register (qubit, cbit) or of a gate
    # below; any other statement, with anything it carries, is unparsed (comments are cut first)
    for stmt in [s.strip() for s in strip_comments(body).split(";") if s.strip()]:
        items += 1
        m = _YQ_REGISTER.match(stmt)
        if m:
            try:
                registered = _yq_register(m, names, classical, circ, bases)
            except _NameDeclaredTwice:
                return Circuit("yquant", "unparsed", "a name is declared twice")
            if not registered:
                unparsed += 1
                steps.append(f"unparsed: {_one_line(stmt)[:80]}")
                data.append((GateOp("unparsed", source=_one_line(stmt)[:80]),))
            continue
        step = _yq_operation(stmt, names, classical)
        if step is None:
            unparsed += 1
            steps.append(f"unparsed: {_one_line(stmt)[:80]}")
            data.append((GateOp("unparsed", source=_one_line(stmt)[:80]),))
        else:
            steps.append(step[0])
            data.append((step[1],))
    if not steps:
        return Circuit("yquant", "unparsed", "no statement")
    circ.parts = [[f"Step {i + 1}: {s}" for i, s in enumerate(steps)]]
    circ.steps = [data]
    circ.n_items, circ.n_unparsed = items, unparsed
    if unparsed:
        circ.status = "partial" if unparsed < len(steps) else "unparsed"
        circ.reason = f"{unparsed} of {len(steps)} statements unparsed"
    return circ


# ------------------------------------------------------------------ block level

_ENV_OR_QCIRCUIT = re.compile(
    r"\\begin\s*\{(?P<env>quantikz|tikzcd|yquant\*?)\}|(?P<q>\\Qcircuit(?![a-zA-Z]))"
)
_BREAKS = re.compile(r"[&\\]")


def _label_of(source: str) -> str:
    m = _LABEL.search(source)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


# What the options of a circuit environment may hold. The reader reads none of them, so an option
# is let through only when it is a size, a scale or a line weight, which draw the same gates, or
# the wire types q, the type every wire has by default. Anything else (wire types={q,c},
# remove end wires, execute at end picture, a yquant option that renames registers) can add,
# remove or rename a wire, and the circuit is unparsed.
_NUMBER = r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)"
_UNIT_LENGTH = rf"{_NUMBER}\s*(?:pt|em|ex|cm|mm|in|bp|pc)"
_ENVIRONMENT_OPTION = re.compile(
    rf"\s*(?:(?:row |column )?sep\s*=\s*(?:{_UNIT_LENGTH}"
    rf"|\{{\s*{_UNIT_LENGTH}\s*,\s*between origins\s*\}}|tiny|scriptsize|small|normal|large|huge)"
    rf"|baseline\s*=\s*{_UNIT_LENGTH}|scale\s*=\s*{_NUMBER}|thick|thin lines|transparent"
    r"|font\s*=\s*\\(?:tiny|scriptsize|footnotesize|small|normalsize|large)"
    r"|slice style\s*=\s*[a-z]+(?:![0-9]{1,3})*"
    r"|wire types\s*=\s*\{\s*q(?:\s*,\s*q)*\s*\})?\s*"
)
_QCIRCUIT_OPTIONS = re.compile(rf"(?:\s*@(?:[CR*]\s*=\s*{_UNIT_LENGTH}|![CR*]?))*\s*")


def _environment_options_ok(kind: str, options: str) -> bool:
    if kind == "qcircuit":
        return _QCIRCUIT_OPTIONS.fullmatch(options) is not None
    if kind == "yquant":
        return not options.strip()
    return all(_ENVIRONMENT_OPTION.fullmatch(x) for x in _split_commas(options))


def _scan(src: str) -> tuple[str, list[tuple[str, str | None, str, str]], list[tuple[int, int]]]:
    """``src`` without its comments, the circuits in it (see :func:`find_circuits`) and the span
    ``(start, end)`` of each in that text: from ``\\begin`` to ``\\end`` and from ``\\Qcircuit`` to
    the end of its group. A circuit that never closes owns the rest of the text, which may hold
    more circuits (their spans lie inside it)."""
    src = strip_comments(src)
    braces = _pairs(src, "{", "}")
    squares = _pairs(src, "[", "]")
    found: list[tuple[str, str | None, str, str]] = []
    spans: list[tuple[int, int]] = []
    pos = 0
    no_end: set[str] = set()  # environments whose end is nowhere after their beginning
    brace_from = -1  # a position after which the text holds no "{"
    for m in _ENV_OR_QCIRCUIT.finditer(src):
        if m.start() < pos:
            continue
        if m.group("env"):
            env = m.group("env")
            kind = "yquant" if env.startswith("yquant") else env
            j = m.end()
            while j < len(src) and src[j] in " \t\n":
                j += 1
            options = ""
            if j < len(src) and src[j] == "[" and j in squares:
                options = src[j + 1 : squares[j] - 1]
                j = squares[j]
            end = -1 if env in no_end else src.find("\\end{" + env + "}", j)
            if end < 0:
                no_end.add(env)
                found.append((kind, None, "", ""))
                spans.append((m.start(), len(src)))
                continue
            found.append((kind, src[j:end], _label_of(src[m.start() : end]), options))
            pos = end + len("\\end{" + env + "}")
            spans.append((m.start(), pos))
            continue
        j = m.end()
        k = -1 if 0 <= brace_from <= j else src.find("{", j)
        if k < 0:
            brace_from = j
        if k < 0 or _BREAKS.search(src, j, k) or k not in braces:
            found.append(("qcircuit", None, "", ""))
            spans.append((m.start(), len(src)))
            continue
        found.append(
            (
                "qcircuit",
                src[k + 1 : braces[k] - 1],
                _label_of(src[m.start() : braces[k]]),
                src[j:k],
            )
        )
        pos = braces[k]
        spans.append((m.start(), pos))
    return src, found, spans


def find_circuits(src: str) -> list[tuple[str, str | None, str, str]]:
    """``(kind, body, label, options)`` of each circuit in a block, in source order. ``body`` is
    None when the environment or group never closes; ``options`` are the ``[..]`` after
    ``\\begin{quantikz}`` or the text between ``\\Qcircuit`` and its group. A circuit read is not
    looked into again, so the circuits of one block are disjoint and a missing end is looked for
    once."""
    return _scan(src)[1]


# What a block may hold outside its circuits without that changing them: the delimiters of math,
# the wrappers of a figure that hold no caption, a label and spacing. A power (^{\otimes n}, ^{2}),
# a tensor mark, an \underbrace, brackets, a macro definition (\def, \let, \newcommand,
# \renewcommand), a caption, a TikZ node and any text can say that the circuit is repeated, is a
# part of a larger one or means something else than it draws, so a circuit of a block with such a
# token is partial. No list of the commands that matter could be complete: this is the list of the
# ones that do not.
_BLANK = " \t\r\n\f\v"  # white space is ASCII only: any other space character is text
_GAP = r"[ \t\r\n]*"
_OUTSIDE_NO_EFFECT = re.compile(
    r"[ \t\r\n\f\v]+|\$\$?|\\[\[\]()]|\\centering(?![A-Za-z])"
    rf"|\\(?:begin|end){_GAP}\{{(?:center|figure\*?|equation\*?)\}}(?:{_GAP}\[[!htbpH]{{1,5}}\])?"
    rf"|\\label{_GAP}\{{[^{{}}]*\}}|\\[,;:! ]|\\q?quad(?![A-Za-z])"
)


def _outside(text: str, spans: Sequence[tuple[int, int]]) -> list[str]:
    """The runs of ``text`` outside ``spans`` that no rule of ``_OUTSIDE_NO_EFFECT`` takes: what
    the block says besides its circuits, in order, with the spaces squeezed."""
    pieces, pos = [], 0
    for start, end in spans:
        pieces.append(text[pos:start])
        pos = max(pos, end)
    pieces.append(text[pos:])
    runs: list[str] = []
    for piece in pieces:
        gaps, last = [], 0  # what lies between two matches of the rules is what they do not take
        for m in _OUTSIDE_NO_EFFECT.finditer(piece):
            gaps.append(piece[last : m.start()])
            last = m.end()
        gaps.append(piece[last:])
        runs += [_one_line(gap) for gap in gaps if gap.strip(_BLANK)]
    return runs


def _with_outside(circ: Circuit, source: str) -> None:
    """``circ`` with one more unparsed item that names the text of the block outside its circuits
    (``source``, as the item shows it). A circuit that could not be read keeps its status."""
    if circ.status == "unparsed" or not circ.parts:
        return
    number = sum(line.startswith("Step ") for line in circ.parts[-1]) + 1
    where = "unparsed" if circ.kind == "yquant" else "unparsed on no wire"
    circ.parts[-1].append(f"Step {number}: {where}: {source}")
    circ.steps[-1].append((GateOp("unparsed", source=source),))
    circ.n_items += 1
    circ.n_unparsed += 1
    circ.status = "partial"
    circ.reason = f"{circ.n_unparsed} of {circ.n_items} items unparsed"


@dataclass(frozen=True)
class GateList:
    """The gate list of one code block: ``text`` is empty when nothing could be read. ``circuits``
    is the same reading as data, one :class:`CircuitSteps` per circuit found (``n_circuits`` of
    them, those that could not be read included); it is built with the text, in memory only."""

    status: str  # "parsed": all circuits fully read; "partial": some part unread; "unparsed"
    text: str
    n_circuits: int = 0
    circuits: tuple[CircuitSteps, ...] = ()


def _steps_of(circ: Circuit) -> CircuitSteps:
    """The data of ``circ``; a circuit that could not be read has no wires and no parts, as it
    has no lines in the text."""
    if circ.status == "unparsed":
        return CircuitSteps(circ.kind, circ.status, circ.label, (), ())
    return CircuitSteps(
        circ.kind, circ.status, circ.label, tuple(circ.wires), tuple(tuple(p) for p in circ.steps)
    )


def gate_list(source: str) -> GateList:
    """The gate list of the circuits in ``source`` (the body of one code block)."""
    circs: list[Circuit] = []
    text, found, spans = _scan(source)
    for kind, body, label, options in found:
        if body is None:
            circ = Circuit(kind, "unparsed", "environment or group does not close")
        elif not _environment_options_ok(kind, options):
            circ = Circuit(kind, "unparsed", f"option not read: {_one_line(options)[:60]}")
        elif kind == "yquant":
            circ = yquant_circuit(body)
        else:
            circ = grid_circuit(body, kind)
        circ.label = label
        circs.append(circ)
    if not circs:
        return GateList("unparsed", "")
    if runs := _outside(text, spans):
        shown = _one_line("outside the circuit: " + " ".join(runs))[:80]
        for circ in circs:
            _with_outside(circ, shown)
    statuses = {c.status for c in circs}
    if statuses == {"parsed"}:
        status = "parsed"
    else:
        status = "unparsed" if statuses == {"unparsed"} else "partial"
    return GateList(status, render(circs), len(circs), tuple(_steps_of(c) for c in circs))


def _limited(lines: list[str]) -> list[str]:
    if len(lines) <= MAX_STEPS:
        return lines
    more = len(lines) - MAX_STEPS
    return [*lines[:MAX_STEPS], f"... {more} more {'line' if more == 1 else 'lines'} not listed"]


def render(circs: list[Circuit]) -> str:
    """The text of the list; empty when no circuit could be read."""
    if all(c.status == "unparsed" for c in circs):
        return ""
    unit = UNIT_STATEMENT if all(c.kind == "yquant" for c in circs) else UNIT_GRID
    header = HEADER.format(unit=unit)
    if len(circs) == 1 and circs[0].label:
        header = header.replace("Gate list (", f"Gate list for \\label{{{circs[0].label}}} (", 1)
    lines = [header]
    for i, c in enumerate(circs):
        if len(circs) > 1:
            tag = f" (\\label{{{c.label}}})" if c.label else ""
            note = " not read (unparsed)" if c.status == "unparsed" else ""
            lines.append(f"Circuit {i + 1} of {len(circs)}{tag}:{note}")
        if c.status == "unparsed":
            continue
        lines.append("Wires: " + "; ".join(c.wires))
        lines += _limited([s for p in c.parts for s in p])
    return "\n".join(lines)


# ------------------------------------------------------------------ circuit code blocks

BLOCK_LANGUAGE = "latex"  # the language the converter gives a circuit's code block
_FENCE = re.compile(r"(`{3,})(.*)$")


@dataclass(frozen=True)
class CodeBlock:
    """A fenced ``latex`` code block of a text: ``text[start:end]`` is both fences and the
    ``source`` between them."""

    start: int
    end: int
    source: str


def circuit_code_blocks(text: str) -> list[CodeBlock]:
    """The fenced ``latex`` code blocks of ``text`` (Markdown), in order. Other fenced blocks are
    skipped whole, so a fence inside one is not an opening. A block that never closes is left
    out. One pass over the lines."""
    blocks: list[CodeBlock] = []
    offset = 0
    fence = 0  # backticks of the opening fence of the block being read, 0 outside a block
    language = ""
    start = 0
    body: list[str] = []
    for line in text.split("\n"):
        m = _FENCE.match(line)
        info = m.group(2).strip() if m else ""
        if not fence:
            if m and "`" not in info:
                words = info.replace("{", " ").replace("}", " ").replace(".", " ").split()
                fence, language, start, body = (
                    len(m.group(1)),
                    (words[0] if words else ""),
                    offset,
                    [],
                )
        elif m and not info and len(m.group(1)) >= fence:
            if language == BLOCK_LANGUAGE:
                blocks.append(CodeBlock(start, offset + len(line), "\n".join(body)))
            fence = 0
        else:
            body.append(line)
        offset += len(line) + 1
    return blocks


def add_gate_lists(text: str, start: int, document: str, blocks: Sequence[CodeBlock]) -> str:
    """``text`` (a chunk, found at ``start`` of ``document``) with the gate list of each circuit
    block whose closing fence lies in it, as a paragraph right after that fence.

    ``blocks`` are the code blocks of ``document`` (:func:`circuit_code_blocks`). A block that
    starts before the chunk is read whole from ``document``. A block cut by the end of the chunk
    gets no list here, and a block that ends exactly where the chunk starts is not in the chunk
    (the next circuit may be: its list would sit above a different circuit). A chunk that is not
    where ``start`` says (the document changed since it was indexed) is returned as it is."""
    stop = start + len(text)
    if document[start:stop] != text:
        return text
    pieces: list[str] = []
    pos = 0
    for block in blocks:
        # a block ending where the chunk starts belongs to the chunk before it
        if not start < block.end <= stop:
            continue
        listed = gate_list(block.source).text
        if listed:
            at = block.end - start
            pieces += [text[pos:at], "\n\n" + listed + "\n"]
            pos = at
    if not pieces:
        return text
    pieces.append(text[pos:])
    return "".join(pieces)
