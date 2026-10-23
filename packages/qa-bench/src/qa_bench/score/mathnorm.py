"""Deterministic normalization and matching of LaTeX / plain-text math for the question set.

Vendored: version 2 of the matcher of the benchmark scorer. Everything after this docstring is the
code of that version unchanged (the tests pin its md5, 49ccac59ddd9dd7ee2319256596fd269, taken from
the first line after the docstring); only this docstring was edited. It differs from version 1 in
two places; the expression matcher (canon, symbols, match_expr) is untouched:
  - match_text: text keys and answers go through tex_flat first (drop $, the backslash before a command name, braces;
    unwrap \\text/\\mathrm; Unicode Greek -> GREEK names; join _ and ^ to their neighbours), so \\sigma_{z} equals sigma_z.
  - match_num: for an integer key from 2 to 20 the English and Spanish number words (two..twenty, dos..veinte) also count,
    as whole words (see NUMBER_WORDS); "one", "uno", "una" and "once" are left out.
A num hit that came only from a number word is reported as word_only: like a digit, a number word counts wherever it
appears in the answer ("eight or nine CNOT gates" for other circuits counts for the key 8), and a hand check of such
hits found about half of them wrong. qa_bench.score therefore calls match_num_digits and match_num_word itself and
leaves the word route off unless it is asked for (see qa_bench.score.keys); match_key and match_num below are the
upstream functions and are not used by the scorer.

canon(s): one canonical string for an expression written in LaTeX, Unicode or ASCII, so that
  \\frac{hR_{\\square}}{2\\pi^2\\Delta(T)}, "h R_sq / (2 pi^2 Delta(T))" and "hR□/(2π²Δ(T))" come out close:
  1. Unicode symbols, Greek letters, super/subscript digits -> LaTeX names (α -> \\alpha, ² -> ^2, † -> ^\\dagger).
  2. drop sizing and spacing (\\left, \\right, \\big.., \\, \\; \\! \\quad ~), math delimiters ($, \\[ \\]), \\label{},
     \\tag{}, \\nonumber, environment wrappers (equation, align, ...); matrix rows -> ';', cells -> ','.
  3. unwrap font/accent wrappers (\\mathrm{x}, \\text{x}, \\operatorname{x}, \\mathbf, \\mathcal, \\hat, \\tilde, \\bar,
     \\vec, \\mathbb ...) to their argument.
  4. synonyms (\\dag -> \\dagger, \\le -> \\leq, \\to -> \\rightarrow, \\tfrac/\\dfrac -> \\frac, \\ket{x} -> |x>,
     \\bra{x} -> <x|, \\langle/\\rangle -> < >, \\lvert/\\vert -> |, \\exp -> exp, \\cdot/\\times/* removed).
  5. \\frac{a}{b} -> (a)/(b), \\sqrt{a} -> sqrt(a); then commands lose the backslash (\\alpha -> alpha), and all
     braces, parentheses, brackets and whitespace are removed; trailing , . ; are stripped.
match_expr(key, text): "exact" if canon(key) (or an alternative) is a substring of canon(text); else the
  symbol recall = share of the key's symbols (command names, letters, numbers) found among the text's symbols.
match_text(phrases, text): any phrase as a substring after lowercasing, removing punctuation and collapsing space.
match_num(value, text, rel): any number in the text whose absolute value is within rel (relative) of |value|
  (signs are often dropped in prose: "an 8 MHz coupling" for J = -8 MHz); handles 1.5e-3, 1.5x10^-3,
  1.5 \\times 10^{-3}, Unicode superscripts and decimal commas.
"""
from __future__ import annotations

import functools
import re

GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "ϵ": "epsilon", "ζ": "zeta",
         "η": "eta", "θ": "theta", "ϑ": "vartheta", "ι": "iota", "κ": "kappa", "λ": "lambda", "μ": "mu", "ν": "nu",
         "ξ": "xi", "π": "pi", "ρ": "rho", "σ": "sigma", "τ": "tau", "υ": "upsilon", "φ": "phi", "ϕ": "phi",
         "χ": "chi", "ψ": "psi", "ω": "omega", "Γ": "Gamma", "Δ": "Delta", "Θ": "Theta", "Λ": "Lambda", "Ξ": "Xi",
         "Π": "Pi", "Σ": "Sigma", "Φ": "Phi", "Ψ": "Psi", "Ω": "Omega", "µ": "mu"}
SYMS = {"†": "^\\dagger ", "⊗": "\\otimes ", "≈": "\\approx ", "≃": "\\simeq ", "≤": "\\leq ", "≥": "\\geq ",
        "≠": "\\neq ", "∑": "\\sum ", "∫": "\\int ", "√": "\\sqrt ", "∞": "\\infty ", "×": "", "·": "", "⋅": "",
        "−": "-", "–": "-", "ħ": "\\hbar ", "∂": "\\partial ", "⟨": "<", "⟩": ">", "〈": "<", "〉": ">",
        "→": "\\rightarrow ", "∈": "\\in ", "±": "\\pm ", "∇": "\\nabla ", "∝": "\\propto ", "∼": "\\sim ",
        "≡": "\\equiv ", "□": "\\square ", "∗": "*", "′": "'", "∣": "|", "⊕": "\\oplus ", "ℏ": "\\hbar ",
        "∆": "\\Delta ", "Ω": "\\Omega ", "ℓ": "\\ell "}
SUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")
SUB = str.maketrans("₀₁₂₃₄₅₆₇₈₉₋₊", "0123456789-+")
DROP = re.compile(r"\\(left|right|bigl|bigr|Bigl|Bigr|biggl|biggr|Biggl|Biggr|big|Big|bigg|Bigg|displaystyle|"
                  r"textstyle|scriptstyle|nonumber|notag|quad|qquad|limits|nolimits|phantom\{[^}]*\})(?![a-zA-Z])")
WRAP = ("mathrm", "text", "textrm", "textit", "textbf", "mathit", "mathbf", "boldsymbol", "bm", "operatorname",
        "mathcal", "mathbb", "mathsf", "mathfrak", "mathscr", "hat", "tilde", "widetilde", "bar", "overline", "vec",
        "widehat", "check", "dot", "ddot", "rm", "mathnormal", "emph", "ensuremath", "boxed", "underline")
SYN = {"dag": "dagger", "le": "leq", "ge": "geq", "ne": "neq", "to": "rightarrow", "tfrac": "frac",
       "dfrac": "frac", "lvert": "|", "rvert": "|", "vert": "|", "lVert": "||", "rVert": "||", "Vert": "||",
       "langle": "<", "rangle": ">", "varepsilon": "epsilon", "varphi": "phi", "cdot": "", "times": "",
       "cdots": "...", "ldots": "...", "dots": "...", "mid": "|", "exp": "exp", "ln": "ln", "log": "log",
       "lbrace": "", "rbrace": "", "{": "", "}": "", "%": "%", "|": "||", "square": "square", "hbar": "hbar",
       "approx": "approx", "simeq": "approx"}
SPACING = re.compile(r"\\[,;:! ]|~|\\hspace\{[^}]*\}|\\vspace\{[^}]*\}")
ENV = re.compile(r"\\(begin|end)\{(equation|align|eqnarray|gather|multline|split|aligned|gathered|flalign|alignat|"
                 r"subequations|displaymath|math|array|cases|dcases)\*?\}(\{[^}]*\})?")
MATENV = re.compile(r"\\(begin|end)\{(pmatrix|bmatrix|vmatrix|Bmatrix|Vmatrix|matrix|smallmatrix)\*?\}")


def _group(s: str, i: int) -> int:
    """index after the {...} group starting at s[i] == '{' (or i if none)."""
    if i >= len(s) or s[i] != "{":
        return i
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return j + 1
    return len(s)


