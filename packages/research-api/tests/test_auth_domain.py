from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from research_api.domain.auth import (
    LoginPolicy,
    Session,
    SessionPolicy,
    check_password,
    normalize_username,
    password_problems,
    validate_display_name,
    validate_username,
)
from research_api.domain.common_passwords import COMMON_PASSWORDS
from research_api.domain.errors import InvalidInputError

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("raw", "stored"),
    [("Ana", "ana"), ("  temp.p ", "temp.p"), ("ana\n", "ana"), ("a_b-c.9", "a_b-c.9")],
)
def test_a_username_is_trimmed_and_lowercased(raw: str, stored: str) -> None:
    assert normalize_username(raw) == stored
    assert validate_username(raw) == stored


@pytest.mark.parametrize(
    "raw",
    ["", " ", "-ana", ".ana", "a b", "ana!", "ñandú", "x" * 33, "a/b", "../etc"],
)
def test_a_bad_username_is_refused_with_its_field(raw: str) -> None:
    with pytest.raises(InvalidInputError) as refused:
        validate_username(raw)
    assert [e.path for e in refused.value.errors] == ["/username"]


def test_a_username_of_32_characters_is_the_longest() -> None:
    assert validate_username("a" * 32) == "a" * 32


def test_a_display_name_is_tidied_and_bounded() -> None:
    assert validate_display_name("  Ana   María  Prueba ") == "Ana María Prueba"
    for bad in ("", "   ", "x" * 81, "bell\x07", "nul\x00"):
        with pytest.raises(InvalidInputError):
            validate_display_name(bad)


def test_a_good_password_has_no_problems() -> None:
    assert password_problems("a-long-enough-phrase", "ana") == []


@pytest.mark.parametrize(
    ("password", "fragment"),
    [
        ("short", "at least 12"),
        ("elevenchars", "at least 12"),
        ("ana", "at least 12"),
        ("x" * 1025, "at most 1024"),
        ("password1234", "too common"),
        ("PASSWORD1234", "too common"),
        ("aaaaaaaaaaaaaa", "too common"),
        ("a-long-username-here", "username"),
    ],
)
def test_a_weak_password_says_why(password: str, fragment: str) -> None:
    username = "a-long-username-here"
    assert any(fragment in p for p in password_problems(password, username))


def test_the_username_check_ignores_case_and_spaces() -> None:
    assert any("username" in p for p in password_problems("Ana.Prueba.001", " ANA.prueba.001 "))


def test_check_password_raises_one_field_error_per_problem() -> None:
    with pytest.raises(InvalidInputError) as refused:
        check_password("short", "ana")
    assert [e.path for e in refused.value.errors] == ["/new"]
    with pytest.raises(InvalidInputError) as refused:
        check_password("short", "ana", path="/password")
    assert [e.path for e in refused.value.errors] == ["/password"]


def test_there_are_no_composition_rules() -> None:
    assert password_problems("alllowercasewords", "ana") == []


def test_the_common_password_list_only_holds_passwords_that_pass_the_length_rule() -> None:
    assert all(len(p) >= 12 and p == p.lower() for p in COMMON_PASSWORDS)
    assert len(COMMON_PASSWORDS) > 100


def session(created: datetime, seen: datetime) -> Session:
    return Session(
        id="s", user_id=1, token_hash="h", csrf_token="c", created_at=created, last_seen_at=seen
    )


def test_a_session_ends_at_the_sooner_of_idle_and_absolute() -> None:
    policy = SessionPolicy(idle=timedelta(hours=12), absolute=timedelta(days=7))
    fresh = session(T0, T0)
    assert policy.expires_at(fresh) == T0 + timedelta(hours=12)
    old = session(T0, T0 + timedelta(days=6, hours=20))
    assert policy.expires_at(old) == T0 + timedelta(days=7)


def test_a_session_is_live_until_the_expiry_instant() -> None:
    policy = SessionPolicy(idle=timedelta(hours=12), absolute=timedelta(days=7))
    s = session(T0, T0)
    assert policy.is_live(s, T0 + timedelta(hours=12) - timedelta(seconds=1))
    assert not policy.is_live(s, T0 + timedelta(hours=12))


def test_the_per_address_budget_is_four_times_the_per_username_one() -> None:
    assert LoginPolicy(max_failures=5, window=timedelta(minutes=15)).max_failures_per_address == 20


def test_retry_after_is_none_below_the_limit() -> None:
    policy = LoginPolicy(max_failures=3, window=timedelta(minutes=15))
    assert policy.retry_after_s([], 3, T0) is None
    assert policy.retry_after_s([T0, T0], 3, T0) is None


def test_retry_after_waits_for_enough_old_failures_to_leave_the_window() -> None:
    policy = LoginPolicy(max_failures=3, window=timedelta(minutes=15))
    times = [T0, T0 + timedelta(minutes=1), T0 + timedelta(minutes=2)]
    now = T0 + timedelta(minutes=3)
    # at the limit: the oldest failure leaves the window at T0 + 15 min
    assert policy.retry_after_s(times, 3, now) == 12 * 60
    # one over the limit: two must leave, so the wait runs to the second-oldest
    more = [*times, T0 + timedelta(minutes=3)]
    assert policy.retry_after_s(more, 3, now) == 13 * 60


def test_retry_after_rounds_up_and_is_at_least_one_second() -> None:
    policy = LoginPolicy(max_failures=1, window=timedelta(seconds=10))
    assert policy.retry_after_s([T0], 1, T0 + timedelta(seconds=4.5)) == 6
    assert policy.retry_after_s([T0], 1, T0 + timedelta(seconds=10)) == 1
