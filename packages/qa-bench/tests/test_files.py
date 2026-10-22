"""Whole files: strict reading, the rules between lines, gzip, md5 identity and deterministic writing."""

import gzip
import hashlib
import json
import sys

import pytest
from conftest import FIXTURES, jsonl_text, lines_of

from qa_bench.formats import (
    QA_RUN,
    QA_SCORE,
    QSET,
    REVIEWS,
    FormatError,
    build,
    load,
    load_bytes,
    parse,
    read,
    read_qset,
    read_reviews,
    read_run,
    read_score,
    write,
)
from qa_bench.formats.jsonl import MAX_DEPTH

NAMES = {
    "example.qset.jsonl": QSET,
    "example-bare.qa-run.jsonl": QA_RUN,
    "example-harness.qa-run.jsonl": QA_RUN,
    "example-oracle.qa-run.jsonl": QA_RUN,
    "example.qa-score.jsonl": QA_SCORE,
    "example.reviews.jsonl": REVIEWS,
    "example-answers.reviews.jsonl": REVIEWS,
}


@pytest.mark.parametrize(("name", "fmt"), NAMES.items())
def test_every_fixture_file_is_valid_and_its_md5_is_that_of_its_text(name, fmt):
    loaded = load(FIXTURES / name)
    assert loaded.ok
    assert loaded.file is not None
    assert loaded.file.format == fmt
    assert loaded.file.md5 == hashlib.md5((FIXTURES / name).read_bytes()).hexdigest()


@pytest.mark.parametrize(("name", "fmt"), NAMES.items())
def test_writing_what_was_read_gives_the_same_bytes(name, fmt):
    file = read(FIXTURES / name, fmt)
    assert file.to_bytes() == (FIXTURES / name).read_bytes()


def test_the_typed_readers_each_expect_their_format(tmp_path):
    assert read_qset(FIXTURES / "example.qset.jsonl").format == QSET
    assert read_run(FIXTURES / "example-bare.qa-run.jsonl").format == QA_RUN
    assert read_score(FIXTURES / "example.qa-score.jsonl").format == QA_SCORE
    assert read_reviews(FIXTURES / "example.reviews.jsonl").format == REVIEWS
    with pytest.raises(FormatError, match="expected qset/1, the file is qa-run/1"):
        read_qset(FIXTURES / "example-bare.qa-run.jsonl")


def test_the_run_ids_of_the_fixtures_point_at_the_md5_of_the_set():
    qset = read_qset(FIXTURES / "example.qset.jsonl")
    for name in ("example-bare.qa-run.jsonl", "example-harness.qa-run.jsonl"):
        assert read_run(FIXTURES / name).header["set"]["md5"] == qset.md5


def test_line_numbers_follow_the_text_even_with_blank_lines():
    lines = lines_of("example.qset.jsonl")
    text = jsonl_text(lines[:2]) + "\n\n" + jsonl_text(lines[2:3])
    loaded = parse(text)
    assert loaded.file is not None
    assert loaded.file.numbers == (2, 5)
    assert loaded.file.line_of(1) == 5


def test_a_repeated_key_is_refused_with_its_line():
    lines = lines_of("example.qset.jsonl")
    text = jsonl_text(lines[:1]) + '{"id": "EX-1", "id": "EX-2"}\n'
    (problem, *_) = parse(text).problems
    assert problem.line == 2
    assert "given twice" in problem.message


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_a_json_constant_that_is_not_json_is_refused(constant):
    header = jsonl_text(lines_of("example.qset.jsonl")[:1])
    (problem, *_) = parse(header + '{"id": ' + constant + "}\n").problems
    assert problem.line == 2
    assert "not valid JSON" in problem.message


def _qset_text_with(item_json):
    header = jsonl_text(lines_of("example.qset.jsonl")[:1])
    return header + item_json + "\n"


