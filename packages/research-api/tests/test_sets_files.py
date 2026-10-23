"""The files of the question sets: private, replaced all at once, reviews only appended."""

from __future__ import annotations

import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from research_api.adapters.files import PrivacyError, SetFileStore
from research_api.domain.sets import SetRef

REF = SetRef("demo", "v1")


@pytest.fixture
def store(tmp_path: Path) -> SetFileStore:
    root = tmp_path / "data" / "sets"
    return SetFileStore(root)


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_a_missing_file_is_none(store: SetFileStore) -> None:
    assert store.read(REF) is None
    assert store.read_reviews(REF) is None


def test_write_then_read(store: SetFileStore) -> None:
    store.write(REF, b"one\n")
    assert store.read(REF) == b"one\n"
    assert (store.root / "demo" / "v1.qset.jsonl").read_bytes() == b"one\n"


def test_folders_and_files_are_private(store: SetFileStore) -> None:
    store.write(REF, b"x")
    store.append_reviews(REF, b"r\n")
    store.keep_copy(REF, "prefreeze", b"y")
    assert mode(store.root) == 0o700
    assert mode(store.root / "demo") == 0o700
    for name in ("v1.qset.jsonl", "v1.reviews.jsonl", "v1.prefreeze.qset.jsonl"):
        assert mode(store.root / "demo" / name) == 0o600


def test_a_replaced_file_has_exactly_the_new_bytes_and_leaves_nothing_beside_it(
    store: SetFileStore,
) -> None:
    store.write(REF, b"a" * 5000)
    store.write(REF, b"b")
    assert store.read(REF) == b"b"
    assert sorted(p.name for p in (store.root / "demo").iterdir()) == ["v1.qset.jsonl"]


def test_a_failed_write_keeps_the_old_file_and_cleans_up(
    store: SetFileStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.write(REF, b"old")

    def boom(self: Path, target: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        store.write(REF, b"new")
    monkeypatch.undo()
    assert store.read(REF) == b"old"
    assert sorted(p.name for p in (store.root / "demo").iterdir()) == ["v1.qset.jsonl"]


def test_versions_do_not_share_a_file(store: SetFileStore) -> None:
    store.write(SetRef("demo", "v1"), b"1")
    store.write(SetRef("demo", "v2"), b"2")
    store.write(SetRef("other", "v1"), b"3")
    assert [
        store.read(SetRef(n, v)) for n, v in (("demo", "v1"), ("demo", "v2"), ("other", "v1"))
    ] == [
        b"1",
        b"2",
        b"3",
    ]


@pytest.mark.parametrize(
    "ref",
    [
        SetRef("..", "v1"),
        SetRef("a/b", "v1"),
        SetRef("../x", "v1"),
        SetRef("demo", "../v1"),
        SetRef("demo", "v1/../../x"),
        SetRef("", "v1"),
        SetRef("demo", ""),
        SetRef(".hidden", "v1"),
    ],
)
def test_a_reference_that_is_a_path_is_refused_everywhere(store: SetFileStore, ref: SetRef) -> None:
    for call in (
        lambda: store.read(ref),
        lambda: store.write(ref, b"x"),
        lambda: store.read_reviews(ref),
        lambda: store.append_reviews(ref, b"x"),
        lambda: store.write_reviews(ref, b"x"),
        lambda: store.keep_copy(ref, "prefreeze", b"x"),
    ):
        with pytest.raises(ValueError, match="not a set reference"):
            call()
    assert not store.root.exists() or list(store.root.iterdir()) == []


@pytest.mark.parametrize("label", ["", "a/b", "..", "pre freeze", "x.y"])
def test_a_copy_label_is_letters_and_digits_only(store: SetFileStore, label: str) -> None:
    with pytest.raises(ValueError, match="label"):
        store.keep_copy(REF, label, b"x")


def test_reviews_are_appended_never_rewritten(store: SetFileStore) -> None:
    store.write_reviews(REF, b"header\n")
    store.append_reviews(REF, b"one\n")
    store.append_reviews(REF, b"two\n")
    assert store.read_reviews(REF) == b"header\none\ntwo\n"


def test_appending_to_no_file_makes_it(store: SetFileStore) -> None:
    store.append_reviews(REF, b"one\n")
    assert store.read_reviews(REF) == b"one\n"


def test_parallel_appends_keep_every_line_whole(store: SetFileStore) -> None:
    line = b"x" * 200 + b"\n"
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda _: store.append_reviews(REF, line), range(100)))
    data = store.read_reviews(REF)
    assert data == line * 100


def test_a_folder_open_to_others_is_refused_not_fixed(store: SetFileStore) -> None:
    store.write(REF, b"x")
    os.chmod(store.root / "demo", 0o755)
    with pytest.raises(PrivacyError, match="chmod 700"):
        store.write(REF, b"y")
    assert store.read(REF) == b"x"
    assert mode(store.root / "demo") == 0o755


def test_keep_copy_does_not_touch_the_version(store: SetFileStore) -> None:
    store.write(REF, b"draft")
    store.keep_copy(REF, "prefreeze", b"draft")
    store.write(REF, b"frozen")
    assert store.read(REF) == b"frozen"
    assert (store.root / "demo" / "v1.prefreeze.qset.jsonl").read_bytes() == b"draft"
