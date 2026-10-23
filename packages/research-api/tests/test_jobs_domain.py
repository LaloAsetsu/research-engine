"""The rules of a benchmark job: its states, its ids, its parameters and who may touch it."""

from __future__ import annotations

import itertools
import re
from datetime import UTC, datetime, timedelta

import pytest

from research_api.domain import jobs
from research_api.domain.errors import ForbiddenError, InvalidInputError
from research_api.domain.jobs import (
    ArmSpec,
    IdMaker,
    Job,
    JobState,
    Progress,
    RunParams,
    ScoreOptions,
    ScoreParams,
    arm_spec,
    base_notes,
    expected_keys,
    header_notes,
    interleaved_in,
    move_allowed,
    require_owner_or_admin,
    run_id_for,
    valid_job_id,
    valid_run_id,
)
from research_api.domain.sets import Actor, SetRef

NOW = datetime(2026, 10, 23, 1, 2, 3, tzinfo=UTC)

LEGAL = {
    (JobState.QUEUED, JobState.RUNNING),
    (JobState.QUEUED, JobState.CANCELLED),
    (JobState.RUNNING, JobState.DONE),
    (JobState.RUNNING, JobState.FAILED),
    (JobState.RUNNING, JobState.CANCELLING),
    (JobState.RUNNING, JobState.INTERRUPTED),
    (JobState.CANCELLING, JobState.CANCELLED),
    (JobState.CANCELLING, JobState.DONE),
    (JobState.CANCELLED, JobState.QUEUED),
    (JobState.INTERRUPTED, JobState.QUEUED),
}


def make_job(state: JobState = JobState.QUEUED, owner: str = "ana") -> Job:
    return Job(
        id="0" * 26,
        kind="run",
        state=state,
        owner=owner,
        created_at=NOW,
        queued_at=NOW,
        params=RunParams(SetRef("demo", "v1"), "a" * 32, ArmSpec("bare"), "m", 1),
        progress=Progress(0, 4),
    )


@pytest.mark.parametrize(("a", "b"), list(itertools.product(JobState, JobState)))
def test_only_the_moves_of_the_state_machine_are_allowed(a: JobState, b: JobState) -> None:
    assert move_allowed(a, b) == ((a, b) in LEGAL)


def test_done_and_failed_never_move_again() -> None:
    for state in (JobState.DONE, JobState.FAILED):
        assert jobs.TRANSITIONS[state] == frozenset()


def test_cancelled_and_interrupted_wait_for_a_resume_and_count_as_settled() -> None:
    assert {JobState.CANCELLED, JobState.INTERRUPTED} == jobs.RESUMABLE_STATES
    assert make_job(JobState.CANCELLED).settled and make_job(JobState.INTERRUPTED).settled
    assert not any(make_job(s).settled for s in jobs.OPEN_STATES)
    assert {JobState.QUEUED, JobState.RUNNING, JobState.CANCELLING} == jobs.OPEN_STATES


def test_every_state_is_reachable_and_every_move_starts_from_a_known_state() -> None:
    assert set(jobs.TRANSITIONS) == set(JobState)
    reachable = {JobState.QUEUED} | {to for targets in jobs.TRANSITIONS.values() for to in targets}
    assert reachable == set(JobState)


def test_the_owner_and_an_admin_may_touch_a_job_and_nobody_else() -> None:
    job = make_job(owner="ana")
    require_owner_or_admin(Actor("ana"), job)
    require_owner_or_admin(Actor("root", admin=True), job)
    with pytest.raises(ForbiddenError):
        require_owner_or_admin(Actor("beto"), job)


# --- ids ------------------------------------------------------------------------------------


def test_ids_have_26_characters_of_the_alphabet_and_sort_by_time() -> None:
    maker = IdMaker()
    first = maker.new(NOW)
    later = maker.new(NOW + timedelta(seconds=1))
    assert re.fullmatch(jobs.JOB_ID_PATTERN, first)
    assert len(first) == len(later) == 26
    assert first < later


def test_ids_made_in_the_same_millisecond_sort_in_the_order_they_were_made() -> None:
    maker = IdMaker()
    made = [maker.new(NOW) for _ in range(500)]
    assert made == sorted(made) and len(set(made)) == 500


def test_a_clock_that_goes_back_still_gives_larger_ids() -> None:
    maker = IdMaker()
    first = maker.new(NOW)
    assert maker.new(NOW - timedelta(hours=1)) > first


def test_the_random_part_running_out_moves_to_the_next_millisecond() -> None:
    maker = IdMaker()
    first = maker.new(NOW)
    maker._last_random = (1 << 80) - 1
    after = maker.new(NOW)
    assert after > first and valid_job_id(after)


def test_the_id_patterns_refuse_what_is_not_an_id() -> None:
    assert valid_job_id("0" * 26) and not valid_job_id("0" * 25) and not valid_job_id("I" * 26)
    assert not valid_job_id("../" + "0" * 23)
    assert valid_run_id("bare-demo-papers-v1-0abc1234")
    for bad in ("", ".hidden", "-x", "a/b", "a b", "x" * 129):
        assert not valid_run_id(bad)


def test_a_run_id_names_the_arm_the_set_and_the_end_of_the_job_id() -> None:
    run_id = run_id_for("oracle", SetRef("demo-papers", "v2"), "01abcdefghjkmnpqrstvwxyz00")
    assert run_id == "oracle-demo-papers-v2-tvwxyz00"
    assert valid_run_id(run_id)


