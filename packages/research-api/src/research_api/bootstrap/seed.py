"""Seeded test accounts for fake mode. They make the web app's end-to-end tests possible."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..domain.auth import Language, Role
from .settings import SettingsError

# In a checkout the file sits beside the tests; an installed copy has no tests folder.
DEFAULT_SEED = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "seed-users.json"


class SeedUser(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    username: str
    display_name: str
    password: str
    role: Role = Role.MEMBER
    must_change_password: bool = False
    language: Language = "es"


class _SeedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    users: list[SeedUser] = Field(default_factory=list)


def load_seed_users(path: Path) -> list[SeedUser]:
    try:
        return _SeedFile.model_validate(json.loads(path.read_text(encoding="utf-8"))).users
    except OSError as e:
        raise SettingsError(f"cannot read seed file {path}: {e.strerror or e}") from e
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}"
            for err in e.errors(include_url=False, include_input=False)
        )
        raise SettingsError(f"seed file {path} is not valid: {problems}") from None
    except ValueError as e:
        raise SettingsError(f"seed file {path} is not JSON: {e}") from None


def default_seed_users() -> list[SeedUser]:
    """The repository's test accounts, or none when this is not a checkout."""
    return load_seed_users(DEFAULT_SEED) if DEFAULT_SEED.is_file() else []
