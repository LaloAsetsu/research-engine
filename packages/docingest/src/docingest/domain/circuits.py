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

Order is the drawing order: columns of the grid (``&``) are steps, left to right, and columns
with only wires are skipped. Two gates in different columns may commute or run together on
hardware; the list does not say so. Labels drawn outside the grid (``\\gategroup`` boxes,
captions, TikZ nodes) are not in the list.

The work is linear in the size of the source: every bracket is matched once, the columns are
read sparsely and the vertical wires of a column are joined with a sweep, so no input makes it
quadratic. Output size is linear too: a gate over many wires is written as a range.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

__all__ = [
    "MAX_STEPS",
    "CodeBlock",
    "GateList",
    "add_gate_lists",
    "circuit_code_blocks",
    "gate_list",
]

MAX_STEPS = 60  # a longer circuit is listed up to here, then "... N more lines not listed"
MAX_REGISTER = 128  # wires of one yquant register that are listed; a larger one is "unparsed"
MAX_NUMBER = 6  # digits of a count or an offset that are read; a longer number is "unparsed"
RANGE_FROM = 12  # more consecutive wires than this are written "wires a to b"
GRID_ENVS = ("quantikz", "tikzcd")

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
                if k < len(s) and s[k] == "[" and k in squares:
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
    name: str  # command name without backslash, or "" for plain text
    opts: list[str] = field(default_factory=list)
    args: list[str] = field(default_factory=list)
    src: str = ""


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
        *("normalsize", "linethrough"),
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


@dataclass
class Facts:
    """What a cell says about its wire rather than about a gate."""

    label: tuple[str, int] | None = None  # \lstick text and the wires it spans
    out: str = ""  # \rstick
    out_span: int = 1  # wires the \rstick label spans
    bundle: str | None = None  # \qwbundle
    wire: bool = False  # \qw or \cw: the cell draws a wire
    stick: str = ""  # \dstick: a label drawn under a pair of dots


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


def _expand(toks: list[Tok], qcircuit: bool) -> list[Tok]:
    """``toks`` with the formatting wrappers and bare groups replaced by what they hold."""
    out: list[Tok] = []
    for t in toks:
        if t.name in WRAPPERS and t.args:
            out += tokenize(t.args[-1], qcircuit)
        elif t.name == "" and t.src.startswith("{") and t.src.endswith("}"):
            out += tokenize(t.src[1:-1], qcircuit)
        elif t.name in IGNORED or (t.name == "" and re.fullmatch(r"\[[^\]]*\]", t.src.strip())):
            continue  # \label, \protect, a column-spacing option [0.5cm]
        else:
            out.append(t)
    return out


def _text_op(src: str) -> Op | None:
    """The item a run of text and math makes: a separator, dots, text, or nothing."""
    bare = _SPACE_ESCAPES.sub(" ", src).strip()
    if not bare:
        return None
    if EQUALS_TEXT.match(src):
        return Op("sep", label=clean_label(src))
    if _DOTS_TEXT.fullmatch(bare):
        return Op("ellipsis") if bare.strip(" $") else None
    return Op("text", clean_label(src))


def cell_ops(cell: str, qcircuit: bool) -> tuple[list[Op], Facts]:
    """Operations of one cell, and the wire facts found in it (label, output label, bundle)."""
    facts = Facts()
    toks = [t for t in _expand(tokenize(cell, qcircuit), qcircuit) if t.name not in SPACING]
    if toks and all(is_text_token(t) for t in toks):
        # a state, an output name or an interval: kept as one text item
        src = " ".join(t.src for t in toks).strip()
        if EQUALS_TEXT.match(src) or src in ("\\equiv", "="):
            return [Op("sep", label=clean_label(src))], facts
        op = _text_op(src)
        return ([op] if op else []), facts
    ops: list[Op] = []
    runs: list[Tok] = []  # runs of text and math between circuit commands become one item each
    commands: list[Tok] = []
    pending: list[str] = []
    for t in toks:
        if is_text_token(t):
            pending.append(t.src)
            continue
        if pending:
            runs.append(Tok("#text", [], [], " ".join(pending)))
            pending = []
        commands.append(t)
    if pending:
        runs.append(Tok("#text", [], [], " ".join(pending)))
    for run in runs:
        if op := _text_op(run.src.strip()):
            ops.append(op)
    for t in commands:
        _read_command(t, ops, facts)
    return ops, facts


