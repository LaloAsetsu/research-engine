"""Settings: the TOML file that configures the service, and the rules its values must meet."""

from __future__ import annotations

import ipaddress
import tomllib
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# Tailscale hands out addresses from the CGNAT block (IPv4) and a unique-local block (IPv6).
_TAILSCALE_NETWORKS = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
)


class SettingsError(ValueError):
    """The settings file is missing, unreadable, not TOML or breaks a rule."""


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_tailscale(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(
        address in network for network in _TAILSCALE_NETWORKS if address.version == network.version
    )


class Settings(BaseModel):
    """Every key is optional; a mistyped one is an error (``extra="forbid"``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bind_host: str = "127.0.0.1"
    port: int = Field(default=8090, ge=1, le=65535)
    public_origin: str | None = None

    @field_validator("bind_host")
    @classmethod
    def _bind_host_is_private(cls, value: str) -> str:
        if not (is_loopback(value) or _is_tailscale(value)):
            raise ValueError(
                "must be a loopback address or a Tailscale address (never 0.0.0.0 or a LAN address)"
            )
        return value

    @field_validator("public_origin")
    @classmethod
    def _origin_is_an_origin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("must look like http://host:port or https://host")
        if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
            raise ValueError("must be an origin only: no path, query, fragment or credentials")
        return value.rstrip("/")

    @property
    def origin(self) -> str:
        """The origin browsers use, which a write request must match."""
        if self.public_origin is not None:
            return self.public_origin
        host = f"[{self.bind_host}]" if ":" in self.bind_host else self.bind_host
        return f"http://{host}:{self.port}"

    @classmethod
    def defaults(cls) -> Self:
        return cls()


def load_settings(path: Path) -> Settings:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise SettingsError(f"cannot read settings file {path}: {e.strerror or e}") from e
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise SettingsError(f"settings file {path} is not valid TOML: {e}") from e
    try:
        return Settings.model_validate(raw)
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in err['loc']) or 'settings'}: {err['msg']}"
            for err in e.errors(include_url=False, include_input=False)
        )
        raise SettingsError(f"settings file {path} is not valid: {problems}") from None
