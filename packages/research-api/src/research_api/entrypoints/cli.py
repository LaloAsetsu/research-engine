"""The ``research-api`` command.

    research-api serve [--config FILE | --fake [--seed FILE] [--ask-pause S]] [--port N]
                                                  run the API (file: $RESEARCH_API_CONFIG)
    research-api openapi [--output FILE]          print the OpenAPI 3.1 document (sorted keys)
    research-api db upgrade [--config FILE]       make or update the database tables
    research-api db status [--config FILE]        say whether the tables are current
    research-api users add USERNAME --name NAME [--role admin|member] [--language es|en]
    research-api users list | disable USERNAME | enable USERNAME | reset USERNAME
    research-api users role USERNAME admin|member          (all take --config FILE)

Exit status: 0 done, 1 refused (message on standard error), 2 bad command line.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import uvicorn
from pydantic import ValidationError

from ..adapters.persistence import Database
from ..application.auth import CreatedUser
from ..bootstrap import (
    Container,
    Settings,
    SettingsError,
    build_container,
    load_private_settings,
    open_database,
    open_user_admin,
)
from ..bootstrap.container import CONFIG_ENV, DEFAULT_FAKE_ASK_PAUSE_S, MAX_FAKE_ASK_PAUSE_S
from ..bootstrap.seed import default_seed_users, load_seed_users
from ..domain.auth import LANGUAGES, Role
from ..domain.errors import ResearchApiError
from .http import build_app
from .logs import configure_logging


def openapi_document() -> str:
    """The contract as stable text: it is built from fake mode, so no settings file is needed."""
    container = build_container(Settings.defaults(), fake=True, cheap_hashing=True)
    try:
        app = build_app(container)
        return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    finally:
        container.close()


def _config_option() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--config", type=Path, help=f"settings file (TOML); default: ${CONFIG_ENV}")
    return parent


def _parser() -> argparse.ArgumentParser:
    config = _config_option()
    parser = argparse.ArgumentParser(prog="research-api", description="The research engine API.")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the API")
    source = serve.add_mutually_exclusive_group()
    source.add_argument("--config", type=Path, help="settings file (TOML)")
    source.add_argument("--fake", action="store_true", help="serve test data on loopback")
    serve.add_argument("--port", type=int, help="listen on this port instead of the settings one")
    serve.add_argument(
        "--seed",
        type=Path,
        help="with --fake: JSON file of test accounts (default: tests/fixtures/seed-users.json)",
    )

    serve.add_argument(
        "--ask-pause",
        type=float,
        default=None,
        help="with --fake: seconds the stand-in engine takes per phase of an ask (default: "
        f"{DEFAULT_FAKE_ASK_PAUSE_S}; 0 answers at once)",
    )

    spec = sub.add_parser("openapi", help="print the OpenAPI document")
    spec.add_argument("--output", "-o", type=Path, help="write to a file instead of stdout")

    db = sub.add_parser("db", help="the database tables").add_subparsers(
        dest="action", required=True
    )
    db.add_parser("upgrade", parents=[config], help="create or update the tables")
    db.add_parser("status", parents=[config], help="say whether the tables are current")

    users = sub.add_parser("users", help="accounts (run by the person who administers the server)")
    actions = users.add_subparsers(dest="action", required=True)
    add = actions.add_parser(
        "add", parents=[config], help="make an account with a one-time password"
    )
    add.add_argument("username")
    add.add_argument("--name", required=True, help="display name")
    add.add_argument("--role", choices=[r.value for r in Role], default=Role.MEMBER.value)
    add.add_argument("--language", choices=LANGUAGES, default="es")
    actions.add_parser("list", parents=[config], help="list accounts")
    for name, text in (
        ("disable", "refuse the account and end its sessions"),
        ("enable", "allow a disabled account again"),
        ("reset", "new one-time password; ends the sessions"),
    ):
        actions.add_parser(name, parents=[config], help=text).add_argument("username")
    role = actions.add_parser("role", parents=[config], help="change an account's role")
    role.add_argument("username")
    role.add_argument("role", choices=[r.value for r in Role])
    return parser


def _with_port(settings: Settings, port: int | None) -> Settings:
    if port is None:
        return settings
    try:
        return Settings.model_validate({**settings.model_dump(), "port": port})
    except ValidationError:
        raise SettingsError(f"--port must be between 1 and 65535, not {port}") from None


def _settings_from(config: Path | None) -> Settings:
    path = config or (Path(os.environ[CONFIG_ENV]) if os.environ.get(CONFIG_ENV) else None)
    if path is None:
        raise SettingsError(f"give --config FILE or set {CONFIG_ENV}")
    return load_private_settings(path)


def _container_for(
    config: Path | None,
    fake: bool,
    port: int | None,
    seed: Path | None,
    ask_pause: float | None = None,
) -> Container:
    if fake:
        accounts = load_seed_users(seed) if seed else default_seed_users()
        if not accounts:
            print(
                "research-api: fake mode has no test accounts (give --seed FILE)", file=sys.stderr
            )
        if ask_pause is None:
            ask_pause = DEFAULT_FAKE_ASK_PAUSE_S
        if not 0 <= ask_pause <= MAX_FAKE_ASK_PAUSE_S:
            raise SettingsError(f"--ask-pause must be between 0 and {MAX_FAKE_ASK_PAUSE_S:g}")
        return build_container(
            _with_port(Settings.defaults(), port),
            fake=True,
            seed=accounts,
            fake_ask_pause_s=ask_pause,
        )
    if seed is not None:
        raise SettingsError("--seed only works with --fake")
    if ask_pause is not None:
        raise SettingsError("--ask-pause only works with --fake")
    if config is None and not os.environ.get(CONFIG_ENV):
        raise SettingsError(f"give --config FILE, set {CONFIG_ENV}, or use --fake")
    return build_container(_with_port(_settings_from(config), port))


def _describe(error: SettingsError | ResearchApiError) -> str:
    if not isinstance(error, ResearchApiError):
        return str(error)
    reasons = [e.message for e in getattr(error, "errors", ())]
    return " ".join([error.detail, *reasons])


def _show_password(done: CreatedUser, verb: str) -> None:
    print(
        f'{verb} "{done.user.username}" ({done.user.role.value}). The one-time password follows; '
        "it is shown only now.\nGive it to the person privately. They must change it at "
        "the first sign-in.",
        file=sys.stderr,
    )
    print(done.password)


def _run_db(args: argparse.Namespace) -> int:
    settings = _settings_from(args.config)
    database = open_database(settings, create=args.action == "upgrade")
    try:
        if args.action == "upgrade":
            database.upgrade()
            print(f"database is at revision {database.current_revision()}")
            return 0
        return _print_status(database)
    finally:
        database.close()


def _print_status(database: Database) -> int:
    current, head = database.current_revision(), database.head_revision()
    print(f"revision {current or 'none'}; code expects {head}")
    return 0 if current == head else 1


def _run_users(args: argparse.Namespace) -> int:
    settings = _settings_from(args.config)
    admin = open_user_admin(settings)
    configure_logging(settings.log_dir)  # after the folder checks: the log folder exists now
    try:
        users = admin.users
        match args.action:
            case "add":
                done = users.create(
                    args.username, args.name, Role(args.role), language=args.language
                )
                _show_password(done, "Created")
            case "list":
                for u in users.all():
                    state = "active" if u.active else "disabled"
                    note = " must-change-password" if u.must_change_password else ""
                    print(
                        f"{u.username}\t{u.role.value}\t{state}\t{u.language}\t{u.display_name}{note}"
                    )
            case "disable":
                print(f"disabled {users.disable(args.username).username}")
            case "enable":
                print(f"enabled {users.enable(args.username).username}")
            case "reset":
                _show_password(users.reset(args.username), "Reset")
            case _:  # "role": argparse allows no other action
                user = users.set_role(args.username, Role(args.role))
                print(f"{user.username} is now {user.role.value}")
        return 0
    finally:
        admin.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "openapi":
        text = openapi_document()
        if args.output:
            args.output.write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
        return 0
    try:
        match args.command:
            case "db":
                return _run_db(args)
            case "users":
                return _run_users(args)
            case _:
                container = _container_for(
                    args.config, args.fake, args.port, args.seed, args.ask_pause
                )
                try:
                    configure_logging(
                        None if container.mode == "fake" else container.settings.log_dir
                    )
                except SettingsError:
                    container.close()
                    raise
    except (SettingsError, ResearchApiError) as e:
        print(f"research-api: {_describe(e)}", file=sys.stderr)
        return 1
    uvicorn.run(
        build_app(container),
        host=container.settings.bind_host,
        port=container.settings.port,
        access_log=False,
        # Nothing sits in front of the service, so a forwarded address is a forged one.
        proxy_headers=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