def _read_command(t: Tok, ops: list[Op], facts: Facts) -> None:
    nm = t.name
    if nm in ("dstick", "ustick") and t.args and clean_label(t.args[0]).strip():
        # a label drawn below or above a gate (Qcircuit's way to name a controlled phase drawn as
        # two dots)
        lab = re.sub(r"\\hspace\{[^}]*\}", "", clean_label(t.args[0])).strip()
        lab = lab[2:-1] if lab.startswith("_{") and lab.endswith("}") else lab
        if lab:
            facts.stick = lab
        return
    if nm in ("qw", "cw"):
        facts.wire = True
        return
    if nm in _SKIPPED or nm in SPACING:
        return
    if nm in ELLIPSIS:
        ops.append(Op("ellipsis"))
    elif nm in SEPARATOR_COMMANDS:
        ops.append(Op("sep", label="\\" + nm))
    elif (reader := _READERS.get(nm)) is not None:
        reader(t, ops, facts)
    else:
        ops.append(Op("unparsed", src=t.src))


_SKIPPED = frozenset(
    {
        *("qw", "cw", "nghost", "setwiretype", "wireoverride", "gateinput", "gateoutput"),
        *("dstick", "ustick", "cds", "inputgroupv", "inputgroupv*", "inputgroup", "gategroup"),
    }
)


def _unparsed(t: Tok) -> Op:
    return Op("unparsed", src=t.src)


def _first(t: Tok) -> str:
    return clean_label(t.args[0]) if t.args else ""


def _r_lstick(t: Tok, ops: list[Op], facts: Facts) -> None:
    if facts.label is not None:
        ops.append(Op("text", _first(t)))
        return
    wires = wires_option(t.opts)
    if wires is None or wires < 1:
        ops.append(_unparsed(t))
    else:
        facts.label = (_first(t), wires)


def _r_rstick(t: Tok, ops: list[Op], facts: Facts) -> None:
    wires = wires_option(t.opts)
    if wires is None or wires < 1:
        ops.append(_unparsed(t))
    else:
        facts.out, facts.out_span = _first(t), wires


def _r_bundle(t: Tok, ops: list[Op], facts: Facts) -> None:
    facts.bundle = _first(t)


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
    classical = any("verticalwire=c" in o.replace(" ", "") for o in t.opts)
    open_ = t.name != "ctrl" or "open" in "".join(t.opts)
    ops.append(Op("ctrl", link=[off] if off else [], open_=open_, classical=classical))


def _r_control(t: Tok, ops: list[Op], facts: Facts) -> None:
    ops.append(Op("ctrl", open_=t.name != "control"))


def _r_targ(t: Tok, ops: list[Op], facts: Facts) -> None:
    ops.append(Op("targ"))


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


def _r_wire(t: Tok, ops: list[Op], facts: Facts) -> None:
    """quantikz ``\\wire[d][2]{q}``."""
    d = t.opts[0].strip() if t.opts else ""
    length = int_arg(t.opts[1], 1) if len(t.opts) > 1 else 1
    if d in ("d", "u") and length is not None:
        ops.append(
            Op("wire", link=[length if d == "d" else -length], classical=t.args[:1] == ["c"])
        )
    elif d not in ("l", "r"):
        ops.append(_unparsed(t))


def _r_push(t: Tok, ops: list[Op], facts: Facts) -> None:
    txt = _first(t)
    txt = "" if _SPACE_ONLY.fullmatch(txt) else txt
    if EQUALS_TEXT.match(txt):  # \midstick[2,brackets=none]{=}, \push{=}: two circuits side by side
        ops.append(Op("sep", label=txt))
    elif txt.strip("\\ ") in ELLIPSIS or txt == "":
        ops.append(Op("ellipsis") if txt else Op("text", ""))
    else:
        ops.append(Op("text", txt))


def _r_discard(t: Tok, ops: list[Op], facts: Facts) -> None:
    ops.append(Op("discard"))


def _r_slice(t: Tok, ops: list[Op], facts: Facts) -> None:
    lab = clean_label(t.args[-1]) if t.name == "slice" and t.args else ""
    ops.append(Op("note", "barrier" + (f" {lab}" if lab else "")))


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


