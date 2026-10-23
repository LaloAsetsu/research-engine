"""The scorer on small synthetic sets whose numbers can be worked out by hand."""

import pytest
from scoredata import (
    CREATED,
    DOC_A,
    answer,
    expr_key,
    make_qset,
    make_run,
    num_key,
    question,
    text_key,
)

from qa_bench.formats import QA_SCORE, FormatError, check_against_set, read_score, write
from qa_bench.score import NamedRun, Options, ScoreError, code_md5, score
from qa_bench.score.scorer import per_question

RIGHT = "The result is concept."
WRONG = "No relevant result was found."


def small_set():
    keys = [text_key("concept")]
    return make_qset(
        [
            question("S-1", keys, category="a", lang="en"),
            question("S-2", keys, category="b", lang="en"),
            question("S-3", keys, category="a", lang="es"),
            question("S-4", keys, category="b", lang="es"),
        ]
    )


def run_of(run_id, qset, texts, **more):
    return make_run(run_id, qset, [answer(f"S-{i}", t) for i, t in enumerate(texts, 1)], **more)


def scored(qset, runs, **options):
    named = [NamedRun(f"{r.header['run_id']}.qa-run.jsonl", r) for r in runs]
    return score(qset, named, created=CREATED, options=Options(**options))


def records(result, kind, **match):
    return [
        r
        for r in result.file.records
        if r["record"] == kind and all(r.get(k) == v for k, v in match.items())
    ]


def one(result, kind, **match):
    (found,) = records(result, kind, **match)
    return found


def test_a_paired_comparison_by_hand():
    q = small_set()
    r1 = run_of("r1", q, [RIGHT, WRONG, RIGHT, WRONG])
    r2 = run_of("r2", q, [RIGHT, RIGHT, RIGHT, RIGHT])
    result = scored(q, [r1, r2], pairs=(("r1", "r2"),))
    cmp = one(result, "compare", group="all", metric="all_keys")
    assert (cmp["n"], cmp["base"], cmp["var"], cmp["diff"], cmp["up"], cmp["down"]) == (
        4,
        0.5,
        1.0,
        0.5,
        2,
        0,
    )
    assert cmp["base_run"] == "r1"
    assert cmp["var_run"] == "r2"
    assert cmp["ci"][0] <= cmp["diff"] <= cmp["ci"][1]
    by_lang = one(result, "compare", group="lang=es", metric="all_keys")
    assert (by_lang["n"], by_lang["base"], by_lang["up"]) == (2, 0.5, 1)


def test_a_group_record_has_the_mean_and_an_interval():
    q = small_set()
    result = scored(q, [run_of("r1", q, [RIGHT, WRONG, RIGHT, WRONG])])
    group = one(result, "group", run_id="r1", group="category=a", metric="all_keys")
    assert (group["n"], group["mean"], group["ci"]) == (2, 1.0, [1.0, 1.0])
    everyone = one(result, "group", run_id="r1", group="all", metric="key_share")
    assert everyone["mean"] == 0.5
    assert 0.0 <= everyone["ci"][0] <= 0.5 <= everyone["ci"][1] <= 1.0


def test_an_item_record_per_answer_with_its_keys():
    q = small_set()
    result = scored(q, [run_of("r1", q, [RIGHT, WRONG, RIGHT, WRONG])])
    items = records(result, "item")
    assert [i["id"] for i in items] == ["S-1", "S-2", "S-3", "S-4"]
    assert items[0]["metrics"]["all_keys"] is True
    assert items[0]["keys"] == [{"i": 0, "exact": True, "lenient": True}]
    assert items[1]["metrics"]["all_keys"] is False
    assert items[1]["metrics"]["wall_s"] == 2.0


