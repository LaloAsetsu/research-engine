"""Password hashing adapter: Argon2id through pwdlib."""

from __future__ import annotations

import threading

from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from pwdlib.hashers.argon2 import Argon2Hasher

from ...ports.auth import Verification

# Each Argon2id hash holds 64 MiB while it runs. A few at a time keeps a burst of logins from
# using gigabytes; the rest wait their turn.
MAX_CONCURRENT_HASHES = 4


class ArgonPasswordHasher:
    """``PasswordHash.recommended()`` (Argon2id), or a cheaper cost for tests."""

    def __init__(self, *, cheap: bool = False) -> None:
        self._hash = (
            PasswordHash((Argon2Hasher(time_cost=1, memory_cost=8, parallelism=1),))
            if cheap
            else PasswordHash.recommended()
        )
        self._slots = threading.BoundedSemaphore(MAX_CONCURRENT_HASHES)
        with self._slots:
            # A real hash of a throwaway string, made once, so ``burn`` verifies for the same
            # time that a real check takes and the answer for an unknown username is not faster.
            self._dummy = self._hash.hash("not-a-real-password-just-a-timing-dummy")

    def hash(self, password: str) -> str:
        with self._slots:
            return self._hash.hash(password)

    def verify(self, password: str, password_hash: str) -> Verification:
        with self._slots:
            try:
                ok, upgraded = self._hash.verify_and_update(password, password_hash)
            except UnknownHashError:
                return Verification(ok=False)
        return Verification(ok=ok, upgraded_hash=upgraded if ok else None)

    def burn(self, password: str) -> None:
        with self._slots:
            self._hash.verify(password, self._dummy)