class Names:
    """Wire numbers of a grid: the rows that draw something, numbered from 1 top to bottom."""

    def __init__(self, drawn: Sequence[int] = ()):
        self.drawn = list(drawn)  # sorted grid rows that are numbered
        self.number = {r: i + 1 for i, r in enumerate(self.drawn)}

    def num(self, r: int) -> int:
        return self.number.get(r, r + 1)

    def wire(self, r: int) -> str:
        return f"wire {self.num(r)}"

    def wires(self, rows: Sequence[int]) -> str:
        return wires_text([self.num(r) for r in sorted(set(rows))])

    def span(self, r: int, count: int) -> str:
        """The wires of rows ``r`` to ``r + count - 1`` that exist and are numbered."""
        lo, hi = bisect_left(self.drawn, r), bisect_left(self.drawn, r + count)
        return wires_text(range(lo + 1, hi + 1))


def describe_group(items: list[tuple[int, Op]], names: Names) -> tuple[str, bool]:
    """:func:`describe_ops` plus the labels drawn next to the operation. Dots with a label are a
    two-qubit gate of that name, not a controlled-Z."""
    tags = [o.tag for _, o in items if o.tag]
    if not tags:
        return describe_ops(items, names)
    ctrls = [r for r, o in items if o.kind == "ctrl"]
    if len(ctrls) == len(items) and len(ctrls) >= 2:
        return (
            f"two-qubit gate drawn as control dots on {names.wires(ctrls)} (symmetric, no "
            f"target), labelled {', '.join(tags)}",
            True,
        )
    txt, ok = describe_ops(items, names)
    return (f"{txt}, labelled {', '.join(tags)}" if ok else txt), ok


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


def _measure_on(r: int, o: Op, names: Names) -> str:
    on = names.span(r, o.span)
    over = " (one measurement over these wires)" if o.span > 1 else ""
    return f"measure {on}" + (f" ({o.label})" if o.label else "") + over


def _measured(
    items: list[tuple[int, Op]], meters: list[tuple[int, Op]], names: Names
) -> tuple[str, bool]:
    """A measurement alone, or one whose result drives a gate through a classical wire."""
    if any(o.classical for _, o in items):
        rest = [(r, o) for r, o in items if o.kind in ("gate", "targ")]
        # anything else in the group (a quantum control, say) would be lost: not a known shape
        if len(meters) != 1 or len(rest) != 1 or len(items) != 2:
            return "", False
        r, o = rest[0]
        gate = o.label if o.kind == "gate" else "X (\\targ)"
        measured = _measure_on(*meters[0], names)
        return (
            f"{measured}; its result classically controls {gate} on {names.span(r, o.span)}",
            True,
        )
    if len(items) == len(meters):
        return "; ".join(_measure_on(r, o, names) for r, o in meters), True
    return "", False


def _single(r: int, o: Op, names: Names) -> tuple[str, bool]:
    if o.kind == "gate":
        on = names.span(r, o.span)
        return f"{o.label} on {on}" + (" (one gate on these wires)" if o.span > 1 else ""), True
    if o.kind == "targ":
        return f"X (NOT, drawn as \\targ) on {names.wire(r)}", True
    if o.kind == "phase":
        return f"phase {o.label} on {names.wire(r)}", True
    if o.kind == "ctrl":
        return f"lone control dot on {names.wire(r)} (no gate joined to it in this column)", True
    return "", False


@dataclass
class _Mix:
    """The operations of one joined group, by kind."""

    ctrls: list[tuple[int, Op]]
    targs: list[int]
    gates: list[tuple[int, Op]]
    swaps: list[int]
    phases: list[tuple[int, Op]]
    cl: str  # " (classically controlled)" or ""


def _swap_text(mix: _Mix, names: Names) -> tuple[str, bool]:
    if len(mix.swaps) != 2:
        return "", False
    if mix.ctrls:
        controls = _controls(mix.ctrls, names)
        return f"controlled-SWAP of {names.wires(mix.swaps)} ({controls}){mix.cl}", True
    return f"SWAP {names.wires(mix.swaps)}", True


