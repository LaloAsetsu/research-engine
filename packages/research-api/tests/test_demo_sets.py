"""The synthetic sets of fake mode: what they hold, and that they repeat exactly."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from qa_bench.formats import check_against_set, read_qset, read_reviews

from research_api.adapters.bench import QaBenchSetFormats
from research_api.adapters.fake import FixedClock
from research_api.adapters.files import SetFileStore
from research_api.adapters.persistence import Database
from research_api.application.sets import build_set_services
from research_api.bootstrap import Container, Settings, build_container
from research_api.bootstrap.demo_sets import circuit_items, paper_items, seed_demo_sets
from research_api.domain.sets import SetRef


def fake() -> Container:
    return build_container(Settings.defaults(), fake=True, cheap_hashing=True)


@pytest.fixture
def demo() -> Container:
    container = fake()
    yield container
    container.close()


def test_the_demo_has_a_frozen_version_a_draft_with_every_review_state_and_circuit_questions(
    demo: Container,
) -> None:
    catalog = {e.name: e for e in demo.sets.manage.list_sets()}
    assert set(catalog) == {"demo-papers", "demo-circuits"}
    v1, v2 = (v.version for v in catalog["demo-papers"].versions)
    assert (v1.status, v1.owner, v1.items) == ("frozen", "ana", 6)
    assert (v2.status, v2.owner, v2.items) == ("draft", "beto", 7)
    (circuits,) = (v.version for v in catalog["demo-circuits"].versions)
    assert (circuits.status, circuits.items) == ("draft", 3)
    progress = catalog["demo-papers"].versions[1].progress
    assert progress is not None
    assert (progress.none, progress.needs_second, progress.accepted) == (3, 1, 1)
    assert (progress.edited, progress.rejected) == (1, 1)


def test_the_frozen_version_lost_the_item_the_reviewers_rejected(demo: Container) -> None:
    ids = [i.id for i in demo.sets.manage.get_version(SetRef("demo-papers", "v1"), limit=200).items]
    assert "DP-006" not in ids
    assert "DP-001" in ids


def test_the_demo_covers_the_forms_a_screen_has_to_show() -> None:
    forms = {i["form"] for i in paper_items()} | {i["form"] for i in circuit_items()}
    assert {
        "single", "multi_chain", "unanswerable", "neardup", "circuit_verify", "circuit_create", "circuit_claim",
    } <= forms  # fmt: skip
    langs = {i["lang"] for i in paper_items()}
    assert langs == {"en", "es"}
    assert any(i.get("mirror_of") for i in paper_items())


def test_the_demo_is_made_up(demo: Container) -> None:
    text = " ".join(str(i) for i in [*paper_items(), *circuit_items()]).lower()
    assert "synthetic" in text
    for real in ("arxiv.org", "doi", "@", "http"):
        assert real not in text


def test_the_demo_repeats_exactly_between_runs() -> None:
    first, second = fake(), fake()
    try:
        one = [
            (v.version.ref, v.version.md5)
            for e in first.sets.manage.list_sets()
            for v in e.versions
        ]
        two = [
            (v.version.ref, v.version.md5)
            for e in second.sets.manage.list_sets()
            for v in e.versions
        ]
        assert one == two
    finally:
        first.close()
        second.close()


def test_the_demo_files_are_valid_and_its_reviews_belong_to_them(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "t.sqlite3")
    database.upgrade()
    files = SetFileStore(tmp_path / "data" / "sets")
    services = build_set_services(
        catalog=database.sets, files=files, formats=QaBenchSetFormats(), clock=FixedClock()
    )
    seed_demo_sets(services)
    root = files.root

    def reviews_problems(name: str, version: str) -> list[str]:
        qset = read_qset(root / name / f"{version}.qset.jsonl")
        reviews = read_reviews(root / name / f"{version}.reviews.jsonl")
        return [p.message for p in check_against_set(reviews, qset)]

    assert reviews_problems("demo-papers", "v2") == []
    # the frozen version no longer has the rejected item its reviews are about
    assert reviews_problems("demo-papers", "v1") == ["'DP-006' is not an item of the set"]
    assert not (root / "demo-circuits" / "v1.reviews.jsonl").exists()
    for name, version in (("demo-papers", "v1"), ("demo-papers", "v2"), ("demo-circuits", "v1")):
        assert read_qset(root / name / f"{version}.qset.jsonl")
    assert (root / "demo-papers" / "v1.prefreeze.qset.jsonl").exists()
    database.close()


def test_closing_the_container_removes_the_demo_files(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[str] = []
    real = tempfile.mkdtemp

    def record(*args: object, **kwargs: object) -> str:
        path = real(*args, **kwargs)  # type: ignore[arg-type]
        made.append(path)
        return path

    monkeypatch.setattr(tempfile, "mkdtemp", record)
    container = fake()
    (folder,) = made
    assert (Path(folder) / "data" / "sets" / "demo-papers" / "v1.qset.jsonl").exists()
    container.close()
    assert not Path(folder).exists()