@pytest.mark.parametrize(
    "answer", ['"J\\ud800"', '"\\udc00 low first"', '"\\ud83d then text"', '"\\ude00\\ud83d"']
)
def test_an_unpaired_surrogate_escape_is_refused_where_it_is_read(answer):
    (problem, *_) = parse(_qset_text_with('{"id": "X", "answer": ' + answer + "}")).problems
    assert problem.line == 2
    assert "unpaired surrogate" in problem.message


def test_an_unpaired_surrogate_is_refused_in_a_key_and_in_a_nested_value():
    (in_key, *_) = parse(_qset_text_with('{"id": "X", "\\ud800": 1}')).problems
    (nested, *_) = parse(_qset_text_with('{"id": "X", "tags": [["a", {"b": "\\udfff"}]]}')).problems
    assert in_key.line == nested.line == 2
    assert "unpaired surrogate" in in_key.message + nested.message


def test_a_surrogate_pair_escape_is_one_character_and_the_file_can_be_written(tmp_path):
    lines = lines_of("example.qset.jsonl")
    lines[1]["answer"] = "pair \U0001f600"
    text = jsonl_text(lines).replace("\U0001f600", "\\ud83d\\ude00")
    loaded = parse(text)
    assert loaded.file is not None
    assert loaded.file.records[0]["answer"] == "pair \U0001f600"
    assert (
        write(tmp_path / "pair.jsonl", loaded.file)
        == hashlib.md5(loaded.file.to_bytes()).hexdigest()
    )


def test_a_file_that_validates_can_always_be_written(tmp_path):
    text = jsonl_text(lines_of("example.qset.jsonl"))
    loaded = parse(text)
    assert loaded.file is not None
    path = tmp_path / "again.jsonl"
    write(path, loaded.file)
    assert read_qset(path).records == loaded.file.records


def test_build_refuses_text_that_utf8_cannot_hold_with_the_line():
    lines = lines_of("example.qset.jsonl")
    lines[2]["answer"] = "J\ud800"
    with pytest.raises(FormatError) as raised:
        build(QSET, lines[0], lines[1:])
    (problem,) = raised.value.problems
    assert problem.line == 3
    assert "surrogates not allowed" in problem.message


def test_build_refuses_a_header_that_cannot_be_written():
    lines = lines_of("example.qset.jsonl")
    lines[0]["notes"] = "x\udc80"
    with pytest.raises(FormatError) as raised:
        build(QSET, lines[0], lines[1:])
    assert raised.value.problems[0].line == 1


def test_build_refuses_an_integer_of_more_than_4300_digits_like_parse_does():
    lines = lines_of("example.qset.jsonl")
    lines[1]["ext"] = {"big": 10**5000}
    with pytest.raises(FormatError) as raised:
        build(QSET, lines[0], lines[1:])
    assert raised.value.problems[0].line == 2
    assert parse(_qset_text_with('{"id": "X", "ext": {"big": ' + "9" * 5000 + "}}")).problems


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1, 2}])
def test_build_refuses_values_that_json_has_no_form_for(value):
    lines = lines_of("example.qset.jsonl")
    lines[1]["ext"] = {"odd": value}
    with pytest.raises(FormatError):
        build(QSET, lines[0], lines[1:])


def _ext(levels):
    """An ``ext`` value of ``levels`` nested objects."""
    value: dict = {}
    for _ in range(levels - 1):
        value = {"k": value}
    return value


def _nested(levels):
    """An item line whose record and ``ext`` nest ``levels`` containers deep in all."""
    item = lines_of("example.qset.jsonl")[1]
    item["ext"] = _ext(levels - 1)
    return json.dumps(item)


def test_nesting_is_refused_beyond_a_fixed_depth_whatever_the_stack():
    deepest = MAX_DEPTH
    assert parse(_qset_text_with(_nested(deepest))).file is not None
    (problem, *_) = parse(_qset_text_with(_nested(deepest + 1))).problems
    assert problem.line == 2
    assert f"nested more than {MAX_DEPTH} levels deep" in problem.message


