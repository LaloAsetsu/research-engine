from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import SEED_FILE

from research_api.bootstrap import Container, Settings, SettingsError, build_container
from research_api.bootstrap.seed import DEFAULT_SEED, SeedUser, default_seed_users, load_seed_users
from research_api.domain.auth import Role
from research_api.domain.errors import InvalidInputError


def test_the_default_seed_is_the_file_in_the_repository() -> None:
    assert DEFAULT_SEED == SEED_FILE
    names = [u.username for u in default_seed_users()]
    assert names == ["ana", "beto", "dani", "temp"]


def test_the_seed_holds_one_admin_one_member_and_one_temporary_password() -> None:
    users = {u.username: u for u in load_seed_users(SEED_FILE)}
    assert users["ana"].role is Role.ADMIN
    assert users["beto"].role is Role.MEMBER
    assert users["temp"].must_change_password and not users["ana"].must_change_password


def test_a_seed_account_can_be_used_to_sign_in(container: Container) -> None:
    opened = container.auth.sign_in("ana", "test-password-ana-01", "10.0.0.1")
    assert opened.user.role is Role.ADMIN


def test_no_seed_means_no_accounts() -> None:
    container = build_container(Settings.defaults(), fake=True, cheap_hashing=True)
    assert container.auth.users.all() == []


def test_a_missing_or_broken_seed_file_is_a_settings_error(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="cannot read"):
        load_seed_users(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(SettingsError, match="not JSON"):
        load_seed_users(bad)


def test_a_seed_with_an_unknown_field_is_refused_without_echoing_passwords(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "users": [
                    {
                        "username": "x",
                        "display_name": "X",
                        "password": "s3cret-value-here",
                        "admin": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SettingsError, match=r"users\.0\.admin") as refused:
        load_seed_users(bad)
    assert "s3cret-value-here" not in str(refused.value)


def test_a_seed_account_with_a_weak_password_stops_the_build() -> None:
    with pytest.raises(InvalidInputError):
        build_container(
            Settings.defaults(),
            fake=True,
            seed=[SeedUser(username="x", display_name="X", password="short")],
            cheap_hashing=True,
        )