def test_the_file_is_a_valid_score_file_for_its_set_and_reads_back(tmp_path):
    q = small_set()
    r1 = run_of("r1", q, [RIGHT, WRONG, RIGHT, WRONG], variant="main")
    result = scored(q, [r1], noise=None)
    assert result.file.format == QA_SCORE
    assert check_against_set(result.file, q) == []
    header = result.file.header
    assert header["set"] == {"name": q.header["set"], "md5": q.md5}
    assert header["runs"] == [
        {"run_id": "r1", "file": "r1.qa-run.jsonl", "md5": r1.md5, "label": "main"}
    ]
    assert header["bootstrap"] == {
        "resamples": 2000,
        "seed": 1,
        "level": 0.95,
        "cluster": "id",
        "engine": "python_random",
    }
    assert header["scorer"]["md5"] == code_md5()
    assert header["scorer"]["mathnorm"] == "v2"
    assert header["created"] == CREATED
    path = tmp_path / "out.qa-score.jsonl.gz"
    md5 = write(path, result.file)
    assert read_score(path).records == result.file.records
    assert md5 == read_score(path).md5


def test_the_same_inputs_give_the_same_bytes():
    q = small_set()
    runs = [run_of("r1", q, [RIGHT, WRONG, RIGHT, WRONG]), run_of("r2", q, [RIGHT] * 4)]
    first = scored(q, runs, pairs=(("r1", "r2"),))
    second = scored(q, runs, pairs=(("r1", "r2"),))
    assert first.file.to_bytes() == second.file.to_bytes()


def test_the_label_is_the_variant_else_the_arm():
    q = small_set()
    plain = scored(q, [run_of("r1", q, [RIGHT] * 4)])
    assert plain.file.header["runs"][0]["label"] == "harness"
    bare = make_run("r2", q, [answer(f"S-{i}", RIGHT) for i in range(1, 5)], arm="bare")
    assert scored(q, [bare]).file.header["runs"][0]["label"] == "bare"


def test_the_order_of_a_run_file_changes_nothing_because_questions_follow_the_set():
    q = small_set()
    forward = run_of("r1", q, [RIGHT, WRONG, RIGHT, WRONG])
    backward = make_run("r1", q, list(reversed(forward.records)))
    other = run_of("r2", q, [RIGHT, RIGHT, WRONG, WRONG])
    a = scored(q, [forward, other], pairs=(("r1", "r2"),))
    b = scored(q, [backward, other], pairs=(("r1", "r2"),))

    def tables(result):
        return [r for r in result.file.records if r["record"] != "item"]

    assert tables(a) == tables(b)


def test_repeats_are_averaged_per_question_so_the_bootstrap_resamples_questions():
    q = small_set()
    answers = [answer("S-1", RIGHT), answer("S-1", WRONG, repeat=2)]
    answers += [answer(f"S-{i}", RIGHT) for i in (2, 3, 4)]
    result = scored(q, [make_run("r1", q, answers)])
    assert [i["repeat"] for i in records(result, "item", id="S-1")] == [1, 2]
    group = one(result, "group", group="all", metric="all_keys")
    assert (group["n"], group["mean"]) == (4, 0.875)  # S-1 counts once, as 0.5


def test_per_question_leaves_a_metric_out_when_no_repeat_has_it():
    scored_rows = [("Q", 1, {"a": 1.0, "b": None}, None), ("Q", 2, {"a": 0.0, "b": None}, None)]
    assert per_question(scored_rows) == {"Q": {"a": 0.5, "b": None}}


def test_a_metric_the_run_lacks_is_null_on_the_item_and_left_out_of_groups():
    q = small_set()
    result = scored(q, [run_of("r1", q, [RIGHT] * 4)])
    assert records(result, "item")[0]["metrics"]["chunks"] is None
    assert not records(result, "group", metric="chunks")
    assert not records(result, "group", metric="psg10")


def test_the_funnel_stages_are_scored_when_the_run_kept_the_text():
    q = small_set()
    ext = {"given": [{"text": "concept is here"}], "answer_prompt": "no key"}
    answers = [answer(f"S-{i}", RIGHT, ext=ext) for i in range(1, 5)]
    result = scored(q, [make_run("r1", q, answers)])
    assert one(result, "group", group="all", metric="chunks")["mean"] == 1.0
    assert one(result, "group", group="all", metric="evidence")["mean"] == 0.0


