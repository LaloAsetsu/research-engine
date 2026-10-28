"""The qa-bench command: validate, convert, and its exit statuses."""

import hashlib
import json
from pathlib import Path

import pytest
from conftest import FIXTURES, SOURCES, jsonl_text, lines_of

from qa_bench.cli import main
from qa_bench.convert import SHAPES
from qa_bench.formats import QA_RUN, read_qset, read_run

CREATED = "2026-01-01T00:00:00+00:00"
SET = str(FIXTURES / "example.qset.jsonl")


def test_validate_prints_one_line_per_good_file_and_exits_0(capsys):
    status = main(
        [
            "validate",
            SET,
            str(FIXTURES / "example-bare.qa-run.jsonl"),
            str(FIXTURES / "example.reviews.jsonl"),
            "--set",
            SET,
        ]
    )
    out = capsys.readouterr().out.splitlines()
    assert status == 0
    expected = hashlib.md5(Path(SET).read_bytes()).hexdigest()
    assert out[0] == f"ok {SET}: qset/1, 9 records, md5 {expected}"
    assert len(out) == 3


def test_validate_reports_file_line_pointer_and_exits_1(tmp_path, capsys):
    lines = lines_of("example.qset.jsonl")
    lines[2]["form"] = "nope"
    bad = tmp_path / "bad.qset.jsonl"
    bad.write_text(jsonl_text(lines), encoding="utf-8")
    status = main(["validate", str(bad), SET])
    out = capsys.readouterr().out.splitlines()
    assert status == 1
    assert out[0].startswith(f"{bad}:3: /form: ")
    assert out[-1].startswith("ok ")


def test_validate_shows_twenty_problems_unless_asked_for_all(tmp_path, capsys):
    lines = lines_of("example.qset.jsonl")
    bad = tmp_path / "bad.jsonl"
    bad.write_text(
        jsonl_text([lines[0], *[dict(lines[1], colour=i, id=f"X-{i}") for i in range(30)]])
    )
    main(["validate", str(bad)])
    assert "and 10 more" in capsys.readouterr().out
    main(["validate", str(bad), "--all"])
    out = capsys.readouterr().out
    assert "more" not in out
    assert len(out.splitlines()) == 30


def test_validate_checks_a_run_against_the_set(tmp_path, capsys):
    lines = lines_of("example-bare.qa-run.jsonl")
    lines[0]["set"]["md5"] = "9" * 32
    other = tmp_path / "other.qa-run.jsonl"
    other.write_text(jsonl_text(lines), encoding="utf-8")
    assert main(["validate", str(other)]) == 0
    capsys.readouterr()
    assert main(["validate", str(other), "--set", SET]) == 1
    assert ":1: /set/md5:" in capsys.readouterr().out


def test_validate_with_a_bad_set_or_a_missing_file_exits_1(tmp_path, capsys):
    assert main(["validate", SET, "--set", str(FIXTURES / "example-bare.qa-run.jsonl")]) == 1
    assert "expected qset/1" in capsys.readouterr().out
    assert main(["validate", str(tmp_path / "missing.jsonl")]) == 1
    assert "cannot read" in capsys.readouterr().out


def test_validate_can_require_a_format(capsys):
    assert main(["validate", SET, "--format", "qa-run/1"]) == 1
    assert "expected qa-run/1, the file is qset/1" in capsys.readouterr().out


def test_a_command_line_that_does_not_parse_exits_2(capsys):
    for argv in ([], ["convert"], ["convert", "nope", "in", "-o", "out"], ["validate"]):
        with pytest.raises(SystemExit) as raised:
            main(argv)
        assert raised.value.code == 2
    capsys.readouterr()


def test_version_names_the_package(capsys):
    with pytest.raises(SystemExit) as raised:
        main(["--version"])
    assert raised.value.code == 0
    assert capsys.readouterr().out.startswith("0.2.1")


