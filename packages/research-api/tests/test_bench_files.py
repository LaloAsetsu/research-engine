"""The files of runs, scores and logs, and the readers of qa-run/1 and qa-score/1 files."""

from __future__ import annotations

import gzip
import json
import os
import stat
from pathlib import Path

import pytest
from helpers import past_the_tick
from qa_bench.formats import QA_RUN, check_line

from research_api.adapters.bench import QaBenchRunFormats, QaBenchScorer
from research_api.adapters.files import BenchFileStore, PrivacyError
from research_api.domain.errors import ConflictError, CorruptStoreError, InvalidInputError
from research_api.domain.jobs import ScoreOptions

RUN = "bare-demo-v1-abcd1234"
JOB = "0" * 26
HEADER = {
    "format": "qa-run/1",
    "run_id": RUN,
    "created": "2026-10-23T01:00:00+00:00",
    "set": {"name": "demo/v1", "md5": "a" * 32},
    "arm": {"name": "bare"},
    "model": {"id": "m"},
    "code": {"runner": "test"},
    "corpus": {"id": "none"},
}


def item(item_id: str, repeat: int = 1, **fields: object) -> dict[str, object]:
    return {"id": item_id, "repeat": repeat, "answer": f"a {item_id}", "contexts": [], **fields}


@pytest.fixture
def store(tmp_path: Path) -> BenchFileStore:
    return BenchFileStore(tmp_path / "data")


formats = QaBenchRunFormats()


# --- the store ------------------------------------------------------------------------------


def test_a_run_is_created_appended_to_and_read(store: BenchFileStore) -> None:
    store.create_run(RUN, formats.header_line(HEADER))
    store.append_run(RUN, formats.item_line(item("q1")))
    store.append_run(RUN, formats.item_line(item("q2")))
    data = store.read_run(RUN)
    assert data is not None and data.count(b"\n") == 3
    assert store.run_ids() == [RUN]
    assert store.read_run("nothing-here") is None and store.run_stamp("nothing-here") is None


def test_folders_and_files_are_private(store: BenchFileStore) -> None:
    store.create_run(RUN, formats.header_line(HEADER))
    store.append_log(JOB, "a line\n")
    store.write_score(JOB, b"{}\n")
    root = store.root
    for folder in (root, root / "runs", root / "scores", root / "jobs"):
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    for path in (
        root / "runs" / f"{RUN}.qa-run.jsonl",
        root / "scores" / f"{JOB}.qa-score.jsonl",
        root / "jobs" / f"{JOB}.log",
    ):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_folder_open_to_others_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    (root / "runs").mkdir()
    (root / "runs").chmod(0o755)
    with pytest.raises(PrivacyError):
        BenchFileStore(root).create_run(RUN, b"x\n")


def test_a_run_id_taken_is_a_conflict(store: BenchFileStore) -> None:
    store.create_run(RUN, b"x\n")
    with pytest.raises(ConflictError):
        store.create_run(RUN, b"y\n")
    assert store.read_run(RUN) == b"x\n"


def test_a_gzip_run_is_found_and_not_appended_to(store: BenchFileStore) -> None:
    folder = store.root / "runs"
    folder.mkdir(parents=True, mode=0o700)
    (folder / f"{RUN}.qa-run.jsonl.gz").write_bytes(gzip.compress(formats.header_line(HEADER)))
    assert store.run_ids() == [RUN]
    assert store.run_stamp(RUN) is not None
    with pytest.raises(ConflictError):
        store.create_run(RUN, b"x\n")
    with pytest.raises(FileNotFoundError):
        store.append_run(RUN, b"x\n")
    assert formats.read(store.read_run(RUN) or b"").header["run_id"] == RUN


def test_the_stamp_changes_when_the_file_grows(store: BenchFileStore) -> None:
    store.create_run(RUN, formats.header_line(HEADER))
    before = store.run_stamp(RUN)
    store.append_run(RUN, formats.item_line(item("q1")))
    assert store.run_stamp(RUN) != before


def test_the_stamp_changes_when_the_file_is_rewritten_with_its_time_and_size_kept(
    store: BenchFileStore,
) -> None:
    store.create_run(RUN, b"old\n")
    path = store.root / "runs" / f"{RUN}.qa-run.jsonl"
    before, seen = store.run_stamp(RUN), path.stat()
    past_the_tick(path, seen.st_ctime_ns)
    path.write_bytes(b"new\n")
    os.utime(path, ns=(seen.st_atime_ns, seen.st_mtime_ns))
    after = store.run_stamp(RUN)
    assert after is not None and before is not None
    assert after[:2] == before[:2] and after != before


