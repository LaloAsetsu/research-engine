"""A paired comparison of two runs of 101 questions, the shape of a real benchmark table.

The set has seven categories, two languages and three survival tags; the base run has 74 questions
with every key exact, the variant 76, with 4 questions up and 2 down; counted leniently the base
has 81 and the variant 80 (2 up, 3 down). A third run repeats the base to give the noise floor.
Every expected number below is worked out from the status table in ``scoredata`` (E, L, X per
question and run) or by a copy of the old comparison script, never by the scorer under test.
"""

import random

import pytest
from scoredata import CREATED, big_qset, big_run, category_of, qid, statuses

from qa_bench.score import NamedRun, Options, score

STATUS = statuses()
INDEXES = list(range(101))


def exact(which: int, flips: dict[int, str] | None = None) -> list[float]:
    flips = flips or {}
    return [float(flips.get(i, STATUS[i][which]) == "E") for i in INDEXES]


def lenient(which: int) -> list[float]:
    return [float(STATUS[i][which] in "EL") for i in INDEXES]


def reference_boot(diffs: list[float]) -> tuple[float, float]:
    """The interval as the old comparison script computed it (Random(1), 2,000 resamples)."""
    rng = random.Random(1)
    n = len(diffs)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(2000))
    return round(means[50], 4), round(means[1949], 4)


@pytest.fixture(scope="module")
def result():
    qset = big_qset()
    # the repeat of the base run: two misses become hits and one hit a miss
    misses = [i for i in INDEXES if STATUS[i] == "XX"][:2]
    hits = [i for i in INDEXES if STATUS[i] == "EE"][:1]
    flips = {**dict.fromkeys(misses, "E"), **dict.fromkeys(hits, "X")}
    runs = [
        NamedRun("main.qa-run.jsonl.gz", big_run("main", 0, qset)),
        NamedRun("branch.qa-run.jsonl.gz", big_run("branch", 1, qset)),
        NamedRun("main-repeat.qa-run.jsonl.gz", big_run("main-repeat", 0, qset, flips)),
    ]
    options = Options(pairs=(("main", "branch"),), noise=("main", "main-repeat"))
    scored = score(qset, runs, created=CREATED, options=options)
    scored_flips = flips
    return scored.file, scored_flips


def compare(result, group, metric):
    (found,) = [
        r
        for r in result[0].records
        if r["record"] == "compare" and r["group"] == group and r["metric"] == metric
    ]
    return found


def test_the_set_is_the_shape_it_claims():
    assert len(STATUS) == 101
    base, var = exact(0), exact(1)
    assert (sum(base), sum(var)) == (74, 76)
    ups = sum(1 for a, b in zip(base, var, strict=True) if b > a)
    downs = sum(1 for a, b in zip(base, var, strict=True) if b < a)
    assert (ups, downs) == (4, 2)
    assert (sum(lenient(0)), sum(lenient(1))) == (81, 80)


def test_all_keys_exact_over_the_whole_set(result):
    row = compare(result, "all", "all_keys")
    assert (row["n"], row["base"], row["var"], row["diff"]) == (101, 0.7327, 0.7525, 0.0198)
    assert (row["up"], row["down"]) == (4, 2)
    diffs = [b - a for a, b in zip(exact(0), exact(1), strict=True)]
    assert tuple(row["ci"]) == reference_boot(diffs) == (-0.0297, 0.0693)


def test_all_keys_counted_leniently_over_the_whole_set(result):
    row = compare(result, "all", "all_keys_lenient")
    assert (row["base"], row["var"], row["diff"]) == (0.802, 0.7921, -0.0099)
    assert (row["up"], row["down"]) == (2, 3)
    diffs = [b - a for a, b in zip(lenient(0), lenient(1), strict=True)]
    assert tuple(row["ci"]) == reference_boot(diffs) == (-0.0594, 0.0297)


def test_the_key_share_equals_the_exact_rate_because_every_answer_is_all_or_nothing(result):
    for metric, wanted in (("key_share", "all_keys"), ("key_share_lenient", "all_keys_lenient")):
        assert compare(result, "all", metric) == {
            **compare(result, "all", wanted),
            "metric": metric,
        }


def test_every_category_is_compared_on_its_own_questions(result):
    for category in "abcdefg":
        members = [i for i in INDEXES if category_of(i) == category]
        base = [exact(0)[i] for i in members]
        var = [exact(1)[i] for i in members]
        row = compare(result, f"category={category}", "all_keys")
        assert row["n"] == len(members)
        assert row["base"] == round(sum(base) / len(base), 4)
        assert row["var"] == round(sum(var) / len(var), 4)
        assert row["up"] == sum(1 for a, b in zip(base, var, strict=True) if b > a)
        assert row["down"] == sum(1 for a, b in zip(base, var, strict=True) if b < a)
        diffs = [b - a for a, b in zip(base, var, strict=True)]
        assert tuple(row["ci"]) == reference_boot(diffs)


def test_f_is_where_the_lenient_rule_matters(result):
    row = compare(result, "category=f", "all_keys_lenient")
    members = [i for i in INDEXES if category_of(i) == "f"]
    assert row["base"] == round(sum(lenient(0)[i] for i in members) / 12, 4)
    exact_f = compare(result, "category=f", "all_keys")
    assert exact_f["base"] < row["base"]


def test_a_language_and_a_union_group_are_compared_too(result):
    es = [i for i in INDEXES if i % 2 == 1]
    row = compare(result, "lang=es", "all_keys")
    assert row["n"] == len(es)
    assert row["base"] == round(sum(exact(0)[i] for i in es) / len(es), 4)
    for group in ("survives=yes", "survives=partial", "survives=no"):
        assert compare(result, group, "all_keys")["n"] > 0


def test_the_noise_floor_is_the_difference_of_the_two_base_runs(result):
    flips = result[1]
    floor = abs(sum(exact(0, flips)) - sum(exact(0))) / 101
    row = compare(result, "all", "all_keys")
    assert row["floor"] == round(floor, 4) == 0.0099
    assert row["counts"] is False  # the interval holds 0
    assert result[0].header["noise_floor"] == {"base_run": "main", "var_run": "main-repeat"}


def test_the_numpy_generator_gives_its_own_interval_for_the_same_differences():
    np = pytest.importorskip("numpy")
    qset = big_qset()
    runs = [
        NamedRun("main.jsonl", big_run("main", 0, qset)),
        NamedRun("branch.jsonl", big_run("branch", 1, qset)),
    ]
    options = Options(pairs=(("main", "branch"),), engine="numpy_pcg64", group_ci=False)
    file = score(qset, runs, created=CREATED, options=options).file
    (row,) = [
        r
        for r in file.records
        if r["record"] == "compare" and r["group"] == "all" and r["metric"] == "all_keys"
    ]
    diffs = np.array([b - a for a, b in zip(exact(0), exact(1), strict=True)])
    means = diffs[np.random.default_rng(1).integers(0, 101, size=(2000, 101))].mean(axis=1)
    low, high = np.percentile(means, [2.5, 97.5])
    assert tuple(row["ci"]) == (round(float(low), 4), round(float(high), 4))
    assert row["diff"] == round(float(diffs.mean()), 4)


def test_the_ids_of_the_set_are_in_the_order_the_bootstrap_uses():
    qset = big_qset()
    assert [r["id"] for r in qset.records] == [qid(i) for i in INDEXES]