def test_the_depth_limit_does_not_depend_on_the_recursion_limit():
    before = sys.getrecursionlimit()
    sys.setrecursionlimit(200)
    try:
        accepted = parse(_qset_text_with(_nested(MAX_DEPTH))).file is not None
        refused = parse(_qset_text_with(_nested(MAX_DEPTH + 1))).problems
    finally:
        sys.setrecursionlimit(before)
    assert accepted
    assert "levels deep" in refused[0].message


def test_nesting_beyond_what_the_json_reader_holds_gets_the_same_message():
    deep = _nested(2).replace('"ext": {}', '"ext": ' + "[" * 20000 + "]" * 20000)
    (problem, *_) = parse(_qset_text_with(deep)).problems
    assert f"nested more than {MAX_DEPTH} levels deep" in problem.message


def test_build_refuses_a_value_nested_too_deep_with_its_line():
    lines = lines_of("example.qset.jsonl")
    lines[1]["ext"] = _ext(MAX_DEPTH - 1)
    assert build(QSET, lines[0], lines[1:]).records[0]["ext"] == lines[1]["ext"]
    lines[1]["ext"] = _ext(MAX_DEPTH)
    with pytest.raises(FormatError, match="levels deep") as raised:
        build(QSET, lines[0], lines[1:])
    assert raised.value.problems[0].line == 2


def test_text_that_is_not_json_is_refused():
    (problem, *_) = parse("{not json}\n").problems
    assert problem.line == 1


def test_an_empty_file_and_a_file_without_a_known_header_are_refused():
    assert parse("").problems[0].message.startswith("the file is empty")
    assert parse("\n\n").problems[0].message.startswith("the file is empty")
    assert parse('{"format": "qset/2"}\n').problems[0].path == "/format"
    assert parse('{"id": "x"}\n').problems[0].path == "/format"
    assert parse("[1, 2]\n").problems[0].path == "/format"


def test_a_header_with_a_missing_field_is_refused():
    header = lines_of("example.qset.jsonl")[0]
    del header["created"]
    problems = parse(jsonl_text([header])).problems
    assert any("created" in p.message for p in problems)


def test_only_line_one_may_carry_a_format(qset_lines):
    again = dict(qset_lines[1], format=QSET)
    problems = parse(jsonl_text([qset_lines[0], again])).problems
    assert problems
    assert all(p.line == 2 for p in problems)


def test_a_line_that_is_not_an_object_is_refused(qset_lines):
    text = jsonl_text(qset_lines[:1]) + "[1]\n"
    assert parse(text).problems[0].line == 2


def test_a_set_may_not_repeat_an_id(qset_lines):
    text = jsonl_text([*qset_lines, qset_lines[1]])
    (problem,) = parse(text).problems
    assert problem.line == len(qset_lines) + 1
    assert problem.path == "/id"
    assert "already on line 2" in problem.message


def test_a_mirror_must_name_an_item_of_the_file(qset_lines):
    kept = [r for r in qset_lines if r.get("id") != "EX-002"]
    (problem,) = parse(jsonl_text(kept)).problems
    assert problem.path == "/mirror_of"


def test_a_run_may_not_repeat_an_id_and_repeat(tmp_path):
    lines = lines_of("example-bare.qa-run.jsonl")
    (problem,) = parse(jsonl_text([*lines, lines[1]])).problems
    assert problem.path == "/repeat"
    assert parse(jsonl_text([*lines, dict(lines[1], repeat=2)])).ok


def test_a_bare_answer_has_no_contexts():
    lines = lines_of("example-bare.qa-run.jsonl")
    lines[1]["contexts"] = [{"doc_id": "a1" * 32, "text": "x"}]
    (problem,) = parse(jsonl_text(lines)).problems
    assert problem.line == 2
    assert problem.path == "/contexts"


