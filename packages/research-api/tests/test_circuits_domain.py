from __future__ import annotations

import json

import pytest

from research_api.domain.circuits import (
    MAX_STATE_WIRES,
    MAX_UNITARY_WIRES,
    CircuitCaps,
    ensure_within_caps,
    operation_count,
    read_json_strict,
    wire_count,
)
from research_api.domain.errors import BadRequestError, InvalidInputError


def circuit(wires: int = 2, ops: int = 2) -> dict[str, object]:
    steps = [[{"kind": "gate", "label": "H", "targets": [1]}] for _ in range(ops)]
    return {"format": "circuit/1", "wires": wires, "parts": [steps]}


def test_json_is_read_like_json() -> None:
    assert read_json_strict('{"a": [1, 2.5, "x", null, true], "b": {"c": {}}}', what="x") == {
        "a": [1, 2.5, "x", None, True],
        "b": {"c": {}},
    }


def test_bytes_are_read_too() -> None:
    assert read_json_strict(b'{"a": 1}', what="x") == {"a": 1}


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1, "a": 2}',
        '{"a": {"b": 1, "b": 1}}',
        '[{"k": 1}, {"j": 1, "j": 2}]',
        '{"a": 1, "\\u0061": 2}',  # the same key spelled with an escape
    ],
)
def test_a_key_written_twice_is_refused_wherever_it_is(text: str) -> None:
    with pytest.raises(BadRequestError, match="twice"):
        read_json_strict(text, what="circuit_json")


def test_the_message_names_the_input_and_the_key() -> None:
    with pytest.raises(BadRequestError) as stop:
        read_json_strict('{"controls": [1], "controls": [2]}', what="circuit_json")
    assert stop.value.detail.startswith("circuit_json gives the key 'controls' twice")


def test_a_very_long_key_is_not_echoed_in_full() -> None:
    key = "k" * 5000
    with pytest.raises(BadRequestError) as stop:
        read_json_strict(json.dumps({key: 1}).replace("}", f', "{key}": 2}}'), what="x")
    assert len(stop.value.detail) < 300


@pytest.mark.parametrize("text", ["NaN", "[Infinity]", '{"a": -Infinity}'])
def test_constants_json_does_not_have_are_refused(text: str) -> None:
    with pytest.raises(BadRequestError, match="does not allow"):
        read_json_strict(text, what="x")


@pytest.mark.parametrize("text", ["", "{", '{"a": }', "nope", "1" * 5000])
def test_text_that_is_not_json_is_refused_without_echoing_it(text: str) -> None:
    with pytest.raises(BadRequestError, match="not valid JSON") as stop:
        read_json_strict(text, what="circuit_json")
    assert text[:20] not in stop.value.detail or text == ""


def test_text_that_is_not_utf8_is_refused() -> None:
    with pytest.raises(BadRequestError, match="not valid JSON"):
        read_json_strict(b"\xff\xfe", what="x")


def test_a_document_nested_too_deeply_is_refused() -> None:
    with pytest.raises(BadRequestError, match="nested too deeply"):
        read_json_strict("[" * 100_000, what="x")


def test_wire_count_reads_integers_only() -> None:
    assert wire_count({"wires": 3}) == 3
    for document in ({"wires": True}, {"wires": "3"}, {"wires": 2.0}, {}, [], "x", None):
        assert wire_count(document) is None


def test_operations_are_counted_per_target() -> None:
    document = {
        "parts": [
            [
                [{"kind": "gate", "targets": [1, 2, 3]}, {"kind": "x", "targets": [1]}],
                [{"kind": "z_dots", "controls": [1, 2]}, {"kind": "measure", "targets": []}],
            ],
            [[{"kind": "swap", "targets": [1, 2]}]],
        ]
    }
    # gate 3, x 1, z_dots 1, a measure with no target 1, a swap 2: a count that errs high
    assert operation_count(document) == 3 + 1 + 1 + 1 + 2


def test_barriers_and_text_cost_nothing() -> None:
    step = [{"kind": "barrier", "targets": [1, 2]}, {"kind": "text", "targets": [1]}]
    assert operation_count({"parts": [[step]]}) == 0


@pytest.mark.parametrize(
    "document",
    [None, [], "x", {}, {"parts": 3}, {"parts": [3]}, {"parts": [[3]]}, {"parts": [[[3, None]]]}],
)
def test_a_document_that_is_not_shaped_like_a_circuit_counts_as_nothing(document: object) -> None:
    assert operation_count(document) == 0


CAPS = CircuitCaps(max_ops=5)


def test_a_circuit_inside_the_caps_passes() -> None:
    ensure_within_caps([("/c", circuit(MAX_STATE_WIRES, 5))], CAPS, max_wires=MAX_STATE_WIRES)


def test_too_many_wires_is_a_422_that_names_the_field() -> None:
    with pytest.raises(InvalidInputError, match="13 wires; this check takes at most 12") as stop:
        ensure_within_caps([("/c", circuit(13))], CAPS, max_wires=MAX_STATE_WIRES)
    assert [e.path for e in stop.value.errors] == ["/c"]
    assert "quantum-sim" in stop.value.detail


def test_the_unitary_limit_is_smaller() -> None:
    ensure_within_caps([("/c", circuit(MAX_UNITARY_WIRES))], CAPS, max_wires=MAX_UNITARY_WIRES)
    with pytest.raises(InvalidInputError, match="9 wires"):
        ensure_within_caps([("/c", circuit(9))], CAPS, max_wires=MAX_UNITARY_WIRES)


def test_the_second_circuit_is_checked_and_named() -> None:
    with pytest.raises(InvalidInputError) as stop:
        ensure_within_caps([("/a", circuit(2)), ("/b", circuit(20))], CAPS, max_wires=12)
    assert [e.path for e in stop.value.errors] == ["/b"]


def test_too_many_operations_is_a_422() -> None:
    with pytest.raises(InvalidInputError, match="6 operations; this endpoint takes at most 5"):
        ensure_within_caps([("/c", circuit(2, 6))], CAPS, max_wires=12)


def test_operations_are_added_up_over_the_circuits() -> None:
    with pytest.raises(InvalidInputError, match="6 operations"):
        ensure_within_caps([("/a", circuit(2, 3)), ("/b", circuit(2, 3))], CAPS, max_wires=12)


def test_a_document_that_is_not_a_circuit_is_left_to_the_simulator() -> None:
    ensure_within_caps([("/c", [1, 2, 3])], CAPS, max_wires=12)