def _arg(s: str, i: int) -> tuple[str, int]:
    """(argument, end) of a macro argument at s[i]: a {...} group or one token."""
    while i < len(s) and s[i] == " ":
        i += 1
    if i < len(s) and s[i] == "{":
        e = _group(s, i)
        return s[i + 1: e - 1], e
    m = re.match(r"\\[a-zA-Z]+|.", s[i:]) if i < len(s) else None
    return (m.group(0), i + len(m.group(0))) if m else ("", i)


def _rewrite(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        m = re.match(r"\\([a-zA-Z]+)", s[i:])
        if not m:
            out.append(s[i])
            i += 1
            continue
        name = m.group(1)
        j = i + len(m.group(0))
        if name in WRAP:
            a, j = _arg(s, j)
            out.append(_rewrite(a))
        elif name in ("frac", "tfrac", "dfrac", "cfrac"):
            a, j = _arg(s, j)
            b, j = _arg(s, j)
            out.append("(" + _rewrite(a) + ")/(" + _rewrite(b) + ")")
        elif name == "sqrt":
            if j < len(s) and s[j] == "[":
                k = s.find("]", j)
                j = k + 1 if k > 0 else j
            a, j = _arg(s, j)
            out.append("sqrt(" + _rewrite(a) + ")")
        elif name in ("ket", "bra", "braket", "ketbra"):
            a, j = _arg(s, j)
            a = _rewrite(a)
            if name == "ket":
                out.append("|" + a + ">")
            elif name == "bra":
                out.append("<" + a + "|")
            elif name == "braket":
                out.append("<" + a + ">")
            else:
                b, j = _arg(s, j)
                out.append("|" + a + "><" + _rewrite(b) + "|")
        elif name in ("label", "tag", "ref", "eqref", "cite", "hspace", "vspace"):
            _, j = _arg(s, j)
        else:
            mapped = SYN.get(name, name)
            if mapped and mapped[0] in "|<>.":
                out.append(mapped)
            elif mapped:
                out.append("\\" + mapped + " ")
        i = j
    return "".join(out)


def _pre(s: str) -> str:
    """steps 1 to 4 and the macro rewrite; commands stay as '\\name '."""
    if not s:
        return ""
    for k, v in GREEK.items():
        s = s.replace(k, "\\" + v + " ")
    for k, v in SYMS.items():
        s = s.replace(k, v)
    s = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+", lambda m: "^{" + m.group(0).translate(SUP) + "}", s)
    s = re.sub(r"[₀₁₂₃₄₅₆₇₈₉₋₊]+", lambda m: "_{" + m.group(0).translate(SUB) + "}", s)
    s = s.replace("$$", " ").replace("$", " ").replace("\\[", " ").replace("\\]", " ")
    s = s.replace("\\(", " ").replace("\\)", " ")
    s = ENV.sub(" ", s)
    s = MATENV.sub(" ", s)
    s = s.replace("\\\\", ",").replace("&", ",")
    s = SPACING.sub(" ", s)
    s = DROP.sub(" ", s)
    return _rewrite(s)


@functools.lru_cache(maxsize=2048)
def canon(s: str) -> str:
    s = _pre(s)
    s = s.replace("\\", "")
    s = re.sub(r"\*", "", s)
    s = re.sub(r"[{}()\[\]\s]", "", s)
    s = re.sub(r"dag(?!ger)", "dagger", s)
    s = re.sub(r",+", ",", s)
    s = re.sub(r"[,.;]+$", "", s)
    s = re.sub(r"^[,.;]+", "", s)
    s = s.replace("=,", "=").replace(",=", "=")
    return s


KNOWN = set(GREEK.values()) | {"sqrt", "exp", "ln", "log", "sin", "cos", "tan", "sinh", "cosh", "tanh", "coth",
                               "dagger", "dag", "hbar", "sum", "int", "prod", "otimes", "oplus", "partial", "infty",
                               "nabla", "approx", "propto", "det", "tr", "Tr", "max", "min", "arctan", "arcsin",
                               "frac", "sim", "pm", "square", "ell", "mod", "erf", "Re", "Im"}
TOK = re.compile(r"\\([a-zA-Z]+)|([A-Za-z]+)|(\d+(?:\.\d+)?)")


@functools.lru_cache(maxsize=2048)
def symbols(s: str) -> frozenset[str]:
    """symbol set: command names, known names written as words (pi, sqrt), other letter runs split into letters
    (math variables are single letters), numbers."""
    out = set()
    for m in TOK.finditer(_pre(s)):
        if m.group(1):
            out.add("dagger" if m.group(1) == "dag" else m.group(1))
        elif m.group(2):
            w = m.group(2)
            if w in KNOWN:
                out.add("dagger" if w == "dag" else w)
            else:
                out.update(w)
        else:
            out.add(m.group(3))
    out.discard("frac")
    return frozenset(out)


def match_expr(key: dict, text: str) -> dict:
    ct = canon(text)
    st = symbols(text)
    best = 0.0
    for v in [key["value"], *key.get("alts", [])]:
        ck = canon(v)
        if ck and ck in ct:
            return {"exact": True, "symbol_recall": 1.0}
        sk = symbols(v)
        if sk:
            best = max(best, len(sk & st) / len(sk))
    return {"exact": False, "symbol_recall": round(best, 4)}


TEXT_WRAP = re.compile(r"\\(?:text|textrm|textit|textbf|mathrm|mathit|mathbf|mathsf|mathbb|mathcal|operatorname|rm)"
                       r"\s*\{([^{}]*)\}")
SCRIPT_GROUP = re.compile(r"([_^])\s*\{\s*([^{}]*?)\s*\}")
SCRIPT_JOIN = re.compile(r"\s*([_^])\s*")


def _unwrap_text(m: re.Match) -> str:
    """\\text{sub} -> sub, without padding right after _ or ^ (H_{\\text{sub}} -> H_{sub}), else padded with spaces."""
    before = m.string[:m.start()].rstrip()
    return m.group(1) if before.endswith(("_", "^", "_{", "^{")) else " " + m.group(1) + " "


@functools.lru_cache(maxsize=2048)
def tex_flat(s: str) -> str:
    """LaTeX-insensitive form of a text key or an answer: Unicode Greek -> names, $ dropped, \\text{x} -> x,
    _{x} and ^{x} -> _x and ^x, the backslash before a command name replaced by a space, spaces around _ and ^ removed.
    Other braces are left for norm_text, which turns them into spaces."""
    for k, v in GREEK.items():
        s = s.replace(k, v)
    s = s.replace("$", " ")
    prev = None
    while prev != s:
        prev = s
        s = TEXT_WRAP.sub(_unwrap_text, s)
        s = SCRIPT_GROUP.sub(r"\1\2", s)
    s = re.sub(r"\\([A-Za-z]+)", r" \1", s)  # a space, so that \sigma_3\otimes stays two words
    return SCRIPT_JOIN.sub(r"\1", s)


@functools.lru_cache(maxsize=2048)
def norm_text(s: str) -> str:
    s = tex_flat(s).lower()
    s = re.sub(r"[\u2010-\u2015-]", " ", s)
    s = re.sub(r"[^\w\s^]", " ", s)
    return " " + re.sub(r"\s+", " ", s).strip() + " "


def match_text(key: dict, text: str) -> bool:
    nt = norm_text(text)
    return any(norm_text(p).strip() and norm_text(p) in nt for p in [key["value"], *key.get("alts", [])])


NUM = re.compile(r"(?<![\w.])[-+]?\d+(?:[.,]\d+)?(?:\s*(?:[eE]\s*[-+]?\d+|(?:\\times|×|x|\*|\\cdot|·)\s*10\s*\^?\s*"
                 r"\{?\s*[-+−]?\d+\}?|(?:×|x)\s*10[⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+))?")


@functools.lru_cache(maxsize=2048)
def numbers(text: str) -> tuple[float, ...]:
    t = text.replace("−", "-").replace("\u2009", "").replace("\\,", "")
    out = []
    for m in NUM.finditer(t):
        tok = m.group(0)
        mant = re.match(r"[-+]?\d+(?:[.,]\d+)?", tok).group(0)
        rest = tok[len(mant):]
        exp = 0
        e = re.search(r"[eE]\s*([-+]?\d+)", rest)
        if e:
            exp = int(e.group(1))
        else:
            e = re.search(r"10\s*\^?\s*\{?\s*([-+−]?\d+)", rest)
            if e:
                exp = int(e.group(1).replace("−", "-"))
            else:
                e = re.search(r"10([⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+)", rest)
                if e:
                    exp = int(e.group(1).translate(SUP))
        cands = {mant.replace(",", ".")} if "," in mant and re.fullmatch(r"[-+]?\d+,\d{1,2}", mant) else set()
        cands |= {mant.replace(",", "")}
        for c in cands:
            try:
                out.append(float(c) * 10.0 ** max(-300, min(300, exp)))
            except (ValueError, OverflowError):
                pass
    return tuple(out)


NUMBER_WORDS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20,
    "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "doce": 12, "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16, "dieciséis": 16, "diecisiete": 17,
    "dieciocho": 18, "diecinueve": 19, "veinte": 20}  # no "one", "uno", "una" (too common) and no "once" (English word)
