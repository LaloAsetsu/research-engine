"""Circuit tasks (verify and create) and circuit claims, and their ``qset/1`` items."""

from __future__ import annotations

from typing import Any


def task_to_item(row: dict[str, Any], corpus: str | None) -> dict[str, Any]:
    """A circuit task: ``verify`` becomes ``circuit_verify``, ``create`` becomes ``circuit_create``.

    A verify task is about a block of a paper (``corpus`` names the corpus); a create task needs no
    corpus (``none``) and has no gold paper.
    """
    item: dict[str, Any] = {
        "id": row["id"],
        "lang": "en",
        "lang_origin": "native",
        "question": row["prompt"],
        "form": "circuit_" + row["kind"],
        "content": "circuit",
        "notes": row["notes"],
    }
    circuit: dict[str, Any] = {"wires": row["wires"]}
    if row["kind"] == "verify":
        target = row["target"]
        item.update(
            {
                "answer": target["answer"],
                "rationale": target["reason"],
                "corpus": corpus,
                "gold_papers": [
                    {"doc_id": row["paper_id"], "passages": [{"block_index": row["block_index"]}]}
                ],
            }
        )
        if "global_phase" in target:
            circuit["global_phase"] = target["global_phase"]
    else:
        item.update({"corpus": "none", "gold_papers": []})
        circuit.update(
            {
                "allowed_gates": row["allowed_gates"],
                "target_kind": row["target_kind"],
                "target": row["target"],
                "equality": row["equality"],
            }
        )
        if row["input_state"] is not None:
            circuit["input_state"] = row["input_state"]
    item["circuit"] = circuit
    return item


def task_from_item(item: dict[str, Any]) -> dict[str, Any]:
    """The circuit task row an item was made from."""
    circuit = item["circuit"]
    kind = item["form"].removeprefix("circuit_")
    out: dict[str, Any] = {
        "id": item["id"],
        "kind": kind,
        "prompt": item["question"],
        "wires": circuit["wires"],
        "notes": item["notes"],
    }
    if kind == "verify":
        target = {"answer": item["answer"], "reason": item["rationale"]}
        if "global_phase" in circuit:
            target["global_phase"] = circuit["global_phase"]
        gold = item["gold_papers"][0]
        out.update(
            {
                "allowed_gates": None,
                "target_kind": "answer",
                "target": target,
                "paper_id": gold["doc_id"],
                "block_index": gold["passages"][0]["block_index"],
            }
        )
    else:
        out.update(
            {
                "allowed_gates": circuit["allowed_gates"],
                "target_kind": circuit["target_kind"],
                "target": circuit["target"],
                "equality": circuit["equality"],
                "input_state": circuit.get("input_state"),
            }
        )
    return out


def claim_to_item(row: dict[str, Any], corpus: str | None) -> dict[str, Any]:
    """A circuit claim (a TSV row, every value a string) as a ``circuit_claim`` item.

    Claims have no ids of their own; the id is ``CL-<paper_id>-b<block_index>``. An ``input_state``
    of ``-`` means none and is left out.
    """
    circuit = {
        "claim": row["claim_quote_short"],
        "target_kind": row["target_kind"],
        "target": row["target"],
    }
    if row["input_state"] != "-":
        circuit["input_state"] = row["input_state"]
    return {
        "id": f"CL-{row['paper_id']}-b{row['block_index']}",
        "lang": "en",
        "form": "circuit_claim",
        "content": "circuit",
        "corpus": corpus,
        "gold_papers": [
            {"doc_id": row["paper_id"], "passages": [{"block_index": int(row["block_index"])}]}
        ],
        "circuit": circuit,
        "notes": row["notes"],
    }


def claim_from_item(item: dict[str, Any]) -> dict[str, Any]:
    """The circuit claim row an item was made from."""
    gold, circuit = item["gold_papers"][0], item["circuit"]
    return {
        "paper_id": gold["doc_id"],
        "block_index": str(gold["passages"][0]["block_index"]),
        "claim_quote_short": circuit["claim"],
        "target_kind": circuit["target_kind"],
        "target": circuit["target"],
        "input_state": circuit.get("input_state", "-"),
        "notes": item["notes"],
    }
