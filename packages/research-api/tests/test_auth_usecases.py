"""The sign-in use cases over a real (throwaway) SQLite database and a clock the test moves."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest
from helpers import MovableClock
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from research_api.application.auth import AuthServices, hash_token
from research_api.bootstrap import Container, Settings, build_container
from research_api.domain.auth import ONE_TIME_ALPHABET, ONE_TIME_LENGTH, Role
from research_api.domain.errors import (
    ConflictError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
    UnauthenticatedError,
)

ADDRESS = "10.0.0.7"
GOOD = "ana-has-a-long-password"


@pytest.fixture
def clock() -> MovableClock:
    return MovableClock()


def make(clock: MovableClock, **settings: object) -> Container:
    """The container, which the caller must keep: the database goes when the container does."""
    container = build_container(
        Settings.model_validate(settings), fake=True, cheap_hashing=True, clock=clock
    )
    users = container.auth.users
    users.create("ana", "Ana", Role.ADMIN, password=GOOD, must_change_password=False)
    users.create(
        "beto", "Beto", Role.MEMBER, password="beto-has-a-long-password", must_change_password=False
    )
    return container


@pytest.fixture
def container(clock: MovableClock) -> Container:
    return make(clock)


@pytest.fixture
def auth(container: Container) -> AuthServices:
    return container.auth


def fail_login(auth: AuthServices, name: str = "ana", address: str = ADDRESS) -> None:
    with pytest.raises(UnauthenticatedError):
        auth.sign_in(name, "wrong-wrong-wrong", address)


# --- sign in --------------------------------------------------------------------------------


def test_signing_in_opens_a_session_whose_token_is_stored_only_as_a_hash(
    auth: AuthServices,
) -> None:
    opened = auth.sign_in("ana", GOOD, ADDRESS)
    assert len(opened.token) >= 43  # 256 bits in url-safe base64
    assert opened.session.token_hash == hashlib.sha256(opened.token.encode()).hexdigest()
    assert opened.token != opened.session.token_hash
    assert opened.session.csrf_token != opened.token
    assert opened.user.username == "ana"


def test_every_sign_in_gets_fresh_secrets(auth: AuthServices) -> None:
    first, second = auth.sign_in("ana", GOOD, ADDRESS), auth.sign_in("ana", GOOD, ADDRESS)
    assert first.token != second.token
    assert first.session.csrf_token != second.session.csrf_token
    assert first.session.id != second.session.id


def test_the_username_is_matched_without_case_or_edge_spaces(auth: AuthServices) -> None:
    assert auth.sign_in("  ANA ", GOOD, ADDRESS).user.username == "ana"


def test_a_wrong_password_and_an_unknown_name_get_the_same_error(auth: AuthServices) -> None:
    errors = []
    for name, password in (("ana", "wrong-wrong-wrong"), ("nobody", GOOD), ("", "x")):
        with pytest.raises(UnauthenticatedError) as caught:
            auth.sign_in(name, password, ADDRESS)
        errors.append(caught.value.detail)
    assert errors == ["Wrong username or password."] * 3


def test_an_unknown_name_still_costs_a_hash_check(clock: MovableClock) -> None:
    container = build_container(Settings.defaults(), fake=True, cheap_hashing=True, clock=clock)
    hasher = container.auth.sign_in._hasher
    burned: list[str] = []
    real = hasher.burn

    def spy(password: str) -> None:
        burned.append(password)
        real(password)

    hasher.burn = spy  # type: ignore[method-assign]
    with pytest.raises(UnauthenticatedError):
        container.auth.sign_in("ghost", "some-password-here", ADDRESS)
    assert burned == ["some-password-here"]


def test_a_known_name_is_verified_not_burned(auth: AuthServices) -> None:
    hasher = auth.sign_in._hasher
    burned: list[str] = []
    hasher.burn = burned.append  # type: ignore[method-assign]
    fail_login(auth, "ana")
    assert burned == []


def test_a_disabled_account_cannot_sign_in_even_with_the_right_password(auth: AuthServices) -> None:
    auth.users.disable("beto")
    with pytest.raises(UnauthenticatedError) as caught:
        auth.sign_in("beto", "beto-has-a-long-password", ADDRESS)
    assert caught.value.detail == "Wrong username or password."


def test_a_hash_with_old_costs_is_replaced_at_sign_in(auth: AuthServices) -> None:
    old_hash = PasswordHash((Argon2Hasher(time_cost=1, memory_cost=16, parallelism=1),)).hash(GOOD)
    repo = auth.sign_in._users
    ana = repo.by_username("ana")
    assert ana is not None
    repo.save(replace(ana, password_hash=old_hash))
    auth.sign_in("ana", GOOD, ADDRESS)
    updated = repo.by_username("ana")
    assert updated is not None
    assert updated.password_hash != old_hash
    assert "m=8,t=1,p=1" in updated.password_hash
    auth.sign_in("ana", GOOD, ADDRESS)  # and the new hash works


# --- rate limit -----------------------------------------------------------------------------


def test_five_failures_block_the_sixth_attempt_even_with_the_right_password(
    auth: AuthServices,
) -> None:
    for _ in range(5):
        fail_login(auth)
    with pytest.raises(RateLimitedError) as blocked:
        auth.sign_in("ana", GOOD, ADDRESS)
    assert blocked.value.retry_after_s == 15 * 60


def test_the_block_lifts_when_the_oldest_failure_leaves_the_window(
    auth: AuthServices, clock: MovableClock
) -> None:
    for _ in range(5):
        fail_login(auth)
        clock.advance(minutes=1)
    clock.advance(minutes=9, seconds=59)  # 14 min 59 s after the first failure
    with pytest.raises(RateLimitedError) as blocked:
        auth.sign_in("ana", GOOD, ADDRESS)
    assert blocked.value.retry_after_s == 1
    clock.advance(seconds=1)
    assert auth.sign_in("ana", GOOD, ADDRESS).user.username == "ana"


def test_blocked_attempts_do_not_extend_the_block(auth: AuthServices, clock: MovableClock) -> None:
    for _ in range(5):
        fail_login(auth)
    for _ in range(10):
        clock.advance(seconds=30)
        with pytest.raises(RateLimitedError):
            auth.sign_in("ana", "guess-after-guess-1", ADDRESS)
    clock.advance(minutes=10)  # 15 min after the five failures
    assert auth.sign_in("ana", GOOD, ADDRESS)


def test_four_failures_do_not_block(auth: AuthServices) -> None:
    for _ in range(4):
        fail_login(auth)
    assert auth.sign_in("ana", GOOD, ADDRESS)


def test_a_success_clears_the_usernames_count(auth: AuthServices) -> None:
    for _ in range(4):
        fail_login(auth)
    auth.sign_in("ana", GOOD, ADDRESS)
    for _ in range(4):
        fail_login(auth)
    assert auth.sign_in("ana", GOOD, ADDRESS)


def test_the_limit_is_per_username_so_others_are_not_locked_out(auth: AuthServices) -> None:
    for _ in range(5):
        fail_login(auth, "ana")
    assert auth.sign_in("beto", "beto-has-a-long-password", ADDRESS)


def test_an_unknown_username_is_throttled_too(auth: AuthServices) -> None:
    for _ in range(5):
        fail_login(auth, "ghost")
    with pytest.raises(RateLimitedError):
        auth.sign_in("ghost", "x", ADDRESS)


def test_one_address_trying_many_names_is_blocked_at_twenty(auth: AuthServices) -> None:
    for i in range(20):
        fail_login(auth, f"guess{i}")
    with pytest.raises(RateLimitedError):
        auth.sign_in("ana", GOOD, ADDRESS)
    assert auth.sign_in("ana", GOOD, "10.0.0.8")  # another address is not affected


def test_the_limits_come_from_the_settings(clock: MovableClock) -> None:
    container = make(clock, login_max_failures=2, login_window_minutes=1)
    auth = container.auth
    fail_login(auth)
    fail_login(auth)
    with pytest.raises(RateLimitedError) as blocked:
        auth.sign_in("ana", GOOD, ADDRESS)
    assert blocked.value.retry_after_s == 60
    clock.advance(minutes=1)
    assert auth.sign_in("ana", GOOD, ADDRESS)


# --- authenticate ---------------------------------------------------------------------------


def test_a_token_finds_its_user(auth: AuthServices) -> None:
    opened = auth.sign_in("ana", GOOD, ADDRESS)
    who = auth.authenticate(opened.token)
    assert (who.user.username, who.session.id) == ("ana", opened.session.id)


@pytest.mark.parametrize("token", [None, "", "not-a-token", "x" * 43])
def test_a_missing_or_unknown_token_is_unauthenticated(
    auth: AuthServices, token: str | None
) -> None:
    with pytest.raises(UnauthenticatedError):
        auth.authenticate(token)


def test_the_stored_hash_is_not_a_token(auth: AuthServices) -> None:
    opened = auth.sign_in("ana", GOOD, ADDRESS)
    with pytest.raises(UnauthenticatedError):
        auth.authenticate(opened.session.token_hash)
    assert hash_token(opened.token) == opened.session.token_hash


def test_a_session_ends_after_twelve_idle_hours(auth: AuthServices, clock: MovableClock) -> None:
    opened = auth.sign_in("ana", GOOD, ADDRESS)
    clock.advance(hours=11, minutes=59)
    auth.authenticate(opened.token)
    clock.advance(hours=12)
    with pytest.raises(UnauthenticatedError):
        auth.authenticate(opened.token)
    # the expired row is gone, not just ignored
    assert auth.list_sessions._sessions.by_id(opened.session.id) is None


def test_activity_keeps_a_session_alive_until_the_absolute_limit(
    auth: AuthServices, clock: MovableClock
) -> None:
    opened = auth.sign_in("ana", GOOD, ADDRESS)
    for _ in range(6 * 2):  # every 12 hours would be idle; every 11 is fine, for 6 days
        clock.advance(hours=11)
        auth.authenticate(opened.token)
    clock.advance(days=1)  # now beyond 7 days from sign-in
    with pytest.raises(UnauthenticatedError):
        auth.authenticate(opened.token)


def test_the_idle_and_absolute_limits_follow_the_settings(clock: MovableClock) -> None:
    container = make(clock, session_idle_hours=1, session_max_days=1)
    auth = container.auth
    opened = auth.sign_in("ana", GOOD, ADDRESS)
    clock.advance(minutes=61)
    with pytest.raises(UnauthenticatedError):
        auth.authenticate(opened.token)


def test_last_seen_is_written_at_most_once_a_minute(
    auth: AuthServices, clock: MovableClock
) -> None:
    opened = auth.sign_in("ana", GOOD, ADDRESS)
    repo = auth.list_sessions._sessions
    clock.advance(seconds=59)
    auth.authenticate(opened.token)
    assert repo.by_id(opened.session.id).last_seen_at == opened.session.created_at  # type: ignore[union-attr]
    clock.advance(seconds=1)
    refreshed = auth.authenticate(opened.token)
    assert refreshed.session.last_seen_at == clock.now()
    assert repo.by_id(opened.session.id).last_seen_at == clock.now()  # type: ignore[union-attr]


def test_a_disabled_account_loses_its_session_at_the_next_request(auth: AuthServices) -> None:
    opened = auth.sign_in("beto", "beto-has-a-long-password", ADDRESS)
    auth.users.disable("beto")
    with pytest.raises(UnauthenticatedError):
        auth.authenticate(opened.token)


def test_a_session_whose_account_was_disabled_behind_its_back_is_removed(
    auth: AuthServices,
) -> None:
    opened = auth.sign_in("beto", "beto-has-a-long-password", ADDRESS)
    repo = auth.sign_in._users
    beto = repo.by_username("beto")
    assert beto is not None
    repo.save(replace(beto, active=False))  # not through the use case, so its sessions remain
    with pytest.raises(UnauthenticatedError):
        auth.authenticate(opened.token)
    assert auth.list_sessions._sessions.by_id(opened.session.id) is None


def test_a_role_change_applies_at_the_next_request(auth: AuthServices) -> None:
    opened = auth.sign_in("beto", "beto-has-a-long-password", ADDRESS)
    assert auth.authenticate(opened.token).user.role is Role.MEMBER
    auth.users.set_role("beto", Role.ADMIN)
    assert auth.authenticate(opened.token).user.role is Role.ADMIN


# --- sign out -------------------------------------------------------------------------------


def test_signing_out_ends_only_that_session(auth: AuthServices) -> None:
    first, second = auth.sign_in("ana", GOOD, ADDRESS), auth.sign_in("ana", GOOD, ADDRESS)
    auth.sign_out(auth.authenticate(first.token))
    with pytest.raises(UnauthenticatedError):
        auth.authenticate(first.token)
    assert auth.authenticate(second.token)


# --- change password ------------------------------------------------------------------------

NEW = "a-brand-new-password-1"


def test_changing_the_password_ends_every_session_and_opens_a_new_one(auth: AuthServices) -> None:
    first, other = auth.sign_in("ana", GOOD, ADDRESS), auth.sign_in("ana", GOOD, ADDRESS)
    beto = auth.sign_in("beto", "beto-has-a-long-password", ADDRESS)
    renewed = auth.change_password(auth.authenticate(first.token), GOOD, NEW, ADDRESS)
    for old in (first.token, other.token):
        with pytest.raises(UnauthenticatedError):
            auth.authenticate(old)
    assert auth.authenticate(renewed.token).user.username == "ana"
    assert renewed.token not in (first.token, other.token)
    assert renewed.session.csrf_token != first.session.csrf_token
    assert auth.authenticate(beto.token)  # someone else's session is untouched
    with pytest.raises(UnauthenticatedError):
        auth.sign_in("ana", GOOD, ADDRESS)
    assert auth.sign_in("ana", NEW, ADDRESS)


def test_changing_the_password_clears_the_forced_change_and_stamps_the_time(
    auth: AuthServices, clock: MovableClock
) -> None:
    created = auth.users.create("temp", "Temp")  # a one-time password, change required
    opened = auth.sign_in("temp", created.password, ADDRESS)
    assert opened.user.must_change_password
    clock.advance(minutes=3)
    renewed = auth.change_password(auth.authenticate(opened.token), created.password, NEW, ADDRESS)
    assert renewed.user.must_change_password is False
    assert renewed.user.password_changed_at == clock.now()


def test_a_wrong_current_password_is_forbidden_and_counts_as_a_failure(auth: AuthServices) -> None:
    who = auth.authenticate(auth.sign_in("ana", GOOD, ADDRESS).token)
    for _ in range(5):
        with pytest.raises(ForbiddenError, match="current password is wrong"):
            auth.change_password(who, "not-the-password-1", NEW, ADDRESS)
    with pytest.raises(RateLimitedError):
        auth.change_password(who, GOOD, NEW, ADDRESS)
    with pytest.raises(RateLimitedError):  # the same budget as signing in
        auth.sign_in("ana", GOOD, ADDRESS)


@pytest.mark.parametrize(
    ("new", "fragment"),
    [
        ("short", "at least 12"),
        ("ana", "at least 12"),
        ("password1234", "too common"),
        (GOOD, "differ"),
    ],
)
def test_a_refused_new_password_names_the_field(
    auth: AuthServices, new: str, fragment: str
) -> None:
    who = auth.authenticate(auth.sign_in("ana", GOOD, ADDRESS).token)
    with pytest.raises(InvalidInputError) as refused:
        auth.change_password(who, GOOD, new, ADDRESS)
    assert {e.path for e in refused.value.errors} == {"/new"}
    assert any(fragment in e.message for e in refused.value.errors)
    assert auth.sign_in("ana", GOOD, ADDRESS)  # nothing changed


def test_a_refused_change_keeps_the_session(auth: AuthServices) -> None:
    opened = auth.sign_in("ana", GOOD, ADDRESS)
    with pytest.raises(InvalidInputError):
        auth.change_password(auth.authenticate(opened.token), GOOD, "short", ADDRESS)
    assert auth.authenticate(opened.token)


# --- sessions for the admin -----------------------------------------------------------------


def test_the_session_list_shows_live_sessions_without_secrets(
    auth: AuthServices, clock: MovableClock
) -> None:
    stale = auth.sign_in("beto", "beto-has-a-long-password", ADDRESS)
    clock.advance(hours=13)
    mine = auth.sign_in("ana", GOOD, ADDRESS)
    listed = auth.list_sessions(auth.authenticate(mine.token))
    assert [(s.username, s.current) for s in listed] == [("ana", True)]
    assert listed[0].expires_at == clock.now().replace() + (listed[0].expires_at - clock.now())
    assert not hasattr(listed[0], "token_hash")
    assert stale.session.id not in [s.id for s in listed]


def test_an_admin_can_end_another_session(auth: AuthServices) -> None:
    beto = auth.sign_in("beto", "beto-has-a-long-password", ADDRESS)
    admin = auth.authenticate(auth.sign_in("ana", GOOD, ADDRESS).token)
    auth.end_session(admin, beto.session.id)
    with pytest.raises(UnauthenticatedError):
        auth.authenticate(beto.token)


def test_ending_a_missing_session_is_not_found(auth: AuthServices) -> None:
    admin = auth.authenticate(auth.sign_in("ana", GOOD, ADDRESS).token)
    with pytest.raises(NotFoundError):
        auth.end_session(admin, "nope")


# --- managing users -------------------------------------------------------------------------


def test_a_made_account_gets_a_random_one_time_password_and_must_change_it(
    auth: AuthServices,
) -> None:
    done = auth.users.create("Marta.P", "  Marta   Pérez ", Role.MEMBER, language="en")
    assert len(done.password) == ONE_TIME_LENGTH
    assert set(done.password) <= set(ONE_TIME_ALPHABET)
    assert done.user.must_change_password is True
    assert (done.user.username, done.user.display_name, done.user.language) == (
        "marta.p",
        "Marta Pérez",
        "en",
    )
    assert done.user.password_hash != done.password
    assert done.password not in done.user.password_hash
    assert auth.sign_in("marta.p", done.password, ADDRESS).user.must_change_password


def test_one_time_passwords_do_not_repeat_and_skip_look_alike_characters(
    auth: AuthServices,
) -> None:
    seen = {auth.users.create(f"u{i}", "U").password for i in range(8)}
    assert len(seen) == 8
    assert not set("".join(seen)) & set("0O1lI")


def test_an_explicit_weak_password_is_refused_on_the_password_field(auth: AuthServices) -> None:
    with pytest.raises(InvalidInputError) as refused:
        auth.users.create("weak", "Weak", password="short")
    assert refused.value.errors[0].path == "/password"
    assert auth.users.all()[-1].username != "weak"


def test_a_taken_name_a_bad_name_and_a_bad_language_are_refused(auth: AuthServices) -> None:
    with pytest.raises(ConflictError):
        auth.users.create("ANA", "Another Ana")
    with pytest.raises(InvalidInputError):
        auth.users.create("bad name", "X")
    with pytest.raises(InvalidInputError):
        auth.users.create("fr", "X", language="fr")  # type: ignore[arg-type]
    with pytest.raises(InvalidInputError):
        auth.users.create("noname", "   ")


def test_disabling_ends_the_sessions_and_enabling_allows_signing_in_again(
    auth: AuthServices,
) -> None:
    opened = auth.sign_in("beto", "beto-has-a-long-password", ADDRESS)
    assert auth.users.disable("Beto").active is False
    with pytest.raises(UnauthenticatedError):
        auth.authenticate(opened.token)
    assert auth.users.enable("beto").active is True
    assert auth.sign_in("beto", "beto-has-a-long-password", ADDRESS)


def test_the_only_active_admin_cannot_be_disabled_or_demoted(auth: AuthServices) -> None:
    with pytest.raises(ConflictError, match="only active admin"):
        auth.users.disable("ana")
    with pytest.raises(ConflictError, match="only active admin"):
        auth.users.set_role("ana", Role.MEMBER)
    auth.users.set_role("beto", Role.ADMIN)
    assert auth.users.set_role("ana", Role.MEMBER).role is Role.MEMBER
    with pytest.raises(ConflictError):
        auth.users.disable("beto")


def test_a_disabled_admin_does_not_count_as_a_second_admin(auth: AuthServices) -> None:
    auth.users.set_role("beto", Role.ADMIN)
    auth.users.disable("beto")
    with pytest.raises(ConflictError):
        auth.users.disable("ana")


def test_resetting_gives_a_new_one_time_password_and_ends_the_sessions(auth: AuthServices) -> None:
    opened = auth.sign_in("beto", "beto-has-a-long-password", ADDRESS)
    done = auth.users.reset("beto")
    assert done.user.username == "beto"
    assert len(done.password) == ONE_TIME_LENGTH
    with pytest.raises(UnauthenticatedError):
        auth.authenticate(opened.token)
    with pytest.raises(UnauthenticatedError):
        auth.sign_in("beto", "beto-has-a-long-password", ADDRESS)
    again = auth.sign_in("beto", done.password, ADDRESS)
    assert again.user.must_change_password is True


def test_changing_a_role_is_kept(auth: AuthServices) -> None:
    assert auth.users.set_role("beto", Role.ADMIN).role is Role.ADMIN
    assert [(u.username, u.role) for u in auth.users.all()] == [
        ("ana", Role.ADMIN),
        ("beto", Role.ADMIN),
    ]


@pytest.mark.parametrize("action", ["disable", "enable", "reset"])
def test_an_unknown_account_is_not_found(auth: AuthServices, action: str) -> None:
    with pytest.raises(NotFoundError, match="ghost"):
        getattr(auth.users, action)("ghost")
    with pytest.raises(NotFoundError):
        auth.users.set_role("ghost", Role.ADMIN)
