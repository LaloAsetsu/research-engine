"""The import contracts of pyproject.toml must fail on the imports they exist to stop.

Each case copies the package to a temporary folder, adds one import and runs import-linter
there. A contract that stopped working would pass `lint-imports` forever; these cases catch that.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
RUN = "from importlinter.cli import lint_imports_command; lint_imports_command()"

LAYERS = "Hexagonal layers: outer layers may import inner ones, never the reverse"
INDEPENDENT = "Adapters are independent of each other"
PURE = "Domain is pure: no framework, driver or sibling-package imports"
PORTS_ONLY = "Application and ports reach docingest, quantum-sim and qa-bench only through ports"
DOCINGEST = "Only the docingest adapters import docingest"
QUANTUM = "Only the circuits adapter imports quantum_sim"
QA_BENCH = "Only the bench adapter imports qa_bench"
SQL = "Only the persistence adapter imports sqlalchemy and alembic"
PROTECTED = "Only the composition root and entrypoints wire concrete adapters"
PLUGIN = "research_api never imports a docingest plugin (docingest finds it by entry point)"

SRC = "src/research_api"

# The application imports the domain, so a forbidden import in the domain also breaks the
# application's contract through the chain; the cases below list both.

# (file, line added, contracts that must break). An empty set is a control: the import is allowed.
CASES = {
    "domain imports an adapter": (
        "domain/status.py",
        "from ..adapters.clock import SystemClock",
        {LAYERS, PROTECTED},
    ),
    "ports import the application": (
        "ports/clock.py",
        "from ..application import status",
        {LAYERS},
    ),
    "domain imports a web framework": ("domain/status.py", "import fastapi", {PURE, PORTS_ONLY}),
    "domain imports sqlalchemy": ("domain/status.py", "import sqlalchemy", {PURE, PORTS_ONLY, SQL}),
    "domain imports docingest": (
        "domain/status.py",
        "import docingest",
        {PURE, PORTS_ONLY, DOCINGEST},
    ),
    "domain imports jsonschema": ("domain/status.py", "import jsonschema", {PURE, PORTS_ONLY}),
    "application imports httpx": ("application/status.py", "import httpx", {PORTS_ONLY}),
    "ports import quantum_sim": ("ports/clock.py", "import quantum_sim", {PORTS_ONLY, QUANTUM}),
    "application imports an adapter": (
        "application/status.py",
        "from ..adapters import fake",
        {LAYERS, PROTECTED},
    ),
    "the clock adapter imports docingest": (
        "adapters/clock/__init__.py",
        "import docingest",
        {DOCINGEST},
    ),
    "the persistence adapter imports docingest": (
        "adapters/persistence/__init__.py",
        "import docingest",
        {DOCINGEST},
    ),
    "the docingest adapter imports quantum_sim": (
        "adapters/docingest_ask/__init__.py",
        "import quantum_sim",
        {QUANTUM},
    ),
    "the circuits adapter imports qa_bench": (
        "adapters/circuits/__init__.py",
        "import qa_bench",
        {QA_BENCH},
    ),
    "the hashing adapter imports sqlalchemy": (
        "adapters/hashing/__init__.py",
        "import sqlalchemy",
        {SQL},
    ),
    "the files adapter imports alembic": ("adapters/files/__init__.py", "import alembic", {SQL}),
    "bootstrap imports docingest itself": (
        "bootstrap/container.py",
        "import docingest",
        {DOCINGEST},
    ),
    "an entrypoint imports qa_bench itself": ("entrypoints/cli.py", "import qa_bench", {QA_BENCH}),
    "one adapter imports another": (
        "adapters/bench/__init__.py",
        "from .. import files",
        {INDEPENDENT},
    ),
    "an adapter imports a plugin": (
        "adapters/circuits/__init__.py",
        "import docingest_index",
        {PLUGIN},
    ),
    "bootstrap imports a plugin": ("bootstrap/container.py", "import docingest_index", {PLUGIN}),
    "control: docingest in its adapter": (
        "adapters/docingest_ask/__init__.py",
        "import docingest",
        set(),
    ),
    "control: docingest in the circuits adapter": (
        "adapters/circuits/__init__.py",
        "import docingest",
        set(),
    ),
    "control: quantum_sim in the circuits adapter": (
        "adapters/circuits/__init__.py",
        "import quantum_sim",
        set(),
    ),
    "control: qa_bench in the bench adapter": (
        "adapters/bench/__init__.py",
        "import qa_bench",
        set(),
    ),
    "control: sqlalchemy in persistence": (
        "adapters/persistence/__init__.py",
        "import sqlalchemy\nimport alembic",
        set(),
    ),
    "control: bootstrap imports an adapter": (
        "bootstrap/container.py",
        "from ..adapters import persistence",
        set(),
    ),
    "control: an entrypoint imports bootstrap": (
        "entrypoints/cli.py",
        "from .. import bootstrap",
        set(),
    ),
}


def lint(root: Path) -> tuple[int, str]:
    env = {**os.environ, "PYTHONPATH": str(root / "src"), "NO_COLOR": "1"}
    done = subprocess.run(
        [sys.executable, "-c", RUN],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode, " ".join((done.stdout + done.stderr).split())


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    shutil.copy(PACKAGE / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copytree(PACKAGE / "src", tmp_path / "src", ignore=shutil.ignore_patterns("__pycache__"))
    return tmp_path


def test_the_package_keeps_every_contract(sandbox: Path) -> None:
    code, out = lint(sandbox)
    assert code == 0, out
    assert " BROKEN" not in out


@pytest.mark.parametrize("case", CASES)
def test_a_contract_breaks_on_the_import_it_forbids(sandbox: Path, case: str) -> None:
    file, line, expected = CASES[case]
    target = sandbox / SRC / file
    target.write_text(target.read_text(encoding="utf-8") + "\n" + line + "\n", encoding="utf-8")
    code, out = lint(sandbox)
    broken = {
        name
        for name in (
            LAYERS,
            INDEPENDENT,
            PURE,
            PORTS_ONLY,
            DOCINGEST,
            QUANTUM,
            QA_BENCH,
            SQL,
            PROTECTED,
            PLUGIN,
        )
        if f"{name} BROKEN" in out
    }
    assert broken == expected, out
    assert (code != 0) == bool(expected)
