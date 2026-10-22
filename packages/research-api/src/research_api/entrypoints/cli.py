"""The ``research-api`` command.

    research-api serve [--config FILE | --fake]   run the API (file: $RESEARCH_API_CONFIG)
    research-api openapi [--output FILE]          print the OpenAPI 3.1 document (sorted keys)

Exit status: 0 done, 1 settings refused (message on standard error), 2 bad command line.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from ..bootstrap import (
    Container,
    Settings,
    SettingsError,
    build_container,
    load_settings,
)
from ..bootstrap.container import CONFIG_ENV
from .http import build_app


def openapi_document() -> str:
    """The contract as stable text: it is built from fake mode, so no settings file is needed."""
    app = build_app(build_container(Settings.defaults(), fake=True))
    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-api", description="The research engine API.")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the API")
    source = serve.add_mutually_exclusive_group()
    source.add_argument("--config", type=Path, help="settings file (TOML)")
    source.add_argument("--fake", action="store_true", help="serve test data on loopback")
    spec = sub.add_parser("openapi", help="print the OpenAPI document")
    spec.add_argument("--output", "-o", type=Path, help="write to a file instead of stdout")
    return parser


def _container_for(config: Path | None, fake: bool) -> Container:
    if fake:
        return build_container(Settings.defaults(), fake=True)
    path = config or (Path(os.environ[CONFIG_ENV]) if os.environ.get(CONFIG_ENV) else None)
    if path is None:
        raise SettingsError(f"give --config FILE, set {CONFIG_ENV}, or use --fake")
    return build_container(load_settings(path))


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
        container = _container_for(args.config, args.fake)
    except SettingsError as e:
        print(f"research-api: {e}", file=sys.stderr)
        return 1
    uvicorn.run(
        build_app(container),
        host=container.settings.bind_host,
        port=container.settings.port,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