def test_items_without_key_facts_get_cost_metrics_only():
    q = make_qset([question("S-1", None), question("S-2", [text_key("concept")])])
    r = make_run("r1", q, [answer("S-1", "x"), answer("S-2", RIGHT)])
    result = scored(q, [r])
    first = records(result, "item", id="S-1")[0]
    assert "all_keys" not in first["metrics"]
    assert "keys" not in first
    assert first["metrics"]["error"] is False
    assert one(result, "group", group="all", metric="all_keys")["n"] == 1


def test_qasper_items_are_scored_by_token_f1():
    refs = [{"answer": "cat sat"}]
    q = make_qset(
        [question("S-1", None, ext={"refs": refs}), question("S-2", None, ext={"refs": refs})]
    )
    r = make_run(
        "r1",
        q,
        [
            answer("S-1", "text", ext={"short": "cat sat"}),
            answer("S-2", "I cannot answer.", ext={"short": "dog"}),
        ],
    )
    result = scored(q, [r])
    assert one(result, "group", group="all", metric="answer_f1")["mean"] == 0.5
    assert one(result, "group", group="all", metric="answer_good")["mean"] == 0.5
    assert one(result, "group", group="all", metric="refused")["mean"] == 0.5


def test_a_noise_floor_counts_only_a_difference_that_beats_it_and_excludes_zero():
    keys = [text_key("concept")]
    q = make_qset([question(f"S-{i}", keys) for i in range(1, 21)])

    def run(run_id, wrong):
        return make_run(
            run_id,
            q,
            [answer(f"S-{i}", WRONG if i in wrong else RIGHT) for i in range(1, 21)],
        )

    base = run("base", set(range(1, 13)))
    repeat = run("repeat", set(range(1, 12)))  # one question moved: the noise
    far = run("far", set())  # twelve moved
    result = scored(
        q,
        [base, repeat, far],
        pairs=(("base", "far"), ("base", "repeat")),
        noise=("base", "repeat"),
    )
    assert result.file.header["noise_floor"] == {"base_run": "base", "var_run": "repeat"}
    big = one(result, "compare", var_run="far", group="all", metric="all_keys")
    assert big["floor"] == 0.05
    assert big["diff"] == 0.6
    assert big["ci"][0] > 0
    assert big["counts"] is True
    small = one(result, "compare", var_run="repeat", group="all", metric="all_keys")
    assert small["diff"] == 0.05
    assert small["counts"] is False


def test_without_a_noise_pair_a_comparison_has_no_floor():
    q = small_set()
    result = scored(
        q, [run_of("r1", q, [RIGHT] * 4), run_of("r2", q, [WRONG] * 4)], pairs=(("r1", "r2"),)
    )
    cmp = one(result, "compare", group="all", metric="all_keys")
    assert "floor" not in cmp
    assert "counts" not in cmp
    assert "noise_floor" not in result.file.header


def test_the_noise_pair_alone_writes_no_comparison():
    q = small_set()
    result = scored(
        q, [run_of("r1", q, [RIGHT] * 4), run_of("r2", q, [WRONG] * 4)], noise=("r1", "r2")
    )
    assert not records(result, "compare")


def test_a_union_group_and_a_chosen_set_of_fields():
    q = small_set()
    result = scored(
        q,
        [run_of("r1", q, [RIGHT, WRONG, RIGHT, WRONG])],
        group_by=("lang",),
        groups=("category=a|b",),
    )
    groups = {r["group"] for r in records(result, "group")}
    assert groups == {"all", "lang=en", "lang=es", "category=a|b"}
    assert one(result, "group", group="category=a|b", metric="all_keys")["n"] == 4


def test_the_interval_of_a_group_can_be_skipped():
    q = small_set()
    result = scored(q, [run_of("r1", q, [RIGHT, WRONG, RIGHT, WRONG])], group_ci=False)
    assert all("ci" not in r for r in records(result, "group"))
    assert all("mean" in r for r in records(result, "group"))


def test_a_group_of_one_question_has_no_interval():
    q = make_qset(
        [
            question("S-1", [text_key("concept")], category="a"),
            question("S-2", [text_key("concept")], category="b"),
        ]
    )
    result = scored(q, [make_run("r1", q, [answer("S-1", RIGHT), answer("S-2", WRONG)])])
    assert "ci" not in one(result, "group", group="category=a", metric="all_keys")
    assert "ci" in one(result, "group", group="all", metric="all_keys")