WORD_TOKEN = re.compile(r"(?<![\w])[^\W\d_]+(?![\w])")
TENS_EN = r"(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
TENS_ES = r"(?:treinta|cuarenta|cincuenta|sesenta|setenta|ochenta|noventa)"
COMPOUND = re.compile(rf"\b{TENS_EN}[-\s](?:one|two|three|four|five|six|seven|eight|nine)\b|"
                      rf"\b{TENS_ES}\s+y\s+(?:un|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve)\b", re.I)
SCALE_AFTER = re.compile(r"\s*(?:hundred|thousand|million|billion|cien|ciento|mil|millon|millones)\b", re.I)


@functools.lru_cache(maxsize=2048)
def number_words(text: str) -> frozenset[int]:
    """Integers 2 to 20 written as English or Spanish words in the text (whole words; not inside 21 to 99 such as
    'thirty-two' or 'treinta y dos'; not before hundred / thousand / mil)."""
    t = COMPOUND.sub(" ", text.lower())
    out = set()
    for m in WORD_TOKEN.finditer(t):
        n = NUMBER_WORDS.get(m.group(0))
        if n is not None and not SCALE_AFTER.match(t, m.end()):
            out.add(n)
    return frozenset(out)


def match_num_digits(key: dict, text: str) -> bool:
    """the v1 rule: any number written in digits within rel of the key (sign ignored)."""
    v = float(key["value"])
    rel = key.get("rel", 0.02)
    return any(abs(abs(x) - abs(v)) <= rel * abs(v) + 1e-30 for x in numbers(text))


def match_num_word(key: dict, text: str) -> bool:
    """an integer key from 2 to 20 written as an English or Spanish number word (whole word)."""
    v = float(key["value"])
    return v == int(v) and 2 <= v <= 20 and int(v) in number_words(text)


def match_num(key: dict, text: str) -> bool:
    return match_num_digits(key, text) or match_num_word(key, text)


def match_key(key: dict, text: str) -> dict:
    if key["type"] == "expr":
        r = match_expr(key, text)
        return {"hit": r["exact"], "lenient": r["exact"] or r["symbol_recall"] >= 0.8, **r}
    if key["type"] == "num":
        d = match_num_digits(key, text)
        w = not d and match_num_word(key, text)
        return {"hit": d or w, "lenient": d or w, "word_only": w}
    h = match_text(key, text)
    return {"hit": h, "lenient": h}
