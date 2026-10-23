from __future__ import annotations

import threading

from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from research_api.adapters.hashing import MAX_CONCURRENT_HASHES, ArgonPasswordHasher

PASSWORD = "correct horse battery"


def test_the_default_hash_is_argon2id_with_the_recommended_cost() -> None:
    hashed = ArgonPasswordHasher().hash(PASSWORD)
    assert hashed.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert PASSWORD not in hashed


def test_a_hash_verifies_only_its_own_password() -> None:
    hasher = ArgonPasswordHasher(cheap=True)
    hashed = hasher.hash(PASSWORD)
    assert hasher.verify(PASSWORD, hashed).ok
    assert not hasher.verify(PASSWORD + "x", hashed).ok
    assert not hasher.verify("", hashed).ok


def test_two_hashes_of_one_password_differ_by_salt() -> None:
    hasher = ArgonPasswordHasher(cheap=True)
    assert hasher.hash(PASSWORD) != hasher.hash(PASSWORD)


def test_a_current_hash_needs_no_upgrade() -> None:
    hasher = ArgonPasswordHasher(cheap=True)
    assert hasher.verify(PASSWORD, hasher.hash(PASSWORD)).upgraded_hash is None


def test_a_hash_made_with_other_costs_is_upgraded_after_a_good_check() -> None:
    old = PasswordHash((Argon2Hasher(time_cost=1, memory_cost=16, parallelism=1),)).hash(PASSWORD)
    hasher = ArgonPasswordHasher(cheap=True)
    done = hasher.verify(PASSWORD, old)
    assert done.ok
    assert done.upgraded_hash is not None
    assert "m=8,t=1,p=1" in done.upgraded_hash
    assert hasher.verify(PASSWORD, done.upgraded_hash).ok


def test_a_wrong_password_is_never_upgraded() -> None:
    old = PasswordHash((Argon2Hasher(time_cost=1, memory_cost=16, parallelism=1),)).hash(PASSWORD)
    done = ArgonPasswordHasher(cheap=True).verify("wrong", old)
    assert not done.ok
    assert done.upgraded_hash is None


def test_text_that_is_not_a_hash_never_verifies() -> None:
    hasher = ArgonPasswordHasher(cheap=True)
    for stored in ("", "plain", PASSWORD, "$2b$12$abcdefghijklmnopqrstuv", "$argon2id$garbage"):
        assert not hasher.verify(PASSWORD, stored).ok


def test_burn_runs_a_real_verification_and_returns_nothing(monkeypatch) -> None:
    hasher = ArgonPasswordHasher(cheap=True)
    calls: list[str] = []
    real = hasher._hash.verify

    def spy(password: str, stored: str) -> bool:
        calls.append(stored)
        return real(password, stored)

    monkeypatch.setattr(hasher._hash, "verify", spy)
    assert hasher.burn("whatever") is None
    assert len(calls) == 1
    assert calls[0].startswith("$argon2id$")


def test_no_more_than_a_few_hashes_run_at_once() -> None:
    hasher = ArgonPasswordHasher(cheap=True)
    running = 0
    peak = 0
    lock = threading.Lock()
    inner = hasher._hash
    real = inner.hash

    def slow_hash(password: str, **kwargs: object) -> str:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        threading.Event().wait(0.05)
        with lock:
            running -= 1
        return real(password)

    inner.hash = slow_hash  # type: ignore[method-assign]
    threads = [
        threading.Thread(target=hasher.hash, args=("p" * 12,))
        for _ in range(3 * MAX_CONCURRENT_HASHES)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert 1 <= peak <= MAX_CONCURRENT_HASHES