def test_a_score_record_must_use_the_runs_of_its_header():
    lines = lines_of("example.qa-score.jsonl")
    lines[1]["run_id"] = "not-listed"
    lines[5]["var_run"] = "also-not-listed"
    lines[0]["noise_floor"] = {"base_run": "x", "var_run": "example-bare-1"}
    problems = parse(jsonl_text(lines)).problems
    assert {(p.line, p.path) for p in problems} == {
        (1, "/noise_floor/base_run"),
        (2, "/run_id"),
        (6, "/var_run"),
    }


def test_a_score_file_may_not_repeat_a_record():
    lines = lines_of("example.qa-score.jsonl")
    for extra in (lines[1], lines[3], lines[5]):
        (problem,) = parse(jsonl_text([*lines, extra])).problems
        assert "already on line" in problem.message


def test_a_gzip_run_reads_the_same_and_its_md5_is_that_of_the_text(tmp_path):
    original = FIXTURES / "example-harness.qa-run.jsonl"
    packed = tmp_path / "run.qa-run.jsonl.gz"
    packed.write_bytes(gzip.compress(original.read_bytes()))
    loaded = load(packed, QA_RUN)
    assert loaded.file is not None
    assert loaded.file.md5 == read_run(original).md5


def test_gzip_is_found_by_its_first_bytes_not_by_the_name(tmp_path):
    original = FIXTURES / "example-bare.qa-run.jsonl"
    odd = tmp_path / "run.bin"
    odd.write_bytes(gzip.compress(original.read_bytes()))
    assert load(odd).ok


def test_writing_gzip_is_reproducible_and_reads_back(tmp_path):
    file = read_run(FIXTURES / "example-harness.qa-run.jsonl")
    first, second = tmp_path / "a.qa-run.jsonl.gz", tmp_path / "b" / "a.qa-run.jsonl.gz"
    assert write(first, file) == file.md5
    write(second, file)
    assert first.read_bytes() == second.read_bytes()
    assert read_run(first).md5 == file.md5
    assert gzip.decompress(first.read_bytes()) == file.to_bytes()


SEPARATORS = "a\u2028b\u2029c\u0085d"


def test_the_unicode_line_separators_are_text_inside_a_string(qset_lines):
    item = dict(qset_lines[1], notes=SEPARATORS)
    built = build(QSET, qset_lines[0], [item, qset_lines[2]])
    text = built.to_bytes().decode("utf-8")
    assert SEPARATORS in text
    assert len(text.split("\n")) == 4
    again = parse(text)
    assert again.file is not None
    assert again.file.records[0]["notes"] == SEPARATORS
    assert again.file.numbers == (2, 3)


def test_a_line_of_only_a_unicode_separator_is_not_blank(qset_lines):
    text = jsonl_text(qset_lines[:1]) + "\u2028\n" + jsonl_text(qset_lines[1:2])
    (problem,) = parse(text).problems
    assert problem.line == 2


def test_carriage_returns_before_the_line_feed_are_whitespace(qset_lines):
    text = "".join(json.dumps(r) + "\r\n" for r in qset_lines[:3])
    loaded = parse(text)
    assert loaded.ok
    assert loaded.file is not None
    assert loaded.file.numbers == (2, 3)


@pytest.mark.parametrize("number", ["1e400", "-1e400", "1E999"])
def test_a_number_out_of_range_is_refused_like_infinity(number):
    header = jsonl_text(lines_of("example.qset.jsonl")[:1])
    (problem, *_) = parse(header + '{"id": ' + number + "}\n").problems
    assert problem.line == 2
    assert "out of range" in problem.message


def test_large_but_finite_numbers_are_kept(qset_lines):
    item = dict(qset_lines[1], notes="x", ext={"big": 1.5e300, "tiny": 1e-320})
    assert build(QSET, qset_lines[0], [item]).records[0]["ext"] == {"big": 1.5e300, "tiny": 1e-320}


