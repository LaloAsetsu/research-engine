"""``qa-bench score``: files in, a qa-score/1 file out, and its exit statuses."""

import os
import subprocess
import sys

import pytest
from scoredata import CREATED, answer, make_qset, make_run, num_key, question, text_key

from qa_bench.cli import main
from qa_bench.formats import QA_SCORE, read_score, write

RIGHT = "The result is concept."
WRONG = "No relevant result was found."


@pytest.fixture
def files(tmp_path):
    keys = [text_key("concept")]
    qset = make_qset([question(f"S-{i}", keys, category="ab"[i % 2]) for i in range(1, 7)])
    paths = {"qset": tmp_path / "set.qset.jsonl"}
    write(paths["qset"], qset)
    plans = {"r1": [RIGHT, WRONG, RIGHT, WRONG, RIGHT, WRONG], "r2": [RIGHT] * 6}
    for run_id, texts in plans.items():
        run = make_run(run_id, qset, [answer(f"S-{i}", t) for i, t in enumerate(texts, 1)])
        paths[run_id] = tmp_path / f"{run_id}.qa-run.jsonl.gz"
        write(paths[run_id], run)
    paths["out"] = tmp_path / "out.qa-score.jsonl"
    return paths


def args(files, *more, runs=("r1", "r2")):
    run_args = [x for r in runs for x in ("--run", str(files[r]))]
    return ["score", "--set", str(files["qset"]), *run_args, "-o", str(files["out"]), *more]


def test_score_writes_a_valid_file_and_says_what_it_wrote(files, capsys):
    status = main(args(files, "--pair", "r1:r2", "--created", CREATED))
    assert status == 0
    out = capsys.readouterr().out
    file = read_score(files["out"])
    assert file.format == QA_SCORE
    assert f"wrote {files['out']}: qa-score/1, {len(file.records)} records, md5 {file.md5}" in out
    assert [r["file"] for r in file.header["runs"]] == [files["r1"].name, files["r2"].name]
    (row,) = [
        r
        for r in file.records
        if r["record"] == "compare" and r["group"] == "all" and r["metric"] == "all_keys"
    ]
    assert (row["base"], row["var"], row["diff"], row["up"], row["down"]) == (0.5, 1.0, 0.5, 3, 0)


def test_the_output_validates_against_the_set(files, capsys):
    main(args(files, "--created", CREATED))
    capsys.readouterr()
    assert main(["validate", str(files["out"]), "--set", str(files["qset"])]) == 0
    assert "ok" in capsys.readouterr().out


def test_the_same_command_twice_gives_the_same_bytes(files, tmp_path):
    main(args(files, "--pair", "r1:r2", "--created", CREATED))
    first = files["out"].read_bytes()
    files["out"].unlink()
    main(args(files, "--pair", "r1:r2", "--created", CREATED))
    assert files["out"].read_bytes() == first


def test_without_a_clock_argument_the_time_has_an_offset(files):
    main(args(files))
    assert read_score(files["out"]).header["created"].endswith("+00:00")


def test_an_output_that_exists_is_not_overwritten_unless_forced(files, capsys):
    files["out"].write_text("keep\n")
    assert main(args(files)) == 1
    assert "already exists" in capsys.readouterr().err
    assert files["out"].read_text() == "keep\n"
    assert main(args(files, "--force")) == 0
    assert read_score(files["out"]).format == QA_SCORE


def test_the_options_reach_the_scorer(files):
    status = main(
        args(
            files,
            "--pair",
            "r1:r2",
            "--noise",
            "r1:r2",
            "--group-by",
            "lang",
            "--group",
            "category=a|b",
            "--bootstrap",
            "numpy_pcg64",
            "--no-group-ci",
            "--number-words",
            "--created",
            CREATED,
        )
    )
    assert status == 0
    file = read_score(files["out"])
    assert file.header["bootstrap"]["engine"] == "numpy_pcg64"
    assert file.header["scorer"]["mathnorm"] == "v2+words"
    assert file.header["noise_floor"] == {"base_run": "r1", "var_run": "r2"}
    assert {r["group"] for r in file.records if r["record"] == "group"} == {
        "all",
        "lang=en",
        "category=a|b",
    }
    assert all("ci" not in r for r in file.records if r["record"] == "group")