def _x_name(controls: int, opened: bool) -> str:
    """CNOT, Toffoli or an n-controlled X (the first two only with closed controls)."""
    if opened and controls > 1:
        return f"{controls}-controlled X"
    return {1: "CNOT", 2: "Toffoli (CCNOT)"}.get(controls, f"{controls}-controlled X")


def _not_text(mix: _Mix, names: Names) -> tuple[str, bool]:
    """CNOT, Toffoli or an n-controlled X: controls and \\targ in one column."""
    if not mix.ctrls or (len(mix.ctrls) > 1 and len(mix.targs) > 1):
        return "", False  # no control, or two gates drawn over one another
    name = _x_name(len(mix.ctrls), any(o.open_ for _, o in mix.ctrls))
    tgt = ("target " if len(mix.targs) == 1 else "targets ") + names.wires(mix.targs)
    return f"{name}{mix.cl}: {_controls(mix.ctrls, names)}, {tgt}", True


def _controlled_gate_text(mix: _Mix, names: Names) -> tuple[str, bool]:
    if len(mix.gates) != 1 or not mix.ctrls:
        return "", False  # a gate joined to nothing, or several gates joined
    r, o = mix.gates[0]
    controls = _controls(mix.ctrls, names)
    return f"controlled-{o.label}{mix.cl}: {controls}, target {names.span(r, o.span)}", True


def _dots_text(mix: _Mix, names: Names) -> tuple[str, bool]:
    """Control dots only: a controlled-Z, which has no target and is symmetric."""
    n = len(mix.ctrls)
    if n < 2:
        return "", False  # one control whose wire leads nowhere: not a gate
    if any(o.open_ for _, o in mix.ctrls):
        controls = _controls(mix.ctrls, names)
        return f"controlled-Z with open controls{mix.cl}: {controls} (dots only, no target)", True
    name = "controlled-Z" if n == 2 else f"multi-controlled Z ({n} wires)"
    both = "both" if n == 2 else "all"
    rows = names.wires([r for r, _ in mix.ctrls])
    return f"{name}{mix.cl} on {rows} (control dots on {both}, symmetric: no target)", True


def _phase_text(mix: _Mix, names: Names) -> tuple[str, bool]:
    labels = sorted({o.label for _, o in mix.phases})
    rows = names.wires([r for r, _ in mix.ctrls] + [r for r, _ in mix.phases])
    return f"controlled-phase {', '.join(labels)}{mix.cl} on {rows} (symmetric dots)", True


# in order: the kind a group must hold, the kinds it must not, and how it is written
_CASES: tuple[tuple[str, frozenset[str], Callable[[_Mix, Names], tuple[str, bool]]], ...] = (
    ("swap", frozenset({"targ", "gate", "phase"}), _swap_text),
    ("targ", frozenset({"gate", "swap", "phase"}), _not_text),
    ("gate", frozenset({"targ", "swap", "phase"}), _controlled_gate_text),
    ("ctrl", frozenset({"targ", "gate", "swap", "phase"}), _dots_text),
    ("phase", frozenset({"targ", "gate", "swap"}), _phase_text),
)
_KNOWN_KINDS = ("ctrl", "targ", "gate", "swap", "meter", "phase", "wire")


def describe_ops(items: list[tuple[int, Op]], names: Names) -> tuple[str, bool]:
    """One line for the operations of one column that a vertical wire (or one gate) joins.
    Returns ``(text, ok)``; not ok means the combination is not one the list knows."""
    kinds: dict[str, list[tuple[int, Op]]] = {}
    for r, o in items:
        kinds.setdefault(o.kind, []).append((r, o))
    if any(k not in _KNOWN_KINDS for k in kinds):
        return "", False
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
    return "", False


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


def _attach_stick(ops: list[Op], previous: list[Op], label: str) -> None:
    """Qcircuit writes the label of a dot gate in its own cell or in the cell to its right: it
    names the gate in this cell, else the dots in the cell before, else it is text."""
    named = [o for o in ops if o.kind in ("ctrl", "targ", "gate", "phase", "swap")]
    left = [o for o in previous if o.kind in ("ctrl", "phase")]
    if named:
        named[0].tag = label
    elif left and not ops:
        left[0].tag = label
    else:
        ops.append(Op("text", label))


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