def test_writing_a_file_read_from_other_text_returns_the_md5_of_what_was_written(tmp_path):
    records = lines_of("example.qset.jsonl")
    records[1]["notes"] = "ñ θ"
    canonical = jsonl_text(records)
    other = "\r\n".join(json.dumps(r, separators=(",", ":")) for r in records) + "\r\n\r\n"
    assert "\\u00f1" in other
    file = parse(other).file
    assert file is not None
    assert file.md5 == hashlib.md5(other.encode("utf-8")).hexdigest()
    target = tmp_path / "set.qset.jsonl"
    written = write(target, file)
    assert target.read_bytes() == canonical.encode("utf-8")
    assert written == hashlib.md5(target.read_bytes()).hexdigest()
    assert written != file.md5
    assert read_qset(target).md5 == written


def test_writing_plain_text_returns_the_md5_of_the_bytes(tmp_path):
    file = read_qset(FIXTURES / "example.qset.jsonl")
    target = tmp_path / "new" / "set.qset.jsonl"
    md5 = write(target, file)
    assert md5 == hashlib.md5(target.read_bytes()).hexdigest()


def test_build_checks_every_line_and_the_rules_of_the_file(qset_lines):
    built = build(QSET, qset_lines[0], qset_lines[1:])
    assert built.md5 == read_qset(FIXTURES / "example.qset.jsonl").md5
    with pytest.raises(FormatError) as raised:
        build(QSET, qset_lines[0], [dict(qset_lines[1], form="nope")])
    assert raised.value.problems[0].line == 2
    with pytest.raises(FormatError):
        build(QSET, qset_lines[0], [qset_lines[1], qset_lines[1]])
    with pytest.raises(FormatError, match="expected qa-run/1"):
        build(QA_RUN, qset_lines[0], [])
    with pytest.raises(ValueError, match="JSON"):
        build(QSET, qset_lines[0], [dict(qset_lines[1], notes=float("nan"))])


def test_a_built_file_does_not_change_when_the_caller_changes_what_it_passed(qset_lines):
    header = qset_lines[0]
    item = dict(qset_lines[1], ext={"a": {"b": [1]}})
    built = build(QSET, header, [item])
    item["ext"]["a"]["b"].append(2)
    header["notes"] = "changed after"
    assert hashlib.md5(built.to_bytes()).hexdigest() == built.md5
    assert built.records[0]["ext"] == {"a": {"b": [1]}}
    assert built.header.get("notes") != "changed after"


def test_a_built_file_holds_the_values_of_its_text_not_the_callers_types(qset_lines):
    item = dict(qset_lines[1], ext={1: "one"})
    built = build(QSET, qset_lines[0], [item])
    assert built.records[0]["ext"] == {"1": "one"}
    assert parse(built.to_bytes().decode("utf-8")).file.records == built.records
    assert built.numbers == ()


def test_the_text_of_a_built_file_is_what_the_formats_write(qset_lines):
    built = build(QSET, qset_lines[0], qset_lines[1:])
    first = built.to_bytes().decode("utf-8").splitlines()[0]
    assert first == json.dumps(qset_lines[0], ensure_ascii=False)


def test_non_ascii_text_is_kept_as_is(qset_lines):
    item = dict(qset_lines[1], question="Que es el acoplamiento ñ? θ")
    built = build(QSET, qset_lines[0], [item])
    assert "ñ? θ" in built.to_bytes().decode("utf-8")


def test_a_file_that_cannot_be_read_is_a_problem_not_a_crash(tmp_path):
    missing = load(tmp_path / "missing.jsonl")
    assert missing.file is None
    assert "cannot read" in missing.problems[0].message
    assert "UTF-8" in load_bytes(b"\xff\xfe\x00").problems[0].message
    assert "UTF-8" in load_bytes(b"\x1f\x8b not gzip").problems[0].message
    with pytest.raises(FormatError):
        read(tmp_path / "missing.jsonl")


def test_a_format_error_lists_the_first_problems(qset_lines):
    bad = [dict(r, form="nope") for r in qset_lines[1:6]]
    with pytest.raises(FormatError) as raised:
        build(QSET, qset_lines[0], bad)
    assert "and 2 more" in str(raised.value)
    assert len(raised.value.problems) == 5