def test_the_stamp_changes_when_a_file_of_the_same_size_and_time_replaces_it(
    store: BenchFileStore,
) -> None:
    store.create_run(RUN, b"old\n")
    path = store.root / "runs" / f"{RUN}.qa-run.jsonl"
    before, seen = store.run_stamp(RUN), path.stat()
    copy = path.with_name("copy")
    copy.write_bytes(b"new\n")
    os.utime(copy, ns=(seen.st_atime_ns, seen.st_mtime_ns))  # what ``cp -p`` does
    copy.replace(path)
    after = store.run_stamp(RUN)
    assert after is not None and before is not None
    assert after[:2] == before[:2] and after != before


def test_the_stamp_is_the_same_for_a_file_that_was_not_touched(store: BenchFileStore) -> None:
    store.create_run(RUN, b"old\n")
    assert store.run_stamp(RUN) == store.run_stamp(RUN)


def test_replacing_a_run_is_all_at_once(store: BenchFileStore) -> None:
    store.create_run(RUN, b"old\n")
    store.replace_run(RUN, b"new\n")
    assert store.read_run(RUN) == b"new\n"
    assert [p.name for p in (store.root / "runs").iterdir()] == [f"{RUN}.qa-run.jsonl"]


@pytest.mark.parametrize("bad", ["../x", "a/b", "", ".x", "x" * 200])
def test_an_id_that_is_not_a_run_id_never_becomes_a_path(store: BenchFileStore, bad: str) -> None:
    with pytest.raises(ValueError, match="not a run id"):
        store.read_run(bad)
    with pytest.raises(ValueError, match="not a run id"):
        store.create_run(bad, b"x")


def test_odd_names_in_the_runs_folder_are_not_runs(store: BenchFileStore) -> None:
    store.create_run(RUN, b"x\n")
    folder = store.root / "runs"
    (folder / ".tmp-abc.part").write_bytes(b"x")
    (folder / "notes.txt").write_bytes(b"x")
    (folder / "-bad.qa-run.jsonl").write_bytes(b"x")
    assert store.run_ids() == [RUN]


def test_scores_are_written_whole_and_listed(store: BenchFileStore) -> None:
    assert store.score_ids() == [] and store.read_score(JOB) is None
    store.write_score(JOB, b"one\n")
    assert store.read_score(JOB) == b"one\n" and store.score_ids() == [JOB]
    assert store.read_score("../etc/passwd") is None
    with pytest.raises(ValueError, match="not a score id"):
        store.write_score("nope", b"x")


def test_a_log_is_read_from_an_offset_up_to_a_line_end(store: BenchFileStore) -> None:
    assert store.read_log(JOB, 0, 100) is None
    for n in range(5):
        store.append_log(JOB, f"line {n}\n")
    whole = store.read_log(JOB, 0, 10_000)
    assert whole is not None and whole.text.count("\n") == 5 and whole.next_offset == whole.size
    part = store.read_log(JOB, 0, 20)  # the limit lands in the middle of a line
    assert part is not None and part.text == "line 0\nline 1\n" and part.next_offset == 14
    rest = store.read_log(JOB, part.next_offset, 10_000)
    assert rest is not None and part.text + rest.text == whole.text
    past = store.read_log(JOB, 10_000, 100)
    assert past is not None and past.text == "" and past.next_offset == whole.size
    again = store.read_log(JOB, -5, 7)
    assert again is not None and again.text.startswith("line 0")
    assert store.read_log("../x", 0, 10) is None


def test_a_log_keeps_whole_characters_and_a_line_longer_than_the_limit_is_cut_anywhere(
    store: BenchFileStore,
) -> None:
    store.append_log(JOB, "acentos: áé\n")
    chunk = store.read_log(JOB, 0, 10)
    assert chunk is not None and chunk.text  # decoded, even when the limit cuts a character
    assert os.fstat(os.open(store.root / "jobs" / f"{JOB}.log", os.O_RDONLY)).st_size == chunk.size


# --- the run format -------------------------------------------------------------------------


def test_the_lines_the_service_writes_follow_qa_run() -> None:
    line = formats.item_line(item("q1"))
    assert line.endswith(b"\n") and line.count(b"\n") == 1
    assert check_line(QA_RUN, json.loads(line), header=False) == []


def test_a_line_that_breaks_the_format_is_a_bug_and_is_refused() -> None:
    with pytest.raises(ValueError, match="not qa-run/1"):
        formats.item_line({"id": "q1", "repeat": 0, "answer": "x", "contexts": []})
    with pytest.raises(ValueError, match="not qa-run/1"):
        formats.header_line({"format": "qa-run/1"})