def _read_grid(grid: list[list[str]], kind: str) -> _Grid:
    """Every cell of ``grid`` read: operations by row and column, and the wire facts."""
    qc = kind == "qcircuit"
    g = _Grid(rows=[{} for _ in grid])
    drawn = [False] * len(grid)
    for r, row in enumerate(grid):
        seen_label = False
        for c, cell in enumerate(row):
            ops, facts = cell_ops(cell, qc)
            drawn[r] = drawn[r] or facts.wire or facts.bundle is not None
            if facts.label is not None:
                lab, wires = facts.label
                if not seen_label and lab:
                    g.labels[r], g.spans[r] = lab, wires
                elif seen_label and lab:
                    ops.append(Op("text", lab))  # a second \lstick further right: text there
                seen_label = True
            if facts.out:
                g.outs[r], g.out_spans[r] = facts.out, facts.out_span
            if facts.bundle is not None:
                g.bundles[r] = facts.bundle
            if facts.stick:
                _attach_stick(ops, g.rows[r].get(c - 1, []), facts.stick)
            if ops:
                g.rows[r][c] = ops
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
        lines.append(f"{names.num(r)}" + (f" = {', '.join(bits)}" if bits else ""))
    return lines


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


def _loose_item(r: int, o: Op, names: Names, circ: Circuit) -> str:
    circ.n_items += 1
    if o.kind == "unparsed":
        circ.n_unparsed += 1
        return f"unparsed on {names.wire(r)}: {_one_line(o.src)[:80]}"
    if o.kind == "text":
        return f"text {o.label} on {names.wire(r)}" if o.label else ""
    if o.kind == "ellipsis":
        return f"dots on {names.wire(r)}"
    if o.kind == "discard":
        return f"{names.wire(r)} discarded"
    if o.kind == "note":
        return o.label
    return f"sign {o.label} on {names.wire(r)}"  # sep


def _wired_single(
    text: str, joined: list[tuple[int, Op]], g: _Grid, names: Names
) -> tuple[str, bool]:
    """``text``, the reading of a lone operation whose cell also draws a vertical wire to a row
    with nothing in this column. A classical wire is its condition (teleportation corrections are
    drawn like this, in a later column than the measurement); any other wire leaves the operation
    unexplained, so it is not read. Any other operation keeps ``text``."""
    [(gate_row, o)] = [(r, o) for r, o in joined if o.kind != "wire"]
    if o.kind not in ("gate", "targ", "phase"):
        return text, True
    classical = [(wr, w) for wr, w in joined if w.kind == "wire" and w.classical]
    if not classical:
        return "", False
    wr, w = classical[0]
    # the wire may be written in the gate's cell and point away, or in the controlling row's cell
    far = wr + w.link[0] if wr == gate_row else wr
    drawn = 0 <= far < len(g.rows) and not g.blank[far]
    return text + ", classically controlled" + (f" by {names.wire(far)}" if drawn else ""), True