def test_number_words_stay_out_of_the_main_columns_by_default():
    q = make_qset([question("S-1", [num_key(3)]), question("S-2", [num_key(3)])])
    r = make_run("r1", q, [answer("S-1", "three gates"), answer("S-2", "3 gates")])
    default = scored(q, [r])
    assert one(default, "group", group="all", metric="all_keys")["mean"] == 0.5
    assert one(default, "group", group="all", metric="all_keys_words")["mean"] == 1.0
    assert one(default, "group", group="all", metric="num_word_only")["mean"] == 0.5
    assert default.file.header["scorer"]["mathnorm"] == "v2"
    assert "do not count" in default.file.header["notes"]
    words = scored(q, [r], number_words=True)
    assert one(words, "group", group="all", metric="all_keys")["mean"] == 1.0
    assert words.file.header["scorer"]["mathnorm"] == "v2+words"
    assert "Number words count" in words.file.header["notes"]


def test_a_lenient_f_hit_needs_the_gold_paper_in_the_scores_too():
    keys = [expr_key("a+b+c+d+e")]
    q = make_qset([question("S-1", keys, category="f"), question("S-2", keys, category="f")])
    cited = f"relation among a b c d e ({DOC_A[:16]} pages 1-1)"
    r = make_run("r1", q, [answer("S-1", "relation among a b c d e"), answer("S-2", cited)])
    result = scored(q, [r])
    assert one(result, "group", group="all", metric="all_keys_lenient")["mean"] == 0.5
    assert one(result, "group", group="all", metric="lenient_unchecked")["mean"] == 0.5
    assert records(result, "item", id="S-1")[0]["keys"][0]["unchecked"] is True


def test_the_two_engines_can_be_chosen_and_are_named_in_the_header():
    q = small_set()
    r = run_of("r1", q, [RIGHT, WRONG, RIGHT, WRONG])
    py = scored(q, [r])
    np_ = scored(q, [r], engine="numpy_pcg64")
    assert np_.file.header["bootstrap"]["engine"] == "numpy_pcg64"
    assert "default_rng(1)" in np_.file.header["notes"]
    assert "random.Random(1)" in py.file.header["notes"]
    a = one(py, "group", group="all", metric="key_share")
    b = one(np_, "group", group="all", metric="key_share")
    assert a["mean"] == b["mean"] == 0.5


def test_warnings_for_questions_without_an_answer_and_for_another_corpus():
    q = small_set()
    short = make_run("r1", q, [answer("S-1", RIGHT), answer("S-2", RIGHT)], corpus="elsewhere")
    result = scored(q, [short])
    assert any("2 of 4 questions have no answer" in w for w in result.warnings)
    assert any("searched corpus elsewhere" in w for w in result.warnings)
    assert all(w in result.file.header["notes"] for w in result.warnings)
    assert one(result, "group", group="all", metric="all_keys")["n"] == 2


def test_chosen_fields_that_give_no_group_are_warned_about_and_the_others_still_group():
    q = small_set()
    result = scored(
        q,
        [run_of("r1", q, [RIGHT, WRONG, RIGHT, WRONG])],
        group_by=("lang", "nosuch", "tag:Bad-Name"),
    )
    assert any("'nosuch' gives no group" in w for w in result.warnings)
    assert any("'tag:Bad-Name' gives no group" in w for w in result.warnings)
    assert all(w in result.file.header["notes"] for w in result.warnings)
    assert {r["group"] for r in records(result, "group")} == {"all", "lang=en", "lang=es"}


def test_the_automatic_fields_give_no_warning():
    q = small_set()
    assert scored(q, [run_of("r1", q, [RIGHT] * 4)]).warnings == ()


def test_a_run_of_no_corpus_is_not_a_warning():
    q = small_set()
    result = scored(q, [run_of("r1", q, [RIGHT] * 4, corpus="none")])
    assert result.warnings == ()


# ---- refusals