def test_number_words_are_off_unless_asked(tmp_path, capsys):
    qset = make_qset([question("S-1", [num_key(3)])])
    run = make_run("r1", qset, [answer("S-1", "it needs three gates")])
    write(tmp_path / "s.jsonl", qset)
    write(tmp_path / "r.jsonl", run)
    base = ["score", "--set", str(tmp_path / "s.jsonl"), "--run", str(tmp_path / "r.jsonl")]
    assert main([*base, "-o", str(tmp_path / "a.jsonl"), "--created", CREATED]) == 0
    assert (
        main([*base, "-o", str(tmp_path / "b.jsonl"), "--created", CREATED, "--number-words"]) == 0
    )

    def all_keys(name):
        file = read_score(tmp_path / name)
        return next(
            r["mean"] for r in file.records if r["record"] == "group" and r["metric"] == "all_keys"
        )

    assert (all_keys("a.jsonl"), all_keys("b.jsonl")) == (0.0, 1.0)


def test_warnings_go_to_stderr_and_the_file_is_still_written(tmp_path, capsys):
    qset = make_qset(
        [question("S-1", [text_key("concept")]), question("S-2", [text_key("concept")])]
    )
    write(tmp_path / "s.jsonl", qset)
    write(tmp_path / "r.jsonl", make_run("r1", qset, [answer("S-1", RIGHT)]))
    status = main(
        [
            "score",
            "--set",
            str(tmp_path / "s.jsonl"),
            "--run",
            str(tmp_path / "r.jsonl"),
            "-o",
            str(tmp_path / "o.jsonl"),
        ]
    )
    assert status == 0
    assert "warning: run r1: 1 of 2 questions have no answer" in capsys.readouterr().err


def test_a_run_for_another_set_exits_1_with_the_reason(files, tmp_path, capsys):
    other = make_qset([question("S-1", [text_key("concept")])], name="other/v1")
    write(files["r2"], make_run("r2", other, [answer("S-1", RIGHT)]))
    assert main(args(files)) == 1
    assert "does not fit the set" in capsys.readouterr().err
    assert not files["out"].exists()


def test_a_bad_file_exits_1_and_lists_the_problems(files, capsys):
    files["r1"].write_text("not json\n")
    assert main(args(files)) == 1
    assert "qa-bench:" in capsys.readouterr().err


def test_a_missing_file_exits_1(files, capsys):
    files["r1"].unlink()
    assert main(args(files)) == 1
    assert "qa-bench:" in capsys.readouterr().err


def test_a_pair_of_an_unknown_run_exits_1(files, capsys):
    assert main(args(files, "--pair", "r1:zz")) == 1
    assert "'zz'" in capsys.readouterr().err


@pytest.mark.parametrize("pair", ["r1", "r1:", ":r2", "a:b:c"])
def test_a_pair_that_does_not_parse_exits_2(files, capsys, pair):
    with pytest.raises(SystemExit) as stop:
        main(args(files, "--pair", pair))
    assert stop.value.code == 2
    assert "BASE_RUN_ID:VAR_RUN_ID" in capsys.readouterr().err


def test_an_unknown_engine_exits_2(files, capsys):
    with pytest.raises(SystemExit) as stop:
        main(args(files, "--bootstrap", "mersenne"))
    assert stop.value.code == 2


def test_the_bytes_do_not_depend_on_the_hash_seed_of_the_interpreter(files, tmp_path):
    """Sets and dicts are iterated in many places; the output must not follow their order."""
    outputs = []
    for seed in ("1", "12345"):
        out = tmp_path / f"seed{seed}.jsonl"
        command = [
            sys.executable, "-m", "qa_bench.cli", "score", "--set", str(files["qset"]),
            "--run", str(files["r1"]), "--run", str(files["r2"]), "--pair", "r1:r2",
            "-o", str(out), "--created", CREATED,
        ]  # fmt: skip
        subprocess.run(
            command, check=True, capture_output=True, env={**os.environ, "PYTHONHASHSEED": seed}
        )
        outputs.append(out.read_bytes())
    assert outputs[0] == outputs[1]


def test_an_output_that_cannot_be_written_is_a_refusal_not_a_traceback(files, tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    files["out"] = blocker / "out.qa-score.jsonl"
    assert main(args(files)) == 1
    err = capsys.readouterr().err
    assert err.startswith("qa-bench: ")
    assert str(blocker) in err
    assert len(err.strip().splitlines()) == 1
    assert "Traceback" not in err


def test_group_fields_that_give_no_group_are_warned_about(files, capsys):
    assert main(args(files, "--group-by", "lang,nosuch,tag:Bad-Name,tag:category")) == 0
    err = capsys.readouterr().err
    assert "warning: group field 'nosuch' gives no group: no question has it" in err
    assert "warning: group field 'tag:Bad-Name' gives no group: its name must be" in err
    assert "tag:category" not in err
    assert "'lang'" not in err