def _step_items(col: list[tuple[int, Op]], g: _Grid, names: Names, circ: Circuit) -> list[str]:
    """The items of one step: each joined operation, then the loose texts and marks."""
    items: list[str] = []
    groups, bad = _column_groups(col, len(g.rows))
    for joined in groups:
        # a classical wire in the group (\\cwx, \\vcw) makes what it joins classically controlled
        classical = any(o.classical for _, o in joined)
        grp = [
            (r, replace(o, classical=True) if classical else o)
            for r, o in joined
            if o.kind != "wire" or len(joined) == 1
        ]
        if not grp or all(o.kind == "wire" for _, o in grp):
            continue  # a vertical wire with nothing on it in this column
        rows = {r for r, _ in grp}
        if len(rows) == 1 and len(grp) > 1 and not any(o.link for _, o in grp):
            # several independent ops on one wire (\gate{H} \meter in one cell) in order
            for r, o in grp:
                txt, ok = describe_group([(r, o)], names)
                items.append(txt if ok else f"unparsed: {o.kind} on {names.wire(r)}")
                circ.n_items += 1
                circ.n_unparsed += 0 if ok else 1
            continue
        txt, ok = describe_group(grp, names)
        if ok and len(grp) == 1 and len(joined) > 1:
            txt, ok = _wired_single(txt, joined, g, names)
        circ.n_items += 1
        if ok:
            items.append(txt)
        else:
            circ.n_unparsed += 1
            srcs = ", ".join(
                f"{names.wire(r)}: {o.kind}{(' ' + o.label) if o.label else ''}" for r, o in grp
            )
            items.append(f"unparsed joined operation ({srcs})")
    # text on a row that is not a wire (a caption, a layout row) is not "on a wire"
    items += [
        t
        for r, o in col
        if o.kind in _LOOSE and not (g.blank[r] and o.kind in ("text", "sep"))
        if (t := _loose_item(r, o, names, circ))
    ]
    if bad:
        circ.n_items += 1
        circ.n_unparsed += 1
        items.append("unparsed: a vertical wire or gate points outside the circuit")
    return items


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
    columns: dict[int, list[tuple[int, Op]]] = {}
    for r, row in enumerate(g.rows):
        for c, ops in row.items():
            columns.setdefault(c, []).extend((r, o) for o in ops)
    part: list[str] = []
    step = 0
    for c in sorted(columns):
        col = [
            (r, o)
            for r, o in columns[c]
            if o.kind != "cover" and not (g.dots[r] and o.kind == "ellipsis")
        ]
        if not col:
            continue
        if any(o.kind == "sep" for _, o in col) and all(o.kind in ("sep", "text") for _, o in col):
            lab = next(o.label for _, o in col if o.kind == "sep")
            if part:
                circ.parts.append(part)
            part = [f"[{lab}: the steps below draw the right-hand side, an equivalent circuit]"]
            step = 0
            continue
        items = _step_items(col, g, names, circ)
        if items:
            step += 1
            part.append(f"Step {step}: " + "; ".join(items))
    if part:
        circ.parts.append(part)
    if not circ.parts:
        return Circuit(kind, "unparsed", "no operation found")
    _grade(circ)
    return circ


def _grade(circ: Circuit) -> None:
    if circ.n_unparsed:
        circ.status = "partial" if circ.n_unparsed < circ.n_items else "unparsed"
        circ.reason = f"{circ.n_unparsed} of {circ.n_items} operations unparsed"


# ------------------------------------------------------------------ yquant

YQ_GATES = {"h": "H", "x": "X", "y": "Y", "z": "Z", "s": "S", "t": "T", "not": "X"}
_YQ_REGISTER = re.compile(
    r"^(qubit|cbit)\s*(\{(?P<lab>.*)\})?\s*(?P<name>[A-Za-z]\w*)(\[(?P<n>\d+)\])?$", re.S
)


def _yq_register(m: re.Match[str], names: dict[str, int], circ: Circuit) -> bool:
    """Declare the wires of a ``qubit`` or ``cbit`` statement; False when it is too large."""
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
        circ.wires.append(f"{len(names)} = {nm}" + (f" (in {lab})" if lab else ""))
    return True


def _yq_operation(stmt: str, names: dict[str, int]) -> str | None:
    """The step a gate statement makes, or None when it is not one the list knows."""
    m = re.match(r"^(?P<g>[a-z]+)\s*", stmt)
    rest = stmt[m.end() :] if m else ""
    lab = ""
    if m and rest.startswith("{"):
        end = _pairs(rest, "{", "}").get(0, -1)
        lab, rest = (clean_label(rest[1 : end - 1]), rest[end:]) if end > 0 else ("", "")
    if not m or not rest.strip() or "{" in rest or "(" in rest:
        return None
    target, _, control = rest.partition("|")
    tg = [x.strip() for x in target.split(",") if x.strip()]
    cs = [x.strip() for x in control.split(",") if x.strip()]
    open_cs = [x[1:] for x in cs if x.startswith("~")]
    cs = [x for x in cs if not x.startswith("~")]
    if any(x not in names for x in (*tg, *cs, *open_cs)):
        return None
    wires = Names()
    tw = wires.wires([names[x] for x in tg]) if tg else ""
    cw = wires.wires([names[x] for x in cs]) if cs else ""
    ow = wires.wires([names[x] for x in open_cs]) if open_cs else ""
    ctrl = ""
    if cs or open_cs:
        both = [f"control {cw}" if cs else "", f"open control {ow}" if open_cs else ""]
        ctrl = ": " + ", ".join(x for x in both if x) + f", target {tw}"
    cnot = _x_name(len(cs) + len(open_cs), bool(open_cs))
    return _yq_text(m.group("g"), lab, ctrl, (tw, cnot))