def test_a_run_made_on_another_version_of_the_set_is_refused():
    q = small_set()
    other = make_qset([question("S-1", [text_key("concept")])], name="syn-set/v2")
    stale = make_run("r1", other, [answer("S-1", RIGHT)])
    with pytest.raises(ScoreError, match="does not fit the set"):
        scored(q, [stale])


def test_an_answer_to_a_question_the_set_does_not_have_is_refused():
    q = small_set()
    r = make_run("r1", q, [answer("S-9", RIGHT)])
    with pytest.raises(ScoreError, match="S-9"):
        scored(q, [r])


def test_a_repeated_answer_cannot_even_be_built():
    q = small_set()
    with pytest.raises(FormatError):
        make_run("r1", q, [answer("S-1", RIGHT), answer("S-1", WRONG)])


def test_a_bare_answer_with_contexts_cannot_even_be_built():
    q = small_set()
    with pytest.raises(FormatError):
        make_run("r1", q, [answer("S-1", RIGHT, contexts=[{"doc_id": DOC_A}])], arm="bare")


def test_an_oracle_run_must_carry_the_gold_passages():
    q = small_set()
    bad = make_run("r1", q, [answer("S-1", RIGHT)], arm="oracle")
    with pytest.raises(ScoreError, match="oracle"):
        scored(q, [bad])


def test_an_oracle_run_with_the_gold_passages_is_scored():
    q = small_set()
    answers = [
        answer(f"S-{i}", RIGHT, contexts=[{"doc_id": DOC_A, "text": f"Passage of S-{i}."}])
        for i in range(1, 5)
    ]
    result = scored(q, [make_run("r1", q, answers, arm="oracle")])
    assert one(result, "group", group="all", metric="all_keys")["mean"] == 1.0


def test_naming_a_run_that_is_not_given_is_refused():
    q = small_set()
    r = run_of("r1", q, [RIGHT] * 4)
    with pytest.raises(ScoreError, match="'zz'"):
        scored(q, [r], pairs=(("r1", "zz"),))
    with pytest.raises(ScoreError, match="'zz'"):
        scored(q, [r], noise=("zz", "r1"))


def test_a_pair_of_a_run_with_itself_or_given_twice_is_refused():
    q = small_set()
    runs = [run_of("r1", q, [RIGHT] * 4), run_of("r2", q, [RIGHT] * 4)]
    with pytest.raises(ScoreError, match="itself"):
        scored(q, runs, pairs=(("r1", "r1"),))
    with pytest.raises(ScoreError, match="twice"):
        scored(q, runs, pairs=(("r1", "r2"), ("r1", "r2")))


def test_two_runs_with_one_id_or_no_run_are_refused():
    q = small_set()
    r = run_of("r1", q, [RIGHT] * 4)
    with pytest.raises(ScoreError, match="same run_id"):
        scored(q, [r, r])
    with pytest.raises(ScoreError, match="at least one"):
        scored(q, [])


def test_an_unknown_engine_and_a_bad_group_are_refused():
    q = small_set()
    r = run_of("r1", q, [RIGHT] * 4)
    with pytest.raises(ScoreError, match="engine"):
        scored(q, [r], engine="mersenne")
    with pytest.raises(ScoreError, match="label=v1"):
        scored(q, [r], groups=("category",))


def test_files_of_the_wrong_format_are_refused():
    q = small_set()
    r = run_of("r1", q, [RIGHT] * 4)
    with pytest.raises(ScoreError, match="must be a qset/1"):
        score(r, [NamedRun("r", r)], created=CREATED)
    with pytest.raises(ScoreError, match="not a qa-run/1"):
        score(q, [NamedRun("q", q)], created=CREATED)


def test_many_problems_are_summarised():
    q = small_set()
    stale = make_run(
        "r1",
        make_qset(
            [question(f"S-{i}", [text_key("concept")]) for i in range(1, 9)], name="syn-set/v2"
        ),
        [answer(f"S-{i}", RIGHT) for i in range(1, 9)],
    )
    with pytest.raises(ScoreError, match=r"and \d+ more"):
        scored(q, [stale])