@pytest.mark.parametrize(
    ("shape", "source", "extra"),
    [
        ("mathcirc-v1", "mathcirc.jsonl", ["--corpus", "c"]),
        ("circuit-tasks-v1", "circuit-tasks.jsonl", ["--corpus", "c"]),
        ("circuit-claims-v1", "circuit-claims.tsv", ["--corpus", "c"]),
        ("qubitqa-pilot", "qubitqa.jsonl", []),
        ("qasper", "qasper.jsonl", ["--corpus", "c"]),
    ],
)
def test_convert_and_back_gives_the_source_rows(tmp_path, capsys, shape, source, extra):
    out = tmp_path / "set.qset.jsonl"
    argv = ["convert", shape, str(SOURCES / source), "-o", str(out), "--set-name", "s/v1"]
    assert main([*argv, "--created", CREATED, *extra]) == 0
    assert capsys.readouterr().out.startswith(f"wrote {out}: qset/1")
    qset = read_qset(out)
    assert qset.header["source"]["file"] == source
    assert qset.header["created"] == CREATED
    back = tmp_path / ("back" + (SOURCES / source).suffix)
    assert main(["convert", shape, str(out), "-o", str(back), "--back"]) == 0
    read_rows = SHAPES[shape].read_rows
    assert read_rows(back.read_text(encoding="utf-8")) == read_rows(
        (SOURCES / source).read_text(encoding="utf-8")
    )


def test_convert_without_a_clock_argument_stamps_the_time_with_an_offset(tmp_path):
    out = tmp_path / "set.qset.jsonl"
    argv = ["convert", "qasper", str(SOURCES / "qasper.jsonl"), "-o", str(out)]
    assert main([*argv, "--set-name", "s/v1", "--corpus", "c"]) == 0
    assert read_qset(out).header["created"].endswith("+00:00")


def test_convert_says_what_is_missing(tmp_path, capsys):
    out = str(tmp_path / "o.jsonl")
    assert (
        main(["convert", "qasper", str(SOURCES / "qasper.jsonl"), "-o", out, "--corpus", "c"]) == 1
    )
    assert "--set-name" in capsys.readouterr().err
    assert (
        main(["convert", "qasper", str(SOURCES / "qasper.jsonl"), "-o", out, "--set-name", "s"])
        == 1
    )
    assert "do not name a corpus" in capsys.readouterr().err
    missing = str(tmp_path / "none.jsonl")
    assert main(["convert", "qasper", missing, "-o", out, "--set-name", "s", "--corpus", "c"]) == 1
    assert "cannot read" in capsys.readouterr().err


def test_convert_back_from_an_invalid_set_lists_the_problems(tmp_path, capsys):
    assert (
        main(
            [
                "convert",
                "qasper",
                str(FIXTURES / "example-bare.qa-run.jsonl"),
                "-o",
                str(tmp_path / "o"),
                "--back",
            ]
        )
        == 1
    )
    assert "expected qset/1" in capsys.readouterr().err


def header_file(tmp_path, **more):
    header = lines_of("example-bare.qa-run.jsonl")[0]
    header.update({"arm": {"name": "harness"}, "run_id": "from-capture-1", **more})
    path = tmp_path / "header.json"
    path.write_text(json.dumps(header, indent=2), encoding="utf-8")
    return str(path)


def test_convert_ask_capture_writes_a_valid_gzip_run(tmp_path, capsys):
    out = tmp_path / "run.qa-run.jsonl.gz"
    retrieval = str(SOURCES / "retrieval.jsonl")
    argv = ["convert", "ask-capture", str(SOURCES / "ask-capture.jsonl"), "-o", str(out)]
    assert main([*argv, "--header", header_file(tmp_path), "--retrieval", retrieval]) == 0
    assert "qa-run/1, 3 answers" in capsys.readouterr().out
    run = read_run(out)
    assert run.format == QA_RUN
    assert [r["id"] for r in run.records] == ["EX-001", "EX-002", "EX-003"]
    assert run.records[0]["ext"]["retrieval"]["hybrid_paper"] == 1
    assert run.records[1]["ext"]["retrieval"] is None


