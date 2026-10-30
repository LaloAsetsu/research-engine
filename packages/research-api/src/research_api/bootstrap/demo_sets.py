"""The synthetic question sets of fake mode: made-up papers, questions and people.

They go through the same use cases as real sets (import, review, freeze, copy, edit), so the
demo shows every state: a frozen version, a draft with reviews in each state, and a draft of
circuit questions. Nothing here comes from a real paper, question or person.
"""

from __future__ import annotations

import json
from typing import Any

from ..application.sets import SetServices
from ..domain.sets import Actor, SetRef, etag_of

OWNER = Actor("ana", admin=True)
REVIEWER = Actor("beto")
CORPUS = "demo-corpus"
ALPHA = "a1" * 32
BETA = "b2" * 32
GAMMA = "c3" * 32
SYNTHETIC = "Synthetic: no real paper, question or person."


def _passage(name: str, text: str) -> dict[str, Any]:
    return {"md_file": f"demo/{name}/document.md", "md_text": text, "source_text": text}


def _paper(doc_id: str, name: str, arxiv: str, text: str) -> dict[str, Any]:
    return {
        "doc_id": doc_id,
        "arxiv": arxiv,
        "doc": f"{name}.pdf",
        "passages": [_passage(name, text)],
    }


ALPHA_TEXT = "The exchange coupling between the two toy modes is written J throughout."
BETA_TEXT = "The follow-up replaces the single scalar J by a tuple of three couplings."
GAMMA_TEXT = "The echo time of the toy qubit is 8.6 ms, measured once in the methods."


def _item(item_id: str, question: str, answer: str, **fields: Any) -> dict[str, Any]:
    return {
        "id": item_id,
        "lang": "en",
        "lang_origin": "native",
        "question": question,
        "answer": answer,
        "form": "single",
        "content": "other",
        "corpus": CORPUS,
        "gold_papers": [],
        "writer": "ana",
        **fields,
    }


def paper_items() -> list[dict[str, Any]]:
    alpha = _paper(ALPHA, "alpha", "0000.00001v1", ALPHA_TEXT)
    beta = _paper(BETA, "beta", "0000.00002v1", BETA_TEXT)
    gamma = _paper(GAMMA, "gamma", "0000.00003v1", GAMMA_TEXT)
    return [
        _item(
            "DP-001",
            "What symbol does the synthetic paper Alpha use for the coupling between its two "
            "toy modes?",
            "J, the exchange coupling.",
            content="math",
            split="dev",
            gold_papers=[alpha],
            key_facts=[{"type": "text", "value": "J", "alts": ["exchange coupling"]}],
            tags={"category": "notation"},
        ),
        _item(
            "DP-001-es",
            "Que simbolo usa el articulo sintetico Alpha para el acoplamiento entre sus dos "
            "modos de juguete?",
            "J, el acoplamiento de intercambio.",
            lang="es",
            lang_origin="mirror",
            mirror_of="DP-001",
            content="math",
            split="dev",
            gold_papers=[alpha],
            key_facts=[{"type": "text", "value": "J", "alts": ["acoplamiento de intercambio"]}],
            tags={"category": "notation"},
        ),
        _item(
            "DP-002",
            "What does the synthetic paper Beta change in the coupling that Alpha introduces?",
            "Beta replaces the scalar coupling J by a tuple of three couplings.",
            form="multi_chain",
            content="math",
            split="test",
            gold_papers=[alpha, beta],
            key_facts=[
                {"type": "text", "value": "J", "paper": ALPHA},
                {"type": "text", "value": "three couplings", "paper": BETA},
            ],
            synthesis={
                "kind": "chain",
                "statement": "Beta generalizes the coupling defined in Alpha.",
                "rubric": "Names both the scalar J and the tuple of three couplings.",
            },
            necessity={ALPHA: [0], BETA: [1]},
            tags={"category": "chain"},
        ),
        _item(
            "DP-003",
            "Which paper in the demo corpus reports a relaxation time above one hour?",
            "No paper in the corpus reports it.",
            form="unanswerable",
            split="test",
            search_record={
                "corpus": CORPUS,
                "terms": ["relaxation time", "T1", "hour"],
                "date": "2026-01-01",
            },
        ),
        _item(
            "DP-004",
            "What echo time does the synthetic paper Gamma report for its toy qubit?",
            "8.6 ms",
            split="dev",
            gold_papers=[gamma],
            key_facts=[{"type": "num", "value": 8.6, "unit": "ms", "rel": 0.02}],
            choices={"distractors": ["0.98 ms", "3.1 ms", "12.5 ms"], "shuffle_seed": 7},
            tags={"qtype": "numeric"},
        ),
        _item(
            "DP-005",
            "Which paper reports the three-coupling generalization, Alpha or its follow-up?",
            "The follow-up (Beta).",
            form="neardup",
            content="math",
            gold_papers=[beta],
            confusers=[{"doc_id": ALPHA, "relation": "follow-up", "answers_too": False}],
        ),
        _item(
            "DP-006",
            "What is the name of the first toy mode of the synthetic paper Alpha?",
            "Mode one.",
            gold_papers=[alpha],
            notes="A duplicate of DP-001 in spirit; the demo rejects it.",
        ),
    ]


