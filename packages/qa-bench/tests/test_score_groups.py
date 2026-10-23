"""Which groups a set has and who belongs to them."""

import pytest
from scoredata import question, text_key

from qa_bench.score.groups import auto_fields, field_value, make_groups


def qs():
    keys = [text_key("k")]
    return [
        question("A-1", keys, category="a", lang="en", survives="yes"),
        question("A-2", keys, category="b", lang="es", survives="no"),
        question("A-3", keys, category="b", lang="en", survives="partial"),
    ]


def names(groups):
    return [g.name for g in groups]


def test_the_fields_that_split_the_set_are_found():
    assert auto_fields(qs()) == ["lang", "tag:category", "tag:survives"]


def test_a_field_with_one_value_does_not_split():
    items = [question("A-1", [text_key("k")]), question("A-2", [text_key("k")])]
    assert auto_fields(items) == []


def test_a_tag_with_more_than_eight_values_does_not_split():
    items = [question(f"A-{i}", [text_key("k")], category=f"c{i}") for i in range(9)]
    assert "tag:category" not in auto_fields(items)
    items = [question(f"A-{i}", [text_key("k")], category=f"c{i}") for i in range(8)]
    assert "tag:category" in auto_fields(items)


def test_all_comes_first_then_each_value_sorted():
    groups = make_groups(qs(), ["lang", "tag:category"])
    assert names(groups) == ["all", "lang=en", "lang=es", "category=a", "category=b"]
    members = {g.name: [q["id"] for q in qs() if g.contains(q)] for g in groups}
    assert members["category=b"] == ["A-2", "A-3"]
    assert members["all"] == ["A-1", "A-2", "A-3"]


def test_a_union_holds_any_of_its_values():
    groups = make_groups(qs(), [], ["survives=partial|no", "lang=es"])
    assert names(groups) == ["all", "survives=partial|no", "lang=es"]
    assert [q["id"] for q in qs() if groups[1].contains(q)] == ["A-2", "A-3"]
    assert [q["id"] for q in qs() if groups[2].contains(q)] == ["A-2"]


@pytest.mark.parametrize(
    "union", ["category", "Category=a", "category=", "tag:category=a", "a=b=c"]
)
def test_a_union_the_group_name_cannot_hold_is_refused(union):
    with pytest.raises(ValueError, match="label=v1"):
        make_groups(qs(), [], [union])


def test_labels_and_values_the_score_format_cannot_name_are_left_out():
    items = [
        question("A-1", [text_key("k")], category="a"),
        question("A-2", [text_key("k")], category="b"),
    ]
    for item in items:
        item["tags"]["Bad-Name"] = "x"
        item["tags"]["eq"] = "a=b"
    groups = make_groups(items, ["tag:Bad-Name", "tag:eq", "tag:category"])
    assert names(groups) == ["all", "category=a", "category=b"]


def test_a_missing_value_belongs_to_no_group_of_that_field():
    items = qs()
    del items[0]["tags"]["survives"]
    groups = make_groups(items, ["tag:survives"])
    assert names(groups) == ["all", "survives=no", "survives=partial"]
    assert field_value(items[0], "tag:survives") is None