def test_convert_ask_capture_needs_a_header_and_refuses_a_bad_one(tmp_path, capsys):
    argv = ["convert", "ask-capture", str(SOURCES / "ask-capture.jsonl"), "-o", str(tmp_path / "r")]
    assert main(argv) == 1
    assert "--header" in capsys.readouterr().err
    broken = tmp_path / "broken.json"
    broken.write_text("{nope", encoding="utf-8")
    assert main([*argv, "--header", str(broken)]) == 1
    assert "not valid JSON" in capsys.readouterr().err
    assert main([*argv, "--header", header_file(tmp_path, run_id="bad id")]) == 1
    assert "/run_id" in capsys.readouterr().err


def test_convert_ask_capture_refuses_a_header_that_repeats_a_key(tmp_path, capsys):
    out = tmp_path / "r.qa-run.jsonl"
    twice = tmp_path / "twice.json"
    text = Path(header_file(tmp_path)).read_text(encoding="utf-8")
    twice.write_text(text.replace('"run_id"', '"run_id": "x", "run_id"', 1), encoding="utf-8")
    argv = ["convert", "ask-capture", str(SOURCES / "ask-capture.jsonl"), "-o", str(out)]
    assert main([*argv, "--header", str(twice)]) == 1
    assert "given twice" in capsys.readouterr().err
    assert not out.exists()


def test_convert_does_not_overwrite_an_existing_file_unless_forced(tmp_path, capsys):
    out = tmp_path / "set.qset.jsonl"
    out.write_text("keep me", encoding="utf-8")
    argv = ["convert", "qasper", str(SOURCES / "qasper.jsonl"), "-o", str(out)]
    argv += ["--set-name", "s/v1", "--corpus", "c", "--created", CREATED]
    assert main(argv) == 1
    assert "already exists" in capsys.readouterr().err
    assert out.read_text(encoding="utf-8") == "keep me"
    assert main([*argv, "--force"]) == 0
    assert read_qset(out).header["set"] == "s/v1"


def test_convert_back_cannot_overwrite_its_own_input(tmp_path, capsys):
    qset = tmp_path / "set.qset.jsonl"
    argv = ["convert", "qasper", str(SOURCES / "qasper.jsonl"), "-o", str(qset)]
    assert main([*argv, "--set-name", "s/v1", "--corpus", "c", "--created", CREATED]) == 0
    before = qset.read_bytes()
    capsys.readouterr()
    assert main(["convert", "qasper", str(qset), "-o", str(qset), "--back"]) == 1
    assert "already exists" in capsys.readouterr().err
    assert qset.read_bytes() == before


def test_convert_prints_the_md5_of_the_file_it_wrote(tmp_path, capsys):
    out = tmp_path / "set.qset.jsonl"
    argv = ["convert", "qasper", str(SOURCES / "qasper.jsonl"), "-o", str(out)]
    assert main([*argv, "--set-name", "s/v1", "--corpus", "c", "--created", CREATED]) == 0
    assert capsys.readouterr().out.endswith(f"md5 {hashlib.md5(out.read_bytes()).hexdigest()}\n")


@pytest.mark.parametrize("back", [False, True])
def test_convert_to_a_path_that_cannot_be_written_is_a_refusal_not_a_traceback(
    tmp_path, capsys, back
):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    qset = tmp_path / "set.qset.jsonl"
    source = str(SOURCES / "qasper.jsonl")
    made = ["convert", "qasper", source, "-o", str(qset), "--set-name", "s/v1", "--corpus", "c"]
    assert main([*made, "--created", CREATED]) == 0
    if back:
        argv = ["convert", "qasper", str(qset), "-o", str(blocker / "out"), "--back"]
    else:
        argv = [*made[:4], str(blocker / "out"), *made[5:], "--created", CREATED]
    capsys.readouterr()
    assert main(argv) == 1
    err = capsys.readouterr().err
    assert err.startswith("qa-bench: ")
    assert len(err.strip().splitlines()) == 1
