"""Groups of questions: ``all``, ``field=value`` for each value, and unions ``field=v1|v2``."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

Question = Mapping[str, Any]

TAG = "tag:"
ITEM_FIELDS = ("lang", "form", "content", "split", "found_by")
MAX_TAG_VALUES = 8
# The group name pattern of qa-score/1: a lower-case label, '=', and a value without '='.
LABEL = re.compile(r"[a-z_]+")


@dataclass(frozen=True)
class Group:
    """A named subset of the questions."""

    name: str
    contains: Callable[[Question], bool]


def field_value(question: Question, field: str) -> Any:
    """The value of a set field, or of a tag when the field is written ``tag:NAME``."""
    if field.startswith(TAG):
        return (question.get("tags") or {}).get(field[len(TAG) :])
    return question.get(field)


def _label(field: str) -> str:
    return field.removeprefix(TAG)


def _usable(field: str, value: Any) -> bool:
    return (
        value is not None and "=" not in str(value) and LABEL.fullmatch(_label(field)) is not None
    )


def auto_fields(questions: Iterable[Question]) -> list[str]:
    """The fields that split the set: item fields with two or more values, and tags with 2 to 8."""
    items = list(questions)
    fields = [f for f in ITEM_FIELDS if len({q.get(f) for q in items if q.get(f) is not None}) > 1]
    tag_values: dict[str, set[str]] = {}
    for q in items:
        for name, value in (q.get("tags") or {}).items():
            tag_values.setdefault(name, set()).add(value)
    fields += [
        TAG + name
        for name, values in sorted(tag_values.items())
        if 1 < len(values) <= MAX_TAG_VALUES
    ]
    return fields


def field_warnings(questions: Iterable[Question], fields: Sequence[str]) -> list[str]:
    """What is wrong with fields the caller named: a label the group name cannot hold, a field no
    question has, values with ``=``. These fields give no group, so the caller hears about it."""
    items = list(questions)
    warnings: list[str] = []
    for field in fields:
        if LABEL.fullmatch(_label(field)) is None:
            warnings.append(
                f"group field {field!r} gives no group: its name must be a-z and _ only"
            )
            continue
        values = [v for q in items if (v := field_value(q, field)) is not None]
        if not values:
            warnings.append(f"group field {field!r} gives no group: no question has it")
        elif any("=" in str(v) for v in values):
            warnings.append(f"group field {field!r}: values with '=' are left out")
    return warnings


def make_groups(
    questions: Sequence[Question], fields: Sequence[str], unions: Sequence[str] = ()
) -> list[Group]:
    """``all``, a group per value of each field, then the explicit unions.

    A union is written ``label=v1|v2``: a question belongs when its field (an item field named
    ``label``, else the tag ``label``) has any of the listed values. Fields whose label or values
    the ``qa-score/1`` group name cannot hold (a label outside ``a-z_``, a value with ``=``) are
    skipped; a union that cannot be written raises ``ValueError``.
    """
    groups = [Group("all", lambda _q: True)]
    for field in fields:
        values = sorted(
            {str(field_value(q, field)) for q in questions if _usable(field, field_value(q, field))}
        )
        groups += [
            Group(
                f"{_label(field)}={value}",
                lambda q, f=field, v=value: str(field_value(q, f)) == v,
            )
            for value in values
        ]
    for union in unions:
        label, _, values = union.partition("=")
        wanted = set(values.split("|"))
        if not LABEL.fullmatch(label) or not values or "=" in values:
            raise ValueError(f"group {union!r} must read label=v1|v2 with a label of a-z and _")
        field = label if label in ITEM_FIELDS else TAG + label
        groups.append(Group(union, lambda q, f=field, w=wanted: str(field_value(q, f)) in w))
    return groups