def _yq_text(g: str, lab: str, ctrl: str, wires: tuple[str, str]) -> str | None:
    """The words for gate ``g`` with the target wires and the name of a cnot with these controls
    (``wires``) and the controls ``ctrl`` (": control .., target .." or empty); None for a gate the
    list does not know."""
    tw, cnot = wires
    if g in ("box", *YQ_GATES):
        gate = lab if g == "box" else YQ_GATES[g]
        return f"controlled-{gate}{ctrl}" if ctrl else f"{gate} on {tw}"
    if g == "cnot":
        return f"{cnot}{ctrl}" if ctrl else f"X on {tw}"
    if g == "swap":
        return f"SWAP {tw}" + (f" ({ctrl[2:]})" if ctrl else "")
    if g in ("measure", "barrier", "discard"):
        return f"{g} {tw}"
    return None


def yquant_circuit(body: str) -> Circuit:
    circ = Circuit("yquant")
    names: dict[str, int] = {}
    steps: list[str] = []
    unparsed = items = 0
    for stmt in [s.strip() for s in strip_comments(body).split(";") if s.strip()]:
        items += 1
        m = _YQ_REGISTER.match(stmt)
        if m:
            if not _yq_register(m, names, circ):
                unparsed += 1
                steps.append(f"unparsed: {_one_line(stmt)[:80]}")
            continue
        step = _yq_operation(stmt, names)
        if step is None:
            unparsed += 1
            steps.append(f"unparsed: {_one_line(stmt)[:80]}")
        else:
            steps.append(step)
    if not steps:
        return Circuit("yquant", "unparsed", "no statement")
    circ.parts = [[f"Step {i + 1}: {s}" for i, s in enumerate(steps)]]
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


def find_circuits(src: str) -> list[tuple[str, str | None, str]]:
    """``(kind, body, label)`` of each circuit in a block, in source order. ``body`` is None when
    the environment or group never closes. A circuit read is not looked into again, so the
    circuits of one block are disjoint and a missing end is looked for once."""
    src = strip_comments(src)
    braces = _pairs(src, "{", "}")
    squares = _pairs(src, "[", "]")
    found: list[tuple[str, str | None, str]] = []
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
            if j < len(src) and src[j] == "[" and j in squares:
                j = squares[j]
            end = -1 if env in no_end else src.find("\\end{" + env + "}", j)
            if end < 0:
                no_end.add(env)
                found.append((kind, None, ""))
                continue
            found.append((kind, src[j:end], _label_of(src[m.start() : end])))
            pos = end + len("\\end{" + env + "}")
            continue
        j = m.end()
        k = -1 if 0 <= brace_from <= j else src.find("{", j)
        if k < 0:
            brace_from = j
        if k < 0 or _BREAKS.search(src, j, k) or k not in braces:
            found.append(("qcircuit", None, ""))
            continue
        found.append(
            ("qcircuit", src[k + 1 : braces[k] - 1], _label_of(src[m.start() : braces[k]]))
        )
        pos = braces[k]
    return found


@dataclass(frozen=True)
class GateList:
    """The gate list of one code block: ``text`` is empty when nothing could be read."""

    status: str  # "parsed": all circuits fully read; "partial": some part unread; "unparsed"
    text: str
    n_circuits: int = 0


def gate_list(source: str) -> GateList:
    """The gate list of the circuits in ``source`` (the body of one code block)."""
    circs: list[Circuit] = []
    for kind, body, label in find_circuits(source):
        if body is None:
            circ = Circuit(kind, "unparsed", "environment or group does not close")
        elif kind == "yquant":
            circ = yquant_circuit(body)
        else:
            circ = grid_circuit(body, kind)
        circ.label = label
        circs.append(circ)
    if not circs:
        return GateList("unparsed", "")
    statuses = {c.status for c in circs}
    if statuses == {"parsed"}:
        status = "parsed"
    else:
        status = "unparsed" if statuses == {"unparsed"} else "partial"
    return GateList(status, render(circs), len(circs))


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
