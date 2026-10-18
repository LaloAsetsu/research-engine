"""The gate-label resolver: a label as an author writes it in LaTeX to a gate of the table, or a
refusal with the reason. Standard library only.

"A wrong gate is worse than none." The table of names is closed and the grammar is small; there
is no fuzzy matching, no case folding beyond the spellings listed in ``SPELLINGS``, and no
guessing. Anything else raises ``LabelError`` ("unknown gate label 'U_1'"). Families whose
meaning depends on the paper (``R(\\theta)``, ``R_\\phi``, ``U_1``, ``U_3``, ``Ph(...)``,
``\\hat{U}...``, ``V``) are refused with ``ambiguous=True`` and never mapped: controlled,
R_z(θ) and P(θ) are different gates, so picking one would give a silently wrong circuit. A box
labelled CNOT, Toffoli or Fredkin is refused too, because the label does not say which of its
wires is the control; the drawn control dots (docingest's ``x`` and ``swap`` items) do.

Shape of a label: ``name``, ``name(arguments)``, with at most one dagger before or after the
arguments (``T^\\dagger``, ``T^{\\dagger}``, ``T^\\dag``, ``T^†``, ``T†``, ``T^{-1}``,
``R_y^\\dagger(\\theta)``, ``R_y(\\theta)^\\dagger``). A superscript of more than one character
needs matching braces, as LaTeX sets it: ``T^-1`` and ``X^1/2`` are refused, ``T^{-1}`` and
``X^{1/2}`` are read, and a second superscript on ``X^{1/2}`` is refused (LaTeX stops on a double
superscript). ``R_xx`` without braces is refused too (R_x followed by x). Braces around the whole
label (``{T^\\dagger}``), font commands around plain letters (``\\mathrm{R}``, ``\\text{H}``,
``\\operatorname{SWAP}``) and ``\\left`` ``\\right`` around the parentheses are removed first; a
font command right after ``_`` or ``^`` is the whole subscript (``R_\\mathrm{zz}`` is ``R_{zz}``).
See ``SPELLINGS`` for every accepted spelling of every gate; the conventions document is checked
against it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from quantum_sim.angles import Angle, format_number, parse_angle
from quantum_sim.domain import GATES, AngleError, LabelError

MAX_LABEL_CHARS = 700  # room for R_{xx}( + a float written out in full + )^\dagger

# gate name -> spellings that resolve to it, as the conventions document lists them. A spelling
# with an angle is written with the placeholder ARG.
SPELLINGS: dict[str, tuple[str, ...]] = {
    "id": ("I", "Id", r"\mathbb{I}", r"\mathbb{1}", r"\mathds{1}"),
    "h": ("H", r"\mathrm{H}", r"\text{H}"),
    "x": ("X", r"\mathrm{X}"),
    "y": ("Y", r"\mathrm{Y}"),
    "z": ("Z", r"\mathrm{Z}"),
    "s": ("S", r"\mathrm{S}"),
    "sdg": (
        r"S^\dagger",
        r"S^{\dagger}",
        r"S^\dag",
        r"S^{\dag}",
        "S^†",
        "S†",
        "S^{-1}",
        "Sdg",
        r"\mathrm{S}^\dagger",
    ),
    "t": ("T", r"\mathrm{T}"),
    "tdg": (
        r"T^\dagger",
        r"T^{\dagger}",
        r"T^\dag",
        r"T^{\dag}",
        "T^†",
        "T†",
        "T^{-1}",
        "Tdg",
        r"\mathrm{T}^\dagger",
    ),
    "sx": (r"\sqrt{X}", r"\sqrt X", r"\sqrt{\mathrm{X}}", r"\sqrt{\mathrm{NOT}}", "SX", "X^{1/2}"),
    "sxdg": (r"\sqrt{X}^\dagger", r"\sqrt{X}^{\dagger}", r"\sqrt{X}^{-1}", "X^{-1/2}"),
    "rx": ("R_x(ARG)", "R_{x}(ARG)", "R_X(ARG)", "RX(ARG)", "Rx(ARG)", r"\mathrm{R}_x(ARG)"),
    "ry": ("R_y(ARG)", "R_{y}(ARG)", "R_Y(ARG)", "RY(ARG)", "Ry(ARG)", r"\mathrm{R}_y(ARG)"),
    "rz": ("R_z(ARG)", "R_{z}(ARG)", "R_Z(ARG)", "RZ(ARG)", "Rz(ARG)", r"\mathrm{R}_z(ARG)"),
    "p": ("P(ARG)", r"\mathrm{P}(ARG)"),
    "rk": ("R_2", "R_{2}", "R_{k=12}", r"\mathrm{R}_3"),
    "rxx": ("R_{xx}(ARG)", "R_{XX}(ARG)", "RXX(ARG)", "Rxx(ARG)", r"R_{\mathrm{xx}}(ARG)"),
    "ryy": ("R_{yy}(ARG)", "R_{YY}(ARG)", "RYY(ARG)", "Ryy(ARG)"),
    "rzz": ("R_{zz}(ARG)", "R_{ZZ}(ARG)", "RZZ(ARG)", "Rzz(ARG)"),
    "swap": ("SWAP", r"\mathrm{SWAP}", r"\text{SWAP}"),
    "iswap": ("iSWAP", r"\mathrm{iSWAP}"),
    "cz": ("CZ", r"\mathrm{CZ}"),
}
"""Label spellings by gate. ``cz`` is not a gate of the table: ``CZ`` is the Z gate with its
first wire as a control (``ResolvedLabel.implied_controls``)."""

# A superscript of more than one character needs matching braces (T^{-1}); in LaTeX T^-1 is a
# T with a lone minus above it. A single command or character needs none (T^\dagger, T^†).
_DAGGER = r"(?:\^(?:\{\s*(?:\\dagger|\\dag|†|-1)\s*\}|\\dagger(?![A-Za-z])|\\dag(?![A-Za-z])|†)|†)"
_DAGGER_END = re.compile(_DAGGER + r"\s*$")
_FONTS = re.compile(
    r"\\(?:mathrm|textrm|text|textsf|mathsf|operatorname|mathit)\s*\{([A-Za-z0-9 ]*)\}"
)
_SPACING = re.compile(r"\\[,;:! ]|\\quad|\\qquad|~")
_AROUND_SYMBOLS = re.compile(r"\s*([_^(){}])\s*")
_ROTATION = re.compile(
    r"^R(?:_\{(?P<braced>[xyzXYZ]{1,2})\}|_(?P<single>[xyzXYZ])|(?P<plain>[xyzXYZ]{1,2}))$"
)
_TWO_AXES_UNBRACED = re.compile(r"^R_[xyzXYZ]{2}$")
# R_k: one digit from 1 to 9, or k written out as R_{k=12}. R_12 is R_1 followed by a 2 in LaTeX,
# and R_{12} or R_{01} usually name a pair of wires or levels, so they are not read as k.
_PHASE_INDEX = re.compile(
    r"^R_(?:\{(?P<braced>[1-9])\}|(?P<plain>[1-9])|\{k\s*=\s*(?P<explicit>[1-9][0-9]*)\})$"
)
_NUMBER_INDEX = re.compile(r"^R_(?:\{[0-9]+\}|[0-9]+)$")
_INDEX_LETTER = re.compile(r"^R_(?:\{\\?[A-Za-z]+\}|\\?[A-Za-z]+)$")
_SQRT_X = re.compile(r"^\\sqrt\s*(?:\{(?:X|NOT)\}|X)$")
_HALF_X = re.compile(r"^X\^\{(?P<sign>-?)(?:1/2|\\frac\{1\}\{2\})\}$")
_DECORATION = re.compile(r"\\(?:hat|widehat|tilde|widetilde|mathcal|mathbb|bar)\s*")
_GENERIC = re.compile(r"^(?:U|V|W|Ph|Phase|R)(?:_.*)?$")

_SUPERSCRIPT_REASON = (
    "the superscript is not a dagger, or it has more than one character and needs braces "
    "(T^{-1}, not T^-1)"
)

_FIXED = {
    "I": "id",
    "Id": "id",
    r"\mathbb{I}": "id",
    r"\mathbb{1}": "id",
    r"\mathds{1}": "id",
    "H": "h",
    "X": "x",
    "Y": "y",
    "Z": "z",
    "S": "s",
    "T": "t",
    "Sdg": "sdg",
    "Tdg": "tdg",
    "SX": "sx",
    "SWAP": "swap",
    "iSWAP": "iswap",
}
_DIRECTION = frozenset(
    {"CNOT", "CNot", "CX", "CCX", "CCNOT", "Toffoli", "CCNot", "Fredkin", "CSWAP", "CSwap"}
)
_INVERSE_OF = {
    "h": "h",
    "x": "x",
    "y": "y",
    "z": "z",
    "id": "id",
    "swap": "swap",
    "s": "sdg",
    "sdg": "s",
    "t": "tdg",
    "tdg": "t",
    "sx": "sxdg",
    "sxdg": "sx",
}
_NEGATED_BY_DAGGER = frozenset({"rx", "ry", "rz", "p", "rxx", "ryy", "rzz"})


@dataclass(frozen=True)
class ResolvedLabel:
    """What a label means: a gate of the table, its parameters, and how many of the drawn wires
    are controls.

    ``params`` are ``Angle`` values (``rk`` has one, a numeric angle that is the integer k).
    ``implied_controls`` is 1 for ``CZ``: the gate is Z, and the first of its two drawn wires is
    the control (Z with one control is symmetric in the two wires). ``drawn_wires`` is the number
    of wires the label needs when it is not applied once per target wire.
    """

    gate: str
    params: tuple[Angle, ...] = ()
    implied_controls: int = 0

    @property
    def drawn_wires(self) -> int:
        return GATES[self.gate].n_targets + self.implied_controls


def _fail(label: str, why: str = "") -> LabelError:
    shown = repr(label if len(label) <= 60 else label[:57] + "...")
    return LabelError(f"unknown gate label {shown}" + (f": {why}" if why else ""))


def _ambiguous(label: str, why: str) -> LabelError:
    shown = repr(label if len(label) <= 60 else label[:57] + "...")
    return LabelError(f"unknown gate label {shown} (ambiguous: {why})", ambiguous=True)


def _unfont(match: re.Match[str]) -> str:
    """The text of a font group; after ``_`` or ``^`` it keeps braces, because the group is the
    whole subscript or superscript (``R_\\mathrm{zz}`` is ``R_{zz}``, not ``R_zz``)."""
    inner = match.group(1).replace(" ", "")
    return f"{{{inner}}}" if match.string[: match.start()].rstrip().endswith(("_", "^")) else inner


def _normalise(label: str) -> str:
    text = label.strip()
    if text.startswith("$") and text.endswith("$") and len(text) > 1:
        text = text.strip("$")
    text = _SPACING.sub(" ", text)
    text = re.sub(r"\\left\s*\(", "(", text)
    text = re.sub(r"\\right\s*\)", ")", text)
    for _ in range(3):  # \mathrm{\mathrm{x}} is not a label; three levels are plenty
        unwrapped = _FONTS.sub(_unfont, text)
        if unwrapped == text:
            break
        text = unwrapped
    text = _AROUND_SYMBOLS.sub(r"\1", text.strip())
    return re.sub(r"\s+", " ", text).strip()


def _unbrace(text: str) -> str:
    """``{T}`` -> ``T`` when the braces wrap the whole text. One pass: it notes where the balance
    first falls to each level below the number of leading braces, because the brace that opens
    level ``k`` closes where the balance first falls to ``k - 1``."""
    leading = len(text) - len(text.lstrip("{"))
    closed: dict[int, int] = {}  # level -> index where the balance first falls to it
    balance = 0
    for index, char in enumerate(text):
        if char == "{":
            balance += 1
        elif char == "}":
            balance -= 1
            if balance < leading:
                closed.setdefault(balance, index)
            if balance == 0:
                break
    stripped = 0
    while stripped < leading and closed.get(stripped) == len(text) - stripped - 1:
        stripped += 1
    return text[stripped : len(text) - stripped]


def _split_arguments(text: str) -> tuple[str, str | None]:
    """``name(arguments)`` -> (name, arguments); ``(None)`` when the text has no final ``(...)``."""
    if not text.endswith(")"):
        return text, None
    depth = 0
    for index in range(len(text) - 1, -1, -1):
        if text[index] == ")":
            depth += 1
        elif text[index] == "(":
            depth -= 1
            if depth == 0:
                return text[:index], text[index + 1 : -1]
    return text, None  # unbalanced: the head keeps the stray parenthesis and is not a name


def _split_dagger(label: str, text: str) -> tuple[str, str | None, bool]:
    """(head, arguments, daggered) from a normalised label; at most one dagger."""
    after = _DAGGER_END.search(text)
    if after:
        text = text[: after.start()]
    if not text.endswith(")") and re.search(r"\)\s*\^[^)]*$", text):
        raise _fail(label, _SUPERSCRIPT_REASON)  # a superscript after the arguments
    head, arguments = _split_arguments(text)
    before = _DAGGER_END.search(head)
    if before:
        head = head[: before.start()]
    if after and before:
        raise _fail(label, "two daggers")
    if (after or before) and _HALF_X.fullmatch(head):
        raise _fail(
            label,
            "a superscript on X^{1/2} is a double superscript, which LaTeX does not set; "
            "write {X^{1/2}}^\\dagger",
        )
    return _unbrace(head), arguments, bool(after or before)


def _angle(label: str, arguments: str | None) -> Angle:
    if arguments is None or not arguments.strip():
        raise _fail(label, "this gate needs an angle in parentheses")
    try:
        return parse_angle(arguments)
    except AngleError as error:
        raise _fail(label, f"bad angle: {error}") from None


def _inverse(label: str, resolved: ResolvedLabel) -> ResolvedLabel:
    gate = resolved.gate
    if gate in _NEGATED_BY_DAGGER:
        return ResolvedLabel(gate, tuple(p.negated() for p in resolved.params))
    inverse = _INVERSE_OF.get(gate)
    if inverse is None:
        raise _fail(label, f"the inverse of {gate!r} is not in the gate table")
    return ResolvedLabel(inverse, resolved.params, resolved.implied_controls)


def resolve_label(label: str) -> ResolvedLabel:
    """The gate a label means, or ``LabelError`` with the reason.

    >>> resolve_label(r"T^\\dagger").gate
    'tdg'
    >>> resolve_label(r"R_y(\\pi/2)").params[0].value == 3.141592653589793 / 2
    True
    """
    if not isinstance(label, str):
        raise LabelError(f"a gate label is text, got {type(label).__name__}")
    if len(label) > MAX_LABEL_CHARS:
        raise LabelError(f"a gate label of more than {MAX_LABEL_CHARS} characters")
    text = _unbrace(_normalise(label))
    if not text:
        raise _fail(label, "empty label")
    head, arguments, daggered = _split_dagger(label, text)
    resolved = _resolve_head(label, head, arguments)
    return _inverse(label, resolved) if daggered else resolved


def _resolve_head(label: str, head: str, arguments: str | None) -> ResolvedLabel:
    if head in _DIRECTION:
        raise _ambiguous(
            label,
            "the label does not say which wire is the control; draw the control dots instead",
        )
    named = _named(label, head, arguments)
    if named is not None:
        return named
    rotation = _rotation(label, head, arguments)
    if rotation is not None:
        return rotation
    if head == "P":
        return ResolvedLabel("p", (_angle(label, arguments),))
    index = _PHASE_INDEX.fullmatch(head)
    if index:
        if arguments is not None:
            raise _fail(label, "R_k takes no angle")
        k = int(index.group("braced") or index.group("plain") or index.group("explicit"))
        try:
            return ResolvedLabel("rk", (Angle.of(float(k)),))
        except OverflowError:  # an integer of more than 308 digits
            raise _fail(label, "k is too large") from None
    if _NUMBER_INDEX.fullmatch(head):
        if int(head.strip("R_{}")) == 0:
            raise _fail(label, "R_k needs k of at least 1")
        raise _ambiguous(
            label,
            "an index of more than one digit or with a leading zero may name a pair of wires "
            "or levels, and R_12 is R_1 followed by 2 in LaTeX; write R_{k=12} for k = 12",
        )
    if _INDEX_LETTER.fullmatch(head):
        raise _ambiguous(
            label,
            "an index that is not a number could be a rotation axis, a phase or a QFT index "
            "(R_2 is the QFT's R_k; R_x, R_y, R_z are rotations)",
        )
    base = head.partition("^")[0]
    if "^" in head and (_ROTATION.fullmatch(base) or base in _FIXED or base in ("P", "CZ")):
        raise _fail(label, _SUPERSCRIPT_REASON)
    stripped = _DECORATION.sub("", head).replace("{", "").replace("}", "")
    if _GENERIC.fullmatch(stripped):
        raise _ambiguous(
            label,
            "this name is defined by each paper (U_1, U_3, R(theta), Ph(...), a hatted U, V "
            "and W can mean different gates)",
        )
    raise _fail(label)


def _rotation(label: str, head: str, arguments: str | None) -> ResolvedLabel | None:
    """R_x(θ), R_{xx}(θ), ...; ``None`` when ``head`` is not a rotation."""
    if _TWO_AXES_UNBRACED.fullmatch(head):
        axes = head[2:]
        raise _fail(
            label, f"{head} is R_{axes[0]} followed by {axes[1]} in LaTeX; write R_{{{axes}}}"
        )
    match = _ROTATION.fullmatch(head)
    if match is None:
        return None
    axes = match.group("braced") or match.group("single") or match.group("plain")
    if len(axes) == 2 and axes[0] != axes[1]:
        raise _fail(label, f"{axes!r} is not a rotation about one axis")
    return ResolvedLabel(f"r{axes.lower()}", (_angle(label, arguments),))


def _named(label: str, head: str, arguments: str | None) -> ResolvedLabel | None:
    """Gates written by name with no angle: H, X, ..., sqrt X, CZ, SWAP."""
    if head in _FIXED or head == "CZ" or _SQRT_X.fullmatch(head) or _HALF_X.fullmatch(head):
        if arguments is not None:
            raise _fail(label, "this gate takes no arguments")
        if head == "CZ":
            return ResolvedLabel("z", (), 1)
        if head in _FIXED:
            return ResolvedLabel(_FIXED[head])
        half = _HALF_X.fullmatch(head)
        return ResolvedLabel("sxdg" if half and half.group("sign") else "sx")
    return None


def label_of(gate: str, params: tuple[float, ...] = ()) -> str:
    """The label that ``resolve_label`` reads back as ``gate`` with ``params`` exactly.

    ``gate`` is a key of ``GATES``. A two-wire gate is written for the item that carries it
    (``kind`` ``gate``), a single-wire gate for any number of target wires.
    """
    if gate not in GATES:
        raise LabelError(f"unknown gate {gate!r}")
    fixed = {
        "id": "I",
        "h": "H",
        "x": "X",
        "y": "Y",
        "z": "Z",
        "s": "S",
        "sdg": r"S^\dagger",
        "t": "T",
        "tdg": r"T^\dagger",
        "sx": r"\sqrt{X}",
        "sxdg": r"\sqrt{X}^\dagger",
        "swap": "SWAP",
        "iswap": "iSWAP",
    }
    if gate in fixed:
        return fixed[gate]
    if gate == "rk":
        k = int(params[0])
        return f"R_{{{k}}}" if k < 10 else f"R_{{k={k}}}"
    letters = gate[1:]
    name = "P" if gate == "p" else f"R_{{{letters}}}" if len(letters) == 2 else f"R_{letters}"
    return f"{name}({format_number(params[0])})"