# --- the arm and the parameters -------------------------------------------------------------


def test_an_arm_is_one_of_three() -> None:
    assert arm_spec("bare", None, {}, at="/arms/0") == ArmSpec("bare", None, {})
    with pytest.raises(InvalidInputError) as caught:
        arm_spec("agent", None, {}, at="/arms/0")
    assert caught.value.errors[0].path == "/arms/0/name"


@pytest.mark.parametrize("arm", ["bare", "oracle"])
def test_only_the_harness_has_settings_to_override(arm: str) -> None:
    with pytest.raises(InvalidInputError) as caught:
        arm_spec(arm, None, {"use_index": False}, at="/arms/1")
    assert caught.value.errors[0].path == "/arms/1/overrides"


@pytest.mark.parametrize(
    ("overrides", "path"),
    [
        ({"colour": "red"}, "/a/overrides/colour"),
        ({"bm25": "sometimes"}, "/a/overrides/bm25"),
        ({"use_index": "yes"}, "/a/overrides/use_index"),
        ({"whole_blocks": 1}, "/a/overrides/whole_blocks"),
    ],
)
def test_the_harness_settings_are_checked_one_by_one(
    overrides: dict[str, object], path: str
) -> None:
    with pytest.raises(InvalidInputError) as caught:
        arm_spec("harness", None, overrides, at="/a")
    assert caught.value.errors[0].path == path


def test_good_harness_settings_pass_and_a_variant_has_a_length() -> None:
    ok = arm_spec(
        "harness", "no index", {"use_index": False, "bm25": "never", "whole_blocks": True}, at="/a"
    )
    assert ok.variant == "no index" and ok.overrides["bm25"] == "never"
    for variant in ("", "x" * (jobs.MAX_VARIANT + 1)):
        with pytest.raises(InvalidInputError):
            arm_spec("harness", variant, {}, at="/a")


def test_run_parameters_survive_json() -> None:
    params = RunParams(
        SetRef("demo", "v3"),
        "b" * 32,
        ArmSpec("harness", "v", {"bm25": "always"}),
        "qwen-local",
        3,
        "my notes",
    )
    assert RunParams.from_json(params.to_json()) == params


def test_score_parameters_survive_json() -> None:
    params = ScoreParams(
        SetRef("demo", "v1"),
        ("a", "b", "c"),
        ScoreOptions(
            pairs=(("a", "b"),),
            noise=("b", "c"),
            group_by=("lang",),
            groups=("x=1|2",),
            engine="numpy_pcg64",
            number_words=True,
        ),
    )
    assert ScoreParams.from_json(params.to_json()) == params
    bare = ScoreParams(SetRef("demo", "v1"), ("a",))
    assert ScoreParams.from_json(bare.to_json()) == bare


# --- the order of the questions -------------------------------------------------------------


def test_the_questions_go_through_the_set_once_for_each_repeat() -> None:
    assert list(expected_keys(["q1", "q2"], 3)) == [
        ("q1", 1),
        ("q2", 1),
        ("q1", 2),
        ("q2", 2),
        ("q1", 3),
        ("q2", 3),
    ]
    assert list(expected_keys([], 2)) == []


# --- the notes of a header ------------------------------------------------------------------


def test_the_notes_carry_the_members_words_the_shared_asks_and_how_far_a_partial_run_got() -> None:
    assert header_notes("", 0, None) == "Interleaved asks: 0."
    full = header_notes("my words", 3, "Cancelled at 2026 with 2 of 6 answers")
    assert full == "my words Interleaved asks: 3. Partial: Cancelled at 2026 with 2 of 6 answers."
    assert interleaved_in(full) == 3
    assert base_notes(full) == "my words"
    assert interleaved_in("nothing here") == 0
    assert base_notes(header_notes("", 1, "x")) == ""


FORGED = "copied from r1. Interleaved asks: 7. Partial: Cancelled at t with 1 of 4 answers."


def test_sentences_in_the_members_words_do_not_count_as_the_services() -> None:
    notes = header_notes(FORGED, 0, None)
    assert notes == f"{FORGED} Interleaved asks: 0."
    assert interleaved_in(notes) == 0 and base_notes(notes) == FORGED
    stopped = header_notes(FORGED, 2, "Stopped at t with 3 of 6 answers")
    assert interleaved_in(stopped) == 2 and base_notes(stopped) == FORGED


def test_the_members_own_partial_sentence_survives_a_rewrite_of_the_header() -> None:
    words = "Partial: my own idea of it."
    once = header_notes(words, 1, "Stopped at t with 1 of 2 answers")
    twice = header_notes(base_notes(once), 3, None)
    assert twice == f"{words} Interleaved asks: 3."


def test_only_the_ending_of_the_notes_is_read_as_the_services_sentences() -> None:
    assert interleaved_in("Interleaved asks: 9. then more words") == 0
    assert (
        base_notes("Interleaved asks: 9. then more words") == "Interleaved asks: 9. then more words"
    )
    assert interleaved_in("words Interleaved asks: 4.") == 4
    assert base_notes("Interleaved asks: 4.") == ""
    assert interleaved_in("") == 0 and base_notes("  ") == ""


def test_writing_the_notes_again_does_not_pile_up_sentences() -> None:
    once = header_notes("words", 1, "Stopped at t with 1 of 2 answers")
    twice = header_notes(base_notes(once), 4, None)
    assert twice == "words Interleaved asks: 4."