def circuit_items() -> list[dict[str, Any]]:
    x = [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0], [1.0, 0.0]]
    return [
        {
            "id": "DC-001",
            "lang": "en",
            "question": (
                "Is this circuit the identity on two wires?\n\nWires: 1; 2\nStep 1: X: wire 1\n"
                "Step 2: CNOT: control wire 1, target wire 2\nStep 3: X: wire 1"
            ),
            "answer": "no",
            "rationale": "It flips wire 2 whenever wire 1 starts as 0.",
            "form": "circuit_verify",
            "content": "circuit",
            "corpus": CORPUS,
            "gold_papers": [{"doc_id": ALPHA[:16], "passages": [{"block_index": 1}]}],
            "circuit": {"wires": 2},
            "notes": SYNTHETIC,
        },
        {
            "id": "DC-002",
            "lang": "en",
            "question": "Prepare the basis state |11> from |00> on two wires, using only X gates.",
            "form": "circuit_create",
            "content": "circuit",
            "corpus": "none",
            "gold_papers": [],
            "circuit": {
                "wires": 2,
                "allowed_gates": ["X"],
                "target_kind": "state",
                "target": x,
                "equality": "exact",
                "input_state": "|00>",
            },
        },
        {
            "id": f"CL-{BETA[:16]}-b2",
            "lang": "en",
            "form": "circuit_claim",
            "content": "circuit",
            "corpus": CORPUS,
            "gold_papers": [{"doc_id": BETA[:16], "passages": [{"block_index": 2}]}],
            "circuit": {
                "claim": "CZ = (I x H) CNOT (I x H)",
                "target_kind": "unitary",
                "target": "CZ (exact)",
            },
            "notes": SYNTHETIC,
        },
    ]


def _file(name: str, version: str, items: list[dict[str, Any]], notes: str) -> bytes:
    header = {
        "format": "qset/1",
        "set": f"{name}/{version}",
        "created": "2026-01-01T00:00:00+00:00",
        "notes": notes,
    }
    return "".join(
        json.dumps(line, ensure_ascii=False) + "\n" for line in [header, *items]
    ).encode()


def _decide(sets: SetServices, who: Actor, ref: SetRef, **decision: Any) -> None:
    """A decision made on the draft as it is now, the way a reviewer who just read it makes it."""
    etag = etag_of(sets.manage.get_version(ref).version.md5)
    sets.review.add(who, ref, if_match=etag, **decision)


def seed_demo_sets(sets: SetServices) -> None:
    """Fill an empty catalog with the demo sets (fake mode only)."""
    manage = sets.manage
    v1 = SetRef("demo-papers", "v1")
    manage.import_set(
        OWNER,
        _file("demo-papers", "v1", paper_items(), f"{SYNTHETIC} Two papers, a chain, a mirror."),
        name=v1.name,
        version=v1.version,
    )
    for who, item_id in [(OWNER, "DP-001"), (REVIEWER, "DP-001"), (OWNER, "DP-002")]:
        _decide(sets, who, v1, item_id=item_id, verdict="accept")
    _decide(sets, REVIEWER, v1, item_id="DP-002", verdict="accept")
    _decide(sets, OWNER, v1, item_id="DP-004", verdict="accept")
    _decide(sets, OWNER, v1, item_id="DP-006", verdict="reject", note="Adds nothing over DP-001.")
    _, plan = sets.freeze.plan(v1)
    sets.freeze.freeze(OWNER, v1, etag_of(plan.token))

    v2 = manage.create(REVIEWER, name="demo-papers", copy_from="v1").ref
    extra = _item(
        "DP-007",
        "How many couplings does the synthetic paper Beta use?",
        "Three.",
        content="math",
        gold_papers=[_paper(BETA, "beta", "0000.00002v1", BETA_TEXT)],
        key_facts=[{"type": "text", "value": "three", "alts": ["3"]}],
    )
    row = manage.get_version(v2).version
    manage.add_item(REVIEWER, v2, extra, etag_of(row.md5))
    _decide(sets, OWNER, v2, item_id="DP-007", verdict="accept")
    _decide(sets, OWNER, v2, item_id="DP-001", verdict="accept")
    _decide(sets, REVIEWER, v2, item_id="DP-001", verdict="accept")
    _decide(sets, REVIEWER, v2, item_id="DP-004", verdict="reject", note="The method is ambiguous.")
    _decide(
        sets,
        OWNER,
        v2,
        item_id="DP-002",
        verdict="edit",
        note="Name the paper that introduces J.",
        edit={"answer": "Beta replaces the scalar coupling J of Alpha by a tuple of three."},
    )

    manage.import_set(
        OWNER,
        _file("demo-circuits", "v1", circuit_items(), f"{SYNTHETIC} Circuit questions."),
        name="demo-circuits",
        version="v1",
    )
