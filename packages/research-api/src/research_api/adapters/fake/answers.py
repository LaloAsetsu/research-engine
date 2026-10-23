"""Canned answers over the invented corpus, in English and Spanish.

An answer writes its citations as ``[[key:first-last]]``; ``render`` turns them into the
``(<16 hex> pages a-b)`` the real answerer prints and builds the References list under it, so
the fake engine goes through the same reader of formatted answers as the real one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .corpus import PAPERS, FakePaper

_CITE = re.compile(r"\[\[([a-z-]+):(\d+)-(\d+)\]\]")
BY_KEY = {paper.key: paper for paper in PAPERS}


@dataclass(frozen=True)
class Topic:
    keywords: tuple[str, ...]  # lowercase words or phrases, English and Spanish
    english: str
    spanish: str
    warning: str | None = None


TOPICS: tuple[Topic, ...] = (
    Topic(
        (
            "t1",
            "t2",
            "transmon",
            "coherence",
            "coherencia",
            "relaxation",
            "relajacion",
            "dephasing",
        ),
        "In fixed-frequency transmons $T_1$ is limited mostly by dielectric loss at the "
        "substrate-metal interface, which sets the loss rate $1/T_1^{\\mathrm{diel}} = \\omega\\, "
        "p_{\\mathrm{surf}} \\tan\\delta$ [[transmon:2-2]]. Two-level systems add a drifting term "
        "that can halve $T_1$ for hours, and the Purcell effect matters only for the strongest "
        "readout couplings [[transmon:1-2]]. Dephasing is dominated by residual thermal photons "
        "in the readout resonator, so $T_2$ improves with filtering, and an echo sequence gives "
        "a $T_2$ about 1.6 times the Ramsey value [[transmon:3-3]].",
        "En los transmones de frecuencia fija $T_1$ queda limitado sobre todo por las pérdidas "
        "dieléctricas en la interfaz sustrato-metal, con una tasa "
        "$1/T_1^{\\mathrm{diel}} = \\omega\\, p_{\\mathrm{surf}} \\tan\\delta$ [[transmon:2-2]]. "
        "Los sistemas de dos niveles añaden un término variable que puede reducir $T_1$ a la "
        "mitad durante horas, y el efecto Purcell solo importa con los acoples de lectura más "
        "fuertes [[transmon:1-2]]. El desfase lo domina el resto de fotones térmicos del "
        "resonador de lectura, y una secuencia de eco da un $T_2$ unas 1.6 veces mayor que el "
        "de Ramsey [[transmon:3-3]].",
    ),
    Topic(
        (
            "surface code",
            "surface-code",
            "codigo de superficie",
            "threshold",
            "umbral",
            "logical",
            "logico",
            "error correction",
            "correccion de errores",
        ),
        "A rotated surface code of distance $d$ has a threshold near 0.9% per operation under "
        "circuit-level noise [[surface:1-1]]. Below threshold the logical error per round scales "
        "as $A\\,(p/p_{\\mathrm{th}})^{\\lfloor (d+1)/2\\rfloor}$, so each step of two in the "
        "distance buys about a factor of eight at $p = 0.1\\%$ [[surface:2-2]]. The price is "
        "$d^2$ data qubits plus $d^2-1$ measurement qubits: 337 physical qubits per logical "
        "qubit for $d = 13$ [[surface:3-3]].",
        "Un código de superficie rotado de distancia $d$ tiene un umbral cercano al 0.9% por "
        "operación con ruido a nivel de circuito [[surface:1-1]]. Por debajo del umbral el error "
        "lógico por ronda escala como $A\\,(p/p_{\\mathrm{th}})^{\\lfloor (d+1)/2\\rfloor}$, de "
        "modo que cada aumento de dos en la distancia da un factor de ocho con $p = 0.1\\%$ "
        "[[surface:2-2]]. El costo son $d^2$ qubits de datos más $d^2-1$ de medición: 337 qubits "
        "físicos por qubit lógico con $d = 13$ [[surface:3-3]].",
        "1 of 6 documents of the corpus is not in the index and cannot be used for this "
        "answer; run `docingest index build`",
    ),
    Topic(
        ("teleport", "teletransport", "bell", "mid-circuit", "measurement", "medicion"),
        "The circuit teleports the top qubit with a CNOT, a Hadamard and two mid-circuit "
        "measurements on the sender's side, and a classically controlled $X^{m_2} Z^{m_1}$ on "
        "the receiver's qubit [[teleport:2-2]]. Each of the four outcomes occurs with "
        "probability $1/4$ and the corrected output equals $|\\psi\\rangle$ in every case "
        "[[teleport:3-3]]. Preparing the Bell pair on the receiver's hardware saves one "
        "two-qubit gate compared with the textbook version [[teleport:3-3]].",
        "El circuito teletransporta el qubit superior con un CNOT, una Hadamard y dos mediciones "
        "a mitad de circuito del lado del emisor, y un $X^{m_2} Z^{m_1}$ controlado "
        "clásicamente en el qubit del receptor [[teleport:2-2]]. Cada uno de los cuatro "
        "resultados ocurre con probabilidad $1/4$ y la salida corregida es igual a "
        "$|\\psi\\rangle$ en todos los casos [[teleport:3-3]]. Preparar el par de Bell en el "
        "hardware del receptor ahorra una compuerta de dos qubits frente a la versión de libro "
        "[[teleport:3-3]].",
    ),
    Topic(
        ("grover", "search", "busqueda", "oracle", "oraculo", "amplitude"),
        "Grover's algorithm needs $O(\\sqrt{N})$ oracle calls, and after $k$ iterations the "
        "success probability is $\\sin^2((2k+1)\\theta)$ with $\\sin\\theta = 1/\\sqrt{N}$ "
        "[[grover:1-1]]. If the oracle fails with probability $\\epsilon$ per call, the best "
        "number of iterations is smaller: for $N = 2^{10}$ and $\\epsilon = 0.5\\%$ it is 21 "
        "instead of 25, and the best success probability falls from 0.999 to 0.82 "
        "[[grover:2-2]]. With a noisy oracle it pays to stop early and repeat the search "
        "[[grover:3-3]].",
        "El algoritmo de Grover necesita $O(\\sqrt{N})$ llamadas al oráculo y, tras $k$ "
        "iteraciones, la probabilidad de éxito es $\\sin^2((2k+1)\\theta)$ con "
        "$\\sin\\theta = 1/\\sqrt{N}$ [[grover:1-1]]. Si el oráculo falla con probabilidad "
        "$\\epsilon$ por llamada, el mejor número de iteraciones es menor: con $N = 2^{10}$ y "
        "$\\epsilon = 0.5\\%$ son 21 en lugar de 25, y la mejor probabilidad de éxito baja de "
        "0.999 a 0.82 [[grover:2-2]]. Con un oráculo ruidoso conviene parar antes y repetir la "
        "búsqueda [[grover:3-3]].",
    ),
    Topic(
        ("vqe", "variational", "variacional", "barren", "plateau", "ansatz", "eigensolver"),
        "A variational eigensolver minimizes $E(\\boldsymbol\\theta)$ over circuit parameters "
        "[[vqe:1-1]]. A hardware-efficient ansatz reaches the ground energy at depth 6 for 4 "
        "qubits but shows a barren plateau at 12 qubits, where the gradient variance shrinks "
        "like $2^{-n}$ [[vqe:2-2]]. Starting from a shallow ansatz with parameters near zero "
        "avoided the plateau up to 16 qubits [[vqe:3-3]].",
        "Un eigensolver variacional minimiza $E(\\boldsymbol\\theta)$ sobre los parámetros del "
        "circuito [[vqe:1-1]]. Un ansatz eficiente en hardware llega a la energía base con "
        "profundidad 6 para 4 qubits, pero con 12 qubits aparece una meseta estéril, donde la "
        "varianza del gradiente decae como $2^{-n}$ [[vqe:2-2]]. Empezar con un ansatz poco "
        "profundo y parámetros cercanos a cero evitó la meseta hasta 16 qubits [[vqe:3-3]].",
    ),
    Topic(
        (
            "cross-resonance",
            "cross resonance",
            "resonancia cruzada",
            "echo",
            "eco",
            "two-qubit gate",
            "compuerta de dos qubits",
            "calibrat",
            "calibrac",
        ),
        "The cross-resonance gate drives the control at the target's frequency and has a wanted "
        "$ZX$ term next to unwanted $IX$, $ZI$ and $ZZ$ terms; an echoed sequence cancels $IX$ "
        "and $ZI$ to first order [[cross-resonance:1-1]]. The amplitude is set so the $ZX$ angle "
        "is $\\pi/2$, and over 30 days the gate error stayed between 0.6% and 1.1% with a "
        "4-minute recalibration [[cross-resonance:2-2]].",
        "La compuerta de resonancia cruzada excita el control a la frecuencia del blanco y tiene "
        "un término $ZX$ deseado junto a términos $IX$, $ZI$ y $ZZ$ no deseados; una secuencia "
        "con eco cancela $IX$ y $ZI$ a primer orden [[cross-resonance:1-1]]. La amplitud se fija "
        "para que el ángulo $ZX$ sea $\\pi/2$ y, en 30 días, el error de la compuerta se mantuvo "
        "entre 0.6% y 1.1% con una recalibración de 4 minutos [[cross-resonance:2-2]].",
    ),
)

NO_EVIDENCE_EN = (
    "The papers in this demonstration corpus do not contain enough information to answer this "
    "question. It covers transmon coherence, surface-code memories, a teleportation circuit, "
    "Grover search with a noisy oracle, variational eigensolvers and cross-resonance gates."
)
NO_EVIDENCE_ES = (
    "Los artículos de este corpus de demostración no contienen información suficiente para "
    "responder la pregunta. Tratan de coherencia en transmones, memorias de código de "
    "superficie, un circuito de teletransportación, la búsqueda de Grover con un oráculo "
    "ruidoso, eigensolvers variacionales y compuertas de resonancia cruzada."
)


def is_spanish(question: str) -> bool:
    text = question.lower()
    accents = any(ch in text for ch in "áéíóúñ¿¡")
    words = set(re.findall(r"[a-záéíóúñ]+", text))
    spanish_words = {
        "que",
        "qué",
        "como",
        "cómo",
        "cual",
        "cuál",
        "el",
        "la",
        "los",
        "las",
        "de",
        "del",
        "un",
        "una",
        "por",
        "para",
        "limita",
        "es",
        "son",
        "con",
        "en",
        "se",
        "dime",
        "explica",
        "cuales",
        "cuáles",
    }
    return accents or len(words & spanish_words) >= 2


def strip_accents(text: str) -> str:
    table = str.maketrans("áéíóúñü", "aeiounu")
    return text.lower().translate(table)


def _mentions(text: str, words: set[str], keyword: str) -> bool:
    """A phrase counts when the text holds it; a word counts when the text has it as a word, or
    (for a stem of five letters or more) a word that starts with it."""
    if " " in keyword or "-" in keyword:
        return keyword in text
    return keyword in words or (len(keyword) >= 5 and any(w.startswith(keyword) for w in words))


def pick_topic(question: str) -> Topic | None:
    """The topic whose keywords the question mentions most (the first one on a tie), or None."""
    text = strip_accents(question)
    words = set(re.findall(r"[a-z0-9]+", text))
    scores = [
        sum(_mentions(text, words, strip_accents(k)) for k in topic.keywords) for topic in TOPICS
    ]
    best = max(scores)
    return TOPICS[scores.index(best)] if best > 0 else None


@dataclass(frozen=True)
class Rendered:
    text: str  # the formatted answer: body, blank line, "References", entries
    cited: tuple[tuple[FakePaper, int, int], ...]  # one per References entry, in order


def render(template: str) -> Rendered:
    """Replace ``[[key:a-b]]`` by ``(<id> pages a-b)`` and add the References list."""
    cited: list[tuple[FakePaper, int, int]] = []

    def cite(match: re.Match[str]) -> str:
        paper = BY_KEY[match[1]]
        entry = (paper, int(match[2]), int(match[3]))
        if entry not in cited:
            cited.append(entry)
        return f"({paper.short_id} pages {match[2]}-{match[3]})"

    body = _CITE.sub(cite, template)
    if not cited:
        return Rendered(body, ())
    entries = "\n".join(
        f"{n}. ({paper.short_id} pages {a}-{b}): {paper.citation}"
        for n, (paper, a, b) in enumerate(cited, start=1)
    )
    return Rendered(f"{body}\n\nReferences\n\n{entries}", tuple(cited))