def test_a_run_file_is_read_up_to_its_last_whole_line() -> None:
    whole = (
        formats.header_line(HEADER) + formats.item_line(item("q1")) + formats.item_line(item("q2"))
    )
    cut = whole + formats.item_line(item("q3"))[:-30]
    run = formats.read(cut)
    assert [i["id"] for i in run.items] == ["q1", "q2"]
    assert run.complete_bytes == len(whole)
    assert formats.read(whole).complete_bytes == len(whole)


def test_a_gzip_run_is_read_whole() -> None:
    data = formats.header_line(HEADER) + formats.item_line(item("q1"))
    run = formats.read(gzip.compress(data))
    assert [i["id"] for i in run.items] == ["q1"] and run.header["run_id"] == RUN


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\n",
        b"not json\n",
        b'{"format": "qa-score/1"}\n',
        b"[1, 2]\n",
        b'{"format": "qa-run/1", "a": 1, "a": 2}\n',
        b'{"format": "qa-run/1"}\n[1]\n',
        b"\x1f\x8bnot really gzip",
    ],
)
def test_a_file_that_is_not_a_run_is_corrupt(data: bytes) -> None:
    with pytest.raises(CorruptStoreError):
        formats.read(data)


def test_a_gzip_that_unzips_to_a_mountain_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from research_api.adapters.bench import runs  # noqa: PLC0415

    monkeypatch.setattr(runs, "MAX_RUN_BYTES", 50)
    with pytest.raises(CorruptStoreError, match="larger"):
        formats.read(gzip.compress(formats.header_line(HEADER) * 3))


# --- the scorer -----------------------------------------------------------------------------


def test_a_score_file_is_read_without_its_schema_checked_again() -> None:
    data = b'{"format": "qa-score/1", "created": "x"}\n{"record": "group", "run_id": "r"}\n'
    scored = QaBenchScorer().read(data)
    assert scored.header["created"] == "x" and len(scored.records) == 1


@pytest.mark.parametrize("data", [b"", b"\xff\xfe", b"{}\n", b"x\n", b'{"format": "qa-run/1"}\n'])
def test_a_score_file_that_does_not_read_is_corrupt(data: bytes) -> None:
    with pytest.raises(CorruptStoreError):
        QaBenchScorer().read(data)


def test_the_scorer_refuses_an_engine_it_does_not_have_and_a_set_that_is_not_one() -> None:
    scorer = QaBenchScorer()
    with pytest.raises(InvalidInputError) as caught:
        scorer.score(b"", [], ScoreOptions(engine="dice"), "2026-01-01T00:00:00+00:00")
    assert caught.value.errors[0].path == "/engine"
    with pytest.raises(InvalidInputError, match="question set"):
        scorer.score(b"not a set\n", [], ScoreOptions(), "2026-01-01T00:00:00+00:00")


def test_the_ids_of_a_write_are_checked_too(store: BenchFileStore) -> None:
    with pytest.raises(ValueError, match="not a run id"):
        store.replace_run("../x", b"x")
    with pytest.raises(ValueError, match="not a job id"):
        store.append_log("../x", "x\n")


def test_a_file_that_goes_away_between_the_check_and_the_read_is_just_missing(
    store: BenchFileStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.create_run(RUN, b"x\n")
    ghost = store.root / "runs" / "ghost.qa-run.jsonl"
    monkeypatch.setattr(store, "_run_path", lambda run_id: ghost)
    assert store.read_run(RUN) is None and store.run_stamp(RUN) is None


def test_two_creators_of_one_run_cannot_both_win(
    store: BenchFileStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.create_run(RUN, b"x\n")
    monkeypatch.setattr(store, "_run_path", lambda run_id: None)  # the check missed it
    with pytest.raises(ConflictError):
        store.create_run(RUN, b"y\n")
    assert store.read_run(RUN) is None  # the file is there, but the patched check cannot see it


def test_the_scorer_says_why_a_run_does_not_fit_the_set() -> None:
    from research_api.bootstrap.demo_sets import paper_items  # noqa: PLC0415

    items = paper_items()
    set_data = "".join(
        json.dumps(line) + "\n"
        for line in [
            {"format": "qset/1", "set": "demo-papers/v1", "created": "2026-01-01T00:00:00+00:00"},
            *items,
        ]
    ).encode()
    other = {**HEADER, "set": {"name": "demo-papers/v1", "md5": "b" * 32}}
    run_data = formats.header_line(other) + formats.item_line(item("DP-001"))
    with pytest.raises(InvalidInputError, match="does not fit the set"):
        QaBenchScorer().score(
            set_data, [("r.qa-run.jsonl", run_data)], ScoreOptions(), "2026-01-01T00:00:00+00:00"
        )
